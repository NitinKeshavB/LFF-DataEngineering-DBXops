"""
Plain data the metadata repository protocol speaks.

Nothing here knows about SQLAlchemy or SQL. Collectors build these records from parsed documents, the
writer guard inspects them, and each repository maps them onto its own statements.
"""

from __future__ import annotations

import uuid
from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime
from typing import (
    Any,
    Mapping,
    Sequence,
)

from dbxops.hashing import content_hash

PLATFORM_REPO_ID = "_platform"

# Tag keys the platform owns in Unity Catalog; a publish may add and remove only these (plan section C).
OWNED_TAG_PREFIXES = ("data_domain_", "data_product_", "data_contract_")
OWNED_TAG_KEYS = frozenset({"classification", "technical_owner", "data_owner", "data_steward"})

STATUS_RETIRED = "retired"
ORIGIN_SPEC = "spec"
ORIGIN_UC_SYSTEM = "uc_system"
ORIGINS = frozenset({ORIGIN_SPEC, ORIGIN_UC_SYSTEM})


def is_owned_tag_key(key: str, required_keys: Sequence[str] = ()) -> bool:
    """Return whether the platform owns ``key``: derived tag prefixes, ``classification`` and project tag keys."""
    return key in OWNED_TAG_KEYS or key in required_keys or key.startswith(OWNED_TAG_PREFIXES)


@dataclass(frozen=True)
class PublishContext:
    """Who is publishing, from where, and under which publish id."""

    repo_id: str
    published_by: str = "dbxops"
    git_commit: str | None = None
    target: str = ""
    publish_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class PublishCounts:
    """Row counts of one publish; they are written to ``md_publish_log``."""

    inserted: int = 0
    closed: int = 0
    unchanged: int = 0
    orphans_cleaned: int = 0

    def __add__(self, other: PublishCounts) -> PublishCounts:
        """Return the field-wise sum of two counts."""
        return PublishCounts(
            self.inserted + other.inserted,
            self.closed + other.closed,
            self.unchanged + other.unchanged,
            self.orphans_cleaned + other.orphans_cleaned,
        )


@dataclass(frozen=True)
class EntityRef:
    """The identity of a definition: its kind, its slug and the repo that owns it."""

    kind: str
    entity_id: str
    repo_id: str


@dataclass(frozen=True)
class Provenance:
    """Where a definition came from: the source file and the commits that last saw and retired it."""

    source_path: str | None = None
    git_commit: str | None = None
    retired_commit: str | None = None


@dataclass(frozen=True)
class DefinitionRecord:
    """
    One versioned definition (a Pattern A entity) with its extracted columns and child rows.

    ``payload`` is the full source document; ``columns`` holds the extracted parent columns; ``children`` maps a
    child table name to its rows, without the parent id and ``record_version`` (the repository adds those).
    """

    ref: EntityRef
    payload: Mapping[str, Any]
    status: str
    semver: str | None = None
    columns: Mapping[str, Any] = field(default_factory=dict)
    children: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def kind(self) -> str:
        """Return the entity kind."""
        return self.ref.kind

    @property
    def entity_id(self) -> str:
        """Return the entity slug."""
        return self.ref.entity_id

    @property
    def repo_id(self) -> str:
        """Return the owning repo."""
        return self.ref.repo_id

    @property
    def source_path(self) -> str | None:
        """Return the file the document came from."""
        return self.provenance.source_path

    @property
    def git_commit(self) -> str | None:
        """Return the commit this publish saw the document at."""
        return self.provenance.git_commit

    @property
    def retired_commit(self) -> str | None:
        """Return the commit that retired the entity, if it is retired."""
        return self.provenance.retired_commit

    @property
    def content_hash(self) -> str:
        """Return the sha256 of the canonical JSON of the payload."""
        return content_hash(self.payload)


@dataclass(frozen=True)
class QualityRuleRef:
    """A current quality rule and the asset it applies to; used to pin results to a rule version."""

    contract_id: str
    record_version: int
    rule_id: str
    asset_id: str


@dataclass(frozen=True)
class TagRow:
    """A tag the platform wants on an entity."""

    entity_type: str
    entity_id: str
    tag_key: str
    tag_value: str = ""


@dataclass(frozen=True)
class SemanticLinkRow:
    """A link from an entity to a glossary concept IRI."""

    entity_type: str
    entity_id: str
    iri: str
    label: str = ""


