"""
Tests for SdkWorkspaceGateway against an autospecced WorkspaceClient.

Every sub-API is replaced by ``create_autospec`` of its real class, so a wrong method name or argument name raises
inside the adapter and fails the test.
"""

from unittest.mock import (
    MagicMock,
    create_autospec,
)

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import ApiClient
from databricks.sdk.errors import (
    NotFound,
    ResourceAlreadyExists,
)
from databricks.sdk.service import (
    catalog,
    ml,
    serving,
    sql,
    vectorsearch,
)

from dbxops.workspace import (
    GatewayError,
    SdkWorkspaceGateway,
    WorkspaceGateway,
)

SUB_APIS = {
    "vector_search_endpoints": vectorsearch.VectorSearchEndpointsAPI,
    "vector_search_indexes": vectorsearch.VectorSearchIndexesAPI,
    "serving_endpoints": serving.ServingEndpointsAPI,
    "quality_monitors": catalog.QualityMonitorsAPI,
    "tables": catalog.TablesAPI,
    "entity_tag_assignments": catalog.EntityTagAssignmentsAPI,
    "registered_models": catalog.RegisteredModelsAPI,
    "model_versions": catalog.ModelVersionsAPI,
    "experiments": ml.ExperimentsAPI,
    "statement_execution": sql.StatementExecutionAPI,
    "api_client": ApiClient,
}


@pytest.fixture
def client() -> MagicMock:
    """Return a WorkspaceClient mock whose sub-APIs enforce the real method signatures."""
    mock = MagicMock(spec=WorkspaceClient)
    for attribute, api in SUB_APIS.items():
        setattr(mock, attribute, create_autospec(api, instance=True))
    return mock


@pytest.fixture
def gateway(client) -> SdkWorkspaceGateway:
    """Return the adapter over the autospecced client, with a warehouse id."""
    return SdkWorkspaceGateway(client, warehouse_id="wh-1")


def test__the_adapter_implements_every_protocol_method():
    """Every method of the WorkspaceGateway protocol exists on the SDK adapter."""
    protocol_methods = {name for name in vars(WorkspaceGateway) if not name.startswith("_")}
    assert protocol_methods <= set(dir(SdkWorkspaceGateway))


# --- vector search ---------------------------------------------------------------------------------------------------


def test__vector_search_endpoint_get_returns_a_dict(gateway, client):
    """get_endpoint is called by endpoint_name and the result is returned as a dict."""
    client.vector_search_endpoints.get_endpoint.return_value = vectorsearch.EndpointInfo(
        name="vs", endpoint_type=vectorsearch.EndpointType.STANDARD
    )
    assert gateway.get_vector_search_endpoint("vs") == {"name": "vs", "endpoint_type": "STANDARD"}
    client.vector_search_endpoints.get_endpoint.assert_called_once_with(endpoint_name="vs")


def test__vector_search_endpoint_get_maps_not_found_to_none(gateway, client):
    """A 404 means the endpoint does not exist."""
    client.vector_search_endpoints.get_endpoint.side_effect = NotFound("missing")
    assert gateway.get_vector_search_endpoint("vs") is None


def test__vector_search_endpoint_create_uses_the_typed_enum(gateway, client):
    """create_endpoint receives name and an EndpointType."""
    gateway.create_vector_search_endpoint("vs", "STANDARD")
    client.vector_search_endpoints.create_endpoint.assert_called_once_with(
        name="vs", endpoint_type=vectorsearch.EndpointType.STANDARD
    )


def test__vector_search_endpoint_type_unknown_to_the_sdk_is_a_clear_error(gateway, client):
    """A type the installed SDK does not enumerate raises GatewayError and calls nothing."""
    with pytest.raises(GatewayError, match="'NOPE' is not supported by the SDK"):
        gateway.create_vector_search_endpoint("vs", "NOPE")
    client.vector_search_endpoints.create_endpoint.assert_not_called()


