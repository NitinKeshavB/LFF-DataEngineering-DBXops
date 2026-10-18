"""Tests for the asset hierarchy: one classification per kind of resource, and the tree built from the real bundles."""

from pathlib import Path

import pytest

from dbxops.metadata.hierarchy import (
    RELATIONS,
    UnclassifiedResource,
    build_hierarchy,
    classify,
)
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import _schema_findings
from dbxops.metastore.records import EDGE_RELATIONS
from tests.consts import PROJECT_DIR


@pytest.mark.parametrize(
    ("resource_type", "resource", "expected"),
    [
        ("jobs", {}, ("uc_job", "", "bundle")),
        ("job_tasks", {"task_key": "t", "pipeline_task": {}}, ("uc_job_task", "pipeline", "workflow")),
        ("job_tasks", {"task_key": "t", "python_wheel_task": {}}, ("uc_job_task", "python_wheel", "workflow")),
        ("job_tasks", {"task_key": "t", "run_job_task": {}}, ("uc_job_task", "run_job", "workflow")),
        ("job_tasks", {"task_key": "t", "condition_task": {}}, ("uc_job_task", "condition", "workflow")),
        ("job_tasks", {"task_key": "t", "sql_task": {}}, ("uc_job_task", "sql", "workflow")),
        ("job_tasks", {"task_key": "t", "notebook_task": {}}, ("uc_job_task", "notebook", "workflow")),
        ("pipelines", {"libraries": []}, ("uc_pipeline", "etl", "bundle")),
        ("pipelines", {"ingestion_definition": {}}, ("lakeflow_connect_pipeline", "managed_ingestion", "bundle")),
        ("dataflow_specs", {}, ("dataflow_spec", "", "pipeline")),
        ("dataflow_flows", {"config": {}}, ("dataflow_flow", "append", "dataflow_spec")),
        (
            "dataflow_flows",
            {"config": {"cdc_settings": {"scd_type": 1}}},
            ("dataflow_flow", "cdc_scd1", "dataflow_spec"),
        ),
        (
            "dataflow_flows",
            {"config": {"cdc_settings": {"scd_type": 2}}},
            ("dataflow_flow", "cdc_scd2", "dataflow_spec"),
        ),
        (
            "dataflow_flows",
            {"config": {"cdc_snapshot_settings": {}}},
            ("dataflow_flow", "cdc_snapshot", "dataflow_spec"),
        ),
        ("dataflow_flows", {"config": {"table_type": "mv"}}, ("dataflow_flow", "mv", "dataflow_spec")),
        ("datasets", {"physical_type": "table"}, ("uc_table", "", "flow")),
        ("datasets", {"physical_type": "streaming_table"}, ("uc_streaming_table", "", "flow")),
        ("datasets", {"physical_type": "materialized_view"}, ("uc_materialized_view", "", "flow")),
        ("datasets", {"physical_type": "view"}, ("uc_view", "", "flow")),
        ("datasets", {"quarantine": True}, ("uc_table", "quarantine", "flow")),
        ("volumes", {}, ("uc_volume", "", "bundle")),
        ("registered_models", {}, ("uc_model", "", "workflow")),
        ("registered_models", {"tags": {"model_kind": "agent"}}, ("agent", "", "workflow")),
        (
            "model_serving_endpoints",
            {"config": {"served_entities": [{"entity_name": "m"}]}},
            ("serving_endpoint", "model", "model"),
        ),
        (
            "model_serving_endpoints",
            {"config": {"served_entities": [{"foundation_model": {}}]}},
            ("serving_endpoint", "foundation_model", "model"),
        ),
        ("model_serving_endpoints", {"tags": {"model_kind": "agent"}}, ("serving_endpoint", "agent", "model")),
        ("vector_search_endpoints", {}, ("vector_search_endpoint", "", "bundle")),
        ("vector_search_indexes", {}, ("vector_index", "", "vector_search_endpoint")),
        ("uc_functions", {}, ("uc_function", "", "agent")),
        ("apps", {}, ("app", "", "bundle")),
        ("apps", {"tags": {"app_kind": "mcp_server"}}, ("mcp_server", "", "app")),
        ("mcp_services", {}, ("mcp_server", "", "app")),
        ("knowledge_assistants", {}, ("knowledge_assistant", "", "bundle")),
        ("genie_spaces", {}, ("genie_space", "", "bundle")),
        ("dashboards", {}, ("dashboard", "", "bundle")),
        ("alerts", {}, ("sql_alert", "", "bundle")),
    ],
)
def test__each_kind_of_resource_classifies_to_one_type_subtype_and_parent(resource_type, resource, expected):
    """The classification table of the hierarchy, row by row."""
    kind = classify(resource_type, resource)
    assert (kind.asset_type, kind.subtype, kind.parent_kind) == expected


