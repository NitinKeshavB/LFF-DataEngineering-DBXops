"""
The workspace boundary: the narrow set of operations the reconcilers and the promoter need.

``WorkspaceGateway`` is a Protocol so reconcilers can be tested against a recording fake and never import the
Databricks SDK. ``SdkWorkspaceGateway`` is the Adapter over ``databricks.sdk.WorkspaceClient``. All payloads are
plain dicts in the SDK's own JSON field names, so a desired-state file maps onto them one to one.
"""

from __future__ import annotations

import re
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
    Sequence,
)

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import (
    AlreadyExists,
    NotFound,
    ResourceAlreadyExists,
)
from databricks.sdk.service.catalog import EntityTagAssignment
from databricks.sdk.service.catalog import MonitorCronSchedule
from databricks.sdk.service.catalog import MonitorDataClassificationConfig
from databricks.sdk.service.catalog import MonitorInferenceLog
from databricks.sdk.service.catalog import MonitorMetric
from databricks.sdk.service.catalog import MonitorNotifications
from databricks.sdk.service.catalog import MonitorSnapshot
from databricks.sdk.service.catalog import MonitorTimeSeries
from databricks.sdk.service.serving import (
    AiGatewayConfig,
    EndpointCoreConfigInput,
    EndpointTag,
)
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EndpointType,
    VectorIndexType,
)

UC_TAG_ENTITY_TYPES = {
    "catalog": "catalogs",
    "schema": "schemas",
    "table": "tables",
    "column": "columns",
    "volume": "volumes",
}
MODEL_ENTITY_TYPE = "registered_model"
ENDPOINT_ENTITY_TYPE = "serving_endpoint"
TAGGABLE_ENTITY_TYPES = frozenset(UC_TAG_ENTITY_TYPES) | {MODEL_ENTITY_TYPE, ENDPOINT_ENTITY_TYPE}

_SQL_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_MODEL_REST_PREFIX = "/api/2.0/mlflow/unity-catalog/registered-models"
_KA_REST_PREFIX = "/api/2.1"
_MONITOR_TYPED_SETTINGS: dict[str, Any] = {
    "inference_log": MonitorInferenceLog,
    "snapshot": MonitorSnapshot,
    "time_series": MonitorTimeSeries,
    "schedule": MonitorCronSchedule,
    "notifications": MonitorNotifications,
    "data_classification_config": MonitorDataClassificationConfig,
}
_MONITOR_PLAIN_SETTINGS = frozenset({"slicing_exprs", "baseline_table_name", "warehouse_id", "skip_builtin_dashboard"})


class GatewayError(RuntimeError):
    """Raised when a workspace operation fails or is given an input the gateway cannot express."""


