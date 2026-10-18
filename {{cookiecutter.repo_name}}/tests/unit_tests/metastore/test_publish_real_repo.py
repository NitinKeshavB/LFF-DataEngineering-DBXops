"""
End to end: publish this repository's real bundles through the metastore CLI into a database and read them back.

This is the check that the metadata tooling (specs, contracts, products) and the metastore (collectors, repository)
agree on the same documents, using the example DataOps bundle rather than hand-made fixtures.
"""

from datetime import (
    datetime,
    timezone,
)

import pytest

from dbxops.metadata.specs import load_specs
from dbxops.metastore.cli import main
from dbxops.metastore.event_log import SparkPipelineEventLog
from dbxops.metastore.records import (
    CiContext,
    DeployedAssetRecord,
    DeploymentRecord,
    PipelineUpdateRecord,
    PublishContext,
    RunRecord,
    TaskRunRecord,
    Timing,
)
from dbxops.metastore.tasks import publish_run
from tests.consts import PROJECT_DIR
from tests.unit_tests.metastore.test_event_log import (
    FLOW_ROW,
    PIPELINE,
    UPDATE_ROW,
    FakeSpark,
)
from tests.unit_tests.metastore.test_sqlalchemy_repository import rows

CONTRACTS = len(list(PROJECT_DIR.glob("bundles/*/src/dataflows/**/contracts/*.odcs.yaml")))
PRODUCTS = len(list(PROJECT_DIR.glob("bundles/*/src/products/*.odps.yaml")))
SPECS = sum(len(load_specs(p)) for p in PROJECT_DIR.glob("bundles/*/src/dataflows/**/dataflowspec/*_main.json"))
ARGV = ["publish", "--target", "dev", "--root", str(PROJECT_DIR), "--what", "definitions,lineage", "--git-sha", "abc"]


@pytest.fixture
def published(repo, sqlite_engine, capsys):
    """Publish the real repo once and return the engine for queries."""
    assert main(ARGV, build=lambda config, target: repo) == 0
    capsys.readouterr()
    return sqlite_engine


def count(engine, table: str, where: str = "1 = 1") -> int:
    """Return the number of rows of a table matching a condition."""
    return rows(engine, f"SELECT count(*) AS n FROM {table} WHERE {where}")[0]["n"]


def test__every_contract_product_and_spec_of_the_example_is_published(published):
    """Every contract, product and (template-expanded) dataflow spec under bundles/ is a current definition."""
    assert count(published, "md_data_contract", "is_current") == CONTRACTS
    assert count(published, "md_data_product", "is_current") == PRODUCTS
    assert count(published, "md_dataflow_spec", "is_current") == SPECS
    assert count(published, "md_contract_object", "1 = 1") == CONTRACTS


def test__contract_structure_and_quality_rules_from_the_generated_contracts_arrive_intact(published):
    """Columns, and the expectation-derived quality rules with their quarantine target and spec id, are stored."""
    columns = rows(
        published,
        "SELECT count(*) AS n FROM md_contract_property WHERE contract_id = 'dc_example_claims_silver_fact_premium'",
    )
    assert columns[0]["n"] == 7
    rule = rows(
        published,
        "SELECT action, origin, spec_id, quarantine_asset_id FROM md_quality_rule "
        "WHERE contract_id = 'dc_example_claims_silver_fact_premium' AND rule_id = 'fact_premium_premium_id_not_null'",
    )
    assert rule == [
        {
            "action": "quarantine",
            "origin": "expectation",
            "spec_id": "fact_premium",
            "quarantine_asset_id": "example_claims_silver.fact_premium_quarantine",
        }
    ]


def test__declared_lineage_follows_the_specs_from_bronze_to_silver(published):
    """The spec edges connect bronze tables to the silver tables built from them, by logical asset id."""
    edges = {(e["source_asset_id"], e["target_asset_id"]) for e in rows(published, "SELECT * FROM md_lineage_edge")}
    assert ("example_claims_bronze.party", "example_claims_silver.dim_policyholder") in edges
    assert ("example_claims_bronze.claim", "example_claims_silver.fact_claim") in edges


def test__ports_and_tags_use_the_project_prefixed_names(published):
    """Products expose their contracts' assets, and the Ontos tag names strip the dp_/dc_ prefix."""
    ports = rows(
        published,
        "SELECT port_name, contract_id, direction FROM md_product_port WHERE product_id = 'dp_example_claims_claims_analytics'",
    )
    assert {(p["direction"], p["port_name"]) for p in ports} >= {
        ("output", "loss_ratio_monthly"),
        ("input", "fact_premium"),
    }
    keys = {r["tag_key"] for r in rows(published, "SELECT tag_key FROM md_tag WHERE is_active")}
    assert "data_product_example_claims_claims_analytics" in keys
    assert any(k.startswith("data_contract_example_claims_silver_") for k in keys)


