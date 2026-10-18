"""
Reconcilers bring workspace objects to a declared desired state.

``Reconciler.reconcile`` is a Template Method: ``read_actual -> diff -> plan -> apply``. A dry run (the default)
stops after ``plan``. Reconcilers only create or update what they were asked to manage and never delete anything.
Running a reconciler twice makes the second run a no-op.
"""

from __future__ import annotations

import re
from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import (
    dataclass,
    field,
)
from functools import partial
from typing import (
    Any,
    Callable,
    Mapping,
    Sequence,
)

from dbxops.workspace import (
    TAGGABLE_ENTITY_TYPES,
    GatewayError,
    WorkspaceGateway,
)

OWNED_TAG_PREFIXES = ("data_domain_", "data_product_", "data_contract_")
OWNED_TAG_KEYS = frozenset({"classification", "technical_owner", "data_owner", "data_steward"})
ONTOS_TAG_PREFIX = "ontos_"
MAX_TAG_KEY_LENGTH = 128
_TAG_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_APPLY_ERRORS = (OSError, GatewayError, ValueError)


@dataclass(frozen=True)
class Change:
    """One planned action; ``operation`` performs it and is not part of equality or the repr."""

    resource: str
    action: str
    detail: str = ""
    operation: Callable[[], Any] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Diff:
    """What ``Reconciler.diff`` found: planned changes, resources already correct, and problems."""

    changes: tuple[Change, ...] = ()
    unchanged: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconcileResult:
    """The outcome of a reconcile run; ``applied`` is False for a dry run."""

    changes: tuple[Change, ...]
    unchanged: tuple[str, ...]
    errors: tuple[str, ...]
    applied: bool

    @property
    def ok(self) -> bool:
        """Return whether the run finished without errors."""
        return not self.errors


class DiffBuilder:
    """Collects changes, unchanged resources and errors while a reconciler walks its desired state."""

    def __init__(self):
        self._changes: list[Change] = []
        self._unchanged: list[str] = []
        self._errors: list[str] = []

    def change(self, resource: str, action: str, operation: Callable[[], Any], detail: str = "") -> None:
        """Record a planned change."""
        self._changes.append(Change(resource, action, detail, operation))

    def keep(self, resource: str) -> None:
        """Record a resource that already matches."""
        self._unchanged.append(resource)

    def fail(self, message: str) -> None:
        """Record a problem that blocks one resource without blocking the others."""
        self._errors.append(message)

    @property
    def change_count(self) -> int:
        """Return the number of changes recorded so far."""
        return len(self._changes)

    def build(self) -> Diff:
        """Return the immutable diff."""
        return Diff(tuple(self._changes), tuple(self._unchanged), tuple(self._errors))


def matches(desired: Any, actual: Any) -> bool:
    """
    Return whether ``actual`` satisfies ``desired``.

    Mappings match when every desired key matches (actual may carry extra, server-populated keys). Lists match
    element by element and must have the same length. Scalars match by equality or by their string form.
    """
    if isinstance(desired, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and matches(desired[key], actual[key]) for key in desired
        )
    if isinstance(desired, (list, tuple)):
        return (
            isinstance(actual, (list, tuple))
            and len(desired) == len(actual)
            and all(matches(want, have) for want, have in zip(desired, actual))
        )
    return desired == actual or str(desired) == str(actual)


class Reconciler(ABC):
    """Template Method for reconciling one kind of workspace object."""

    def __init__(self, gateway: WorkspaceGateway):
        self._gateway = gateway

    def reconcile(self, desired: Any, *, dry_run: bool = True) -> ReconcileResult:
        """Read the workspace, plan the changes that reach ``desired``, and apply them unless ``dry_run``."""
        actual = self.read_actual(desired)
        diff = self.diff(desired, actual)
        changes = tuple(self.plan(diff.changes))
        errors = list(diff.errors)
        if not dry_run:
            errors.extend(self.apply(changes))
        return ReconcileResult(changes, diff.unchanged, tuple(errors), applied=not dry_run)

    @abstractmethod
    def read_actual(self, desired: Any) -> Any:
        """Read the current state of everything ``desired`` mentions."""

    @abstractmethod
    def diff(self, desired: Any, actual: Any) -> Diff:
        """Compare desired and actual state."""

    def plan(self, changes: Sequence[Change]) -> Sequence[Change]:
        """Order the changes for application; the default keeps the order ``diff`` produced."""
        return changes

    def apply(self, changes: Sequence[Change]) -> list[str]:
        """Perform each change in order and return one error message per failed change."""
        errors = []
        for change in changes:
            try:
                if change.operation is not None:
                    change.operation()
            except _APPLY_ERRORS as error:
                errors.append(f"{change.resource}: {change.action} failed: {error}")
        return errors


