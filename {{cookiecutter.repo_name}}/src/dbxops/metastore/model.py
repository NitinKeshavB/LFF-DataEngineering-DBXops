"""
The metadata database model: every table of plan section D4 and the views over them.

``build_model(schema)`` returns one SQLAlchemy Core ``MetaData`` wrapped in a ``MetaModel``. The schema name is a
build argument (default ``platform_meta``): PostgreSQL and Lakebase use it as a real schema, SQLite has no schemas
so it is built with ``schema=None``, and Unity Catalog is built with a placeholder schema that the UC repository
replaces by ``catalog.schema`` when it renders statements. Building a fresh model per schema keeps every
dialect on one code path, with no ``schema_translate_map`` at execution time.

Table patterns (plan D2): A is an SCD2 definition with child snapshots keyed by ``(parent_id, record_version, ...)``,
B is derived current state with ``is_active``, and C is an append-only fact with an idempotency key.
"""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass
from typing import (
    Any,
    Iterable,
)

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Double,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    and_,
    case,
    exists,
    false,
    func,
    literal,
    select,
    text,
    true,
    union_all,
)
from sqlalchemy.dialects import (
    postgresql,
    sqlite,
)
from sqlalchemy.schema import (
    CreateIndex,
    CreateTable,
)
from sqlalchemy.sql import quoted_name
from sqlalchemy.sql.expression import Select

from dbxops.metastore.errors import SchemaError
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    ORIGIN_UC_SYSTEM,
)
from dbxops.metastore.types import (
    JsonType,
    UtcTimestamp,
)

DEFAULT_SCHEMA = "platform_meta"
SCHEMA_VERSION = 2
UC_SCHEMA_PLACEHOLDER = "dbxops_uc_ns"

WRITER_PLATFORM = "platform"
WRITER_SQUAD = "squad"


@dataclass(frozen=True)
class ChildSpec:
    """A Pattern A child table: keyed ``(parent_column, record_version, *key_columns)``."""

    table: str
    parent_column: str
    key_columns: tuple[str, ...]


@dataclass(frozen=True)
class EntitySpec:
    """A Pattern A entity: its parent table, the extracted parent columns, and its child tables."""

    kind: str
    table: str
    owner: str
    semver_required: bool
    columns: tuple[str, ...]
    children: tuple[ChildSpec, ...] = ()

    def child(self, table: str) -> ChildSpec:
        """Return the child spec of ``table``."""
        for spec in self.children:
            if spec.table == table:
                return spec
        raise SchemaError(f"{self.table} has no child table {table!r}")


_TYPE_CODES: dict[str, Any] = {
    "text": Text,
    "bool": Boolean,
    "int": Integer,
    "bigint": BigInteger,
    "double": Double,
    "json": JsonType,
    "ts": UtcTimestamp,
}

# parent table -> (kind, owner, semver_required, extracted columns)
_PARENTS: dict[str, tuple[str, str, bool, tuple[tuple[str, str], ...]]] = {
    "md_domain": (
        "domain",
        WRITER_PLATFORM,
        False,
        (("name", "text"), ("parent_domain_id", "text"), ("description", "text")),
    ),
    "md_team": (
        "team",
        WRITER_PLATFORM,
        False,
        (("name", "text"), ("title", "text"), ("domain_id", "text"), ("github_team", "text")),
    ),
    "md_project": (
        "project",
        WRITER_PLATFORM,
        False,
        (
            ("title", "text"),
            ("project_type", "text"),
            ("repo_kind", "text"),
            ("owner_team_id", "text"),
            ("domain_id", "text"),
            ("repo_url", "text"),
            ("service_principal_app_id", "text"),
        ),
    ),
    "md_glossary_concept": (
        "glossary_concept",
        WRITER_PLATFORM,
        False,
        (
            ("iri", "text"),
            ("label", "text"),
            ("definition", "text"),
            ("scheme_iri", "text"),
            ("broader_iris", "json"),
        ),
    ),
    "md_data_product": (
        "data_product",
        WRITER_SQUAD,
        True,
        (
            ("name", "text"),
            ("api_version", "text"),
            ("domain_id", "text"),
            ("project_id", "text"),
            ("owner_team_id", "text"),
        ),
    ),
    "md_data_contract": (
        "data_contract",
        WRITER_SQUAD,
        True,
        (
            ("name", "text"),
            ("api_version", "text"),
            ("domain_id", "text"),
            ("product_id", "text"),
            ("owner_team_id", "text"),
            ("purpose", "text"),
            ("usage", "text"),
            ("limitations", "text"),
        ),
    ),
    "md_dataflow_spec": (
        "dataflow_spec",
        WRITER_SQUAD,
        False,
        (("bundle", "text"), ("flow_group", "text"), ("layer", "text"), ("spec_type", "text")),
    ),
    "md_asset": (
        "asset",
        WRITER_SQUAD,
        False,
        (
            ("asset_type", "text"),
            ("ontos_asset_type", "text"),
            ("fqn_template", "text"),
            ("bundle", "text"),
            ("resource_key", "text"),
            ("product_id", "text"),
            ("parent_asset_id", "text"),
            ("workload", "text"),
            ("layer", "text"),
            ("subtype", "text"),
        ),
    ),
}

