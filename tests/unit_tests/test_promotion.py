"""Tests for promotion gates, decisions and the ModelPromoter."""

import pytest

from dbxops.promotion import (
    EvalPassRate,
    GateConfigError,
    GateDecision,
    GateReport,
    MetricThreshold,
    ModelPromoter,
    PromotionGate,
    PromotionRefused,
    pass_rate,
)

MODEL = "cat.claims_ml.fraud"


def _gate(*policies, path="conf/gates/fraud.yml") -> PromotionGate:
    return PromotionGate(policies or [MetricThreshold("auc", 0.8)], source_path=path)


def test__a_metric_above_the_threshold_passes():
    """AUC 0.85 against >= 0.8 passes and the decision carries every field of the eval fact."""
    decision = MetricThreshold("auc", 0.8).evaluate({"auc": 0.85})
    assert (decision.metric, decision.value, decision.threshold, decision.comparator) == ("auc", 0.85, 0.8, ">=")
    assert decision.passed is True


def test__a_metric_below_the_threshold_fails():
    """AUC 0.79 against >= 0.8 fails and the reason names the metric."""
    decision = MetricThreshold("auc", 0.8).evaluate({"auc": 0.79})
    assert decision.passed is False
    assert "auc" in decision.reason


def test__a_metric_equal_to_the_threshold_passes_for_gte_and_fails_for_gt():
    """The boundary is inclusive for >= and exclusive for >."""
    assert MetricThreshold("auc", 0.8, ">=").evaluate({"auc": 0.8}).passed is True
    assert MetricThreshold("auc", 0.8, ">").evaluate({"auc": 0.8}).passed is False


def test__a_less_than_comparator_gates_error_metrics():
    """A metric where lower is better can use <=."""
    assert MetricThreshold("log_loss", 0.3, "<=").evaluate({"log_loss": 0.25}).passed is True
    assert MetricThreshold("log_loss", 0.3, "<=").evaluate({"log_loss": 0.31}).passed is False


def test__a_missing_metric_fails_with_a_clear_reason():
    """A metric absent from the results fails, has no value, and names what was available."""
    decision = MetricThreshold("auc", 0.8).evaluate({"f1": 0.9})
    assert decision.passed is False
    assert decision.value is None
    assert "metric 'auc' is missing" in decision.reason
    assert "available: f1" in decision.reason


def test__a_nan_metric_fails():
    """NaN is not a passing value."""
    assert MetricThreshold("auc", 0.8).evaluate({"auc": float("nan")}).passed is False


def test__an_unknown_comparator_is_rejected():
    """The comparator must be one of the supported operators."""
    with pytest.raises(GateConfigError, match="unknown comparator '=>'"):
        MetricThreshold("auc", 0.8, "=>")


def test__pass_rate_policy_passes_at_the_boundary_and_fails_below():
    """EvalPassRate is a >= gate on the pass_rate metric."""
    assert EvalPassRate(0.9).evaluate({"pass_rate": 0.9}).passed is True
    assert EvalPassRate(0.9).evaluate({"pass_rate": 0.89}).passed is False


def test__pass_rate_policy_fails_when_the_metric_is_missing():
    """No pass_rate in the results is a failing decision."""
    decision = EvalPassRate(0.9).evaluate({})
    assert decision.passed is False
    assert "pass_rate" in decision.reason


def test__pass_rate_threshold_must_be_a_fraction():
    """A threshold of 90 is rejected rather than silently failing every agent."""
    with pytest.raises(GateConfigError, match="between 0 and 1"):
        EvalPassRate(90)


def test__pass_rate_is_computed_from_case_outcomes():
    """Three of four passing cases is 0.75; no cases is an error."""
    assert pass_rate([True, True, True, False]) == 0.75
    with pytest.raises(ValueError, match="zero evaluation cases"):
        pass_rate([])


def test__a_gate_passes_only_when_every_policy_passes():
    """One failing policy fails the report, and every decision is still returned."""
    gate = _gate(MetricThreshold("auc", 0.8), MetricThreshold("recall", 0.7))
    report = gate.evaluate(MODEL, 3, {"auc": 0.9, "recall": 0.6})
    assert report.passed is False
    assert [d.passed for d in report.decisions] == [True, False]


def test__every_decision_carries_the_gate_source_path():
    """The git path of the gate config is copied onto each decision."""
    report = _gate().evaluate(MODEL, 3, {"auc": 0.9})
    assert {d.gate_source_path for d in report.decisions} == {"conf/gates/fraud.yml"}


def test__a_gate_without_policies_is_rejected():
    """An empty gate would pass everything, so it cannot be built."""
    with pytest.raises(GateConfigError, match="at least one policy"):
        PromotionGate([])


def test__a_report_without_decisions_does_not_pass():
    """A hand-built empty report is not a passing report."""
    assert GateReport(MODEL, 1, "model", "challenger", "champion", ()).passed is False