def test__every_asset_is_published_with_its_workload_parent_and_layer(published):
    """md_asset carries the tree columns: the workload per bundle family, a parent for every non-root asset, a layer."""
    by_workload = {
        r["workload"]: r["n"]
        for r in rows(published, "SELECT workload, count(*) AS n FROM md_asset WHERE is_current GROUP BY workload")
    }
    assert set(by_workload) == {"de", "ml", "ai"} and all(by_workload.values())
    assert count(published, "md_asset", "is_current AND (parent_asset_id IS NULL OR parent_asset_id = '')") == 0
    table = rows(
        published,
        "SELECT workload, layer, subtype, parent_asset_id FROM md_asset "
        "WHERE is_current AND entity_id = 'example_claims_silver.dim_policy'",
    )
    assert table == [
        {
            "workload": "de",
            "layer": "silver",
            "subtype": "",
            "parent_asset_id": "dataflow_flow/example_claims/dim_policy/dim_policy",
        }
    ]


def test__a_workflow_publishes_its_tasks_and_the_asset_edges(published):
    """md_job_task has the workflow's tasks with kinds and dependencies; md_asset_edge has contains, runs and writes."""
    tasks = rows(
        published,
        "SELECT task_key, task_kind, target_asset_id FROM v_job_task_current "
        "WHERE asset_id = 'uc_job/example_claims/dataops_run' ORDER BY task_key",
    )
    assert {t["task_key"] for t in tasks} == {
        "generate_data",
        "bronze",
        "silver",
        "gold",
        "apply_table_tags",
        "publish_run_metadata",
    }
    assert {t["task_key"]: t["target_asset_id"] for t in tasks}[
        "silver"
    ] == "uc_pipeline/example_claims/dataops_silver"
    relations = {r["relation"] for r in rows(published, "SELECT DISTINCT relation FROM md_asset_edge WHERE is_active")}
    assert {"contains", "runs", "writes", "reads", "exposes", "serves", "indexes"} <= relations


def tree_by_asset(engine, where: str = "1 = 1") -> dict:
    """Return the rows of v_asset_tree keyed by asset id."""
    return {r["asset_id"]: r for r in rows(engine, f"SELECT * FROM v_asset_tree WHERE {where}")}


def test__the_asset_tree_gives_each_asset_a_path_and_a_designed_state(published):
    """Before any deployment a workflow is designed, its tasks are contained, and a dataset has a full path."""
    tree = tree_by_asset(published)
    path = tree["example_claims_silver.dim_policy"]["path"]
    assert path.startswith("dataops > ") and path.endswith(" > dim_policy")
    assert tree["uc_job/example_claims/dataops_run"]["state"] == "designed"
    assert tree["uc_job_task/example_claims/dataops_run/silver"]["state"] == "contained"


def deploy(repo, context) -> None:
    """Record a deployment of the dataops workflow (job 77) and its silver pipeline (pipeline 99)."""
    ci = CiContext("ci-1", 1, "", "abc123", "someone")
    deployed = [
        DeployedAssetRecord("uc_job/example_claims/dataops_run", 1, "dataops run", "77"),
        DeployedAssetRecord("uc_pipeline/example_claims/dataops_silver", 1, "silver", "99"),
    ]
    repo.record_deployment(DeploymentRecord("dev", "dataops", ci, "success", assets=deployed), context)


def test__a_deployment_moves_an_asset_to_deployed_and_names_the_commit(repo, published):
    """After a deployment the workflow is deployed, with the commit that deployed it, and not yet running."""
    deploy(repo, PublishContext("example_claims", "test", "abc", "dev"))
    job = tree_by_asset(published, "target = 'dev'")["uc_job/example_claims/dataops_run"]
    assert (job["state"], job["deployed_commit"]) == ("deployed", "abc123")


def test__a_run_and_a_pipeline_update_move_assets_to_running(repo, published):
    """A recorded run of job 77 and an update of pipeline 99 make their assets running, with the last status."""
    context = PublishContext("example_claims", "test", "abc", "dev")
    deploy(repo, context)
    started = Timing(datetime(2026, 1, 2, tzinfo=timezone.utc), datetime(2026, 1, 2, 1, tzinfo=timezone.utc))
    update = PipelineUpdateRecord("u1", "uc_pipeline/99", "COMPLETED", timing=started)
    repo.record_run(
        RunRecord("dev", "5", "uc_job/example_claims/77", "SUCCESS", started, pipeline_updates=[update]), context
    )
    repo.record_task_runs([TaskRunRecord("dev", "5", "silver", "SUCCESS", started, 1)], context)
    tree = tree_by_asset(published, "target = 'dev'")
    assert (
        tree["uc_job/example_claims/dataops_run"]["state"],
        tree["uc_job/example_claims/dataops_run"]["last_run_status"],
    ) == (
        "running",
        "SUCCESS",
    )
    assert tree["uc_pipeline/example_claims/dataops_silver"]["last_run_status"] == "COMPLETED"
    assert count(published, "md_task_run", "task_key = 'silver' AND attempt = 1") == 1