# child table -> (parent table, parent column, key columns, data columns)
_CHILDREN: dict[str, tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "md_team_member": ("md_team", "team_id", ("member_type", "member_identifier", "role"), ()),
    "md_project_contact": ("md_project", "project_id", ("role", "email"), (("channel", "text"),)),
    "md_project_group": ("md_project", "project_id", ("access_role",), (("ad_group", "text"),)),
    "md_product_port": (
        "md_data_product",
        "product_id",
        ("direction", "port_name"),
        (
            ("port_version", "text"),
            ("contract_id", "text"),
            ("asset_id", "text"),
            ("asset_type", "text"),
            ("contains_pii", "bool"),
        ),
    ),
    "md_port_input_contract": (
        "md_data_product",
        "product_id",
        ("port_name", "contract_id"),
        (("contract_semver", "text"),),
    ),
    "md_contract_server": (
        "md_data_contract",
        "contract_id",
        ("environment",),
        (("type", "text"), ("catalog", "text"), ("schema", "text")),
    ),
    "md_contract_object": (
        "md_data_contract",
        "contract_id",
        ("object_name",),
        (
            ("physical_name", "text"),
            ("logical_type", "text"),
            ("physical_type", "text"),
            ("business_name", "text"),
            ("asset_id", "text"),
        ),
    ),
    "md_contract_property": (
        "md_data_contract",
        "contract_id",
        ("object_name", "property_path"),
        (
            ("parent_path", "text"),
            ("logical_type", "text"),
            ("physical_type", "text"),
            ("required", "bool"),
            ("primary_key", "bool"),
            ("pk_position", "int"),
            ("unique", "bool"),
            ("partitioned", "bool"),
            ("classification", "text"),
            ("critical", "bool"),
            ("is_pii", "bool"),
            ("business_name", "text"),
            ("description", "text"),
        ),
    ),
    "md_quality_rule": (
        "md_data_contract",
        "contract_id",
        ("rule_id",),
        (
            ("object_name", "text"),
            ("property_path", "text"),
            ("type", "text"),
            ("dimension", "text"),
            ("expression", "text"),
            ("action", "text"),
            ("quarantine_asset_id", "text"),
            ("origin", "text"),
            ("spec_id", "text"),
        ),
    ),
    "md_job_task": (
        "md_asset",
        "asset_id",
        ("task_key",),
        (("task_kind", "text"), ("depends_on", "json"), ("target_asset_id", "text")),
    ),
    "md_dataflow_flow": (
        "md_dataflow_spec",
        "spec_id",
        ("flow_name",),
        (
            ("flow_type", "text"),
            ("source_asset_id", "text"),
            ("target_asset_id", "text"),
            ("target_kind", "text"),
        ),
    ),
}

# derived (Pattern B) table -> (key columns, data columns)
_DERIVED: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "md_semantic_link": (("entity_type", "entity_id", "iri"), (("label", "text"),)),
    "md_tag": (("entity_type", "entity_id", "tag_key"), (("tag_value", "text"),)),
    "md_lineage_edge": (("target", "source_asset_id", "target_asset_id", "origin"), (("via", "text"),)),
    "md_asset_edge": (("parent_asset_id", "child_asset_id", "relation"), ()),
}

