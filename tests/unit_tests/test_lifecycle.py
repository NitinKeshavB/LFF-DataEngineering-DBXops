"""Tests for the lifecycle CLI: argument handling, exit codes, and the end-to-end reconcile and promote flows."""

import json

import pytest

from dbxops.lifecycle import main
from tests.consts import PROJECT_DIR

ENVIRONMENTS = str(PROJECT_DIR / "conf/environments.yml")
MODEL = "cat.claims_ml.fraud"

RECONCILE_CONFIG = """
warehouse_id: wh-1
vector_search_indexes:
  - name: "{catalog}.claims_ai.policy_index"
    endpoint_name: claims-vs
    primary_key: chunk_id
    delta_sync_index_spec: {source_table: "{catalog}.claims_ai.chunks", pipeline_type: TRIGGERED}
metadata_sync:
  owned_extra_keys: [cost_center]
  tags:
    - {entity_type: table, entity_id: "{catalog}.claims_silver.fact_claim", tag_key: cost_center, tag_value: cc-1}
"""

GATE = "policies:\n  - {type: metric_threshold, metric: auc, comparator: '>=', threshold: 0.8}\n"


@pytest.fixture
def factory(fake_gateway):
    """Return a gateway factory that hands out the shared fake and remembers the warehouse id."""
    seen = []

    def build(warehouse_id):
        seen.append(warehouse_id)
        return fake_gateway

    build.seen = seen
    return build


@pytest.fixture
def reconcile_config(tmp_path):
    """Write the reconcile config and return its path."""
    path = tmp_path / "lifecycle.yml"
    path.write_text(RECONCILE_CONFIG, encoding="utf-8")
    return str(path)


@pytest.fixture
def gate_file(tmp_path):
    """Write a gate config and return its path."""
    path = tmp_path / "gate.yml"
    path.write_text(GATE, encoding="utf-8")
    return str(path)


def _reconcile(config, *extra):
    return ["reconcile", "--target", "dev", "--config", config, "--environments", ENVIRONMENTS, *extra]


def test__reconcile_applies_by_default_with_the_target_catalog(fake_gateway, factory, reconcile_config, capsys):
    """The {catalog} placeholder resolves to the dev catalog and the changes are applied; exit code 0."""
    fake_gateway.tags[("table", "<PLACEHOLDER-dev-catalog>.claims_silver.fact_claim")] = {}
    code = main(_reconcile(reconcile_config), gateway_factory=factory)
    assert code == 0
    assert factory.seen == ["wh-1"]
    names = [call[1] for call in fake_gateway.writes]
    assert "<PLACEHOLDER-dev-catalog>.claims_ai.policy_index" in names
    assert "applied" in capsys.readouterr().out


def test__reconcile_dry_run_writes_nothing_and_exits_zero(fake_gateway, factory, reconcile_config, capsys):
    """--dry-run prints the plan and calls no write."""
    fake_gateway.tags[("table", "<PLACEHOLDER-dev-catalog>.claims_silver.fact_claim")] = {}
    code = main(_reconcile(reconcile_config, "--dry-run"), gateway_factory=factory)
    assert code == 0
    assert fake_gateway.writes == []
    assert "dry run" in capsys.readouterr().out


def test__reconcile_exits_one_when_a_section_reports_errors(factory, reconcile_config, capsys):
    """A tagged table that does not exist is a reconcile error, so the task fails with exit code 1."""
    code = main(_reconcile(reconcile_config), gateway_factory=factory)
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test__unknown_target_exits_two_and_names_the_valid_targets(factory, reconcile_config, capsys):
    """An unknown target is a usage error."""
    argv = ["reconcile", "--target", "qa", "--config", reconcile_config, "--environments", ENVIRONMENTS]
    assert main(argv, gateway_factory=factory) == 2
    assert "valid targets: dev, preprod, prod, test, uat" in capsys.readouterr().err


def test__missing_config_file_exits_two(factory, tmp_path, capsys):
    """An unreadable config file is a usage error."""
    assert main(_reconcile(str(tmp_path / "absent.yml")), gateway_factory=factory) == 2
    assert "cannot load reconcile config" in capsys.readouterr().err


def test__unknown_config_section_exits_two(factory, tmp_path, capsys):
    """A typo in a section name is caught instead of silently ignored."""
    path = tmp_path / "bad.yml"
    path.write_text("serving_endpoint: []\n", encoding="utf-8")
    assert main(_reconcile(str(path)), gateway_factory=factory) == 2
    assert "unknown section(s) serving_endpoint" in capsys.readouterr().err