def test__vector_search_index_get_returns_a_dict_and_maps_not_found(gateway, client):
    """get_index is called by index_name; a 404 yields None."""
    client.vector_search_indexes.get_index.return_value = vectorsearch.VectorIndex(name="c.s.i", primary_key="id")
    assert gateway.get_vector_search_index("c.s.i") == {"name": "c.s.i", "primary_key": "id"}
    client.vector_search_indexes.get_index.assert_called_once_with(index_name="c.s.i")
    client.vector_search_indexes.get_index.side_effect = NotFound("missing")
    assert gateway.get_vector_search_index("c.s.i") is None


def test__vector_search_index_create_builds_the_typed_spec(gateway, client):
    """create_index receives the typed Delta Sync spec and index type."""
    spec = {
        "source_table": "c.s.t",
        "pipeline_type": "TRIGGERED",
        "embedding_source_columns": [{"name": "text", "embedding_model_endpoint_name": "gte"}],
    }
    gateway.create_vector_search_index(
        "c.s.i",
        {"endpoint_name": "vs", "primary_key": "id", "index_type": "DELTA_SYNC", "delta_sync_index_spec": spec},
    )
    client.vector_search_indexes.create_index.assert_called_once_with(
        name="c.s.i",
        endpoint_name="vs",
        primary_key="id",
        index_type=vectorsearch.VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=vectorsearch.DeltaSyncVectorIndexSpecRequest.from_dict(spec),
    )


def test__vector_search_index_sync(gateway, client):
    """sync_index is called by index_name."""
    gateway.sync_vector_search_index("c.s.i")
    client.vector_search_indexes.sync_index.assert_called_once_with(index_name="c.s.i")


# --- serving ---------------------------------------------------------------------------------------------------------

CONFIG = {
    "served_entities": [{"entity_name": "c.s.m", "entity_version": "3"}],
    "traffic_config": {"routes": [{"served_model_name": "m-3", "traffic_percentage": 100}]},
}


def test__serving_get_returns_tags_and_maps_not_found(gateway, client):
    """The endpoint is read by name and the result includes the tags."""
    client.serving_endpoints.get.return_value = serving.ServingEndpointDetailed(
        name="ep", tags=[serving.EndpointTag(key="k", value="v")]
    )
    assert gateway.get_serving_endpoint("ep") == {"name": "ep", "tags": [{"key": "k", "value": "v"}]}
    client.serving_endpoints.get.assert_called_once_with(name="ep")
    client.serving_endpoints.get.side_effect = NotFound("missing")
    assert gateway.get_serving_endpoint("ep") is None


def test__serving_create_passes_config_gateway_and_tags(gateway, client):
    """Create receives typed config, AI Gateway settings and EndpointTag objects."""
    ai_gateway = {"rate_limits": [{"calls": 5, "renewal_period": "minute", "key": "endpoint"}]}
    gateway.create_serving_endpoint("ep", CONFIG, ai_gateway, {"k": "v"})
    client.serving_endpoints.create.assert_called_once_with(
        name="ep",
        config=serving.EndpointCoreConfigInput.from_dict(CONFIG),
        ai_gateway=serving.AiGatewayConfig.from_dict(ai_gateway),
        tags=[serving.EndpointTag(key="k", value="v")],
    )


def test__serving_create_without_gateway_or_tags_passes_none(gateway, client):
    """Absent optional parts are passed as None, not as empty objects."""
    gateway.create_serving_endpoint("ep", CONFIG)
    kwargs = client.serving_endpoints.create.call_args.kwargs
    assert kwargs["ai_gateway"] is None
    assert kwargs["tags"] is None


def test__serving_update_config_splits_the_config_into_the_sdk_arguments(gateway, client):
    """update_config receives served_entities, traffic_config and auto_capture_config as separate arguments."""
    gateway.update_serving_endpoint_config("ep", CONFIG)
    parsed = serving.EndpointCoreConfigInput.from_dict(CONFIG)
    client.serving_endpoints.update_config.assert_called_once_with(
        name="ep",
        served_entities=parsed.served_entities,
        traffic_config=parsed.traffic_config,
        auto_capture_config=None,
    )