DERIVED_KEYS: dict[str, tuple[str, ...]] = {name: keys for name, (keys, _) in _DERIVED.items()}
TAG_TABLE = "md_tag"

# fact (Pattern C) table -> (key columns, data columns); every fact table also gets repo_id and recorded_at
_FACTS: dict[str, tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]] = {
    "md_registration": (
        (("project_id", "text"), ("registry_pr_url", "text")),
        (
            ("issue_url", "text"),
            ("requested_by", "text"),
            ("approved_by", "text"),
            ("approved_at", "ts"),
            ("provisioned_repo_url", "text"),
        ),
    ),
    "md_deployment": (
        (("target", "text"), ("bundle", "text"), ("ci_run_id", "text"), ("ci_run_attempt", "int")),
        (
            ("deployment_id", "text"),
            ("git_sha", "text"),
            ("ci_run_url", "text"),
            ("deployed_by", "text"),
            ("deployed_at", "ts"),
            ("status", "text"),
            ("framework_version", "text"),
            ("dbxops_version", "text"),
        ),
    ),
    "md_deployed_asset": (
        (("deployment_id", "text"), ("asset_id", "text")),
        (("asset_record_version", "int"), ("physical_name", "text"), ("workspace_object_id", "text")),
    ),
    "md_run": (
        (("target", "text"), ("run_id", "text")),
        (
            ("job_asset_id", "text"),
            ("status", "text"),
            ("trigger", "text"),
            ("started_at", "ts"),
            ("ended_at", "ts"),
            ("git_sha", "text"),
        ),
    ),
    "md_task_run": (
        (("target", "text"), ("run_id", "text"), ("task_key", "text")),
        (
            ("status", "text"),
            ("started_at", "ts"),
            ("ended_at", "ts"),
            ("attempt", "int"),
            ("error_class", "text"),
        ),
    ),
    "md_pipeline_update": (
        (("target", "text"), ("update_id", "text")),
        (
            ("run_id", "text"),
            ("pipeline_asset_id", "text"),
            ("status", "text"),
            ("full_refresh", "bool"),
            ("started_at", "ts"),
            ("ended_at", "ts"),
        ),
    ),
    "md_dataset_metrics": (
        (("target", "text"), ("update_id", "text"), ("asset_id", "text")),
        (
            ("rows_written", "bigint"),
            ("rows_upserted", "bigint"),
            ("rows_deleted", "bigint"),
            ("rows_dropped", "bigint"),
        ),
    ),
    "md_quality_result": (
        (("target", "text"), ("update_id", "text"), ("asset_id", "text"), ("rule_id", "text")),
        (
            ("contract_id", "text"),
            ("rule_record_version", "int"),
            ("passed", "bigint"),
            ("failed", "bigint"),
            ("dropped", "bigint"),
            ("quarantined", "bigint"),
        ),
    ),
    "md_eval_result": (
        (
            ("target", "text"),
            ("run_id", "text"),
            ("subject_asset_id", "text"),
            ("subject_version", "text"),
            ("metric", "text"),
        ),
        (
            ("eval_kind", "text"),
            ("value", "double"),
            ("threshold", "double"),
            ("comparator", "text"),
            ("passed", "bool"),
            ("alias", "text"),
            ("gate_source_path", "text"),
        ),
    ),
    "md_publish_log": (
        (("publish_id", "text"), ("status", "text")),
        (
            ("target", "text"),
            ("what", "text"),
            ("git_sha", "text"),
            ("inserted", "int"),
            ("closed", "int"),
            ("unchanged", "int"),
            ("orphans_cleaned", "int"),
            ("message", "text"),
            ("started_at", "ts"),
            ("finished_at", "ts"),
        ),
    ),
}

# runtime facts purged after retention_days; definitions and deployments are kept forever
RUNTIME_TABLES = (
    "md_run",
    "md_task_run",
    "md_pipeline_update",
    "md_dataset_metrics",
    "md_quality_result",
    "md_eval_result",
)


def _build_specs() -> dict[str, EntitySpec]:
    specs = {}
    for table, (kind, owner, semver_required, extras) in _PARENTS.items():
        children = tuple(
            ChildSpec(child, parent_column, keys)
            for child, (parent, parent_column, keys, _) in _CHILDREN.items()
            if parent == table
        )
        specs[kind] = EntitySpec(kind, table, owner, semver_required, tuple(name for name, _ in extras), children)
    return specs