def test__decisions_become_eval_result_facts():
    """The report yields one md_eval_result-shaped dict per decision, with threshold, comparator and source path."""
    report = _gate().evaluate(MODEL, 3, {"auc": 0.85})
    assert report.eval_results() == [
        {
            "subject_asset_id": MODEL,
            "subject_version": "3",
            "metric": "auc",
            "eval_kind": "model",
            "value": 0.85,
            "threshold": 0.8,
            "comparator": ">=",
            "passed": True,
            "alias": "challenger",
            "gate_source_path": "conf/gates/fraud.yml",
        }
    ]


def test__a_gate_config_file_loads(tmp_path):
    """The documented YAML format builds a gate with both policy kinds, aliases and the source path."""
    path = tmp_path / "gate.yml"
    path.write_text(
        "eval_kind: agent\nchallenger_alias: candidate\nchampion_alias: live\npolicies:\n"
        "  - {type: metric_threshold, metric: auc, comparator: '>=', threshold: 0.8}\n"
        "  - {type: eval_pass_rate, threshold: 0.9}\n",
        encoding="utf-8",
    )
    gate = PromotionGate.from_yaml(path)
    report = gate.evaluate(MODEL, 2, {"auc": 0.9, "pass_rate": 0.95})
    assert report.passed
    assert (report.eval_kind, report.challenger_alias, report.champion_alias) == ("agent", "candidate", "live")
    assert report.decisions[0].gate_source_path == str(path)


@pytest.mark.parametrize(
    "content, message",
    [
        ("policies: []\n", "at least one policy"),
        ("policies:\n  - {type: nope}\n", "unknown policy type 'nope'"),
        ("policies:\n  - {metric: auc}\n", "needs a 'type'"),
        ("policies:\n  - {type: metric_threshold, metric: auc}\n", "invalid metric_threshold policy"),
        ("eval_kind: other\npolicies:\n  - {type: eval_pass_rate, threshold: 0.9}\n", "eval_kind must be one of"),
        ("extra: 1\npolicies:\n  - {type: eval_pass_rate, threshold: 0.9}\n", "unknown key"),
        ("- a\n", "expected a mapping"),
    ],
)
def test__an_invalid_gate_config_is_rejected(tmp_path, content, message):
    """Each malformed gate config fails with a message that says what is wrong."""
    path = tmp_path / "gate.yml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(GateConfigError, match=message):
        PromotionGate.from_yaml(path)


def test__a_missing_gate_config_is_a_gate_config_error(tmp_path):
    """An unreadable file raises GateConfigError."""
    with pytest.raises(GateConfigError, match="cannot load gate config"):
        PromotionGate.from_yaml(tmp_path / "absent.yml")


def test__promoter_refuses_a_failing_report_and_sets_nothing(fake_gateway):
    """A failed gate leaves the champion alias untouched."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    report = _gate().evaluate(MODEL, 3, {"auc": 0.5})
    with pytest.raises(PromotionRefused, match="passing gate report"):
        ModelPromoter(fake_gateway).promote(report)
    assert fake_gateway.writes == []


def test__promoter_refuses_something_that_is_not_a_report(fake_gateway):
    """No report at all is also a refusal."""
    with pytest.raises(PromotionRefused):
        ModelPromoter(fake_gateway).promote(None)  # type: ignore[arg-type]
    assert fake_gateway.writes == []


def test__promoter_sets_the_champion_alias_for_a_passing_report(fake_gateway):
    """The challenger version becomes the champion and the previous champion is reported."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    fake_gateway.aliases[(MODEL, "champion")] = 2
    report = _gate().evaluate(MODEL, 3, {"auc": 0.9})
    result = ModelPromoter(fake_gateway).promote(report)
    assert fake_gateway.writes == [("set_model_alias", MODEL, "champion", 3)]
    assert (result.promoted, result.previous_champion, result.version) == (True, 2, 3)


def test__promoter_refuses_a_version_that_is_not_the_challenger(fake_gateway):
    """Promoting version 4 while the challenger alias points at 3 is refused."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    report = _gate().evaluate(MODEL, 4, {"auc": 0.9})
    with pytest.raises(PromotionRefused, match="is not @challenger \\(it is 3\\)"):
        ModelPromoter(fake_gateway).promote(report)
    assert fake_gateway.writes == []


def test__promoting_the_current_champion_again_is_a_no_op(fake_gateway):
    """Re-running a promotion that already happened sets nothing."""
    fake_gateway.aliases[(MODEL, "challenger")] = 3
    fake_gateway.aliases[(MODEL, "champion")] = 3
    result = ModelPromoter(fake_gateway).promote(_gate().evaluate(MODEL, 3, {"auc": 0.9}))
    assert result.promoted is False
    assert fake_gateway.writes == []


def test__decision_is_immutable():
    """GateDecision is a frozen value."""
    decision = GateDecision("auc", 0.9, 0.8, ">=", True, "ok")
    with pytest.raises(AttributeError):
        decision.passed = False  # type: ignore[misc]
