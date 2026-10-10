"""Tests for the Reconciler template method and the vector search, serving, monitor and knowledge assistant kinds."""

import pytest

from dbxops.reconcile import (
    Change,
    DesiredKnowledgeAssistant,
    DesiredMonitor,
    DesiredServingEndpoint,
    DesiredVectorSearchIndex,
    Diff,
    KnowledgeAssistantReconciler,
    MonitorReconciler,
    Reconciler,
    ServingEndpointReconciler,
    VectorSearchIndexReconciler,
    matches,
)
from dbxops.workspace import (
    GatewayError,
    WorkspaceGateway,
)
from tests.fixtures.workspace import FakeWorkspaceGateway

# --- matches ---------------------------------------------------------------------------------------------------------


def test__matches_ignores_extra_keys_in_the_actual_mapping():
    """Server-populated keys that the desired state does not mention never cause a diff."""
    assert matches({"a": 1}, {"a": 1, "b": 2})


def test__matches_requires_every_desired_key_to_be_present():
    """A desired key missing from the actual mapping is a mismatch."""
    assert not matches({"a": 1, "c": 3}, {"a": 1})


def test__matches_compares_lists_by_position_and_length():
    """Lists must have the same length and match element by element."""
    assert matches([{"a": 1}], [{"a": 1, "z": 0}])
    assert not matches([{"a": 1}], [{"a": 1}, {"a": 2}])


def test__matches_treats_a_number_and_its_string_as_equal():
    """The API returns versions as strings while a config file may hold integers."""
    assert matches({"entity_version": 3}, {"entity_version": "3"})


# --- template method -------------------------------------------------------------------------------------------------


class _Recording(Reconciler):
    """A reconciler whose single change either works or raises, to test the template method."""

    def __init__(self, gateway, operation):
        super().__init__(gateway)
        self._operation = operation
        self.order: list[str] = []

    def read_actual(self, desired):
        """Record the phase."""
        self.order.append("read_actual")
        return desired

    def diff(self, desired, actual):
        """Record the phase and plan one change."""
        self.order.append("diff")
        return Diff(changes=(Change("thing:1", "create", operation=self._operation),), unchanged=("thing:0",))

    def plan(self, changes):
        """Record the phase."""
        self.order.append("plan")
        return changes


def test__reconcile_runs_read_diff_plan_in_order_and_does_not_apply_by_default(fake_gateway):
    """The default is a dry run: the plan is returned and the operation is never called."""
    called = []
    reconciler = _Recording(fake_gateway, lambda: called.append(1))
    result = reconciler.reconcile("desired")
    assert reconciler.order == ["read_actual", "diff", "plan"]
    assert [change.resource for change in result.changes] == ["thing:1"]
    assert result.unchanged == ("thing:0",)
    assert result.applied is False
    assert called == []


def test__reconcile_applies_the_planned_operations_when_not_a_dry_run(fake_gateway):
    """With dry_run=False each planned operation runs once and the result says it was applied."""
    called = []
    result = _Recording(fake_gateway, lambda: called.append(1)).reconcile("desired", dry_run=False)
    assert called == [1]
    assert result.applied is True
    assert result.ok


def test__a_failing_operation_is_reported_and_does_not_raise(fake_gateway):
    """A gateway error during apply becomes an error entry naming the change."""

    def boom():
        raise GatewayError("denied")

    result = _Recording(fake_gateway, boom).reconcile("desired", dry_run=False)
    assert result.errors == ("thing:1: create failed: denied",)
    assert not result.ok


# --- vector search ---------------------------------------------------------------------------------------------------


def _index(**overrides) -> DesiredVectorSearchIndex:
    values = {
        "name": "cat.claims_ai.policy_index",
        "endpoint_name": "claims-vs",
        "primary_key": "chunk_id",
        "delta_sync_index_spec": {
            "source_table": "cat.claims_ai.policy_chunks",
            "pipeline_type": "TRIGGERED",
            "embedding_source_columns": [{"name": "text", "embedding_model_endpoint_name": "databricks-gte-large-en"}],
        },
    }
    return DesiredVectorSearchIndex(**{**values, **overrides})