ENTITIES: dict[str, EntitySpec] = _build_specs()
PLATFORM_KINDS = frozenset(kind for kind, spec in ENTITIES.items() if spec.owner == WRITER_PLATFORM)
SQUAD_KINDS = frozenset(kind for kind, spec in ENTITIES.items() if spec.owner == WRITER_SQUAD)
PLATFORM_FACT_TABLES = frozenset({"md_registration"})
DERIVED_TABLES = tuple(_DERIVED)
FACT_TABLES = tuple(_FACTS)


def entity_spec(kind: str) -> EntitySpec:
    """Return the spec of an entity kind, or raise ``SchemaError`` for an unknown kind."""
    try:
        return ENTITIES[kind]
    except KeyError:
        raise SchemaError(f"unknown entity kind {kind!r}; expected one of {', '.join(sorted(ENTITIES))}") from None


def _column(name: str, code: str, **kwargs: Any) -> Column:
    column_name = quoted_name(name, True) if name in {"unique", "schema"} else name
    return Column(column_name, _TYPE_CODES[code](), **kwargs)


def _key_columns(names: Iterable[tuple[str, str]]) -> list[Column]:
    return [_column(name, code, nullable=False) for name, code in names]


def _header_columns() -> list[Column]:
    return [
        Column("entity_id", Text, nullable=False),
        Column("record_version", Integer, nullable=False),
        Column("repo_id", Text, nullable=False),
        Column("semver", Text),
        Column("status", Text, nullable=False),
        Column("content_hash", Text, nullable=False),
        Column("payload", JsonType, nullable=False),
        Column("introduced_commit", Text),
        Column("last_seen_commit", Text),
        Column("source_path", Text),
        Column("retired_commit", Text),
        Column("valid_from", UtcTimestamp, nullable=False),
        Column("valid_to", UtcTimestamp),
        Column("is_current", Boolean, nullable=False),
        Column("published_at", UtcTimestamp, nullable=False),
        Column("published_by", Text, nullable=False),
    ]


