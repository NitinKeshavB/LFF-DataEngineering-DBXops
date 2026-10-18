"""Tests for the ZIP connector: safe unpacking, nested archives, idempotent reruns, and zip-bomb refusal."""

import zipfile
from pathlib import Path

import pytest

from dbxops.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    InMemoryState,
    LandingTarget,
)
from dbxops.connectors.zip_files import (
    ZipConnector,
    _safe_name,
)

SYMLINK_MODE = 0o120000 << 16


def _write_zip(path: Path, entries: dict[str, bytes]):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _write_nested_zip(tmp_path: Path) -> Path:
    """Write ``outer.zip`` containing ``inner.zip`` (itself containing ``deep.txt``) under ``source``, and return it."""
    source = tmp_path / "source"
    source.mkdir()
    inner_path = tmp_path / "fixture" / "inner.zip"
    inner_path.parent.mkdir()
    _write_zip(inner_path, {"deep.txt": b"buried"})
    _write_zip(source / "outer.zip", {"inner.zip": inner_path.read_bytes()})
    return source


def test__safe_name_accepts_a_plain_relative_path():
    """A plain relative path is returned unchanged."""
    assert str(_safe_name("a/b.txt")) == "a/b.txt"


def test__safe_name_rejects_an_absolute_path():
    """An absolute path raises ``ConnectorError``."""
    with pytest.raises(ConnectorError, match="unsafe path"):
        _safe_name("/etc/passwd")


def test__safe_name_rejects_a_parent_traversal():
    """A path containing ``..`` raises ``ConnectorError``."""
    with pytest.raises(ConnectorError, match="unsafe path"):
        _safe_name("../escape.txt")


def test__safe_name_rejects_a_windows_drive():
    """A path with a Windows drive letter raises ``ConnectorError``."""
    with pytest.raises(ConnectorError, match="unsafe path"):
        _safe_name("C:\\evil.txt")


def test__validate_requires_source_path():
    """A config missing source_path is rejected."""
    assert "source_path is required" in ZipConnector().validate({})


def test__validate_rejects_a_non_positive_limit():
    """A limit of 0 or less is rejected."""
    assert any("max_depth" in p for p in ZipConnector().validate({"source_path": "/x", "max_depth": 0}))


def test__validate_passes_a_well_formed_config():
    """A config with only source_path has no problems."""
    assert ZipConnector().validate({"source_path": "/x"}) == []


def test__land_unpacks_every_file_into_a_digest_named_folder(tmp_path: Path):
    """Every entry in the archive is unpacked under a folder named after its content digest."""
    source = tmp_path / "source"
    source.mkdir()
    _write_zip(source / "data.zip", {"a.txt": b"hello", "sub/b.txt": b"world"})
    target = tmp_path / "landing"
    target.mkdir()
    result = ZipConnector().land({"source_path": str(source)}, LandingTarget(target), InMemoryState(), ConnectorEnv())
    assert result.rows == 2
    assert len(result.files) == 2
    assert all((target / f).exists() for f in result.files)


def test__land_skips_an_archive_already_unpacked(tmp_path: Path):
    """An archive whose content hash was already landed is skipped on rerun."""
    source = tmp_path / "source"
    source.mkdir()
    _write_zip(source / "data.zip", {"a.txt": b"hello"})
    target = tmp_path / "landing"
    target.mkdir()
    state = InMemoryState()
    connector = ZipConnector()
    connector.land({"source_path": str(source)}, LandingTarget(target), state, ConnectorEnv())
    result = connector.land({"source_path": str(source)}, LandingTarget(target), state, ConnectorEnv())
    assert result.skipped == 1
    assert result.files == []


def test__land_unpacks_a_nested_archive(tmp_path: Path):
    """A ZIP entry that is itself a ZIP is recursively unpacked."""
    source = _write_nested_zip(tmp_path)
    target = tmp_path / "landing"
    target.mkdir()
    result = ZipConnector().land({"source_path": str(source)}, LandingTarget(target), InMemoryState(), ConnectorEnv())
    assert result.rows == 1
    assert any(f.endswith("deep.txt") for f in result.files)


def test__land_refuses_an_archive_nested_deeper_than_the_limit(tmp_path: Path):
    """Nesting beyond max_depth raises ``ConnectorError`` and leaves no partial output."""
    source = _write_nested_zip(tmp_path)
    target = tmp_path / "landing"
    target.mkdir()
    with pytest.raises(ConnectorError, match="nested deeper than the limit"):
        ZipConnector().land(
            {"source_path": str(source), "max_depth": 0}, LandingTarget(target), InMemoryState(), ConnectorEnv()
        )
    assert not any(target.iterdir())


def test__land_refuses_an_archive_with_too_many_files(tmp_path: Path):
    """An archive with more entries than max_files raises ``ConnectorError``."""
    source = tmp_path / "source"
    source.mkdir()
    _write_zip(source / "data.zip", {"a.txt": b"1", "b.txt": b"2"})
    target = tmp_path / "landing"
    target.mkdir()
    with pytest.raises(ConnectorError, match="too large"):
        ZipConnector().land(
            {"source_path": str(source), "max_files": 1}, LandingTarget(target), InMemoryState(), ConnectorEnv()
        )


def test__land_refuses_a_zip_bomb_by_compression_ratio(tmp_path: Path):
    """An entry compressing far beyond max_ratio raises ``ConnectorError``."""
    source = tmp_path / "source"
    source.mkdir()
    _write_zip(source / "bomb.zip", {"a.txt": b"A" * 100_000})
    target = tmp_path / "landing"
    target.mkdir()
    with pytest.raises(ConnectorError, match="refusing a zip bomb"):
        ZipConnector().land(
            {"source_path": str(source), "max_ratio": 2}, LandingTarget(target), InMemoryState(), ConnectorEnv()
        )


def test__land_refuses_an_unsafe_path_and_leaves_no_partial_folder(tmp_path: Path):
    """An entry with a path-traversal name raises ``ConnectorError`` and leaves no partial output."""
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(source / "evil.zip", "w") as archive:
        archive.writestr("../escape.txt", b"data")
    target = tmp_path / "landing"
    target.mkdir()
    with pytest.raises(ConnectorError, match="unsafe path"):
        ZipConnector().land({"source_path": str(source)}, LandingTarget(target), InMemoryState(), ConnectorEnv())
    assert not any(target.iterdir())


def test__land_refuses_a_symbolic_link_entry(tmp_path: Path):
    """A symbolic-link entry raises ``ConnectorError``."""
    source = tmp_path / "source"
    source.mkdir()
    info = zipfile.ZipInfo("link")
    info.external_attr = SYMLINK_MODE | 0o777 << 16
    with zipfile.ZipFile(source / "evil.zip", "w") as archive:
        archive.writestr(info, "target.txt")
    target = tmp_path / "landing"
    target.mkdir()
    with pytest.raises(ConnectorError, match="symbolic link"):
        ZipConnector().land({"source_path": str(source)}, LandingTarget(target), InMemoryState(), ConnectorEnv())