def test__vector_search_creates_the_missing_endpoint_then_the_missing_index(fake_gateway):
    """With nothing in the workspace the plan is the endpoint create followed by the index create."""
    result = VectorSearchIndexReconciler(fake_gateway).reconcile([_index()])
    assert [(c.action, c.resource) for c in result.changes] == [
        ("create", "vector_search_endpoint:claims-vs"),
        ("create", "vector_search_index:cat.claims_ai.policy_index"),
    ]


def test__vector_search_dry_run_makes_no_writes(fake_gateway):
    """A dry run only reads."""
    VectorSearchIndexReconciler(fake_gateway).reconcile([_index()])
    assert fake_gateway.writes == []


def test__vector_search_apply_performs_exactly_the_planned_calls(fake_gateway):
    """Applying issues the endpoint create then the index create with the desired arguments, and nothing else."""
    desired = _index()
    VectorSearchIndexReconciler(fake_gateway).reconcile([desired], dry_run=False)
    assert fake_gateway.writes == [
        ("create_vector_search_endpoint", "claims-vs", "STANDARD"),
        (
            "create_vector_search_index",
            "cat.claims_ai.policy_index",
            "claims-vs",
            "chunk_id",
            "DELTA_SYNC",
            desired.delta_sync_index_spec,
        ),
    ]


def test__vector_search_second_run_is_a_no_op(fake_gateway):
    """After an apply the same desired state produces no changes, even though the server added fields."""
    reconciler = VectorSearchIndexReconciler(fake_gateway)
    reconciler.reconcile([_index()], dry_run=False)
    second = reconciler.reconcile([_index()], dry_run=False)
    assert second.changes == ()
    assert second.errors == ()
    assert second.unchanged == ("vector_search_endpoint:claims-vs", "vector_search_index:cat.claims_ai.policy_index")


def test__vector_search_shares_one_endpoint_between_indexes(fake_gateway):
    """Two indexes on one endpoint plan a single endpoint create."""
    result = VectorSearchIndexReconciler(fake_gateway).reconcile([_index(), _index(name="cat.claims_ai.other")])
    assert [c.resource for c in result.changes].count("vector_search_endpoint:claims-vs") == 1


def test__vector_search_reports_a_differing_index_and_never_recreates_it(fake_gateway):
    """An existing index with another source table is an error, not a delete-and-create."""
    reconciler = VectorSearchIndexReconciler(fake_gateway)
    reconciler.reconcile([_index()], dry_run=False)
    fake_gateway.calls.clear()
    changed = _index(delta_sync_index_spec={"source_table": "cat.claims_ai.other_chunks", "pipeline_type": "TRIGGERED"})
    result = reconciler.reconcile([changed], dry_run=False)
    assert result.changes == ()
    assert "cannot be updated" in result.errors[0]
    assert fake_gateway.writes == []


def test__vector_search_reports_an_endpoint_of_another_type(fake_gateway):
    """An existing endpoint of a different type is reported and left alone."""
    fake_gateway.vs_endpoints["claims-vs"] = {"name": "claims-vs", "endpoint_type": "STORAGE_OPTIMIZED"}
    result = VectorSearchIndexReconciler(fake_gateway).reconcile([_index()])
    assert any("type is STORAGE_OPTIMIZED" in error for error in result.errors)


def test__vector_search_sync_is_planned_only_for_a_triggered_index_when_requested(fake_gateway):
    """sync=True on an existing, correct, triggered index plans a sync action."""
    reconciler = VectorSearchIndexReconciler(fake_gateway)
    reconciler.reconcile([_index()], dry_run=False)
    fake_gateway.calls.clear()
    result = reconciler.reconcile([_index(sync=True)], dry_run=False)
    assert fake_gateway.writes == [("sync_vector_search_index", "cat.claims_ai.policy_index")]
    assert [c.action for c in result.changes] == ["sync"]