class MetaModel:
    """The tables and views of the metadata database for one schema name."""

    def __init__(self, schema: str | None = DEFAULT_SCHEMA):
        self.schema = schema
        self.metadata = MetaData()
        self.tables: dict[str, Table] = {}
        self._build_parents()
        self._build_children()
        self._build_derived()
        self._build_facts()
        self._build_admin()

    def table(self, name: str) -> Table:
        """Return the table called ``name``."""
        try:
            return self.tables[name]
        except KeyError:
            raise SchemaError(f"unknown table {name!r}") from None

    def parent(self, kind: str) -> Table:
        """Return the parent table of an entity kind."""
        return self.table(entity_spec(kind).table)

    def _add(self, name: str, *columns: Column, key: Iterable[str], extra: Iterable[Any] = ()) -> Table:
        table = Table(name, self.metadata, *columns, PrimaryKeyConstraint(*key), *extra, schema=self.schema)
        self.tables[name] = table
        return table

    def _build_parents(self) -> None:
        for name, (_, _, _, extras) in _PARENTS.items():
            columns = _header_columns() + [_column(col, code) for col, code in extras]
            index = Index(
                f"uq_{name}_current",
                "entity_id",
                unique=True,
                sqlite_where=text("is_current"),
                postgresql_where=text("is_current"),
            )
            self._add(name, *columns, key=("entity_id", "record_version"), extra=(index,))

    def _build_children(self) -> None:
        for name, (_, parent_column, keys, data) in _CHILDREN.items():
            columns = [Column(parent_column, Text, nullable=False), Column("record_version", Integer, nullable=False)]
            columns += _key_columns((key, "text") for key in keys)
            columns += [_column(col, code) for col, code in data]
            self._add(name, *columns, key=(parent_column, "record_version", *keys))

    def _build_derived(self) -> None:
        for name, (keys, data) in _DERIVED.items():
            columns = _key_columns((key, "text") for key in keys)
            columns += [_column(col, code) for col, code in data]
            columns += [
                Column("repo_id", Text, nullable=False),
                Column("is_active", Boolean, nullable=False),
                Column("first_seen_at", UtcTimestamp, nullable=False),
                Column("last_seen_at", UtcTimestamp, nullable=False),
            ]
            self._add(name, *columns, key=keys)

    def _build_facts(self) -> None:
        for name, (keys, data) in _FACTS.items():
            columns = _key_columns(keys) + [_column(col, code) for col, code in data]
            columns += [Column("repo_id", Text, nullable=False), Column("recorded_at", UtcTimestamp, nullable=False)]
            self._add(name, *columns, key=[key for key, _ in keys])

    def _build_admin(self) -> None:
        self._add(
            "md_schema_version",
            Column("version", Integer, nullable=False),
            Column("description", Text),
            Column("applied_at", UtcTimestamp, nullable=False),
            key=("version",),
        )

    def views(self) -> dict[str, Select]:
        """Return the view name -> defining select, in creation order."""
        views: dict[str, Select] = {}
        for spec in ENTITIES.values():
            parent = self.table(spec.table)
            views[_view_name(spec.table)] = select(parent).where(parent.c.is_current.is_(true()))
            for child in spec.children:
                views[_view_name(child.table)] = self._child_view(parent, child)
        views["v_lineage"] = self._lineage_view()
        views["v_lineage_drift"] = self._lineage_drift_view()
        views["v_project_directory"] = self._project_directory_view()
        views["v_asset_inventory"] = self._asset_inventory_view()
        views["v_asset_tree"] = self._asset_tree_view()
        return views

    def _asset_inventory_view(self) -> Select:
        asset = self.table("md_asset")
        return (
            select(asset.c.repo_id, asset.c.workload, asset.c.asset_type, func.count().label("assets"))
            .where(asset.c.is_current.is_(true()))
            .group_by(asset.c.repo_id, asset.c.workload, asset.c.asset_type)
        )

    def _last_runs(self):
        """Return the newest run, pipeline update or dataset write of each asset per target, keyed by the asset's own id."""
        run, update = self.table("md_run"), self.table("md_pipeline_update")
        metric, deployed = self.table("md_dataset_metrics"), self.table("md_deployed_asset")
        deployment = self.table("md_deployment")
        # runs and pipeline updates record the workspace object id, which md_deployed_asset maps to the asset id
        jobs = (
            select(
                deployed.c.asset_id.label("asset_id"),
                run.c.target.label("target"),
                run.c.started_at.label("run_at"),
                run.c.status.label("run_status"),
                literal(None).label("rows_written"),
            )
            .select_from(
                run.join(
                    deployed, run.c.job_asset_id == "uc_job/" + run.c.repo_id + "/" + deployed.c.workspace_object_id
                ).join(deployment, deployment.c.deployment_id == deployed.c.deployment_id)
            )
            .where(deployment.c.target == run.c.target)
        )
        pipelines = (
            select(
                deployed.c.asset_id,
                update.c.target,
                update.c.started_at,
                update.c.status,
                literal(None),
            )
            .select_from(
                update.join(
                    deployed, update.c.pipeline_asset_id == "uc_pipeline/" + deployed.c.workspace_object_id
                ).join(deployment, deployment.c.deployment_id == deployed.c.deployment_id)
            )
            .where(deployment.c.target == update.c.target)
        )
        datasets = select(
            metric.c.asset_id, metric.c.target, update.c.started_at, update.c.status, metric.c.rows_written
        ).select_from(
            metric.join(update, and_(update.c.update_id == metric.c.update_id, update.c.target == metric.c.target))
        )
        both = union_all(jobs, pipelines, datasets).subquery("runs")
        ranked = select(
            both,
            func.row_number()
            .over(partition_by=[both.c.asset_id, both.c.target], order_by=both.c.run_at.desc())
            .label("rn"),
        ).subquery("ranked_runs")
        return select(ranked).where(ranked.c.rn == 1).subquery("last_runs")

    def _last_deployments(self):
        """Return the newest deployment of each asset per target."""
        deployed, deployment = self.table("md_deployed_asset"), self.table("md_deployment")
        joined = (
            select(
                deployed.c.asset_id.label("asset_id"),
                deployment.c.target.label("target"),
                deployment.c.git_sha.label("git_sha"),
                deployment.c.deployed_at.label("deployed_at"),
                func.row_number()
                .over(
                    partition_by=[deployed.c.asset_id, deployment.c.target],
                    order_by=deployment.c.deployed_at.desc(),
                )
                .label("rn"),
            )
            .select_from(deployed.join(deployment, deployment.c.deployment_id == deployed.c.deployment_id))
            .subquery("deployments")
        )
        return select(joined).where(joined.c.rn == 1).subquery("last_deployments")

    def _asset_tree_view(self) -> Select:
        """
        Return every current asset with its path, and whether it is designed, deployed and running, per target.

        The path runs bundle > workflow > task > pipeline > spec > flow > dataset, up to six levels. State is
        ``running`` when a run, pipeline update or dataset write is recorded, ``deployed`` when only a deployment is,
        ``designed`` when neither, and ``contained`` for tasks, specs and flows, which are deployed with their parent.
        """
        asset = self.table("md_asset")
        levels = [asset.alias(f"l{n}") for n in range(6)]
        joined = levels[0].outerjoin(
            levels[1],
            and_(levels[1].c.entity_id == levels[0].c.parent_asset_id, levels[1].c.is_current.is_(true())),
        )
        for n in range(2, 6):
            joined = joined.outerjoin(
                levels[n],
                and_(levels[n].c.entity_id == levels[n - 1].c.parent_asset_id, levels[n].c.is_current.is_(true())),
            )
        deployments, runs = self._last_deployments(), self._last_runs()
        joined = joined.outerjoin(deployments, deployments.c.asset_id == levels[0].c.entity_id).outerjoin(
            runs, and_(runs.c.asset_id == levels[0].c.entity_id, runs.c.target == deployments.c.target)
        )
        segments = [
            case(
                (lvl.c.entity_id.is_(None), ""),
                else_=func.coalesce(func.nullif(lvl.c.resource_key, ""), lvl.c.entity_id) + " > ",
            )
            for lvl in reversed(levels[1:])
        ]
        path = segments[0]
        for segment in segments[1:]:
            path = path + segment
        path = path + func.coalesce(func.nullif(levels[0].c.resource_key, ""), levels[0].c.entity_id)
        contained = levels[0].c.asset_type.in_(("uc_job_task", "dataflow_spec", "dataflow_flow"))
        state = case(
            (contained, "contained"),
            (runs.c.run_at.is_not(None), "running"),
            (deployments.c.deployed_at.is_not(None), "deployed"),
            else_="designed",
        )
        depth = (
            case((levels[1].c.entity_id.is_not(None), 1), else_=0)
            + case((levels[2].c.entity_id.is_not(None), 1), else_=0)
            + case((levels[3].c.entity_id.is_not(None), 1), else_=0)
            + case((levels[4].c.entity_id.is_not(None), 1), else_=0)
            + case((levels[5].c.entity_id.is_not(None), 1), else_=0)
        )
        top = levels[0]
        return (
            select(
                top.c.entity_id.label("asset_id"),
                top.c.repo_id,
                top.c.asset_type,
                top.c.subtype,
                top.c.workload,
                top.c.layer,
                top.c.parent_asset_id,
                path.label("path"),
                depth.label("depth"),
                deployments.c.target.label("target"),
                deployments.c.git_sha.label("deployed_commit"),
                deployments.c.deployed_at,
                runs.c.run_status.label("last_run_status"),
                runs.c.run_at.label("last_run_at"),
                runs.c.rows_written.label("last_rows_written"),
                state.label("state"),
            )
            .select_from(joined)
            .where(top.c.is_current.is_(true()))
        )

    def _child_view(self, parent: Table, child: ChildSpec) -> Select:
        table = self.table(child.table)
        joined = table.join(
            parent,
            and_(
                parent.c.entity_id == table.c[child.parent_column],
                parent.c.record_version == table.c.record_version,
            ),
        )
        return (
            select(table, parent.c.repo_id, parent.c.semver, parent.c.status)
            .select_from(joined)
            .where(parent.c.is_current.is_(true()))
        )

    def _edge_exists(self, edge, other_origin: str):
        other = self.table("md_lineage_edge").alias("o")
        return exists().where(
            and_(
                other.c.target == edge.c.target,
                other.c.source_asset_id == edge.c.source_asset_id,
                other.c.target_asset_id == edge.c.target_asset_id,
                other.c.origin == other_origin,
                other.c.is_active.is_(true()),
            )
        )

    def _lineage_view(self) -> Select:
        edge = self.table("md_lineage_edge")
        observed = case((self._edge_exists(edge, ORIGIN_UC_SYSTEM), true()), else_=false()).label("observed")
        return select(
            edge.c.target, edge.c.source_asset_id, edge.c.target_asset_id, edge.c.via, edge.c.repo_id, observed
        ).where(and_(edge.c.origin == ORIGIN_SPEC, edge.c.is_active.is_(true())))

    def _lineage_drift_view(self) -> Select:
        edge = self.table("md_lineage_edge")
        return select(edge.c.target, edge.c.source_asset_id, edge.c.target_asset_id, edge.c.via, edge.c.repo_id).where(
            and_(
                edge.c.origin == ORIGIN_UC_SYSTEM,
                edge.c.is_active.is_(true()),
                ~self._edge_exists(edge, ORIGIN_SPEC),
            )
        )

    def _project_directory_view(self) -> Select:
        project, group = self.table("md_project"), self.table("md_project_group")
        joined = project.outerjoin(
            group, and_(group.c.project_id == project.c.entity_id, group.c.record_version == project.c.record_version)
        )
        return (
            select(
                project.c.entity_id.label("project_id"),
                project.c.title,
                project.c.status,
                project.c.repo_kind,
                project.c.domain_id,
                project.c.owner_team_id,
                project.c.repo_url,
                group.c.access_role,
                group.c.ad_group,
            )
            .select_from(joined)
            .where(project.c.is_current.is_(true()))
        )


