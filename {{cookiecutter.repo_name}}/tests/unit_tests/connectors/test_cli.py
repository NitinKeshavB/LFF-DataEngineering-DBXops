"""Tests for the dbxops-land job task (dbxops.connectors.cli): the landing CLI every bundle's job task runs."""

import json
from pathlib import Path

import pytest

from dbxops.connectors.base import LandResult
from dbxops.connectors.cli import (
    CONNECTORS,
    main,
)
from dbxops.connectors.xlsx_files import XlsxConnector


class FakeSecrets:
    """A ``SecretResolver`` backed by a dict, for tests."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, scope: str, key: str) -> str:
        """Return the secret value."""
        return self._values[f"{scope}/{key}"]


def test__connectors_registry_offers_the_three_file_based_connectors():
    """JDBC is not offered: it needs a real Spark JdbcSource wired by hand, not a generic CLI."""
    assert set(CONNECTORS) == {"sftp", "xlsx", "zip"}
    assert CONNECTORS["xlsx"] is XlsxConnector


def test__lands_new_xlsx_workbooks_and_is_idempotent_on_rerun(tmp_path: Path):
    """A dropped workbook lands as Parquet; running again with the same state lands nothing new."""
    from openpyxl import Workbook  # pylint: disable=import-outside-toplevel

    source = tmp_path / "source"
    source.mkdir()
    workbook = Workbook()
    workbook.active.append(["claim_id", "amount"])
    workbook.active.append(["CLM0000001", 123.45])
    workbook.save(source / "extract.xlsx")

    config_path = tmp_path / "config.yml"
    config_path.write_text(json.dumps({"source_path": str(source)}), encoding="utf-8")
    output = tmp_path / "landing"
    state = tmp_path / "state.json"
    argv = ["--connector", "xlsx", "--config", str(config_path), "--output", str(output), "--state", str(state)]

    assert main(argv) == 0
    assert len(list(output.glob("*.parquet"))) == 1

    assert main(argv) == 0
    assert len(list(output.glob("*.parquet"))) == 1


def test__a_bad_config_exits_with_the_validation_problems(tmp_path: Path):
    """A config missing source_path exits non-zero and never calls land()."""
    config_path = tmp_path / "config.yml"
    config_path.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="source_path is required"):
        main(
            [
                "--connector",
                "xlsx",
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "landing"),
                "--state",
                str(tmp_path / "state.json"),
            ]
        )


def test__a_secret_reference_resolves_through_the_given_resolver(tmp_path: Path):
    """The sftp config's password reference is resolved through the injected SecretResolver, not read literally."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        json.dumps({"host": "h", "username": "u", "remote_path": "/p", "password": "{{secrets/scope/key}}"}),
        encoding="utf-8",
    )
    secrets = FakeSecrets({"scope/key": "s3cr3t"})
    captured: dict[str, object] = {}
    original = CONNECTORS["sftp"]

    class RecordingConnector(original):  # pylint: disable=too-few-public-methods
        """Wraps the real connector to capture the resolved secret instead of opening a network session."""

        def land(self, config, target, state, env):  # noqa: D102
            captured["password"] = env.secret(config["password"])
            return LandResult()

    CONNECTORS["sftp"] = RecordingConnector
    try:
        assert (
            main(
                [
                    "--connector",
                    "sftp",
                    "--config",
                    str(config_path),
                    "--output",
                    str(tmp_path / "landing"),
                    "--state",
                    str(tmp_path / "state.json"),
                ],
                secrets=secrets,
            )
            == 0
        )
    finally:
        CONNECTORS["sftp"] = original
    assert captured["password"] == "s3cr3t"