# --- serving ---------------------------------------------------------------------------------------------------------


def _endpoint(**overrides) -> DesiredServingEndpoint:
    values = {
        "name": "claims_fraud_scorer",
        "served_entities": [{"entity_name": "cat.claims_ml.fraud", "entity_version": "3", "workload_size": "Small"}],
        "traffic_routes": [{"served_model_name": "fraud-3", "traffic_percentage": 100}],
        "rate_limits": [{"calls": 100, "renewal_period": "minute", "key": "endpoint"}],
        "inference_table": {"catalog_name": "cat", "schema_name": "claims_ml", "enabled": True},
        "tags": {"project": "claims"},
    }
    return DesiredServingEndpoint(**{**values, **overrides})


def test__serving_create_is_one_call_carrying_config_gateway_and_tags(fake_gateway):
    """A missing endpoint is created in one call with its config, AI Gateway settings and tags."""
    desired = _endpoint()
    ServingEndpointReconciler(fake_gateway).reconcile([desired], dry_run=False)
    assert fake_gateway.writes == [
        ("create_serving_endpoint", "claims_fraud_scorer", desired.config, desired.ai_gateway, {"project": "claims"})
    ]
    assert desired.ai_gateway["inference_table_config"]["enabled"] is True
    assert desired.ai_gateway["rate_limits"][0]["calls"] == 100


def test__serving_dry_run_makes_no_writes(fake_gateway):
    """A dry run only reads."""
    result = ServingEndpointReconciler(fake_gateway).reconcile([_endpoint()])
    assert fake_gateway.writes == []
    assert [c.action for c in result.changes] == ["create"]


def test__serving_second_run_is_a_no_op(fake_gateway):
    """The tags come back in the SDK list shape and still match, so nothing is planned on the second run."""
    reconciler = ServingEndpointReconciler(fake_gateway)
    reconciler.reconcile([_endpoint()], dry_run=False)
    second = reconciler.reconcile([_endpoint()], dry_run=False)
    assert second.changes == ()
    assert second.unchanged == ("serving_endpoint:claims_fraud_scorer",)


def test__serving_new_model_version_updates_only_the_config(fake_gateway):
    """Pointing at a new model version plans update_config and nothing for the unchanged gateway and tags."""
    reconciler = ServingEndpointReconciler(fake_gateway)
    reconciler.reconcile([_endpoint()], dry_run=False)
    fake_gateway.calls.clear()
    newer = _endpoint(
        served_entities=[{"entity_name": "cat.claims_ml.fraud", "entity_version": "4", "workload_size": "Small"}],
        traffic_routes=[{"served_model_name": "fraud-4", "traffic_percentage": 100}],
    )
    result = reconciler.reconcile([newer], dry_run=False)
    assert [c.action for c in result.changes] == ["update_config"]
    assert fake_gateway.writes == [("update_serving_endpoint_config", "claims_fraud_scorer", newer.config)]


def test__serving_changed_rate_limit_updates_only_the_ai_gateway(fake_gateway):
    """A new rate limit plans update_ai_gateway only."""
    reconciler = ServingEndpointReconciler(fake_gateway)
    reconciler.reconcile([_endpoint()], dry_run=False)
    fake_gateway.calls.clear()
    stricter = _endpoint(rate_limits=[{"calls": 10, "renewal_period": "minute", "key": "endpoint"}])
    result = reconciler.reconcile([stricter], dry_run=False)
    assert [c.action for c in result.changes] == ["update_ai_gateway"]
    assert fake_gateway.writes[0][0] == "update_serving_endpoint_ai_gateway"


