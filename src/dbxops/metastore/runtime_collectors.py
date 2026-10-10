"""
Runtime collectors: runs, deployments and lineage, built on injected client interfaces.

The clients are Protocols defined here: ``JobsClient`` (Jobs API ``runs/get``), ``PipelineEventLog`` (the SDP event
log, normalised), ``BundleRunner`` (``databricks bundle summary -o json``) and ``LineageReader``
(``system.access.table_lineage``). No real call is made in this package's tests; a real client only has to satisfy
the Protocol.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
    replace,
)
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Protocol,
    Sequence,
)

from dbxops.metastore.records import (
    ORIGIN_SPEC,
    ORIGIN_UC_SYSTEM,
    CiContext,
    DatasetMetricsRecord,
    DefinitionRecord,
    DeployedAssetRecord,
    DeploymentRecord,
    LineageEdge,
    PipelineUpdateRecord,
    QualityResultRecord,
    QualityRuleRef,
    RuleCounts,
    RunOrigin,
    RunRecord,
    Timing,
)

# bundle resource type -> asset type; asset id is "<asset_type>/<repo_id>/<resource_key>" (plan section C)
RESOURCE_ASSET_TYPES = {
    "jobs": "uc_job",
    "pipelines": "uc_pipeline",
    "registered_models": "uc_model",
    "volumes": "uc_volume",
    "model_serving_endpoints": "serving_endpoint",
}


class JobsClient(Protocol):
    """Reads a job run as the Jobs API ``runs/get`` returns it."""

    def get_run(self, run_id: str) -> Mapping[str, Any]:
        """Return the run: ``run_id``, ``job_id``, ``state``, ``start_time``/``end_time`` (ms), ``trigger``, ..."""


@dataclass(frozen=True)
class ExpectationInfo:
    """Counts of one expectation on one dataset, from the event log."""

    dataset: str
    name: str
    counts: RuleCounts = field(default_factory=RuleCounts)


@dataclass(frozen=True)
class PipelineUpdateInfo:
    """
    A pipeline update as the event-log reader reports it.

    ``datasets`` carry the dataset name (e.g. a three-part table name) in ``asset_id``; the collector resolves it to
    a logical asset id.
    """

    update_id: str
    pipeline_asset_id: str
    state: str
    full_refresh: bool = False
    timing: Timing = field(default_factory=Timing)
    datasets: Sequence[DatasetMetricsRecord] = ()
    expectations: Sequence[ExpectationInfo] = ()


class PipelineEventLog(Protocol):
    """Reads the pipeline updates a run triggered."""

    def updates_for_run(self, run: Mapping[str, Any]) -> Sequence[PipelineUpdateInfo]:
        """Return the pipeline updates of ``run`` with dataset metrics and expectation counts."""


class BundleRunner(Protocol):
    """Runs ``databricks bundle summary -o json`` for a target."""

    def summary(self, target: str) -> Mapping[str, Any]:
        """Return the parsed summary: ``bundle`` and ``resources`` by type and key, each with ``id`` and ``name``."""


class LineageReader(Protocol):
    """Reads observed table lineage."""

    def table_lineage(self) -> Iterable[tuple[str, str]]:
        """Return ``(source_full_name, target_full_name)`` pairs, e.g. from ``system.access.table_lineage``."""


class RuleCatalog(Protocol):
    """Finds the current quality rule an expectation belongs to."""

    def lookup(self, asset_id: str, rule_id: str) -> QualityRuleRef | None:
        """Return the rule with id ``rule_id`` that applies to ``asset_id``, or ``None``."""


class InMemoryRuleCatalog:
    """A ``RuleCatalog`` over a list of rules, typically ``repository.current_quality_rules(repo_id)``."""

    def __init__(self, rules: Iterable[QualityRuleRef]):
        self._rules = {(rule.asset_id, rule.rule_id): rule for rule in rules}

    def lookup(self, asset_id: str, rule_id: str) -> QualityRuleRef | None:
        """Return the matching rule or ``None``."""
        return self._rules.get((asset_id, rule_id))


@dataclass(frozen=True)
class RunSources:
    """The three readers a run collection needs."""

    jobs: JobsClient
    event_log: PipelineEventLog
    rules: RuleCatalog


def _from_millis(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc) if value else None


@dataclass(frozen=True)
class RunCollection:
    """What a run collection produced; ``unmapped`` lists expectations that match no contract rule."""

    run: RunRecord
    quality: list[QualityResultRecord] = field(default_factory=list)
    unmapped: list[ExpectationInfo] = field(default_factory=list)


class RunCollector:
    """Builds a run record, its pipeline updates and quality results, pinned to the rule versions current now."""

    def __init__(self, sources: RunSources, asset_resolver: Callable[[str], str], repo_id: str):
        self._sources = sources
        self._asset = asset_resolver
        self._repo_id = repo_id

    def collect(self, run_id: str, target: str, git_sha: str = "") -> RunCollection:
        """Collect one run."""
        run = self._sources.jobs.get_run(run_id)
        updates = list(self._sources.event_log.updates_for_run(run))
        state = run.get("state") or {}
        record = RunRecord(
            target=target,
            run_id=str(run.get("run_id", run_id)),
            job_asset_id=f"uc_job/{self._repo_id}/{run.get('job_id')}",
            status=state.get("result_state") or state.get("life_cycle_state") or "UNKNOWN",
            timing=Timing(_from_millis(run.get("start_time")), _from_millis(run.get("end_time"))),
            origin=RunOrigin(
                str(run.get("trigger") or ""), git_sha or (run.get("git_source") or {}).get("git_commit", "")
            ),
            pipeline_updates=[self._update(update) for update in updates],
        )
        collection = RunCollection(record)
        for update in updates:
            self._quality(target, update, collection)
        return collection

    def _update(self, update: PipelineUpdateInfo) -> PipelineUpdateRecord:
        return PipelineUpdateRecord(
            update_id=update.update_id,
            pipeline_asset_id=update.pipeline_asset_id,
            status=update.state,
            full_refresh=update.full_refresh,
            timing=update.timing,
            metrics=[replace(item, asset_id=self._asset(item.asset_id)) for item in update.datasets],
        )

    def _quality(self, target: str, update: PipelineUpdateInfo, collection: RunCollection) -> None:
        for item in update.expectations:
            asset_id = self._asset(item.dataset)
            rule = self._sources.rules.lookup(asset_id, item.name)
            if rule is None:
                collection.unmapped.append(item)
                continue
            collection.quality.append(
                QualityResultRecord(
                    target,
                    update.update_id,
                    asset_id,
                    rule.rule_id,
                    rule.contract_id,
                    rule.record_version,
                    item.counts,
                )
            )


class DeploymentCollector:
    """Builds a deployment record from ``bundle summary`` output and the CI context."""

    def __init__(self, runner: BundleRunner, repo_id: str, asset_version: Callable[[str], int] | None = None):
        self._runner = runner
        self._repo_id = repo_id
        self._asset_version = asset_version or (lambda asset_id: 0)

    def collect(self, target: str, ci: CiContext, bundle: str = "", status: str = "success") -> DeploymentRecord:
        """Collect one deployment to ``target``; ``bundle`` defaults to the bundle name in the summary."""
        summary = self._runner.summary(target)
        bundle = bundle or (summary.get("bundle") or {}).get("name", "")
        assets = []
        for resource_type, asset_type in RESOURCE_ASSET_TYPES.items():
            for key, resource in sorted((summary.get("resources") or {}).get(resource_type, {}).items()):
                asset_id = f"{asset_type}/{self._repo_id}/{key}"
                assets.append(
                    DeployedAssetRecord(
                        asset_id=asset_id,
                        asset_record_version=self._asset_version(asset_id),
                        physical_name=str(resource.get("name") or ""),
                        workspace_object_id=str(resource.get("id") or ""),
                    )
                )
        return DeploymentRecord(target, bundle, ci, status, assets=assets)


class LineageCollector:
    """Builds declared (spec) edges from dataflow specs and observed edges from the lineage reader."""

    def __init__(self, reader: LineageReader | None = None, asset_resolver: Callable[[str], str | None] | None = None):
        self._reader = reader
        self._asset = asset_resolver or (lambda name: ".".join(name.split(".")[-2:]))

    @staticmethod
    def spec_edges(specs: Iterable[DefinitionRecord]) -> list[LineageEdge]:
        """Return the declared edges: every flow with a source and a target asset."""
        edges = {}
        for spec in specs:
            for flow in spec.children.get("md_dataflow_flow", ()):
                source, target = flow.get("source_asset_id"), flow.get("target_asset_id")
                if source and target:
                    edges[(source, target)] = LineageEdge(source, target, ORIGIN_SPEC, via=spec.entity_id)
        return list(edges.values())

    def observed_edges(self) -> list[LineageEdge]:
        """Return the observed edges whose two ends both resolve to a logical asset."""
        if self._reader is None:
            return []
        edges = {}
        for source_name, target_name in self._reader.table_lineage():
            source, target = self._asset(source_name), self._asset(target_name)
            if source and target:
                edges[(source, target)] = LineageEdge(source, target, ORIGIN_UC_SYSTEM)
        return list(edges.values())
