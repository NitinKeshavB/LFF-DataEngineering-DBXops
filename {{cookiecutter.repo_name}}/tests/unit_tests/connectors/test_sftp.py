"""Tests for the SFTP connector: pattern matching, skip-on-rerun, and a download that must match the listing."""

from pathlib import Path
from typing import Iterator

import pytest

from dbxops.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    InMemoryState,
    LandingTarget,
)
from dbxops.connectors.sftp import (
    SftpConnector,
    SftpEntry,
)


class FakeSftpClient:
    """An ``SftpClient`` backed by an in-memory file map, for tests."""

    def __init__(self, entries: list[SftpEntry], contents: dict[str, bytes]):
        self._entries = entries
        self._contents = contents
        self.closed = False

    def list(self, _path: str) -> list[SftpEntry]:
        """Return the configured entries."""
        return self._entries

    def read(self, path: str) -> Iterator[bytes]:
        """Yield the configured content for ``path`` in one chunk."""
        name = path.rsplit("/", 1)[-1]
        yield self._contents[name]

    def close(self) -> None:
        """Record that the session was closed."""
        self.closed = True


def _connector(client: FakeSftpClient) -> SftpConnector:
    return SftpConnector(lambda config, env: client)


VALID_CONFIG = {
    "host": "sftp.example.com",
    "username": "user",
    "remote_path": "/inbound",
    "password": "{{secrets/scope/key}}",
}


def test__validate_requires_host_username_and_remote_path():
    """A config missing host, username and remote_path is rejected for all three."""
    problems = SftpConnector().validate({"password": "{{secrets/s/k}}"})
    assert "host is required" in problems
    assert "username is required" in problems
    assert "remote_path is required" in problems


def test__validate_requires_a_password_or_a_private_key():
    """A config with neither a password nor a private key is rejected."""
    problems = SftpConnector().validate({"host": "h", "username": "u", "remote_path": "/p"})
    assert any("password or private_key_file is required" in p for p in problems)


def test__validate_rejects_a_literal_password():
    """A password given as a literal value is rejected."""
    config = {**VALID_CONFIG, "password": "literal-value"}
    assert any("secret" in p for p in SftpConnector().validate(config))


def test__validate_passes_a_well_formed_config():
    """A complete config has no problems."""
    assert SftpConnector().validate(VALID_CONFIG) == []


def test__land_downloads_only_files_matching_the_pattern(tmp_path: Path):
    """Only files matching ``file_pattern`` are downloaded; the rest are skipped."""
    entries = [SftpEntry("a.csv", 3, 100), SftpEntry("b.txt", 3, 100)]
    client = FakeSftpClient(entries, {"a.csv": b"abc", "b.txt": b"xyz"})
    config = {**VALID_CONFIG, "file_pattern": "*.csv"}
    result = _connector(client).land(config, LandingTarget(tmp_path), InMemoryState(), ConnectorEnv())
    assert result.files == ["a.csv"]
    assert result.skipped == 1
    assert (tmp_path / "a.csv").read_bytes() == b"abc"


def test__land_skips_a_file_already_recorded_in_state(tmp_path: Path):
    """A file whose name, size and mtime are already recorded in state is skipped."""
    entry = SftpEntry("a.csv", 3, 100)
    client = FakeSftpClient([entry], {"a.csv": b"abc"})
    state = InMemoryState({f"sftp:{VALID_CONFIG['remote_path']}/a.csv:3:100": "a.csv"})
    result = _connector(client).land(VALID_CONFIG, LandingTarget(tmp_path), state, ConnectorEnv())
    assert result.files == []
    assert result.skipped == 1


def test__land_records_each_downloaded_file_in_state(tmp_path: Path):
    """A newly downloaded file is recorded in state by name, size and mtime."""
    entry = SftpEntry("a.csv", 3, 100)
    client = FakeSftpClient([entry], {"a.csv": b"abc"})
    state = InMemoryState()
    _connector(client).land(VALID_CONFIG, LandingTarget(tmp_path), state, ConnectorEnv())
    assert state.get(f"sftp:{VALID_CONFIG['remote_path']}/a.csv:3:100") == "a.csv"


def test__land_closes_the_client_even_on_success(tmp_path: Path):
    """The SFTP session is closed after a successful run."""
    entry = SftpEntry("a.csv", 3, 100)
    client = FakeSftpClient([entry], {"a.csv": b"abc"})
    _connector(client).land(VALID_CONFIG, LandingTarget(tmp_path), InMemoryState(), ConnectorEnv())
    assert client.closed is True


def test__land_raises_and_removes_the_partial_file_on_a_size_mismatch(tmp_path: Path):
    """A download whose size disagrees with the listing fails the run and removes the partial file."""
    entry = SftpEntry("a.csv", 999, 100)
    client = FakeSftpClient([entry], {"a.csv": b"abc"})
    with pytest.raises(ConnectorError, match="downloaded 3 bytes but the server listed 999"):
        _connector(client).land(VALID_CONFIG, LandingTarget(tmp_path), InMemoryState(), ConnectorEnv())
    assert not (tmp_path / "a.csv").exists()
    assert client.closed is True