def _view_name(table: str) -> str:
    return f"v_{table.removeprefix('md_')}_current"


def build_model(schema: str | None = DEFAULT_SCHEMA) -> MetaModel:
    """Build the metadata model for ``schema`` (``None`` for databases without schemas, like SQLite)."""
    return MetaModel(schema)


def _dialect(name: str):
    if name == "sqlite":
        return sqlite.dialect()
    if name == "postgresql":
        return postgresql.dialect()
    if name == "databricks":
        return importlib.import_module("databricks.sqlalchemy").DatabricksDialect()
    raise SchemaError(f"unsupported dialect {name!r}; expected sqlite, postgresql or databricks")


def render_table_ddl(model: MetaModel, dialect_name: str) -> list[str]:
    """Render ``CREATE TABLE IF NOT EXISTS`` for every table, in dependency order."""
    dialect = _dialect(dialect_name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the Databricks dialect warns about autoincrement on every integer key
        return [
            str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
            for table in model.metadata.sorted_tables
        ]


def render_index_ddl(model: MetaModel, dialect_name: str) -> list[str]:
    """Render ``CREATE UNIQUE INDEX`` statements (the at-most-one-current-row guard) where the dialect has indexes."""
    if dialect_name not in {"sqlite", "postgresql"}:
        return []
    dialect = _dialect(dialect_name)
    statements = []
    for table in model.metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)).strip())
    return statements


def render_view_ddl(model: MetaModel, dialect_name: str) -> list[str]:
    """Render the view statements; SQLite drops and recreates because it has no ``CREATE OR REPLACE VIEW``."""
    dialect = _dialect(dialect_name)
    preparer = dialect.identifier_preparer
    statements = []
    for name, query in model.views().items():
        qualified = preparer.quote(name) if model.schema is None else f"{preparer.quote_schema(model.schema)}.{name}"
        body = str(query.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        if dialect_name == "sqlite":
            statements.append(f"DROP VIEW IF EXISTS {qualified}")
            statements.append(f"CREATE VIEW {qualified} AS {body}")
        else:
            statements.append(f"CREATE OR REPLACE VIEW {qualified} AS {body}")
    return statements


__all__ = [
    "DEFAULT_SCHEMA",
    "ENTITIES",
    "PLATFORM_KINDS",
    "SCHEMA_VERSION",
    "SQUAD_KINDS",
    "ChildSpec",
    "EntitySpec",
    "MetaModel",
    "build_model",
    "entity_spec",
    "render_index_ddl",
    "render_table_ddl",
    "render_view_ddl",
]
