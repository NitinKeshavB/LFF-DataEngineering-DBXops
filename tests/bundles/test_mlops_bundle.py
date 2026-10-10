"""Checks specific to the MLOps bundle that the generic bundle tests do not cover: the job's wiring and the serving switch."""

import pytest

from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import load_bundle

BUNDLE = PROJECT_DIR / "bundles/mlops"


@pytest.fixture(scope="module")
def doc():
    """Return the merged MLOps bundle configuration."""
    return load_bundle(BUNDLE)


@pytest.fixture(scope="module")
def tasks(doc):
    """Return the tasks of the mlops_run job by key."""
    return {task["task_key"]: task for task in doc["resources"]["jobs"]["mlops_run"]["tasks"]}


def test__the_job_runs_features_then_train_then_promote_then_score(tasks):
    """Each step waits for the one before it, so scoring only sees a promoted champion."""
    assert [(key, [d["task_key"] for d in task.get("depends_on", [])]) for key, task in tasks.items()][:4] == [
        ("features", []),
        ("train", ["features"]),
        ("promote", ["train"]),
        ("score", ["promote"]),
    ]


def test__serving_is_reconciled_only_when_the_condition_task_is_true(doc, tasks):
    """The serve task waits for the true outcome of a condition on deploy_serving, which only uat, preprod and prod set."""
    condition = tasks["serving_enabled"]["condition_task"]
    assert condition == {"op": "EQUAL_TO", "left": "${var.deploy_serving}", "right": "true"}
    assert tasks["serve"]["depends_on"] == [{"task_key": "serving_enabled", "outcome": "true"}]
    assert doc["variables"]["deploy_serving"]["default"] == "false"
    enabled = {name for name, target in doc["targets"].items() if target["variables"].get("deploy_serving") == "true"}
    assert enabled == {"uat", "preprod", "prod"}


def test__the_dbxops_wheel_is_installed_from_a_variable_with_an_empty_default(doc):
    """CI sets dbxops_wheel_path; the job environment lists it first so lifecycle_task.py can import dbxops."""
    assert doc["variables"]["dbxops_wheel_path"]["default"] == ""
    environment = doc["resources"]["jobs"]["mlops_run"]["environments"][0]
    assert environment["spec"]["dependencies"][0] == "${var.dbxops_wheel_path}"


def test__every_python_file_and_config_the_job_names_exists_in_the_bundle(tasks):
    """The scripts, gate and serving config referenced by the job are files of the bundle."""
    for task in tasks.values():
        script = task.get("spark_python_task", {}).get("python_file")
        if script:
            assert (BUNDLE / "resources" / script).resolve().is_file(), script
    parameters = [p for task in tasks.values() for p in task.get("spark_python_task", {}).get("parameters", [])]
    configs = [p.replace("${workspace.file_path}/", "") for p in parameters if p.startswith("${workspace.file_path}/")]
    assert sorted(configs) == ["src/gates/claims_fraud_model.gate.yml", "src/serving/claims_fraud_endpoint.yml"]
    assert all((BUNDLE / config).is_file() for config in configs)
