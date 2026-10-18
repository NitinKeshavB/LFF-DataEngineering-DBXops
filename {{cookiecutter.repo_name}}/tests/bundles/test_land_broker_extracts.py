"""
Tests for the DataOps bundle's connector-based landing wiring (resources/dataops.jobs.yml, src/config/land_broker_extracts.yml).

The landing logic itself (dbxops.connectors.cli) is tested once in tests/unit_tests/connectors/test_cli.py; this
file only checks that the dataops bundle wires it correctly.
"""

import yaml

from dbxops.connectors.cli import CONNECTORS
from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import load_bundle

CONFIG = PROJECT_DIR / "bundles/dataops/src/config/land_broker_extracts.yml"


def test__the_task_uses_dbxops_land_with_the_xlsx_connector():
    """The job task is a python_wheel_task on dbxops-land, parameterised for the xlsx connector."""
    doc = load_bundle(PROJECT_DIR / "bundles/dataops")
    task = next(t for t in doc["resources"]["jobs"]["dataops_run"]["tasks"] if t["task_key"] == "land_broker_extracts")
    wheel = task["python_wheel_task"]
    assert wheel["entry_point"] == "dbxops-land"
    assert wheel["parameters"][:2] == ["--connector", "xlsx"]
    assert task["environment_key"] == "land"


def test__the_task_runs_independently_and_is_waited_on_by_publish_run_metadata():
    """land_broker_extracts has no dependency of its own, and publish_run_metadata waits for it."""
    doc = load_bundle(PROJECT_DIR / "bundles/dataops")
    tasks = doc["resources"]["jobs"]["dataops_run"]["tasks"]
    land = next(t for t in tasks if t["task_key"] == "land_broker_extracts")
    assert "depends_on" not in land
    publish = next(t for t in tasks if t["task_key"] == "publish_run_metadata")
    assert {d["task_key"] for d in publish["depends_on"]} >= {"land_broker_extracts"}


def test__the_committed_config_is_accepted_by_the_xlsx_connector_once_filled_in():
    """The config's only missing piece is source_path, which is PLACEHOLDER-blocked until a person sets it."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert "PLACEHOLDER-" in config["source_path"]
    filled = {**config, "source_path": "/tmp/placeholder"}
    assert CONNECTORS["xlsx"]().validate(filled) == []
