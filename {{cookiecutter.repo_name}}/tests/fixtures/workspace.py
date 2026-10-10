"""A recording in-memory ``WorkspaceGateway`` so reconcilers and the promoter are tested without a workspace."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    Mapping,
)

import pytest

READ_PREFIXES = ("get_", "list_", "run_sql")


@dataclass
class _FakeState:
    """Recorded calls and the tag store shared by every capability of the fake."""

    calls: list[tuple] = field(default_factory=list)
    tags: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    @property
    def writes(self) -> list[tuple]:
        """Return the recorded calls that changed state, in order."""
        return [call for call in self.calls if not call[0].startswith(READ_PREFIXES)]

    def _record(self, *call: Any) -> None:
        self.calls.append(call)


@dataclass
class _FakeVectorSearch(_FakeState):
    """Vector search endpoints and indexes."""

    vs_endpoints: dict[str, dict] = field(default_factory=dict)
    vs_indexes: dict[str, dict] = field(default_factory=dict)

    def get_vector_search_endpoint(self, name: str) -> dict | None:
        """Return the stored endpoint."""
        self._record("get_vector_search_endpoint", name)
        return self.vs_endpoints.get(name)

    def create_vector_search_endpoint(self, name: str, endpoint_type: str) -> None:
        """Store an endpoint."""
        self._record("create_vector_search_endpoint", name, endpoint_type)
        self.vs_endpoints[name] = {"name": name, "endpoint_type": endpoint_type}

    def get_vector_search_index(self, name: str) -> dict | None:
        """Return the stored index."""
        self._record("get_vector_search_index", name)
        return self.vs_indexes.get(name)

    def create_vector_search_index(self, name: str, spec: Mapping) -> None:
        """Store an index, adding a server-populated field like the real API."""
        self._record("create_vector_search_index", name, dict(spec))
        delta_sync = {**spec.get("delta_sync_index_spec", {}), "pipeline_id": "pipeline-1"}
        self.vs_indexes[name] = {"name": name, **spec, "delta_sync_index_spec": delta_sync}

    def sync_vector_search_index(self, name: str) -> None:
        """Record a sync."""
        self._record("sync_vector_search_index", name)


@dataclass
class _FakeServing(_FakeState):
    """Serving endpoints."""

    serving: dict[str, dict] = field(default_factory=dict)

    def get_serving_endpoint(self, name: str) -> dict | None:
        """Return the stored endpoint with its tags in the SDK's list shape."""
        self._record("get_serving_endpoint", name)
        found = self.serving.get(name)
        if found is None:
            return None
        tags = self.tags.get(("serving_endpoint", name), {})
        return {**found, "tags": [{"key": key, "value": value} for key, value in tags.items()]}

    def create_serving_endpoint(self, name: str, config: Mapping, ai_gateway=None, tags=None) -> None:
        """Store an endpoint."""
        self._record("create_serving_endpoint", name, dict(config), ai_gateway, tags)
        self.serving[name] = {"name": name, "config": dict(config), "ai_gateway": dict(ai_gateway or {})}
        self.tags[("serving_endpoint", name)] = dict(tags or {})

    def update_serving_endpoint_config(self, name: str, config: Mapping) -> None:
        """Replace the stored config."""
        self._record("update_serving_endpoint_config", name, dict(config))
        self.serving[name]["config"] = dict(config)

    def update_serving_endpoint_ai_gateway(self, name: str, ai_gateway: Mapping) -> None:
        """Replace the stored AI Gateway settings."""
        self._record("update_serving_endpoint_ai_gateway", name, dict(ai_gateway))
        self.serving[name]["ai_gateway"] = dict(ai_gateway)


@dataclass
class _FakeMonitors(_FakeState):
    """Quality monitors."""

    monitors: dict[str, dict] = field(default_factory=dict)

    def get_quality_monitor(self, table_name: str) -> dict | None:
        """Return the stored monitor."""
        self._record("get_quality_monitor", table_name)
        return self.monitors.get(table_name)

    def create_quality_monitor(self, table_name: str, output_schema_name: str, assets_dir: str, settings) -> None:
        """Store a monitor."""
        self._record("create_quality_monitor", table_name, output_schema_name, assets_dir, dict(settings))
        self.monitors[table_name] = {
            "table_name": table_name,
            "output_schema_name": output_schema_name,
            "assets_dir": assets_dir,
            "status": "MONITOR_STATUS_ACTIVE",
            **settings,
        }

    def update_quality_monitor(self, table_name: str, output_schema_name: str, settings) -> None:
        """Merge settings into the stored monitor."""
        self._record("update_quality_monitor", table_name, output_schema_name, dict(settings))
        self.monitors[table_name].update({"output_schema_name": output_schema_name, **settings})