def test__serving_ai_gateway_uses_put_ai_gateway(gateway, client):
    """put_ai_gateway receives the rate limits and the inference table config."""
    ai_gateway = {
        "rate_limits": [{"calls": 5, "renewal_period": "minute", "key": "endpoint"}],
        "inference_table_config": {"catalog_name": "c", "schema_name": "s", "enabled": True},
    }
    gateway.update_serving_endpoint_ai_gateway("ep", ai_gateway)
    parsed = serving.AiGatewayConfig.from_dict(ai_gateway)
    client.serving_endpoints.put_ai_gateway.assert_called_once_with(
        name="ep",
        fallback_config=None,
        guardrails=None,
        inference_table_config=parsed.inference_table_config,
        rate_limits=parsed.rate_limits,
        usage_tracking_config=None,
    )


# --- quality monitors ------------------------------------------------------------------------------------------------


def test__monitor_get_returns_a_dict_and_maps_not_found(gateway, client):
    """quality_monitors.get is called by table_name; a 404 yields None."""
    client.quality_monitors.get.return_value = catalog.MonitorInfo(
        output_schema_name="c.s",
        table_name="c.s.t",
        status=catalog.MonitorInfoStatus.MONITOR_STATUS_ACTIVE,
        profile_metrics_table_name="p",
        drift_metrics_table_name="d",
        monitor_version=1,
    )
    assert gateway.get_quality_monitor("c.s.t")["table_name"] == "c.s.t"
    client.quality_monitors.get.assert_called_once_with(table_name="c.s.t")
    client.quality_monitors.get.side_effect = NotFound("missing")
    assert gateway.get_quality_monitor("c.s.t") is None


def test__monitor_create_types_the_settings(gateway, client):
    """Create receives typed settings plus the plain pass-through options."""
    schedule = {"quartz_cron_expression": "0 0 * * * ?", "timezone_id": "UTC"}
    settings = {"schedule": schedule, "slicing_exprs": ["region"], "warehouse_id": "wh-1"}
    gateway.create_quality_monitor("c.s.t", "c.s", "/Workspace/Shared/m", settings)
    client.quality_monitors.create.assert_called_once_with(
        table_name="c.s.t",
        output_schema_name="c.s",
        assets_dir="/Workspace/Shared/m",
        schedule=catalog.MonitorCronSchedule.from_dict(schedule),
        slicing_exprs=["region"],
        warehouse_id="wh-1",
    )


def test__monitor_update_has_no_assets_dir(gateway, client):
    """Update receives table_name, output_schema_name and the settings only."""
    gateway.update_quality_monitor("c.s.t", "c.s", {"slicing_exprs": ["region"]})
    client.quality_monitors.update.assert_called_once_with(
        table_name="c.s.t", output_schema_name="c.s", slicing_exprs=["region"]
    )


def test__monitor_unknown_setting_is_rejected(gateway, client):
    """A setting the adapter does not know raises instead of being silently dropped."""
    with pytest.raises(GatewayError, match="unknown quality monitor setting 'nope'"):
        gateway.update_quality_monitor("c.s.t", "c.s", {"nope": 1})
    client.quality_monitors.update.assert_not_called()


# --- knowledge assistants --------------------------------------------------------------------------------------------


def test__knowledge_assistant_get_finds_by_display_name_across_pages(gateway, client):
    """The list is paged until the display name is found."""
    client.api_client.do.side_effect = [
        {"knowledge_assistants": [{"display_name": "a", "name": "knowledge-assistants/1"}], "next_page_token": "t"},
        {"knowledge_assistants": [{"display_name": "b", "name": "knowledge-assistants/2"}]},
    ]
    assert gateway.get_knowledge_assistant("b")["name"] == "knowledge-assistants/2"
    assert client.api_client.do.call_args_list[1].kwargs == {"query": {"page_token": "t"}}


