"""
End to end: publish this repository's real bundles through the metastore CLI into a database and read them back.

This is the check that the metadata tooling (specs, contracts, products) and the metastore (collectors, repository)
agree on the same documents, using the example DataOps bundle rather than hand-made fixtures.
"""

import pytest

from dbxops.metastore.cli import main
from dbxops.metastore.event_log import SparkPipelineEventLog
from dbxops.metastore.records import PublishContext
from dbxops.metastore.tasks import publish_run
from tests.consts import PROJECT_DIR
from tests.unit_tests.metastore.test_event_log import (
    FLOW_ROW,
    PIPELINE,
    UPDATE_ROW,
    FakeSpark,
)
from tests.unit_tests.metastore.test_sqlalchemy_repository import rows

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
    """15 contracts, 3 products and 15 dataflow specs from bundles/dataops are current definitions."""
    assert count(published, "md_data_contract", "is_current") == 15
    assert count(published, "md_data_product", "is_current") == 3
    assert count(published, "md_dataflow_spec", "is_current") == 15
    assert count(published, "md_contract_object", "1 = 1") == 15


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
    assert len(edges) == 6


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


def test__publishing_again_changes_nothing(repo, published, capsys):
    """A second publish of the same commit reports no inserts or closes: every definition is unchanged."""
    assert main(ARGV, build=lambda config, target: repo) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert "inserted=0, closed=0" in first_line
    assert count(published, "md_data_contract") == 15


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
