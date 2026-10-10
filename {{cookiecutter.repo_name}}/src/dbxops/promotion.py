"""
Promotion gates: decide whether a model version may move from ``@challenger`` to ``@champion``.

A ``PromotionGate`` holds Strategy policies (``MetricThreshold``, ``EvalPassRate``). Evaluating it yields a
``GateReport`` whose ``GateDecision`` rows carry the metric, value, threshold, comparator, outcome and the git path
of the gate config, which is the shape of an ``md_eval_result`` fact. ``ModelPromoter`` moves the alias through the
workspace gateway only for a passing report.
"""

from __future__ import annotations

import math
import operator
from dataclasses import (
    dataclass,
    replace,
)
from pathlib import Path
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
    Sequence,
)

import yaml

from dbxops.workspace import WorkspaceGateway

COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}
EVAL_KINDS = frozenset({"model", "agent"})
DEFAULT_CHALLENGER_ALIAS = "challenger"
DEFAULT_CHAMPION_ALIAS = "champion"
DEFAULT_PASS_RATE_METRIC = "pass_rate"


class GateConfigError(ValueError):
    """Raised when a gate config file or a policy definition is invalid."""


class PromotionRefused(RuntimeError):
    """Raised when a promotion is attempted without a passing gate report or for the wrong version."""


@dataclass(frozen=True)
class GateDecision:
    """The outcome of one policy; ``value`` is None when the metric was missing."""

    metric: str
    value: float | None
    threshold: float
    comparator: str
    passed: bool
    reason: str
    gate_source_path: str | None = None

    def as_eval_result(
        self, *, eval_kind: str, alias: str, subject_asset_id: str, subject_version: str | int
    ) -> dict[str, Any]:
        """Return the decision as the fields of an ``md_eval_result`` fact (the caller adds target and run id)."""
        return {
            "subject_asset_id": subject_asset_id,
            "subject_version": str(subject_version),
            "metric": self.metric,
            "eval_kind": eval_kind,
            "value": self.value,
            "threshold": self.threshold,
            "comparator": self.comparator,
            "passed": self.passed,
            "alias": alias,
            "gate_source_path": self.gate_source_path,
        }


class GatePolicy(Protocol):
    """A Strategy that turns evaluation metrics into one decision."""

    def evaluate(self, metrics: Mapping[str, float]) -> GateDecision:
        """Decide whether the metrics satisfy this policy."""


def _decide(metric: str, metrics: Mapping[str, float], threshold: float, comparator: str) -> GateDecision:
    value = metrics.get(metric)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        available = ", ".join(sorted(metrics)) or "none"
        reason = f"metric {metric!r} is missing from the evaluation results (available: {available})"
        return GateDecision(metric, None, threshold, comparator, False, reason)
    passed = COMPARATORS[comparator](value, threshold)
    reason = f"{metric} {value} {'satisfies' if passed else 'does not satisfy'} {comparator} {threshold}"
    return GateDecision(metric, float(value), threshold, comparator, passed, reason)


@dataclass(frozen=True)
class MetricThreshold:
    """Pass when a metric compares to a threshold, for example ``auc >= 0.8``."""

    metric: str
    threshold: float
    comparator: str = ">="

    def __post_init__(self):
        """Reject an invalid policy definition."""
        if self.comparator not in COMPARATORS:
            raise GateConfigError(f"unknown comparator {self.comparator!r}; use one of {', '.join(COMPARATORS)}")

    def evaluate(self, metrics: Mapping[str, float]) -> GateDecision:
        """Compare the metric with the threshold."""
        return _decide(self.metric, metrics, self.threshold, self.comparator)


@dataclass(frozen=True)
class EvalPassRate:
    """Pass when the share of passing agent evaluation cases is at least ``threshold`` (a fraction from 0 to 1)."""

    threshold: float
    metric: str = DEFAULT_PASS_RATE_METRIC

    def __post_init__(self):
        """Reject an invalid policy definition."""
        if not 0 <= self.threshold <= 1:
            raise GateConfigError(f"a pass-rate threshold must be between 0 and 1, got {self.threshold}")

    def evaluate(self, metrics: Mapping[str, float]) -> GateDecision:
        """Require the pass-rate metric to be at least the threshold."""
        return _decide(self.metric, metrics, self.threshold, ">=")


def pass_rate(outcomes: Sequence[bool]) -> float:
    """Return the fraction of passing evaluation cases."""
    if not outcomes:
        raise ValueError("cannot compute a pass rate from zero evaluation cases")
    return sum(1 for outcome in outcomes if outcome) / len(outcomes)