# --- Vector Search ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DesiredVectorSearchIndex:
    """
    A vector search index and the endpoint that hosts it.

    ``sync`` is an action rather than state: when true, every run triggers a sync of an existing TRIGGERED index.
    """

    name: str
    endpoint_name: str
    primary_key: str
    delta_sync_index_spec: Mapping[str, Any]
    index_type: str = "DELTA_SYNC"
    endpoint_type: str = "STANDARD"
    sync: bool = False


class VectorSearchIndexReconciler(Reconciler):
    """
    Ensures vector search endpoints and indexes exist.

    An index spec cannot be changed in place, so a differing index is reported as an error and never dropped. A
    freshly created endpoint is not ready at once; if the index create fails for that reason, run again.
    """

    def read_actual(self, desired: Sequence[DesiredVectorSearchIndex]) -> dict:
        """Read each endpoint and index once."""
        endpoints = {item.endpoint_name: None for item in desired}
        return {
            "endpoints": {name: self._gateway.get_vector_search_endpoint(name) for name in endpoints},
            "indexes": {item.name: self._gateway.get_vector_search_index(item.name) for item in desired},
        }

    def diff(self, desired: Sequence[DesiredVectorSearchIndex], actual: dict) -> Diff:
        """Plan endpoint creates, then index creates and syncs."""
        builder = DiffBuilder()
        self._diff_endpoints(builder, desired, actual["endpoints"])
        for item in desired:
            self._diff_index(builder, item, actual["indexes"][item.name])
        return builder.build()

    def _diff_endpoints(self, builder: DiffBuilder, desired: Sequence[DesiredVectorSearchIndex], actual: dict) -> None:
        seen: set[str] = set()
        for item in desired:
            if item.endpoint_name in seen:
                continue
            seen.add(item.endpoint_name)
            resource = f"vector_search_endpoint:{item.endpoint_name}"
            found = actual[item.endpoint_name]
            if found is None:
                builder.change(
                    resource,
                    "create",
                    partial(self._gateway.create_vector_search_endpoint, item.endpoint_name, item.endpoint_type),
                    f"type {item.endpoint_type}",
                )
            elif found.get("endpoint_type", item.endpoint_type) != item.endpoint_type:
                builder.fail(
                    f"{resource}: type is {found['endpoint_type']}, desired {item.endpoint_type}; not changed"
                )
            else:
                builder.keep(resource)

    def _diff_index(self, builder: DiffBuilder, item: DesiredVectorSearchIndex, found: dict | None) -> None:
        resource = f"vector_search_index:{item.name}"
        if found is None:
            builder.change(resource, "create", lambda: self._create_index(item), f"on {item.endpoint_name}")
            return
        wanted = {
            "endpoint_name": item.endpoint_name,
            "primary_key": item.primary_key,
            "index_type": item.index_type,
            "delta_sync_index_spec": item.delta_sync_index_spec,
        }
        if not matches(wanted, found):
            builder.fail(
                f"{resource}: differs from the desired spec and a spec cannot be updated; recreate it manually"
            )
        elif item.sync and found.get("delta_sync_index_spec", {}).get("pipeline_type") == "TRIGGERED":
            builder.change(resource, "sync", lambda: self._gateway.sync_vector_search_index(item.name))
        else:
            builder.keep(resource)

    def _create_index(self, item: DesiredVectorSearchIndex) -> None:
        self._gateway.create_vector_search_index(
            item.name,
            {
                "endpoint_name": item.endpoint_name,
                "primary_key": item.primary_key,
                "index_type": item.index_type,
                "delta_sync_index_spec": item.delta_sync_index_spec,
            },
        )


# --- Model serving ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DesiredServingEndpoint:
    """
    A serving endpoint: served entities, traffic routes, AI Gateway rate limits, inference table and tags.

    Tags are added or updated, never removed.
    """

    name: str
    served_entities: Sequence[Mapping[str, Any]]
    traffic_routes: Sequence[Mapping[str, Any]] = ()
    rate_limits: Sequence[Mapping[str, Any]] = ()
    inference_table: Mapping[str, Any] | None = None
    tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def config(self) -> dict:
        """Return the endpoint config in the serving API's shape."""
        config: dict[str, Any] = {"served_entities": list(self.served_entities)}
        if self.traffic_routes:
            config["traffic_config"] = {"routes": list(self.traffic_routes)}
        return config

    @property
    def ai_gateway(self) -> dict:
        """Return the AI Gateway settings; empty when neither rate limits nor an inference table are desired."""
        gateway: dict[str, Any] = {}
        if self.rate_limits:
            gateway["rate_limits"] = list(self.rate_limits)
        if self.inference_table is not None:
            gateway["inference_table_config"] = dict(self.inference_table)
        return gateway