@dataclass(frozen=True)
class LineageEdge:
    """A directed edge between two logical assets; ``origin`` says whether it is declared or observed."""

    source_asset_id: str
    target_asset_id: str
    origin: str = ORIGIN_SPEC
    via: str = ""


@dataclass(frozen=True)
class RegistrationRecord:
    """An append-only registration audit row; the project slug has no foreign key."""

    project_id: str
    registry_pr_url: str
    issue_url: str = ""
    requested_by: str = ""
    approved_by: str = ""
    approved_at: datetime | None = None
    provisioned_repo_url: str = ""


@dataclass(frozen=True)
class DeployedAssetRecord:
    """An asset a deployment put into a workspace."""

    asset_id: str
    asset_record_version: int = 0
    physical_name: str = ""
    workspace_object_id: str = ""


@dataclass(frozen=True)
class CiContext:
    """Where a deployment ran from; local deploys use a ``local-<uuid>`` run id."""

    ci_run_id: str
    ci_run_attempt: int = 1
    ci_run_url: str = ""
    git_sha: str = ""
    deployed_by: str = ""
    framework_version: str = ""
    dbxops_version: str = ""


@dataclass(frozen=True)
class DeploymentRecord:
    """One bundle deployment; ``(target, bundle, ci_run_id, ci_run_attempt)`` is its idempotency key."""

    target: str
    bundle: str
    ci: CiContext
    status: str = "success"
    deployed_at: datetime | None = None
    assets: Sequence[DeployedAssetRecord] = ()

    @property
    def deployment_id(self) -> str:
        """Return the stable text id other rows use to point at this deployment."""
        return f"{self.target}/{self.bundle}/{self.ci.ci_run_id}/{self.ci.ci_run_attempt}"


@dataclass(frozen=True)
class Timing:
    """A start and an end time reported by Databricks (timezone-aware)."""

    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True)
class DatasetMetricsRecord:
    """Dataset totals of one pipeline update."""

    asset_id: str
    rows_written: int = 0
    rows_upserted: int = 0
    rows_deleted: int = 0
    rows_dropped: int = 0


@dataclass(frozen=True)
class PipelineUpdateRecord:
    """One pipeline update of a run, with its dataset metrics."""

    update_id: str
    pipeline_asset_id: str
    status: str
    full_refresh: bool = False
    timing: Timing = field(default_factory=Timing)
    metrics: Sequence[DatasetMetricsRecord] = ()


@dataclass(frozen=True)
class RunOrigin:
    """What started a run and which commit it ran."""

    trigger: str = ""
    git_sha: str = ""


@dataclass(frozen=True)
class RunRecord:
    """One job run and the pipeline updates it triggered."""

    target: str
    run_id: str
    job_asset_id: str
    status: str
    timing: Timing = field(default_factory=Timing)
    origin: RunOrigin = field(default_factory=RunOrigin)
    pipeline_updates: Sequence[PipelineUpdateRecord] = ()


@dataclass(frozen=True)
class RuleCounts:
    """Per-rule record counts; they overlap between rules, so never sum them across rules."""

    passed: int = 0
    failed: int = 0
    dropped: int = 0
    quarantined: int = 0


@dataclass(frozen=True)
class QualityResultRecord:
    """Per-rule counts of one pipeline update, pinned to the rule's record version."""

    target: str
    update_id: str
    asset_id: str
    rule_id: str
    contract_id: str
    rule_record_version: int
    counts: RuleCounts = field(default_factory=RuleCounts)


@dataclass(frozen=True)
class EvalSubject:
    """The model or agent version an evaluation is about."""

    asset_id: str
    version: str
    kind: str = "model"
    alias: str = ""


@dataclass(frozen=True)
class EvalGate:
    """The threshold a metric was gated on (it comes from the gate config in the repo) and the outcome."""

    threshold: float | None = None
    comparator: str = ""
    passed: bool | None = None
    source_path: str = ""


@dataclass(frozen=True)
class EvalResultRecord:
    """One evaluation metric of a model or agent version, with the gate it was checked against."""

    target: str
    run_id: str
    subject: EvalSubject
    metric: str
    value: float | None = None
    gate: EvalGate = field(default_factory=EvalGate)


@dataclass(frozen=True)
class PublishLogEntry:
    """One status event of a publish; ``(publish_id, status)`` is its idempotency key."""

    ctx: PublishContext
    what: str
    status: str
    counts: PublishCounts = field(default_factory=PublishCounts)
    message: str = ""