@dataclass
class _FakeKnowledgeAssistants(_FakeState):
    """Knowledge assistants and sources."""

    assistants: dict[str, dict] = field(default_factory=dict)
    sources: dict[str, list[dict]] = field(default_factory=dict)

    def get_knowledge_assistant(self, display_name: str) -> dict | None:
        """Return the stored assistant."""
        self._record("get_knowledge_assistant", display_name)
        return self.assistants.get(display_name)

    def create_knowledge_assistant(self, display_name: str, description: str) -> dict:
        """Store an assistant."""
        self._record("create_knowledge_assistant", display_name, description)
        assistant = {
            "name": f"knowledge-assistants/ka-{len(self.assistants) + 1}",
            "display_name": display_name,
            "description": description,
        }
        self.assistants[display_name] = assistant
        self.sources[assistant["name"]] = []
        return assistant

    def update_knowledge_assistant(self, name: str, description: str) -> None:
        """Change the stored description."""
        self._record("update_knowledge_assistant", name, description)
        for assistant in self.assistants.values():
            if assistant["name"] == name:
                assistant["description"] = description

    def list_knowledge_sources(self, name: str) -> list[dict]:
        """Return the stored sources."""
        self._record("list_knowledge_sources", name)
        return list(self.sources.get(name, []))

    def create_knowledge_source(self, name: str, source: Mapping) -> None:
        """Store a source."""
        self._record("create_knowledge_source", name, dict(source))
        self.sources.setdefault(name, []).append(dict(source))

    def sync_knowledge_sources(self, name: str) -> None:
        """Record a sync."""
        self._record("sync_knowledge_sources", name)


@dataclass
class _FakeCatalog(_FakeState):
    """Unity Catalog comments and tags."""

    tables: dict[str, dict] = field(default_factory=dict)

    def get_table_metadata(self, full_name: str) -> dict | None:
        """Return the stored table metadata."""
        self._record("get_table_metadata", full_name)
        return self.tables.get(full_name)

    def set_comment(self, entity_type: str, entity_id: str, comment: str) -> None:
        """Store a table or column comment."""
        self._record("set_comment", entity_type, entity_id, comment)
        if entity_type == "table":
            self.tables[entity_id]["comment"] = comment
        else:
            table, column = entity_id.rsplit(".", 1)
            self.tables[table]["columns"][column] = comment

    def list_tags(self, entity_type: str, entity_id: str) -> dict[str, str] | None:
        """Return the stored tags, or None for an unknown entity."""
        self._record("list_tags", entity_type, entity_id)
        found = self.tags.get((entity_type, entity_id))
        return None if found is None else dict(found)

    def set_tag(self, entity_type: str, entity_id: str, key: str, value: str) -> None:
        """Store a tag."""
        self._record("set_tag", entity_type, entity_id, key, value)
        self.tags[(entity_type, entity_id)][key] = value

    def unset_tag(self, entity_type: str, entity_id: str, key: str) -> None:
        """Remove a tag."""
        self._record("unset_tag", entity_type, entity_id, key)
        self.tags[(entity_type, entity_id)].pop(key, None)

    def run_sql(self, statement: str) -> list[dict]:
        """Record the statement and return no rows."""
        self._record("run_sql", statement)
        return []


@dataclass
class _FakeModels(_FakeState):
    """Registered-model aliases and metrics."""

    aliases: dict[tuple[str, str], int] = field(default_factory=dict)
    metrics: dict[tuple[str, int], dict[str, float]] = field(default_factory=dict)

    def get_model_alias_version(self, model: str, alias: str) -> int | None:
        """Return the stored alias target."""
        self._record("get_model_alias_version", model, alias)
        return self.aliases.get((model, alias))

    def set_model_alias(self, model: str, alias: str, version: int) -> None:
        """Store an alias target."""
        self._record("set_model_alias", model, alias, version)
        self.aliases[(model, alias)] = version

    def get_model_version_metrics(self, model: str, version: int) -> dict[str, float]:
        """Return the stored run metrics."""
        self._record("get_model_version_metrics", model, version)
        return dict(self.metrics.get((model, version), {}))


@dataclass
class FakeWorkspaceGateway(
    _FakeVectorSearch, _FakeServing, _FakeMonitors, _FakeKnowledgeAssistants, _FakeCatalog, _FakeModels
):
    """
    Keeps workspace state in dicts and records every call as ``(method, *arguments)``.

    Tests seed state through the public dict attributes, then assert on ``writes`` (the calls that changed state).
    """


@pytest.fixture
def fake_gateway() -> FakeWorkspaceGateway:
    """Return an empty recording gateway."""
    return FakeWorkspaceGateway()
