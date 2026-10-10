"""Tests for the MLOps bundle's lifecycle job task (bundles/mlops/src/jobs/mlops_lifecycle_task.py) and its gate and serving configs."""

import json
import re

import pytest
import yaml

from dbxops.lifecycle import (
    build_tasks,
    load_reconcile_config,
)
from dbxops.promotion import PromotionGate
from tests.bundles.job_script import loaded_script
from tests.consts import PROJECT_DIR

BUNDLE = PROJECT_DIR / "bundles/mlops"
SCRIPT = BUNDLE / "src/jobs/mlops_lifecycle_task.py"
GATE = BUNDLE / "src/gates/claims_fraud_model.gate.yml"
ENDPOINT = BUNDLE / "src/serving/claims_fraud_endpoint.yml"
MODEL = "cat.example_claims_ml.claims_fraud_model"


@pytest.fixture(scope="module")
def task():
    """Import the task script as a module (it is a job script, not part of the package)."""
    with loaded_script(SCRIPT) as module:
        yield module


@pytest.fixture
def factory(fake_gateway):
    """Return a gateway factory that hands out the shared fake."""
    return lambda warehouse_id: fake_gateway


def reconcile_argv(dry_run=False):
    """Return the arguments the job passes to the reconcile task, for target uat."""
    argv = [
        "reconcile",
        "--model",
        MODEL,
        "--config",
        str(ENDPOINT),
        "--catalog",
        "cat",
        "--project-id",
        "example_claims",
        "--target",
        "uat",
    ]
    return argv + ["--dry-run"] * dry_run


def test__the_gate_config_loads_and_requires_the_auc_threshold():
    """The gate file is valid for dbxops.promotion and names auc, the metric train_model.py logs."""
    gate = PromotionGate.from_yaml(GATE)
    assert (gate.eval_kind, gate.challenger_alias, gate.champion_alias) == ("model", "challenger", "champion")
    passing = gate.evaluate(MODEL, 1, {"auc": 0.80})
    failing = gate.evaluate(MODEL, 1, {"auc": 0.79})
    assert passing.passed and not failing.passed
    assert not gate.evaluate(MODEL, 1, {"accuracy": 0.99}).passed


def test__promote_promotes_the_version_holding_the_challenger_alias(task, fake_gateway, factory, capsys):
    """The version comes from @challenger, the gate reads the run's metrics, and a pass moves @champion."""
    fake_gateway.aliases[(MODEL, "challenger")] = 5
    fake_gateway.metrics[(MODEL, 5)] = {"auc": 0.93}
    assert task.main(["promote", "--model", MODEL, "--gate", str(GATE)], factory) == 0
    assert fake_gateway.aliases[(MODEL, "champion")] == 5
    assert f"{MODEL} version 5: promoted (@champion)" in capsys.readouterr().out


def test__promote_keeps_the_champion_when_the_gate_fails(task, fake_gateway, factory):
    """A version below the threshold exits 1 and leaves @champion where it was."""
    fake_gateway.aliases.update({(MODEL, "challenger"): 6, (MODEL, "champion"): 4})
    fake_gateway.metrics[(MODEL, 6)] = {"auc": 0.5}
    assert task.main(["promote", "--model", MODEL, "--gate", str(GATE)], factory) == 1
    assert fake_gateway.aliases[(MODEL, "champion")] == 4


def test__promote_without_a_challenger_exits_one(task, factory, capsys):
    """Nothing to promote is a failure that names the missing alias."""
    assert task.main(["promote", "--model", MODEL, "--gate", str(GATE)], factory) == 1
    assert "@challenger" in capsys.readouterr().err


def test__reconcile_creates_the_endpoint_for_the_champion_version(task, fake_gateway, factory):
    """The endpoint serves the champion version of the target's model, with an inference table and tags."""
    fake_gateway.aliases[(MODEL, "champion")] = 7
    assert task.main(reconcile_argv(), factory) == 0
    _, name, config, ai_gateway, tags = fake_gateway.writes[0]
    assert name == "claims-fraud-uat"
    entity = config["served_entities"][0]
    assert (entity["entity_name"], entity["entity_version"]) == (MODEL, "7")
    assert config["traffic_config"]["routes"] == [{"served_entity_name": "champion", "traffic_percentage": 100}]
    assert ai_gateway["inference_table_config"]["schema_name"] == "example_claims_ml"
    assert tags == {"data_product": "dp_example_claims_fraud_detection"}


def test__reconcile_dry_run_writes_nothing_and_a_second_run_changes_nothing(task, fake_gateway, factory, capsys):
    """A dry run only plans; after an applied run the same desired state is unchanged."""
    fake_gateway.aliases[(MODEL, "champion")] = 7
    assert task.main(reconcile_argv(dry_run=True), factory) == 0
    assert fake_gateway.writes == [] and "dry run: 1 change(s)" in capsys.readouterr().out
    assert task.main(reconcile_argv(), factory) == 0
    assert task.main(reconcile_argv(), factory) == 0
    assert len(fake_gateway.writes) == 1 and "applied: 0 change(s)" in capsys.readouterr().out


def test__reconcile_without_a_champion_exits_one_and_creates_nothing(task, fake_gateway, factory, capsys):
    """Serving needs a champion; without one the task fails before touching the workspace."""
    assert task.main(reconcile_argv(), factory) == 1
    assert fake_gateway.writes == [] and "@champion" in capsys.readouterr().err


def test__reconcile_reports_a_bad_config_as_usage_error(task, factory, tmp_path, capsys):
    """An unreadable or unknown-section config exits 2 like dbxops.lifecycle."""
    path = tmp_path / "bad.yml"
    path.write_text("nonsense: []\n", encoding="utf-8")
    argv = reconcile_argv()
    argv[argv.index("--config") + 1] = str(path)
    assert task.main(argv, factory) == 2
    assert "unknown section" in capsys.readouterr().err


def test__usage_errors_exit_two_and_help_exits_zero(task, factory):
    """Missing arguments exit 2; --help exits 0."""
    assert task.main([], factory) == 2
    assert task.main(["promote"], factory) == 2
    assert task.main(["--help"], factory) == 0


def test__fill_tokens_replaces_strings_at_any_depth_and_leaves_other_values(task):
    """Tokens are replaced inside nested lists and mappings; numbers and booleans are untouched."""
    value = {"a": ["x-{t}", {"b": "{t}{u}"}], "n": 3, "flag": True}
    assert task.fill_tokens(value, {"t": "1", "u": "2"}) == {"a": ["x-1", {"b": "12"}], "n": 3, "flag": True}


def test__the_serving_config_has_no_token_the_job_does_not_fill(task):
    """After the job's tokens and {catalog} are filled no braces remain, and the config is accepted by the reconcilers."""
    config = load_reconcile_config(str(ENDPOINT), "cat")
    filled = task.fill_tokens(config, {"project_id": "p", "target": "t", "champion_version": "1"})
    assert not re.search(r"\{\w+\}", json.dumps(filled))
    assert [section for section, _, _ in build_tasks(filled, gateway=None)] == ["serving_endpoints"]
    assert set(yaml.safe_load(ENDPOINT.read_text(encoding="utf-8"))) == {"serving_endpoints"}
