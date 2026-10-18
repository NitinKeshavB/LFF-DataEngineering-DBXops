"""
The connector interface: one way to get a source that no managed connector reaches into a Volume, safely and repeatably.

A connector lands files in a ``LandingTarget`` (a Unity Catalog Volume path, or a local folder in tests); Auto Loader
reads them from there. Every connector validates its own config, never holds a secret in clear text, writes only
inside the target, and can run twice without landing the same data twice (``LandingState`` remembers what it has
done). Clients (SFTP, HTTP, JDBC) are injected, so the logic is tested without a network.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Protocol,
    runtime_checkable,
)

SECRET_REF = re.compile(r"^\{\{secrets/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\}\}$")
SECRET_KEYS = ("password", "passwd", "token", "secret", "api_key", "apikey", "private_key", "authorization")


class ConnectorError(Exception):
    """Raised when a connector cannot run: bad config, an unsafe path, or a source that misbehaves."""


class NotLandable(ConnectorError):
    """Raised by a connector that is a streaming source in a spec, not a landing job."""


@runtime_checkable
class SecretResolver(Protocol):
    """Reads a secret by scope and key (a secret scope in a job; a fake in tests)."""

    def get(self, scope: str, key: str) -> str:
        """Return the secret value."""


class LandingState(Protocol):
    """A small key-value memory that survives between runs, so a connector can skip what it has already landed."""

    def get(self, key: str) -> str | None:
        """Return the stored value, or ``None``."""

    def set(self, key: str, value: str) -> None:
        """Store a value."""


class InMemoryState:
    """A ``LandingState`` held in a dict, for tests."""

    def __init__(self, values: Mapping[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, key: str) -> str | None:
        """Return the stored value, or ``None``."""
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        """Store a value."""
        self._values[key] = value


class JsonFileState:
    """A ``LandingState`` kept in one JSON file, written atomically; a Volume path works as well as a local one."""

    def __init__(self, path: Path):
        self._path = path

    def _load(self) -> dict[str, str]:
        return json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}

    def get(self, key: str) -> str | None:
        """Return the stored value, or ``None``."""
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        """Store a value, replacing the file in one step."""
        values = self._load()
        values[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self._path.parent, delete=False, encoding="utf-8") as handle:
            json.dump(values, handle, sort_keys=True)
        os.replace(handle.name, self._path)


@dataclass(frozen=True)
class ConnectorEnv:
    """What a connector may use besides its config: secrets, a clock and a sleep, all replaceable in tests."""

    secrets: SecretResolver | None = None
    now: Callable[[], datetime] = datetime.now
    sleep: Callable[[float], None] = lambda seconds: None

    def secret(self, value: str) -> str:
        """Return ``value``, or the secret it names when it is a ``{{secrets/scope/key}}`` reference."""
        match = SECRET_REF.match(value)
        if not match:
            return value
        if self.secrets is None:
            raise ConnectorError(f"{value} needs a secret resolver, and none was given")
        return self.secrets.get(match.group(1), match.group(2))


@dataclass
class LandResult:
    """What one run did: the files it wrote (relative to the target), rows landed, items skipped and a watermark."""

    files: list[str] = field(default_factory=list)
    rows: int = 0
    skipped: int = 0
    watermark: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LandingTarget:
    """The folder a connector writes to; every path is checked to stay inside it."""

    root: Path

    def path(self, *parts: str) -> Path:
        """Return a path under the root; raise ``ConnectorError`` if it would leave the root."""
        root = self.root.resolve()
        candidate = root.joinpath(*parts).resolve()
        if candidate != root and root not in candidate.parents:
            raise ConnectorError(f"{'/'.join(parts)!r} would write outside the landing folder")
        return candidate

    def write_bytes(self, relative: str, data: bytes) -> str:
        """Write a file in one step (temporary name, then rename) and return its path relative to the root."""
        destination = self.path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix=".part") as handle:
            handle.write(data)
        os.replace(handle.name, destination)
        return destination.relative_to(self.root.resolve()).as_posix()

    def write_stream(self, relative: str, chunks: Iterable[bytes]) -> tuple[str, int]:
        """Write a file from chunks in one step; return its relative path and its size in bytes."""
        destination = self.path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix=".part") as handle:
            for chunk in chunks:
                handle.write(chunk)
                size += len(chunk)
        os.replace(handle.name, destination)
        return destination.relative_to(self.root.resolve()).as_posix(), size


@runtime_checkable
class Connector(Protocol):
    """One landing plugin. ``landable`` is false for a connector that only describes a streaming source."""

    kind: str
    landable: bool

    def validate(self, config: Mapping[str, Any]) -> list[str]:
        """Return what is wrong with ``config``; an empty list means it can run."""

    def land(
        self, config: Mapping[str, Any], target: LandingTarget, state: LandingState, env: ConnectorEnv
    ) -> LandResult:
        """Land the source into ``target``; running again lands only what is new."""


def literal_secret_problems(config: Mapping[str, Any], prefix: str = "") -> list[str]:
    """Return one problem for each secret-looking key whose value is a literal rather than a ``{{secrets/..}}`` reference."""
    problems = []
    for key, value in config.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            problems += literal_secret_problems(value, f"{name}.")
        elif isinstance(value, str) and value and any(word in key.lower() for word in SECRET_KEYS):
            if not SECRET_REF.match(value):
                problems.append(
                    f"{name}: a secret must be a {{{{secrets/<scope>/<key>}}}} reference, not a literal value"
                )
    return problems


def missing_keys(config: Mapping[str, Any], required: tuple[str, ...]) -> list[str]:
    """Return one problem for each required key that is absent or empty."""
    return [f"{key} is required" for key in required if not config.get(key)]