def test__serving_tags_are_added_but_foreign_tags_are_never_removed(fake_gateway):
    """A missing tag is set; a tag the reconciler was not given survives."""
    reconciler = ServingEndpointReconciler(fake_gateway)
    reconciler.reconcile([_endpoint()], dry_run=False)
    fake_gateway.tags[("serving_endpoint", "claims_fraud_scorer")]["owner_added_by_hand"] = "x"
    fake_gateway.calls.clear()
    result = reconciler.reconcile([_endpoint(tags={"project": "claims", "stage": "uat"})], dry_run=False)
    assert fake_gateway.writes == [("set_tag", "serving_endpoint", "claims_fraud_scorer", "stage", "uat")]
    assert fake_gateway.tags[("serving_endpoint", "claims_fraud_scorer")]["owner_added_by_hand"] == "x"
    assert [c.action for c in result.changes] == ["set_tag"]


# --- monitors --------------------------------------------------------------------------------------------------------


def _monitor(**overrides) -> DesiredMonitor:
    values = {
        "table_name": "cat.claims_ml.fraud_inference",
        "output_schema_name": "cat.claims_ml",
        "assets_dir": "/Workspace/Shared/monitors/fraud",
        "settings": {
            "inference_log": {
                "problem_type": "PROBLEM_TYPE_CLASSIFICATION",
                "timestamp_col": "ts",
                "granularities": ["1 day"],
                "prediction_col": "prediction",
                "model_id_col": "model_id",
            }
        },
        "create_options": {"warehouse_id": "wh-1"},
    }
    return DesiredMonitor(**{**values, **overrides})


def test__monitor_create_merges_create_options_into_the_settings(fake_gateway):
    """A missing monitor is created with the settings plus the create-only options."""
    desired = _monitor()
    MonitorReconciler(fake_gateway).reconcile([desired], dry_run=False)
    assert fake_gateway.writes == [
        (
            "create_quality_monitor",
            desired.table_name,
            "cat.claims_ml",
            desired.assets_dir,
            {"warehouse_id": "wh-1", **desired.settings},
        )
    ]


def test__monitor_dry_run_makes_no_writes(fake_gateway):
    """A dry run only reads."""
    MonitorReconciler(fake_gateway).reconcile([_monitor()])
    assert fake_gateway.writes == []


def test__monitor_second_run_is_a_no_op(fake_gateway):
    """Server-populated fields such as status do not cause a diff."""
    reconciler = MonitorReconciler(fake_gateway)
    reconciler.reconcile([_monitor()], dry_run=False)
    second = reconciler.reconcile([_monitor()], dry_run=False)
    assert second.changes == ()
    assert second.unchanged == ("quality_monitor:cat.claims_ml.fraud_inference",)


def test__monitor_changed_setting_updates_without_create_options(fake_gateway):
    """A changed granularity plans an update that carries only the settings."""
    reconciler = MonitorReconciler(fake_gateway)
    reconciler.reconcile([_monitor()], dry_run=False)
    fake_gateway.calls.clear()
    changed = _monitor(settings={"slicing_exprs": ["region"]})
    reconciler.reconcile([changed], dry_run=False)
    assert fake_gateway.writes == [
        ("update_quality_monitor", "cat.claims_ml.fraud_inference", "cat.claims_ml", {"slicing_exprs": ["region"]})
    ]


def test__monitor_assets_dir_cannot_change(fake_gateway):
    """A different assets_dir is reported and nothing is written."""
    reconciler = MonitorReconciler(fake_gateway)
    reconciler.reconcile([_monitor()], dry_run=False)
    fake_gateway.calls.clear()
    result = reconciler.reconcile([_monitor(assets_dir="/Workspace/Shared/other")], dry_run=False)
    assert "assets_dir" in result.errors[0]
    assert fake_gateway.writes == []


# --- knowledge assistants --------------------------------------------------------------------------------------------

SOURCE = {
    "display_name": "Policies",
    "description": "Policy index",
    "source_type": "index",
    "index": {"index_name": "cat.claims_ai.policy_index", "text_col": "text", "doc_uri_col": "uri"},
}


def _assistant(**overrides) -> DesiredKnowledgeAssistant:
    values = {"display_name": "policy_assistant", "description": "Answers policy questions", "sources": [SOURCE]}
    return DesiredKnowledgeAssistant(**{**values, **overrides})