class ServingEndpointReconciler(Reconciler):
    """Ensures serving endpoints exist with the desired config, AI Gateway settings and tags."""

    def read_actual(self, desired: Sequence[DesiredServingEndpoint]) -> dict:
        """Read each endpoint by name."""
        return {item.name: self._gateway.get_serving_endpoint(item.name) for item in desired}

    def diff(self, desired: Sequence[DesiredServingEndpoint], actual: dict) -> Diff:
        """Plan a create for a missing endpoint, otherwise the config, AI Gateway and tag updates it needs."""
        builder = DiffBuilder()
        for item in desired:
            found = actual[item.name]
            if found is None:
                builder.change(f"serving_endpoint:{item.name}", "create", partial(self._create, item))
            else:
                self._diff_existing(builder, item, found)
        return builder.build()

    def _create(self, item: DesiredServingEndpoint) -> None:
        self._gateway.create_serving_endpoint(item.name, item.config, item.ai_gateway or None, item.tags or None)

    def _diff_existing(self, builder: DiffBuilder, item: DesiredServingEndpoint, found: dict) -> None:
        resource = f"serving_endpoint:{item.name}"
        in_sync = True
        if not matches(item.config, found.get("config", {})):
            in_sync = False
            builder.change(
                resource, "update_config", lambda: self._gateway.update_serving_endpoint_config(item.name, item.config)
            )
        if item.ai_gateway and not matches(item.ai_gateway, found.get("ai_gateway", {})):
            in_sync = False
            builder.change(
                resource,
                "update_ai_gateway",
                lambda: self._gateway.update_serving_endpoint_ai_gateway(item.name, item.ai_gateway),
            )
        actual_tags = {tag["key"]: tag.get("value", "") for tag in found.get("tags", [])}
        for key, value in item.tags.items():
            if actual_tags.get(key) != value:
                in_sync = False
                builder.change(
                    f"{resource}/tag:{key}",
                    "set_tag",
                    partial(self._gateway.set_tag, "serving_endpoint", item.name, key, value),
                )
        if in_sync:
            builder.keep(resource)


# --- Quality monitors ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DesiredMonitor:
    """
    A quality monitor on a table.

    ``settings`` holds the monitor's own configuration (``inference_log``, ``schedule`` and so on) and is compared on
    every run; ``create_options`` (``warehouse_id``, ``skip_builtin_dashboard``) apply at creation only.
    """

    table_name: str
    output_schema_name: str
    assets_dir: str
    settings: Mapping[str, Any] = field(default_factory=dict)
    create_options: Mapping[str, Any] = field(default_factory=dict)


class MonitorReconciler(Reconciler):
    """Ensures a quality monitor exists with the desired settings."""

    def read_actual(self, desired: Sequence[DesiredMonitor]) -> dict:
        """Read each table's monitor."""
        return {item.table_name: self._gateway.get_quality_monitor(item.table_name) for item in desired}

    def diff(self, desired: Sequence[DesiredMonitor], actual: dict) -> Diff:
        """Plan a create for a missing monitor, or an update when its settings differ."""
        builder = DiffBuilder()
        for item in desired:
            resource = f"quality_monitor:{item.table_name}"
            found = actual[item.table_name]
            if found is None:
                builder.change(resource, "create", partial(self._create, item))
            elif found.get("assets_dir", item.assets_dir) != item.assets_dir:
                builder.fail(
                    f"{resource}: assets_dir is {found['assets_dir']}, desired {item.assets_dir}; not changed"
                )
            elif matches({"output_schema_name": item.output_schema_name, **item.settings}, found):
                builder.keep(resource)
            else:
                builder.change(resource, "update", partial(self._update, item))
        return builder.build()

    def _create(self, item: DesiredMonitor) -> None:
        self._gateway.create_quality_monitor(
            item.table_name, item.output_schema_name, item.assets_dir, {**item.create_options, **item.settings}
        )

    def _update(self, item: DesiredMonitor) -> None:
        self._gateway.update_quality_monitor(item.table_name, item.output_schema_name, dict(item.settings))


# --- Knowledge assistants --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DesiredKnowledgeAssistant:
    """
    A Knowledge Assistant and its knowledge sources.

    Each source is a mapping with ``display_name``, ``description``, ``source_type`` (``files`` or ``index``) and
    the matching ``files`` or ``index`` block. Sources are matched by ``display_name``.
    """

    display_name: str
    description: str
    sources: Sequence[Mapping[str, Any]] = ()