def test__the_asset_inventory_counts_assets_by_project_workload_and_type(published):
    """v_asset_inventory groups the current assets, so an owner can see what a project is made of."""
    inventory = {
        (r["workload"], r["asset_type"]): r["assets"]
        for r in rows(published, "SELECT * FROM v_asset_inventory WHERE repo_id = 'example_claims'")
    }
    assert inventory[("de", "uc_job")] == 1 and inventory[("ml", "uc_job")] == 1 and inventory[("ai", "uc_job")] == 1
    assert inventory[("de", "uc_pipeline")] == 3


def test__publishing_again_changes_nothing(repo, published, capsys):
    """A second publish of the same commit reports no inserts or closes: every definition is unchanged."""
    assert main(ARGV, build=lambda config, target: repo) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert "inserted=0, closed=0" in first_line
    assert count(published, "md_data_contract") == CONTRACTS


def publish_registry(repo, capsys) -> str:
    """Publish the registry scaffold and return what the command printed."""
    argv = [
        "publish-registry",
        "--target",
        "dev",
        "--registry",
        str(PROJECT_DIR / "templates/registry-repo"),
        "--git-sha",
        "abc",
    ]
    assert main(argv, build=lambda config, target: repo) == 0
    return capsys.readouterr().out


def test__the_registry_scaffold_publishes_projects_squads_domains_and_glossary(repo, sqlite_engine, capsys):
    """`publish-registry` writes the platform-owned records: the example project and squad, 160 domain nodes, the glossary."""
    assert "registry: PublishCounts(inserted=" in publish_registry(repo, capsys)
    assert count(sqlite_engine, "md_project", "is_current AND repo_id = '_platform'") == 1
    assert count(sqlite_engine, "md_team", "is_current") == 1
    assert count(sqlite_engine, "md_domain", "is_current") == 160
    assert count(sqlite_engine, "md_glossary_concept", "is_current") == 7


def test__registry_contacts_and_groups_are_stored_per_role_and_a_republish_changes_nothing(
    repo, sqlite_engine, capsys
):
    """Owners and AD groups land per role, and publishing the registry again is a no-op."""
    publish_registry(repo, capsys)
    contacts = {r["role"] for r in rows(sqlite_engine, "SELECT role FROM md_project_contact")}
    assert contacts == {"technical_owner", "data_owner", "executive_owner", "data_steward", "support"}
    groups = {r["access_role"] for r in rows(sqlite_engine, "SELECT access_role FROM md_project_group")}
    assert groups == {"developers", "readers", "admins"}
    assert "inserted=0, closed=0" in publish_registry(repo, capsys)


class FakeJobs:
    """A Jobs API client that returns one finished run with a single pipeline task."""

    def get_run(self, run_id):
        """Return the run."""
        return {
            "run_id": int(run_id),
            "job_id": 1,
            "state": {"result_state": "SUCCESS"},
            "start_time": 1000,
            "end_time": 2000,
            "tasks": [{"pipeline_task": {"pipeline_id": PIPELINE}}],
        }


def test__event_log_expectations_resolve_to_the_published_contract_rules(repo, published):
    """A run's expectation counts are pinned to the real contract's rule id and version, with nothing left unmapped."""
    readers = (FakeJobs(), SparkPipelineEventLog(FakeSpark([UPDATE_ROW], [FLOW_ROW])))
    context = PublishContext("example_claims", "test", "abc", "dev")
    _, collection = publish_run("7", repository=repo, context=context, readers=readers)
    assert not collection.unmapped
    results = rows(
        published,
        "SELECT contract_id, rule_id, rule_record_version, passed, failed FROM md_quality_result ORDER BY rule_id",
    )
    contract = "dc_example_claims_silver_fact_premium"
    assert results == [
        {
            "contract_id": contract,
            "rule_id": "fact_premium_premium_id_not_null",
            "rule_record_version": 1,
            "passed": 98,
            "failed": 2,
        },
        {
            "contract_id": contract,
            "rule_id": "fact_premium_written_premium_non_negative",
            "rule_record_version": 1,
            "passed": 100,
            "failed": 0,
        },
    ]