def test__knowledge_assistant_get_returns_none_when_absent(gateway, client):
    """No match on the last page yields None."""
    client.api_client.do.return_value = {"knowledge_assistants": []}
    assert gateway.get_knowledge_assistant("x") is None


def test__knowledge_assistant_rest_calls(gateway, client):
    """Create, update, list sources, add a source and sync use the documented resource names."""
    client.api_client.do.return_value = {"name": "knowledge-assistants/1"}
    assert gateway.create_knowledge_assistant("ka", "desc") == {"name": "knowledge-assistants/1"}
    gateway.update_knowledge_assistant("knowledge-assistants/1", "new")
    client.api_client.do.return_value = {"knowledge_sources": [{"display_name": "s"}]}
    assert gateway.list_knowledge_sources("knowledge-assistants/1") == [{"display_name": "s"}]
    gateway.create_knowledge_source("knowledge-assistants/1", {"display_name": "s"})
    gateway.sync_knowledge_sources("knowledge-assistants/1")
    methods = [(c.args[0], c.args[1]) for c in client.api_client.do.call_args_list]
    assert methods == [
        ("POST", "/api/2.1/knowledge-assistants"),
        ("PATCH", "/api/2.1/knowledge-assistants/1"),
        ("GET", "/api/2.1/knowledge-assistants/1/knowledge-sources"),
        ("POST", "/api/2.1/knowledge-assistants/1/knowledge-sources"),
        ("POST", "/api/2.1/knowledge-assistants/1/knowledge-sources:sync"),
    ]


# --- Unity Catalog comments and tags ---------------------------------------------------------------------------------


def test__table_metadata_collects_table_and_column_comments(gateway, client):
    """tables.get is called by full_name and columns are returned by name."""
    client.tables.get.return_value = catalog.TableInfo(
        comment="t", columns=[catalog.ColumnInfo(name="a", comment="ca"), catalog.ColumnInfo(name="b")]
    )
    assert gateway.get_table_metadata("c.s.t") == {"comment": "t", "columns": {"a": "ca", "b": None}}
    client.tables.get.assert_called_once_with(full_name="c.s.t")


def test__table_metadata_maps_not_found_to_none(gateway, client):
    """A missing table yields None."""
    client.tables.get.side_effect = NotFound("missing")
    assert gateway.get_table_metadata("c.s.t") is None


def _sql_ok(client, rows=None, columns=()):
    client.statement_execution.execute_statement.return_value = sql.StatementResponse(
        status=sql.StatementStatus(state=sql.StatementState.SUCCEEDED),
        manifest=sql.ResultManifest(schema=sql.ResultSchema(columns=[sql.ColumnInfo(name=n) for n in columns])),
        result=sql.ResultData(data_array=rows),
    )


def test__table_comment_is_set_with_comment_on_sql(gateway, client):
    """The comment is written with COMMENT ON TABLE through the configured warehouse."""
    _sql_ok(client)
    gateway.set_comment("table", "c.s.t", "One row per claim")
    client.statement_execution.execute_statement.assert_called_once_with(
        statement="COMMENT ON TABLE `c`.`s`.`t` IS 'One row per claim'", warehouse_id="wh-1", wait_timeout="50s"
    )


def test__column_comment_escapes_quotes_and_backslashes(gateway, client):
    """A quote or backslash in the comment cannot break out of the SQL literal."""
    _sql_ok(client)
    gateway.set_comment("column", "c.s.t.col", "it's a \\ path")
    statement = client.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert statement == "COMMENT ON COLUMN `c`.`s`.`t`.`col` IS 'it\\'s a \\\\ path'"


def test__comment_on_an_unsafe_identifier_is_refused(gateway, client):
    """An identifier with backticks or semicolons never reaches SQL."""
    with pytest.raises(GatewayError, match="not a plain identifier path"):
        gateway.set_comment("table", "c.s.t`; DROP TABLE x; --", "x")
    client.statement_execution.execute_statement.assert_not_called()


