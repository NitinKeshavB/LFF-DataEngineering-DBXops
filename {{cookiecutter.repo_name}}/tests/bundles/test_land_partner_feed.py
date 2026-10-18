"""
Tests for the DataOps bundle's Lakeflow-mode landing wiring (resources/dataops.jobs.yml, src/config/land_partner_feed_sftp.yml).

The lakeflow-mode counterpart to test_land_broker_extracts.py (custom mode): both land independently of
generate_data -> bronze -> silver -> gold, side by side in the same bundle. The Auto Loader logic itself is
tested once in tests/unit_tests/connectors/test_sftp_autoloader.py; this file only checks that the dataops
bundle wires it correctly.
"""

import yaml

from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import load_bundle

CONFIG = PROJECT_DIR / "bundles/dataops/src/config/land_partner_feed_sftp.yml"


def test__the_task_runs_dbxops_land_sftp_with_no_extra_dependency():
    """The job task is a python_wheel_task on dbxops-land-sftp, needing only the dbxops wheel."""
    doc = load_bundle(PROJECT_DIR / "bundles/dataops")
    task = next(t for t in doc["resources"]["jobs"]["dataops_run"]["tasks"] if t["task_key"] == "land_partner_feed")
    wheel = task["python_wheel_task"]
    assert wheel["entry_point"] == "dbxops-land-sftp"
    environments = {e["environment_key"]: e for e in doc["resources"]["jobs"]["dataops_run"]["environments"]}
    assert environments[task["environment_key"]]["spec"]["dependencies"] == ["${var.dbxops_wheel_path}"]


def test__the_task_runs_independently_and_is_waited_on_by_publish_run_metadata():
    """land_partner_feed has no dependency of its own, and publish_run_metadata waits for it."""
    doc = load_bundle(PROJECT_DIR / "bundles/dataops")
    tasks = doc["resources"]["jobs"]["dataops_run"]["tasks"]
    land = next(t for t in tasks if t["task_key"] == "land_partner_feed")
    assert "depends_on" not in land
    publish = next(t for t in tasks if t["task_key"] == "publish_run_metadata")
    assert {d["task_key"] for d in publish["depends_on"]} >= {"land_partner_feed"}


def test__the_committed_config_names_a_connection_and_an_sftp_source():
    """The config's connection and source_path are PLACEHOLDER-blocked until a person sets them."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert "PLACEHOLDER-" in config["connection"]
    assert "PLACEHOLDER-" in config["source_path"]


def test__the_connection_setup_note_exists():
    """A person must create the UC Connection once before this task can run; the note explains how."""
    note = PROJECT_DIR / "bundles/dataops/src/config/land_partner_feed_connection.md"
    assert note.exists()
    assert "CREATE CONNECTION" in note.read_text(encoding="utf-8")