class WorkspaceGateway(Protocol):
    """
    Everything the lifecycle library needs from a Databricks workspace.

    Each ``get_*`` returns ``None`` when the object does not exist. Entity types for tags and comments are
    ``catalog``, ``schema``, ``table``, ``column``, ``volume``, ``registered_model`` and ``serving_endpoint``;
    a column id is ``catalog.schema.table.column``.
    """

    def get_vector_search_endpoint(self, name: str) -> dict | None:
        """Return the vector search endpoint, or None."""

    def create_vector_search_endpoint(self, name: str, endpoint_type: str) -> None:
        """Create a vector search endpoint."""

    def get_vector_search_index(self, name: str) -> dict | None:
        """Return the vector search index, or None."""

    def create_vector_search_index(
        self,
        name: str,
        endpoint_name: str,
        primary_key: str,
        index_type: str,
        delta_sync_index_spec: Mapping[str, Any] | None = None,
    ) -> None:
        """Create a vector search index."""

    def sync_vector_search_index(self, name: str) -> None:
        """Trigger a sync of a triggered Delta Sync index."""

    def get_serving_endpoint(self, name: str) -> dict | None:
        """Return the serving endpoint with its config, AI Gateway settings and tags, or None."""

    def create_serving_endpoint(
        self,
        name: str,
        config: Mapping[str, Any],
        ai_gateway: Mapping[str, Any] | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        """Create a serving endpoint."""

    def update_serving_endpoint_config(self, name: str, config: Mapping[str, Any]) -> None:
        """Replace the served entities and traffic config of a serving endpoint."""

    def update_serving_endpoint_ai_gateway(self, name: str, ai_gateway: Mapping[str, Any]) -> None:
        """Set the AI Gateway settings (rate limits, inference table) of a serving endpoint."""

    def get_quality_monitor(self, table_name: str) -> dict | None:
        """Return the quality monitor of a table, or None."""

    def create_quality_monitor(
        self, table_name: str, output_schema_name: str, assets_dir: str, settings: Mapping[str, Any]
    ) -> None:
        """Create a quality monitor."""

    def update_quality_monitor(self, table_name: str, output_schema_name: str, settings: Mapping[str, Any]) -> None:
        """Update a quality monitor."""

    def get_knowledge_assistant(self, display_name: str) -> dict | None:
        """Return the knowledge assistant with this display name (its ``name`` is ``knowledge-assistants/<id>``)."""

    def create_knowledge_assistant(self, display_name: str, description: str) -> dict:
        """Create a knowledge assistant and return it."""

    def update_knowledge_assistant(self, name: str, description: str) -> None:
        """Update the description of a knowledge assistant."""

    def list_knowledge_sources(self, name: str) -> list[dict]:
        """Return the knowledge sources of a knowledge assistant."""

    def create_knowledge_source(self, name: str, source: Mapping[str, Any]) -> None:
        """Add a knowledge source to a knowledge assistant."""

    def sync_knowledge_sources(self, name: str) -> None:
        """Re-sync the knowledge sources of a knowledge assistant."""

    def get_table_metadata(self, full_name: str) -> dict | None:
        """Return ``{"comment": str | None, "columns": {name: comment | None}}`` for a table, or None."""

    def set_comment(self, entity_type: str, entity_id: str, comment: str) -> None:
        """Set the comment of a table or column."""

    def list_tags(self, entity_type: str, entity_id: str) -> dict[str, str] | None:
        """Return the tags of an entity, or None when the entity does not exist."""

    def set_tag(self, entity_type: str, entity_id: str, key: str, value: str) -> None:
        """Create or update one tag."""

    def unset_tag(self, entity_type: str, entity_id: str, key: str) -> None:
        """Remove one tag."""

    def get_model_alias_version(self, model: str, alias: str) -> int | None:
        """Return the version a registered-model alias points at, or None when the alias is not set."""

    def set_model_alias(self, model: str, alias: str, version: int) -> None:
        """Point a registered-model alias at a version."""

    def get_model_version_metrics(self, model: str, version: int) -> dict[str, float]:
        """Return the latest metrics of the run that produced a model version."""

    def run_sql(self, statement: str) -> list[dict]:
        """Run a read statement and return its rows as dicts."""


def _or_none(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except NotFound:
        return None


def _quote_path(path: str) -> str:
    parts = path.split(".")
    if not all(_SQL_IDENTIFIER.match(part) for part in parts):
        raise GatewayError(f"{path!r} is not a plain identifier path; refusing to build SQL from it")
    return ".".join(f"`{part}`" for part in parts)


def _sql_literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _typed_monitor_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    typed: dict[str, Any] = {}
    for key, value in settings.items():
        if key in _MONITOR_TYPED_SETTINGS:
            typed[key] = _MONITOR_TYPED_SETTINGS[key].from_dict(value)
        elif key == "custom_metrics":
            typed[key] = [MonitorMetric.from_dict(metric) for metric in value]
        elif key in _MONITOR_PLAIN_SETTINGS:
            typed[key] = value
        else:
            raise GatewayError(f"unknown quality monitor setting {key!r}")
    return typed


class SdkWorkspaceGateway:
    """
    ``WorkspaceGateway`` backed by ``databricks.sdk.WorkspaceClient`` (databricks-sdk >= 0.60).

    Verified against the installed SDK by introspection and by unit tests that call each method on an
    autospecced client (vector search endpoints and indexes, serving create/update_config/put_ai_gateway/patch,
    quality monitors, table reads, entity tag assignments for catalogs, schemas, tables, columns and volumes,
    registered-model aliases, model version reads, experiment run reads, SQL statement execution).

    NOT verified against a live workspace, and not in the SDK: Knowledge Assistant operations call
    ``/api/2.1/knowledge-assistants`` through ``api_client.do``; the paths are inferred from the CLI resource names in
    the databricks-agent-bricks skill (``knowledge-assistants/{id}``), which gives no REST paths. Registered-model
    tags call ``/api/2.0/mlflow/unity-catalog/registered-models/{set-tag,delete-tag,get}`` (the REST endpoints
    behind ``MlflowClient.set_registered_model_tag``); the SDK has no UC model tag API and mlflow is not a dependency.
    The column entity name for tags (``catalog.schema.table.column``) is also unverified live.

    Comments are written with ``COMMENT ON`` SQL because the SDK cannot set table or column comments; this needs
    ``warehouse_id``. Vector search endpoints can only be created with the types the installed SDK enumerates.
    """

    def __init__(self, client: WorkspaceClient, warehouse_id: str | None = None):
        self._w = client
        self._warehouse_id = warehouse_id

    # --- vector search -------------------------------------------------------------------------------------------

    def get_vector_search_endpoint(self, name: str) -> dict | None:
        """Return the vector search endpoint, or None."""
        info = _or_none(lambda: self._w.vector_search_endpoints.get_endpoint(endpoint_name=name))
        return None if info is None else info.as_dict()

    def create_vector_search_endpoint(self, name: str, endpoint_type: str) -> None:
        """Create a vector search endpoint."""
        try:
            kind = EndpointType(endpoint_type)
        except ValueError as error:
            supported = ", ".join(member.value for member in EndpointType)
            raise GatewayError(f"endpoint type {endpoint_type!r} is not supported by the SDK ({supported})") from error
        self._w.vector_search_endpoints.create_endpoint(name=name, endpoint_type=kind)

    def get_vector_search_index(self, name: str) -> dict | None:
        """Return the vector search index, or None."""
        index = _or_none(lambda: self._w.vector_search_indexes.get_index(index_name=name))
        return None if index is None else index.as_dict()

    def create_vector_search_index(
        self,
        name: str,
        endpoint_name: str,
        primary_key: str,
        index_type: str,
        delta_sync_index_spec: Mapping[str, Any] | None = None,
    ) -> None:
        """Create a vector search index."""
        spec = None if delta_sync_index_spec is None else DeltaSyncVectorIndexSpecRequest.from_dict(
            dict(delta_sync_index_spec)
        )
        self._w.vector_search_indexes.create_index(
            name=name,
            endpoint_name=endpoint_name,
            primary_key=primary_key,
            index_type=VectorIndexType(index_type),
            delta_sync_index_spec=spec,
        )

    def sync_vector_search_index(self, name: str) -> None:
        """Trigger a sync of a triggered Delta Sync index."""
        self._w.vector_search_indexes.sync_index(index_name=name)

    # --- model serving -------------------------------------------------------------------------------------------

    def get_serving_endpoint(self, name: str) -> dict | None:
        """Return the serving endpoint with its config, AI Gateway settings and tags, or None."""
        endpoint = _or_none(lambda: self._w.serving_endpoints.get(name=name))
        return None if endpoint is None else endpoint.as_dict()

    def create_serving_endpoint(
        self,
        name: str,
        config: Mapping[str, Any],
        ai_gateway: Mapping[str, Any] | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        """Create a serving endpoint."""
        self._w.serving_endpoints.create(
            name=name,
            config=EndpointCoreConfigInput.from_dict(dict(config)),
            ai_gateway=None if ai_gateway is None else AiGatewayConfig.from_dict(dict(ai_gateway)),
            tags=None if not tags else [EndpointTag(key=key, value=value) for key, value in tags.items()],
        )

    def update_serving_endpoint_config(self, name: str, config: Mapping[str, Any]) -> None:
        """Replace the served entities and traffic config of a serving endpoint."""
        parsed = EndpointCoreConfigInput.from_dict(dict(config))
        self._w.serving_endpoints.update_config(
            name=name,
            served_entities=parsed.served_entities or None,
            traffic_config=parsed.traffic_config,
            auto_capture_config=parsed.auto_capture_config,
        )

    def update_serving_endpoint_ai_gateway(self, name: str, ai_gateway: Mapping[str, Any]) -> None:
        """Set the AI Gateway settings (rate limits, inference table) of a serving endpoint."""
        parsed = AiGatewayConfig.from_dict(dict(ai_gateway))
        self._w.serving_endpoints.put_ai_gateway(
            name=name,
            fallback_config=parsed.fallback_config,
            guardrails=parsed.guardrails,
            inference_table_config=parsed.inference_table_config,
            rate_limits=parsed.rate_limits,
            usage_tracking_config=parsed.usage_tracking_config,
        )

    # --- quality monitors ----------------------------------------------------------------------------------------

    def get_quality_monitor(self, table_name: str) -> dict | None:
        """Return the quality monitor of a table, or None."""
        info = _or_none(lambda: self._w.quality_monitors.get(table_name=table_name))
        return None if info is None else info.as_dict()

    def create_quality_monitor(
        self, table_name: str, output_schema_name: str, assets_dir: str, settings: Mapping[str, Any]
    ) -> None:
        """Create a quality monitor."""
        self._w.quality_monitors.create(
            table_name=table_name,
            output_schema_name=output_schema_name,
            assets_dir=assets_dir,
            **_typed_monitor_settings(settings),
        )

    def update_quality_monitor(self, table_name: str, output_schema_name: str, settings: Mapping[str, Any]) -> None:
        """Update a quality monitor."""
        self._w.quality_monitors.update(
            table_name=table_name, output_schema_name=output_schema_name, **_typed_monitor_settings(settings)
        )

    # --- knowledge assistants (REST; not in the SDK) -------------------------------------------------------------

    def get_knowledge_assistant(self, display_name: str) -> dict | None:
        """Return the knowledge assistant with this display name (its ``name`` is ``knowledge-assistants/<id>``)."""
        query: dict[str, Any] = {}
        while True:
            page = self._w.api_client.do("GET", f"{_KA_REST_PREFIX}/knowledge-assistants", query=query)
            for assistant in page.get("knowledge_assistants", []):
                if assistant.get("display_name") == display_name:
                    return assistant
            if not page.get("next_page_token"):
                return None
            query = {"page_token": page["next_page_token"]}

    def create_knowledge_assistant(self, display_name: str, description: str) -> dict:
        """Create a knowledge assistant and return it."""
        body = {"display_name": display_name, "description": description}
        return self._w.api_client.do("POST", f"{_KA_REST_PREFIX}/knowledge-assistants", body=body)

    def update_knowledge_assistant(self, name: str, description: str) -> None:
        """Update the description of a knowledge assistant."""
        self._w.api_client.do(
            "PATCH", f"{_KA_REST_PREFIX}/{name}", query={"update_mask": "description"}, body={"description": description}
        )

    def list_knowledge_sources(self, name: str) -> list[dict]:
        """Return the knowledge sources of a knowledge assistant."""
        page = self._w.api_client.do("GET", f"{_KA_REST_PREFIX}/{name}/knowledge-sources")
        return list(page.get("knowledge_sources", []))

    def create_knowledge_source(self, name: str, source: Mapping[str, Any]) -> None:
        """Add a knowledge source to a knowledge assistant."""
        self._w.api_client.do("POST", f"{_KA_REST_PREFIX}/{name}/knowledge-sources", body=dict(source))

    def sync_knowledge_sources(self, name: str) -> None:
        """Re-sync the knowledge sources of a knowledge assistant."""
        self._w.api_client.do("POST", f"{_KA_REST_PREFIX}/{name}/knowledge-sources:sync", body={})

    # --- Unity Catalog comments and tags -------------------------------------------------------------------------

    def get_table_metadata(self, full_name: str) -> dict | None:
        """Return ``{"comment": str | None, "columns": {name: comment | None}}`` for a table, or None."""
        table = _or_none(lambda: self._w.tables.get(full_name=full_name))
        if table is None:
            return None
        return {"comment": table.comment, "columns": {column.name: column.comment for column in table.columns or []}}

    def set_comment(self, entity_type: str, entity_id: str, comment: str) -> None:
        """Set the comment of a table or column."""
        if entity_type not in ("table", "column"):
            raise GatewayError(f"comments can be set on table or column, not {entity_type!r}")
        self.run_sql(f"COMMENT ON {entity_type.upper()} {_quote_path(entity_id)} IS {_sql_literal(comment)}")

    def list_tags(self, entity_type: str, entity_id: str) -> dict[str, str] | None:
        """Return the tags of an entity, or None when the entity does not exist."""
        if entity_type == MODEL_ENTITY_TYPE:
            return self._model_tags(entity_id)
        if entity_type == ENDPOINT_ENTITY_TYPE:
            endpoint = _or_none(lambda: self._w.serving_endpoints.get(name=entity_id))
            return None if endpoint is None else {tag.key: tag.value or "" for tag in endpoint.tags or []}
        assignments = _or_none(
            lambda: list(
                self._w.entity_tag_assignments.list(entity_type=self._uc_entity_type(entity_type), entity_name=entity_id)
            )
        )
        return None if assignments is None else {item.tag_key: item.tag_value or "" for item in assignments}

    def set_tag(self, entity_type: str, entity_id: str, key: str, value: str) -> None:
        """Create or update one tag."""
        if entity_type == MODEL_ENTITY_TYPE:
            body = {"name": entity_id, "key": key, "value": value}
            self._w.api_client.do("POST", f"{_MODEL_REST_PREFIX}/set-tag", body=body)
        elif entity_type == ENDPOINT_ENTITY_TYPE:
            self._w.serving_endpoints.patch(name=entity_id, add_tags=[EndpointTag(key=key, value=value)])
        else:
            self._set_uc_tag(self._uc_entity_type(entity_type), entity_id, key, value)

    def unset_tag(self, entity_type: str, entity_id: str, key: str) -> None:
        """Remove one tag."""
        if entity_type == MODEL_ENTITY_TYPE:
            self._w.api_client.do("DELETE", f"{_MODEL_REST_PREFIX}/delete-tag", query={"name": entity_id, "key": key})
        elif entity_type == ENDPOINT_ENTITY_TYPE:
            self._w.serving_endpoints.patch(name=entity_id, delete_tags=[key])
        else:
            self._w.entity_tag_assignments.delete(
                entity_type=self._uc_entity_type(entity_type), entity_name=entity_id, tag_key=key
            )

    def _uc_entity_type(self, entity_type: str) -> str:
        if entity_type not in UC_TAG_ENTITY_TYPES:
            raise GatewayError(f"cannot tag entity type {entity_type!r}; use one of {sorted(TAGGABLE_ENTITY_TYPES)}")
        return UC_TAG_ENTITY_TYPES[entity_type]

    def _set_uc_tag(self, uc_type: str, entity_id: str, key: str, value: str) -> None:
        assignment = EntityTagAssignment(entity_name=entity_id, tag_key=key, entity_type=uc_type, tag_value=value)
        try:
            self._w.entity_tag_assignments.create(tag_assignment=assignment)
        except (AlreadyExists, ResourceAlreadyExists):
            self._w.entity_tag_assignments.update(
                entity_type=uc_type,
                entity_name=entity_id,
                tag_key=key,
                tag_assignment=assignment,
                update_mask="tag_value",
            )

    def _model_tags(self, model: str) -> dict[str, str] | None:
        found = _or_none(lambda: self._w.api_client.do("GET", f"{_MODEL_REST_PREFIX}/get", query={"name": model}))
        if found is None:
            return None
        tags = found.get("registered_model", {}).get("tags", [])
        return {tag["key"]: tag.get("value", "") for tag in tags}

    # --- registered-model aliases and metrics --------------------------------------------------------------------

    def get_model_alias_version(self, model: str, alias: str) -> int | None:
        """Return the version a registered-model alias points at, or None when the alias is not set."""
        info = _or_none(lambda: self._w.model_versions.get_by_alias(full_name=model, alias=alias))
        return None if info is None else info.version

    def set_model_alias(self, model: str, alias: str, version: int) -> None:
        """Point a registered-model alias at a version."""
        self._w.registered_models.set_alias(full_name=model, alias=alias, version_num=version)

    def get_model_version_metrics(self, model: str, version: int) -> dict[str, float]:
        """Return the latest metrics of the run that produced a model version."""
        info = self._w.model_versions.get(full_name=model, version=version)
        if not info.run_id:
            raise GatewayError(f"{model} version {version} has no source run, so it has no metrics")
        run = self._w.experiments.get_run(run_id=info.run_id).run
        metrics = run.data.metrics if run and run.data and run.data.metrics else []
        return {metric.key: metric.value for metric in metrics}

    # --- SQL -----------------------------------------------------------------------------------------------------

    def run_sql(self, statement: str) -> list[dict]:
        """Run a statement on the configured SQL warehouse and return its rows as dicts."""
        if not self._warehouse_id:
            raise GatewayError("a warehouse_id is required to run SQL")
        response = self._w.statement_execution.execute_statement(
            statement=statement, warehouse_id=self._warehouse_id, wait_timeout="50s"
        )
        state = response.status.state.value if response.status and response.status.state else None
        if state != "SUCCEEDED":
            detail = response.status.error.message if response.status and response.status.error else state
            raise GatewayError(f"SQL statement did not succeed ({state}): {detail}")
        return _rows(response)


def _rows(response: Any) -> list[dict]:
    if response.manifest is None or response.result is None or response.result.data_array is None:
        return []
    names: Sequence[str] = [column.name for column in response.manifest.schema.columns]
    return [dict(zip(names, row)) for row in response.result.data_array]