def test__comment_on_another_entity_type_is_refused(gateway):
    """Only tables and columns take comments."""
    with pytest.raises(GatewayError, match="table or column"):
        gateway.set_comment("schema", "c.s", "x")


def test__run_sql_returns_rows_as_dicts(gateway, client):
    """Rows are zipped with the manifest column names."""
    _sql_ok(client, rows=[["1", "a"], ["2", "b"]], columns=("id", "name"))
    assert gateway.run_sql("SELECT 1") == [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]


def test__run_sql_failure_raises_with_the_server_message(gateway, client):
    """A statement that did not succeed raises GatewayError carrying the error message."""
    client.statement_execution.execute_statement.return_value = sql.StatementResponse(
        status=sql.StatementStatus(state=sql.StatementState.FAILED, error=sql.ServiceError(message="no access"))
    )
    with pytest.raises(GatewayError, match="FAILED.*no access"):
        gateway.run_sql("SELECT 1")


def test__run_sql_without_a_warehouse_is_refused(client):
    """No warehouse id means no SQL."""
    with pytest.raises(GatewayError, match="warehouse_id is required"):
        SdkWorkspaceGateway(client).run_sql("SELECT 1")


def test__uc_tags_are_listed_through_entity_tag_assignments(gateway, client):
    """Listing uses the plural entity type; empty values come back as empty strings."""
    client.entity_tag_assignments.list.return_value = iter(
        [
            catalog.EntityTagAssignment(entity_name="c.s.t", tag_key="a", entity_type="tables", tag_value="1"),
            catalog.EntityTagAssignment(entity_name="c.s.t", tag_key="b", entity_type="tables"),
        ]
    )
    assert gateway.list_tags("table", "c.s.t") == {"a": "1", "b": ""}
    client.entity_tag_assignments.list.assert_called_once_with(entity_type="tables", entity_name="c.s.t")


def test__uc_tags_on_a_missing_entity_are_none(gateway, client):
    """A 404 on the listing means the entity does not exist."""
    client.entity_tag_assignments.list.side_effect = NotFound("missing")
    assert gateway.list_tags("column", "c.s.t.col") is None


def test__uc_tag_is_created_with_an_entity_tag_assignment(gateway, client):
    """set_tag creates an EntityTagAssignment with the plural entity type."""
    gateway.set_tag("column", "c.s.t.col", "classification", "pii")
    client.entity_tag_assignments.create.assert_called_once_with(
        tag_assignment=catalog.EntityTagAssignment(
            entity_name="c.s.t.col", tag_key="classification", entity_type="columns", tag_value="pii"
        )
    )


def test__uc_tag_that_already_exists_is_updated(gateway, client):
    """A conflict on create falls back to update with the tag_value mask."""
    client.entity_tag_assignments.create.side_effect = ResourceAlreadyExists("exists")
    gateway.set_tag("table", "c.s.t", "classification", "pii")
    client.entity_tag_assignments.update.assert_called_once_with(
        entity_type="tables",
        entity_name="c.s.t",
        tag_key="classification",
        tag_assignment=catalog.EntityTagAssignment(
            entity_name="c.s.t", tag_key="classification", entity_type="tables", tag_value="pii"
        ),
        update_mask="tag_value",
    )


def test__uc_tag_is_deleted_by_entity_type_name_and_key(gateway, client):
    """unset_tag calls delete with the plural entity type."""
    gateway.unset_tag("table", "c.s.t", "classification")
    client.entity_tag_assignments.delete.assert_called_once_with(
        entity_type="tables", entity_name="c.s.t", tag_key="classification"
    )


def test__tagging_an_unsupported_entity_type_is_refused(gateway):
    """An entity type outside the supported set raises."""
    with pytest.raises(GatewayError, match="cannot tag entity type 'job'"):
        gateway.set_tag("job", "1", "k", "v")