class KnowledgeAssistantReconciler(Reconciler):
    """Ensures a Knowledge Assistant and its sources exist, then syncs when sources were added."""

    def read_actual(self, desired: Sequence[DesiredKnowledgeAssistant]) -> dict:
        """Read each assistant by display name, with its sources when it exists."""
        actual: dict[str, dict | None] = {}
        for item in desired:
            found = self._gateway.get_knowledge_assistant(item.display_name)
            if found is not None:
                found = {**found, "sources": self._gateway.list_knowledge_sources(found["name"])}
            actual[item.display_name] = found
        return actual

    def diff(self, desired: Sequence[DesiredKnowledgeAssistant], actual: dict) -> Diff:
        """Plan the assistant create or update, the missing sources, and one sync."""
        builder = DiffBuilder()
        for item in desired:
            self._diff_one(builder, item, actual[item.display_name])
        return builder.build()

    def _diff_one(self, builder: DiffBuilder, item: DesiredKnowledgeAssistant, found: dict | None) -> None:
        resource = f"knowledge_assistant:{item.display_name}"
        start = builder.change_count
        created: dict[str, Any] = {}
        if found is None:
            builder.change(resource, "create", lambda: created.update(self._create(item)))
            existing: list[dict] = []
        else:
            self._diff_description(builder, item, found, resource)
            existing = found["sources"]

        def name_of() -> str:
            return created["name"] if found is None else found["name"]

        if self._diff_sources(builder, item, existing, name_of):
            builder.change(resource, "sync", lambda: self._gateway.sync_knowledge_sources(name_of()))
        if found is not None and builder.change_count == start:
            builder.keep(resource)

    def _add_source(self, name_of: Callable[[], str], source: Mapping[str, Any]) -> None:
        self._gateway.create_knowledge_source(name_of(), source)

    def _create(self, item: DesiredKnowledgeAssistant) -> dict:
        return self._gateway.create_knowledge_assistant(item.display_name, item.description)

    def _diff_description(
        self, builder: DiffBuilder, item: DesiredKnowledgeAssistant, found: dict, resource: str
    ) -> None:
        if found.get("description") != item.description:
            builder.change(
                resource, "update", lambda: self._gateway.update_knowledge_assistant(found["name"], item.description)
            )

    def _diff_sources(
        self,
        builder: DiffBuilder,
        item: DesiredKnowledgeAssistant,
        existing: Sequence[dict],
        name_of: Callable[[], str],
    ) -> bool:
        by_name = {source.get("display_name"): source for source in existing}
        added = False
        for source in item.sources:
            label = f"knowledge_assistant:{item.display_name}/source:{source['display_name']}"
            have = by_name.get(source["display_name"])
            if have is None:
                added = True
                builder.change(label, "create_source", partial(self._add_source, name_of, source))
            elif not matches(source, have):
                builder.fail(f"{label}: differs from the desired source and sources cannot be updated; not changed")
        return added


# --- Unity Catalog metadata sync -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DesiredTag:
    """
    One desired tag row.

    ``entity_type`` is one of ``catalog``, ``schema``, ``table``, ``column``, ``volume``, ``registered_model`` or
    ``serving_endpoint``; ``entity_id`` is its full name (a column is ``catalog.schema.table.column``). An inactive
    row asks for the tag to be removed.
    """

    entity_type: str
    entity_id: str
    tag_key: str
    tag_value: str | None = None
    is_active: bool = True


@dataclass(frozen=True)
class DesiredComment:
    """A desired comment on a ``table`` or ``column``."""

    entity_type: str
    entity_id: str
    comment: str


@dataclass(frozen=True)
class MetadataSyncInput:
    """The tag rows and comments that Unity Catalog should reflect."""

    tags: Sequence[DesiredTag] = ()
    comments: Sequence[DesiredComment] = ()


def is_owned_tag_key(key: str, owned_extra_keys: Sequence[str] = ()) -> bool:
    """Return whether the reconciler may write and remove this tag key."""
    return key in OWNED_TAG_KEYS or key.startswith(OWNED_TAG_PREFIXES) or key in owned_extra_keys


_MISSING = object()


def _table_of(comment: DesiredComment) -> str:
    return comment.entity_id if comment.entity_type == "table" else comment.entity_id.rsplit(".", 1)[0]


