"""Download new files from an SFTP server into a landing folder."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Iterator,
    Mapping,
    Protocol,
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

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class SftpEntry:
    """A remote file: its name, size in bytes and modification time (epoch seconds)."""

    name: str
    size: int
    mtime: int


class SftpClient(Protocol):
    """The part of an SFTP session the connector needs."""

    def list(self, path: str) -> list[SftpEntry]:
        """Return the files directly under ``path``."""

    def read(self, path: str) -> Iterator[bytes]:
        """Return the content of a remote file in chunks."""

    def close(self) -> None:
        """End the session."""


def paramiko_client(config: Mapping[str, Any], env: ConnectorEnv) -> SftpClient:
    """Open an SFTP session with paramiko; the password or key comes from a secret reference, never from the config."""
    import paramiko  # type: ignore[import-untyped]  # pylint: disable=import-outside-toplevel  # a connectors extra: only an SFTP job needs it

    transport = paramiko.Transport((config["host"], int(config.get("port", 22))))
    password = env.secret(config["password"]) if config.get("password") else None
    key = (
        paramiko.RSAKey.from_private_key_file(env.secret(config["private_key_file"]))
        if config.get("private_key_file")
        else None
    )
    transport.connect(username=config["username"], password=password, pkey=key)
    session = paramiko.SFTPClient.from_transport(transport)

    class _Session:
        def list(self, path: str) -> list[SftpEntry]:
            """Return the files directly under ``path``."""
            return [
                SftpEntry(item.filename, item.st_size or 0, int(item.st_mtime or 0))
                for item in session.listdir_attr(path)
                if not (item.st_mode or 0) & 0o040000
            ]

        def read(self, path: str) -> Iterator[bytes]:
            """Return the content of a remote file in chunks."""
            with session.open(path, "rb") as handle:
                while chunk := handle.read(CHUNK):
                    yield chunk

        def close(self) -> None:
            """End the session."""
            session.close()
            transport.close()

    return _Session()


class SftpConnector:
    """Download each file under ``remote_path`` that matches ``file_pattern`` and has not been landed (by name, size and time)."""

    kind = "sftp"
    landable = True

    def __init__(self, client_factory: Callable[[Mapping[str, Any], ConnectorEnv], SftpClient] = paramiko_client):
        self._client_factory = client_factory

    def validate(self, config: Mapping[str, Any]) -> list[str]:
        """Return what is wrong with ``config``: host, username and remote_path are required; secrets must be references."""
        problems = missing_keys(config, ("host", "username", "remote_path"))
        if not (config.get("password") or config.get("private_key_file")):
            problems.append("password or private_key_file is required (as a secret reference)")
        return problems + literal_secret_problems(config)

    def land(
        self, config: Mapping[str, Any], target: LandingTarget, state: LandingState, env: ConnectorEnv
    ) -> LandResult:
        """Download new files; a file whose size differs from the listing is discarded and fails the run."""
        result = LandResult()
        client = self._client_factory(config, env)
        try:
            for entry in sorted(client.list(config["remote_path"]), key=lambda item: item.name):
                key = f"sftp:{config['remote_path']}/{entry.name}:{entry.size}:{entry.mtime}"
                if not fnmatch.fnmatch(entry.name, config.get("file_pattern", "*")) or state.get(key):
                    result.skipped += 1
                    continue
                relative, size = target.write_stream(entry.name, client.read(f"{config['remote_path']}/{entry.name}"))
                if size != entry.size:
                    target.path(relative).unlink()
                    raise ConnectorError(f"{entry.name}: downloaded {size} bytes but the server listed {entry.size}")
                result.files.append(relative)
                state.set(key, relative)
        finally:
            client.close()
        return result
