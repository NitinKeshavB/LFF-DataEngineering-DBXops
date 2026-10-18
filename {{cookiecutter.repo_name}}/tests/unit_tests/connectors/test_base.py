"""Tests for the connector contract: secrets, state, and a landing target that cannot be escaped."""

from datetime import datetime
from pathlib import Path

import pytest

from dbxops.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    InMemoryState,
    JsonFileState,
    LandingTarget,
    LandResult,
    literal_secret_problems,
    missing_keys,
)


class FakeSecrets:
    """A ``SecretResolver`` backed by a dict, for tests."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, scope: str, key: str) -> str:
        """Return the secret value."""
        return self._values[f"{scope}/{key}"]


def test__connector_env_resolves_a_secret_reference():
    """A ``{{secrets/scope/key}}`` value is resolved through the ``SecretResolver``."""
    env = ConnectorEnv(secrets=FakeSecrets({"my_scope/my_key": "s3cr3t"}))
    assert env.secret("{{secrets/my_scope/my_key}}") == "s3cr3t"


def test__connector_env_passes_through_a_literal_value():
    """A value that is not a secret reference is returned unchanged."""
    env = ConnectorEnv()
    assert env.secret("not-a-secret-reference") == "not-a-secret-reference"


def test__connector_env_without_a_resolver_raises_on_a_secret_reference():
    """Resolving a secret reference with no resolver configured raises."""
    env = ConnectorEnv()
    with pytest.raises(ConnectorError, match="needs a secret resolver"):
        env.secret("{{secrets/my_scope/my_key}}")


def test__connector_env_default_now_and_sleep_do_not_raise():
    """The default ``now`` and ``sleep`` callables are usable without configuration."""
    env = ConnectorEnv()
    assert isinstance(env.now(), datetime)
    env.sleep(0)


def test__in_memory_state_round_trips_and_defaults_to_none():
    """A value set is returned by a later get; an unset key returns ``None``."""
    state = InMemoryState()
    assert state.get("missing") is None
    state.set("key", "value")
    assert state.get("key") == "value"


def test__in_memory_state_starts_from_given_values():
    """The state can be seeded with initial values at construction."""
    state = InMemoryState({"seeded": "yes"})
    assert state.get("seeded") == "yes"


def test__json_file_state_round_trips_across_instances(tmp_path: Path):
    """A value written by one instance is read back by a new instance over the same file."""
    path = tmp_path / "nested" / "state.json"
    JsonFileState(path).set("key", "value")
    assert JsonFileState(path).get("key") == "value"


def test__json_file_state_returns_none_before_the_file_exists(tmp_path: Path):
    """Reading before any write returns ``None`` rather than failing."""
    assert JsonFileState(tmp_path / "state.json").get("key") is None


def test__json_file_state_keeps_other_keys_on_a_second_write(tmp_path: Path):
    """Writing one key does not drop a previously written key."""
    path = tmp_path / "state.json"
    state = JsonFileState(path)
    state.set("a", "1")
    state.set("b", "2")
    assert state.get("a") == "1"
    assert state.get("b") == "2"


def test__land_result_defaults_are_empty():
    """A freshly constructed ``LandResult`` has no files, rows, skips, watermark or notes."""
    result = LandResult()
    assert result.files == []
    assert result.rows == 0
    assert result.skipped == 0
    assert result.watermark is None
    assert not result.notes


def test__landing_target_path_stays_inside_the_root(tmp_path: Path):
    """A plain relative path resolves under the target's root."""
    target = LandingTarget(tmp_path)
    assert target.path("a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test__landing_target_path_rejects_a_traversal_attempt(tmp_path: Path):
    """A path that would escape the root raises ``ConnectorError``."""
    target = LandingTarget(tmp_path)
    with pytest.raises(ConnectorError, match="outside the landing folder"):
        target.path("..", "escaped.txt")


def test__landing_target_write_bytes_creates_parents_and_returns_a_relative_path(tmp_path: Path):
    """Writing bytes creates missing parent folders and returns the path relative to the root."""
    target = LandingTarget(tmp_path)
    relative = target.write_bytes("sub/dir/file.bin", b"hello")
    assert relative == "sub/dir/file.bin"
    assert (tmp_path / "sub" / "dir" / "file.bin").read_bytes() == b"hello"


def test__landing_target_write_stream_returns_the_relative_path_and_size(tmp_path: Path):
    """Writing a stream of chunks returns the relative path and the total byte count."""
    target = LandingTarget(tmp_path)
    relative, size = target.write_stream("file.bin", [b"abc", b"de"])
    assert relative == "file.bin"
    assert size == 5
    assert (tmp_path / "file.bin").read_bytes() == b"abcde"


def test__literal_secret_problems_flags_a_literal_password():
    """A password given as a literal value is reported as a problem."""
    problems = literal_secret_problems({"password": "hunter2"})
    assert problems == ["password: a secret must be a {{secrets/<scope>/<key>}} reference, not a literal value"]


def test__literal_secret_problems_accepts_a_secret_reference():
    """A password given as a ``{{secrets/..}}`` reference is not flagged."""
    assert literal_secret_problems({"password": "{{secrets/scope/key}}"}) == []


def test__literal_secret_problems_ignores_non_secret_keys():
    """A key whose name does not look like a secret is never flagged."""
    assert literal_secret_problems({"username": "anything"}) == []


def test__literal_secret_problems_recurses_into_nested_mappings():
    """A literal secret nested under another key is reported with its dotted path."""
    problems = literal_secret_problems({"auth": {"token": "literal"}})
    assert problems == ["auth.token: a secret must be a {{secrets/<scope>/<key>}} reference, not a literal value"]


def test__missing_keys_reports_absent_and_empty_values():
    """A key that is absent or empty is reported; one present and non-empty is not."""
    assert missing_keys({"a": "", "b": "x"}, ("a", "b", "c")) == ["a is required", "c is required"]


def test__missing_keys_passes_when_everything_required_is_present():
    """No problems are reported when every required key is present."""
    assert missing_keys({"a": "x"}, ("a",)) == []