def test__an_invalid_entry_exits_two(factory, tmp_path, capsys):
    """An entry with an unknown field is a config error naming the section."""
    path = tmp_path / "bad.yml"
    path.write_text("monitors:\n  - {table_name: t, nope: 1}\n", encoding="utf-8")
    assert main(_reconcile(str(path)), gateway_factory=factory) == 2
    assert "monitors: invalid entry" in capsys.readouterr().err


def test__missing_required_arguments_exit_two(factory):
    """Usage errors from argparse map to exit code 2."""
    assert main(["promote", "--model", MODEL], gateway_factory=factory) == 2
    assert main([], gateway_factory=factory) == 2


def test__help_exits_zero(factory):
    """--help is not an error."""
    assert main(["--help"], gateway_factory=factory) == 0


def test__promote_a_passing_model_sets_the_champion_and_prints_facts(fake_gateway, factory, gate_file, capsys):
    """Metrics come from the model version's run; a passing gate promotes and prints one fact per decision."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    fake_gateway.metrics[(MODEL, 3)] = {"auc": 0.91}
    code = main(["promote", "--model", MODEL, "--version", "3", "--gate", gate_file], gateway_factory=factory)
    assert code == 0
    assert fake_gateway.aliases[(MODEL, "champion")] == 3
    out = capsys.readouterr().out
    facts = json.loads(out[: out.rindex("]") + 1])
    assert facts[0]["metric"] == "auc" and facts[0]["passed"] is True
    assert facts[0]["gate_source_path"] == gate_file


def test__promote_a_failing_model_exits_one_and_still_prints_the_fact(fake_gateway, factory, gate_file, capsys):
    """A failing gate sets no alias, exits 1, and the failing decision is still printed for recording."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    fake_gateway.metrics[(MODEL, 3)] = {"auc": 0.5}
    code = main(["promote", "--model", MODEL, "--version", "3", "--gate", gate_file], gateway_factory=factory)
    captured = capsys.readouterr()
    assert code == 1
    assert (MODEL, "champion") not in fake_gateway.aliases
    assert '"passed": false' in captured.out
    assert "gate failed" in captured.err


def test__promote_with_explicit_metrics_does_not_read_the_run(fake_gateway, factory, gate_file):
    """--metric values replace the run's metrics."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    args = ["promote", "--model", MODEL, "--version", "3", "--gate", gate_file, "--metric", "auc=0.95"]
    assert main(args, gateway_factory=factory) == 0
    assert not [call for call in fake_gateway.calls if call[0] == "get_model_version_metrics"]


def test__promote_a_missing_metric_fails_the_gate(fake_gateway, factory, gate_file, capsys):
    """A run without the gated metric fails with the missing-metric reason."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    code = main(["promote", "--model", MODEL, "--version", "3", "--gate", gate_file], gateway_factory=factory)
    assert code == 1
    assert "metric 'auc' is missing" in capsys.readouterr().err


def test__promote_a_version_that_is_not_the_challenger_exits_one(fake_gateway, factory, gate_file, capsys):
    """A passing gate for the wrong version is refused with exit 1 and no alias change."""
    fake_gateway.aliases[(MODEL, "challenger")] = 2
    args = ["promote", "--model", MODEL, "--version", "3", "--gate", gate_file, "--metric", "auc=0.95"]
    assert main(args, gateway_factory=factory) == 1
    assert fake_gateway.writes == []
    assert "is not @challenger" in capsys.readouterr().err


def test__promote_with_a_bad_metric_argument_exits_two(factory, gate_file, capsys):
    """--metric must be NAME=VALUE with a numeric value."""
    args = ["promote", "--model", MODEL, "--version", "3", "--gate", gate_file, "--metric", "auc"]
    assert main(args, gateway_factory=factory) == 2
    assert "NAME=VALUE" in capsys.readouterr().err


def test__promote_with_an_invalid_gate_exits_two(factory, tmp_path, capsys):
    """An invalid gate config is a usage error."""
    path = tmp_path / "gate.yml"
    path.write_text("policies: []\n", encoding="utf-8")
    assert main(["promote", "--model", MODEL, "--version", "3", "--gate", str(path)], gateway_factory=factory) == 2
    assert "at least one policy" in capsys.readouterr().err
