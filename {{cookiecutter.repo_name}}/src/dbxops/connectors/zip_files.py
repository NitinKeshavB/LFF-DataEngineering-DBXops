"""Unpack ZIP archives, nested ones included, into a landing folder; the unsafe ones are refused."""

from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import (
    Path,
    PurePosixPath,
)
from typing import (
    Any,
    Mapping,
)

from dbxops.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    LandingState,
    LandingTarget,
    LandResult,
    literal_secret_problems,
    missing_keys,
)

SYMLINK = 0o120000


@dataclass
class _Budget:
    """What an unpack may still spend: files, bytes and nesting depth. Exceeding it is a zip bomb."""

    files: int
    size: int
    depth: int
    ratio: int


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or (path.parts and path.parts[0].endswith(":")):
        raise ConnectorError(f"unsafe path in archive: {name!r}")
    return path


def _check_entry(info: zipfile.ZipInfo, budget: _Budget) -> None:
    if (info.external_attr >> 16) & 0o170000 == SYMLINK:
        raise ConnectorError(f"symbolic link in archive: {info.filename!r}")
    budget.files -= 1
    budget.size -= info.file_size
    if budget.files < 0 or budget.size < 0:
        raise ConnectorError("archive is too large: it exceeds the file or size limit")
    if info.compress_size and info.file_size / info.compress_size > budget.ratio:
        raise ConnectorError(f"compression ratio of {info.filename!r} is above {budget.ratio}: refusing a zip bomb")


def _unpack(data: bytes, folder: Path, budget: _Budget) -> int:
    """Unpack ``data`` into ``folder``, recursing into inner archives; return the files written."""
    if budget.depth < 0:
        raise ConnectorError("archives are nested deeper than the limit")
    written = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)
            _check_entry(info, budget)
            content = archive.read(info)
            if name.suffix.lower() == ".zip" and zipfile.is_zipfile(io.BytesIO(content)):
                inner = _Budget(budget.files, budget.size, budget.depth - 1, budget.ratio)
                written += _unpack(content, folder / name.parent / name.stem, inner)
                continue
            destination = folder.joinpath(*name.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            written += 1
    return written


class ZipConnector:
    """Unpack each ``*.zip`` under ``source_path`` into ``<stem>__<sha8>/``; a ZIP already unpacked is skipped."""

    kind = "zip"
    landable = True

    def validate(self, config: Mapping[str, Any]) -> list[str]:
        """Return what is wrong with ``config``: ``source_path`` is required; limits must be positive numbers."""
        problems = missing_keys(config, ("source_path",)) + literal_secret_problems(config)
        for key in ("max_depth", "max_files", "max_total_mb", "max_ratio"):
            if key in config and not (isinstance(config[key], int) and config[key] > 0):
                problems.append(f"{key} must be a positive whole number")
        return problems

    def land(
        self, config: Mapping[str, Any], target: LandingTarget, state: LandingState, _env: ConnectorEnv
    ) -> LandResult:
        """Unpack every new archive; one that is unsafe stops the run without recording or leaving a partial folder."""
        result = LandResult()
        for archive in sorted(Path(config["source_path"]).rglob("*.zip")):
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if state.get(f"zip:{digest}"):
                result.skipped += 1
                continue
            folder = f"{archive.stem}__{digest[:8]}"
            budget = _Budget(
                config.get("max_files", 10_000),
                config.get("max_total_mb", 2048) * 1024 * 1024,
                config.get("max_depth", 5),
                config.get("max_ratio", 100),
            )
            staging = target.path(".staging", digest)
            try:
                count = _unpack(archive.read_bytes(), staging, budget)
                final = target.path(folder)
                final.parent.mkdir(parents=True, exist_ok=True)
                shutil.rmtree(final, ignore_errors=True)
                staging.rename(final)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            result.files += sorted(
                p.relative_to(target.root.resolve()).as_posix() for p in final.rglob("*") if p.is_file()
            )
            result.rows += count
            state.set(f"zip:{digest}", folder)
        return result