def test__serving_endpoint_tags_go_through_patch(gateway, client):
    """Endpoint tags are added and deleted with serving_endpoints.patch."""
    gateway.set_tag("serving_endpoint", "ep", "k", "v")
    gateway.unset_tag("serving_endpoint", "ep", "k")
    assert client.serving_endpoints.patch.call_args_list[0].kwargs == {
        "name": "ep",
        "add_tags": [serving.EndpointTag(key="k", value="v")],
    }
    assert client.serving_endpoints.patch.call_args_list[1].kwargs == {"name": "ep", "delete_tags": ["k"]}


def test__serving_endpoint_tags_are_listed_from_the_endpoint(gateway, client):
    """list_tags on an endpoint reads its tags; a missing endpoint is None."""
    client.serving_endpoints.get.return_value = serving.ServingEndpointDetailed(
        tags=[serving.EndpointTag(key="k", value="v")]
    )
    assert gateway.list_tags("serving_endpoint", "ep") == {"k": "v"}
    client.serving_endpoints.get.side_effect = NotFound("missing")
    assert gateway.list_tags("serving_endpoint", "ep") is None


def test__registered_model_tags_use_the_uc_model_rest_endpoints(gateway, client):
    """Model tags are set, deleted and read through the MLflow UC REST endpoints (not SDK-verified)."""
    gateway.set_tag("registered_model", "c.s.m", "k", "v")
    gateway.unset_tag("registered_model", "c.s.m", "k")
    client.api_client.do.return_value = {"registered_model": {"tags": [{"key": "k", "value": "v"}]}}
    assert gateway.list_tags("registered_model", "c.s.m") == {"k": "v"}
    prefix = "/api/2.0/mlflow/unity-catalog/registered-models"
    calls = client.api_client.do.call_args_list
    assert calls[0].args == ("POST", f"{prefix}/set-tag")
    assert calls[0].kwargs == {"body": {"name": "c.s.m", "key": "k", "value": "v"}}
    assert calls[1].args == ("DELETE", f"{prefix}/delete-tag")
    assert calls[1].kwargs == {"query": {"name": "c.s.m", "key": "k"}}
    assert calls[2].args == ("GET", f"{prefix}/get")


# --- registered-model aliases and metrics ----------------------------------------------------------------------------


def test__alias_version_is_read_by_alias(gateway, client):
    """get_by_alias is called with full_name and alias; a missing alias yields None."""
    client.model_versions.get_by_alias.return_value = catalog.ModelVersionInfo(version=3)
    assert gateway.get_model_alias_version("c.s.m", "champion") == 3
    client.model_versions.get_by_alias.assert_called_once_with(full_name="c.s.m", alias="champion")
    client.model_versions.get_by_alias.side_effect = NotFound("missing")
    assert gateway.get_model_alias_version("c.s.m", "champion") is None


def test__alias_is_set_with_version_num(gateway, client):
    """set_alias is called with full_name, alias and version_num."""
    gateway.set_model_alias("c.s.m", "champion", 3)
    client.registered_models.set_alias.assert_called_once_with(full_name="c.s.m", alias="champion", version_num=3)


def test__model_version_metrics_come_from_the_source_run(gateway, client):
    """The version's run_id is looked up and its metrics returned by key."""
    client.model_versions.get.return_value = catalog.ModelVersionInfo(run_id="run-1")
    client.experiments.get_run.return_value = ml.GetRunResponse(
        run=ml.Run(data=ml.RunData(metrics=[ml.Metric(key="auc", value=0.9)]))
    )
    assert gateway.get_model_version_metrics("c.s.m", 3) == {"auc": 0.9}
    client.model_versions.get.assert_called_once_with(full_name="c.s.m", version=3)
    client.experiments.get_run.assert_called_once_with(run_id="run-1")


def test__model_version_without_a_run_has_no_metrics(gateway, client):
    """A version with no source run is an error rather than an empty metric set."""
    client.model_versions.get.return_value = catalog.ModelVersionInfo()
    with pytest.raises(GatewayError, match="has no source run"):
        gateway.get_model_version_metrics("c.s.m", 3)