@dataclass(frozen=True)
class GateReport:
    """Every decision for one model version; ``passed`` requires at least one decision and all of them to pass."""

    model: str
    version: int
    eval_kind: str
    challenger_alias: str
    champion_alias: str
    decisions: tuple[GateDecision, ...]

    @property
    def passed(self) -> bool:
        """Return whether the version may be promoted."""
        return bool(self.decisions) and all(decision.passed for decision in self.decisions)

    def eval_results(self) -> list[dict[str, Any]]:
        """Return one ``md_eval_result`` fact per decision, keyed to the challenger alias."""
        return [
            decision.as_eval_result(
                eval_kind=self.eval_kind,
                alias=self.challenger_alias,
                subject_asset_id=self.model,
                subject_version=self.version,
            )
            for decision in self.decisions
        ]


class PromotionGate:
    """All policies a version must satisfy, plus the aliases it moves between."""

    def __init__(
        self,
        policies: Sequence[GatePolicy],
        source_path: str | None = None,
        eval_kind: str = "model",
        aliases: tuple[str, str] = (DEFAULT_CHALLENGER_ALIAS, DEFAULT_CHAMPION_ALIAS),
    ):
        if not policies:
            raise GateConfigError("a promotion gate needs at least one policy")
        if eval_kind not in EVAL_KINDS:
            raise GateConfigError(f"eval_kind must be one of {', '.join(sorted(EVAL_KINDS))}, got {eval_kind!r}")
        self._policies = tuple(policies)
        self.source_path = source_path
        self.eval_kind = eval_kind
        self.challenger_alias, self.champion_alias = aliases

    @classmethod
    def from_yaml(cls, path: str | Path) -> PromotionGate:
        """Load a gate config; see ``dbxops.lifecycle`` for the file format."""
        try:
            with open(path, encoding="utf-8") as file:
                data = yaml.safe_load(file)
        except (OSError, yaml.YAMLError) as error:
            raise GateConfigError(f"cannot load gate config {path}: {error}") from error
        if not isinstance(data, dict) or not isinstance(data.get("policies"), list):
            raise GateConfigError(f"{path}: expected a mapping with a 'policies' list")
        unknown = sorted(set(data) - {"eval_kind", "challenger_alias", "champion_alias", "policies"})
        if unknown:
            raise GateConfigError(f"{path}: unknown key(s) {', '.join(unknown)}")
        return cls(
            [_policy_from_dict(entry, str(path)) for entry in data["policies"]],
            source_path=str(path),
            eval_kind=data.get("eval_kind", "model"),
            aliases=(
                data.get("challenger_alias", DEFAULT_CHALLENGER_ALIAS),
                data.get("champion_alias", DEFAULT_CHAMPION_ALIAS),
            ),
        )

    def evaluate(self, model: str, version: int, metrics: Mapping[str, float]) -> GateReport:
        """Evaluate every policy against the metrics of one model version."""
        decisions = tuple(
            replace(policy.evaluate(metrics), gate_source_path=self.source_path) for policy in self._policies
        )
        return GateReport(model, version, self.eval_kind, self.challenger_alias, self.champion_alias, decisions)


def _policy_from_dict(entry: Any, where: str) -> GatePolicy:
    if not isinstance(entry, dict) or "type" not in entry:
        raise GateConfigError(f"{where}: every policy needs a 'type'")
    fields = {key: value for key, value in entry.items() if key != "type"}
    kinds: dict[str, type] = {"metric_threshold": MetricThreshold, "eval_pass_rate": EvalPassRate}
    if entry["type"] not in kinds:
        raise GateConfigError(f"{where}: unknown policy type {entry['type']!r}; use one of {', '.join(kinds)}")
    try:
        return kinds[entry["type"]](**fields)
    except TypeError as error:
        raise GateConfigError(f"{where}: invalid {entry['type']} policy: {error}") from error


@dataclass(frozen=True)
class PromotionResult:
    """What a promotion did; ``promoted`` is False when the version already was the champion."""

    model: str
    version: int
    promoted: bool
    previous_champion: int | None
    report: GateReport


class ModelPromoter:
    """Moves a registered-model alias from challenger to champion, but only behind a passing gate report."""

    def __init__(self, gateway: WorkspaceGateway):
        self._gateway = gateway

    def promote(self, report: GateReport) -> PromotionResult:
        """
        Make the report's version the champion.

        Raises ``PromotionRefused`` when the report did not pass or its version does not hold the challenger alias.
        """
        if not isinstance(report, GateReport) or not report.passed:
            raise PromotionRefused("promotion requires a passing gate report")
        holder = self._gateway.get_model_alias_version(report.model, report.challenger_alias)
        if holder != report.version:
            raise PromotionRefused(
                f"{report.model} version {report.version} is not @{report.challenger_alias} (it is {holder})"
            )
        previous = self._gateway.get_model_alias_version(report.model, report.champion_alias)
        if previous == report.version:
            return PromotionResult(report.model, report.version, False, previous, report)
        self._gateway.set_model_alias(report.model, report.champion_alias, report.version)
        return PromotionResult(report.model, report.version, True, previous, report)