class MetadataSyncReconciler(Reconciler):
    """
    Applies comments and tags to Unity Catalog tables, columns, models and serving endpoints.

    It adds and updates desired tags, and removes a tag only when its row is inactive and its key is owned: the
    ``data_domain_*``, ``data_product_*``, ``data_contract_*`` and ``classification`` keys plus ``owned_extra_keys``
    (the project tag keys). A tag outside that set is never written or removed, and ``ontos_*`` tags are never
    written. A desired tag whose key is not owned, not a valid identifier or starts with ``ontos_`` is reported as an
    error and skipped.
    """

    def __init__(self, gateway: WorkspaceGateway, owned_extra_keys: Sequence[str] = ()):
        super().__init__(gateway)
        self._owned_extra_keys = tuple(owned_extra_keys)

    def read_actual(self, desired: MetadataSyncInput) -> dict:
        """Read the tags of each tagged entity and the metadata of each commented table."""
        entities = {(tag.entity_type, tag.entity_id) for tag in desired.tags if self._tag_problem(tag) is None}
        tables = {_table_of(comment) for comment in desired.comments if comment.entity_type in ("table", "column")}
        return {
            "tags": {entity: self._gateway.list_tags(*entity) for entity in sorted(entities)},
            "tables": {table: self._gateway.get_table_metadata(table) for table in sorted(tables)},
        }

    def diff(self, desired: MetadataSyncInput, actual: dict) -> Diff:
        """Plan comment writes, tag sets and owned-tag removals."""
        builder = DiffBuilder()
        for comment in desired.comments:
            self._diff_comment(builder, comment, actual["tables"])
        missing: set[tuple[str, str]] = set()
        for tag in desired.tags:
            problem = self._tag_problem(tag)
            entity = (tag.entity_type, tag.entity_id)
            if problem is not None:
                builder.fail(problem)
            elif actual["tags"][entity] is None:
                if entity not in missing:
                    missing.add(entity)
                    builder.fail(f"{tag.entity_type}:{tag.entity_id}: not found; its tags were not synced")
            else:
                self._diff_tag(builder, tag, actual["tags"][entity])
        return builder.build()

    def _tag_problem(self, tag: DesiredTag) -> str | None:
        where = f"{tag.entity_type}:{tag.entity_id}/tag:{tag.tag_key}"
        if tag.entity_type not in TAGGABLE_ENTITY_TYPES:
            return f"{where}: cannot tag entity type {tag.entity_type!r}"
        if tag.tag_key.startswith(ONTOS_TAG_PREFIX):
            return f"{where}: ontos_ tags are never written"
        if not _TAG_KEY_PATTERN.match(tag.tag_key) or len(tag.tag_key) > MAX_TAG_KEY_LENGTH:
            return f"{where}: tag key must match [a-z][a-z0-9_]* and be at most {MAX_TAG_KEY_LENGTH} characters"
        if not is_owned_tag_key(tag.tag_key, self._owned_extra_keys):
            return f"{where}: tag key is not owned by the sync, so it is not touched"
        return None

    def _diff_tag(self, builder: DiffBuilder, tag: DesiredTag, current: Mapping[str, str]) -> None:
        resource = f"{tag.entity_type}:{tag.entity_id}/tag:{tag.tag_key}"
        value = tag.tag_value or ""
        if not tag.is_active and tag.tag_key in current:
            builder.change(
                resource, "unset_tag", lambda: self._gateway.unset_tag(tag.entity_type, tag.entity_id, tag.tag_key)
            )
        elif tag.is_active and current.get(tag.tag_key) != value:
            builder.change(
                resource, "set_tag", lambda: self._gateway.set_tag(tag.entity_type, tag.entity_id, tag.tag_key, value)
            )
        else:
            builder.keep(resource)

    def _diff_comment(self, builder: DiffBuilder, comment: DesiredComment, tables: Mapping[str, dict | None]) -> None:
        resource = f"{comment.entity_type}:{comment.entity_id}/comment"
        if comment.entity_type not in ("table", "column"):
            builder.fail(f"{resource}: comments can be set on table or column, not {comment.entity_type!r}")
            return
        metadata = tables[_table_of(comment)]
        if metadata is None:
            builder.fail(f"{resource}: table not found; its comment was not synced")
            return
        if comment.entity_type == "table":
            current = metadata["comment"]
        else:
            current = metadata["columns"].get(comment.entity_id.rsplit(".", 1)[-1], _MISSING)
        if current is _MISSING:
            builder.fail(f"{resource}: column not found; its comment was not synced")
        elif (current or "") == comment.comment:
            builder.keep(resource)
        else:
            builder.change(
                resource,
                "set_comment",
                lambda: self._gateway.set_comment(comment.entity_type, comment.entity_id, comment.comment),
            )