@pytest.mark.parametrize("resource_type", ["clusters", "experiments", "secret_scopes", "not_a_type"])
def test__a_resource_with_no_place_in_the_tree_is_unclassified(resource_type):
    """The error names the type, so `make validate-metadata` can report an `unclassified asset`."""
    with pytest.raises(UnclassifiedResource, match="unclassified asset"):
        classify(resource_type, {})


def test__the_relations_agree_between_the_metadata_and_the_metastore():
    """One list of edge relations, kept in both layers."""
    assert RELATIONS == EDGE_RELATIONS


@pytest.fixture(scope="module")
def hierarchy():
    """Return the hierarchy of this repository's three example bundles."""
    return build_hierarchy(RepoContext.load(Path(PROJECT_DIR)))


def test__every_asset_has_a_workload_and_the_bundle_family_decides_it(hierarchy):
    """The dataops bundle is de, mlops is ml, aiops is ai, and every asset takes its bundle's workload."""
    assert not hierarchy.problems
    families = {"dataops": "de", "mlops": "ml", "aiops": "ai"}
    for asset in hierarchy.assets.values():
        if asset.bundle in families:
            assert asset.workload == families[asset.bundle], asset.asset_id
    assert {a.workload for a in hierarchy.assets.values()} == {"de", "ml", "ai"}


def test__workflows_tasks_and_bundles_have_one_parent_each(hierarchy):
    """Each asset has one parent: the project holds the bundle, the bundle the workflow, the workflow its tasks."""
    assets = hierarchy.assets
    workflow = assets["uc_job/example_claims/dataops_run"]
    assert workflow.parent_asset_id == "bundle/example_claims/dataops"
    assert assets["bundle/example_claims/dataops"].parent_asset_id == "project/example_claims"
    task = assets["uc_job_task/example_claims/dataops_run/silver"]
    assert (task.parent_asset_id, task.subtype) == (workflow.asset_id, "pipeline")
    assert all(a.parent_asset_id for a in assets.values())


def test__a_dataset_sits_under_its_flow_spec_and_pipeline(hierarchy):
    """The chain pipeline, spec, flow, dataset is unbroken, with the layer carried down."""
    assets = hierarchy.assets
    spec = next(a for a in assets.values() if a.asset_type == "dataflow_spec" and a.layer == "silver")
    assert assets[spec.parent_asset_id].asset_type == "uc_pipeline"
    flow = next(a for a in assets.values() if a.asset_type == "dataflow_flow" and a.subtype == "cdc_scd2")
    assert assets[flow.parent_asset_id].asset_type == "dataflow_spec"
    dataset = assets["example_claims_silver.dim_policy"]
    assert assets[dataset.parent_asset_id].asset_type == "dataflow_flow" and dataset.layer == "silver"


def test__a_pipeline_task_runs_its_pipeline_and_the_flows_read_and_write_datasets(hierarchy):
    """The runs edge points from the task that starts a pipeline; flows read their sources and write their targets."""
    edges = {(e.parent_asset_id, e.child_asset_id, e.relation) for e in hierarchy.edges}
    assert (
        "uc_job_task/example_claims/dataops_run/silver",
        "uc_pipeline/example_claims/dataops_silver",
        "runs",
    ) in edges
    flow = "dataflow_flow/example_claims/dim_policy/dim_policy"
    assert (flow, "example_claims_silver.dim_policy", "writes") in edges
    assert any(relation == "reads" and child == flow for _, child, relation in edges)
    assert {relation for *_, relation in edges} <= set(RELATIONS)


def test__the_tasks_of_a_workflow_keep_their_order_and_dependencies(hierarchy):
    """Task rows carry the kind, the dependencies and the pipeline a task targets."""
    tasks = hierarchy.tasks_of("uc_job/example_claims/dataops_run")
    assert [t.task_key for t in tasks][:3] == ["generate_data", "bronze", "silver"]
    silver = next(t for t in tasks if t.task_key == "silver")
    assert (silver.kind, silver.depends_on, silver.target_asset_id) == (
        "pipeline",
        ("bronze",),
        "uc_pipeline/example_claims/dataops_silver",
    )


def test__a_bundle_may_not_write_another_workloads_schema(hierarchy):
    """A de bundle writing an `_ai` schema is reported; the example bundles are clean."""
    assert not _schema_findings(hierarchy, "example_claims")
    asset = hierarchy.assets["example_claims_silver.dim_policy"]
    forged = type(hierarchy)(
        assets={"example_claims_ai.t": type(asset)(**{**asset.__dict__, "asset_id": "example_claims_ai.t"})}
    )
    assert [f.rule for f in _schema_findings(forged, "example_claims")] == ["hierarchy"]