def test__knowledge_assistant_create_adds_sources_then_syncs(fake_gateway):
    """A missing assistant is created, each source is added to the new assistant, then one sync runs."""
    result = KnowledgeAssistantReconciler(fake_gateway).reconcile([_assistant()], dry_run=False)
    assert fake_gateway.writes == [
        ("create_knowledge_assistant", "policy_assistant", "Answers policy questions"),
        ("create_knowledge_source", "knowledge-assistants/ka-1", SOURCE),
        ("sync_knowledge_sources", "knowledge-assistants/ka-1"),
    ]
    assert [c.action for c in result.changes] == ["create", "create_source", "sync"]
    assert result.ok


def test__knowledge_assistant_dry_run_makes_no_writes(fake_gateway):
    """A dry run only reads, and still plans the whole create."""
    result = KnowledgeAssistantReconciler(fake_gateway).reconcile([_assistant()])
    assert fake_gateway.writes == []
    assert len(result.changes) == 3


def test__knowledge_assistant_second_run_is_a_no_op(fake_gateway):
    """Once the assistant and its source exist nothing is planned, and no sync is triggered."""
    reconciler = KnowledgeAssistantReconciler(fake_gateway)
    reconciler.reconcile([_assistant()], dry_run=False)
    fake_gateway.calls.clear()
    second = reconciler.reconcile([_assistant()], dry_run=False)
    assert second.changes == ()
    assert second.unchanged == ("knowledge_assistant:policy_assistant",)
    assert fake_gateway.writes == []


def test__knowledge_assistant_new_source_is_added_and_synced(fake_gateway):
    """Adding a source to an existing assistant plans create_source and sync, not a re-create."""
    reconciler = KnowledgeAssistantReconciler(fake_gateway)
    reconciler.reconcile([_assistant()], dry_run=False)
    fake_gateway.calls.clear()
    extra = {**SOURCE, "display_name": "Claims FAQ"}
    reconciler.reconcile([_assistant(sources=[SOURCE, extra])], dry_run=False)
    assert fake_gateway.writes == [
        ("create_knowledge_source", "knowledge-assistants/ka-1", extra),
        ("sync_knowledge_sources", "knowledge-assistants/ka-1"),
    ]


def test__knowledge_assistant_description_change_is_an_update(fake_gateway):
    """A new description plans only update_knowledge_assistant."""
    reconciler = KnowledgeAssistantReconciler(fake_gateway)
    reconciler.reconcile([_assistant()], dry_run=False)
    fake_gateway.calls.clear()
    reconciler.reconcile([_assistant(description="New text")], dry_run=False)
    assert fake_gateway.writes == [("update_knowledge_assistant", "knowledge-assistants/ka-1", "New text")]


def test__knowledge_assistant_reports_a_changed_source_without_touching_it(fake_gateway):
    """A source whose definition differs is an error, because sources cannot be updated or deleted here."""
    reconciler = KnowledgeAssistantReconciler(fake_gateway)
    reconciler.reconcile([_assistant()], dry_run=False)
    fake_gateway.calls.clear()
    changed = {**SOURCE, "index": {**SOURCE["index"], "text_col": "body"}}
    result = reconciler.reconcile([_assistant(sources=[changed])], dry_run=False)
    assert "sources cannot be updated" in result.errors[0]
    assert fake_gateway.writes == []


def test__reconciler_subclasses_must_implement_read_and_diff(fake_gateway):
    """The base class is abstract."""
    with pytest.raises(TypeError):
        Reconciler(fake_gateway)  # type: ignore[abstract]


def test__fake_gateway_is_a_complete_workspace_gateway():
    """The fake implements every protocol method, so a method added to the protocol cannot be forgotten."""
    protocol_methods = {name for name in vars(WorkspaceGateway) if not name.startswith("_")}
    assert protocol_methods <= set(dir(FakeWorkspaceGateway))
