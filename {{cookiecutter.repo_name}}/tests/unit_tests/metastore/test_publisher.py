"""Tests of MetadataPublisher: idempotence, the publish log, retire-missing and the guard in front of the repository."""

import copy
from dataclasses import replace

import pytest

from dbxops.metastore.collectors import (
    asset_records,
    semantic_link_rows,
    tag_rows,
)
from dbxops.metastore.errors import (
    CollectError,
    PublishError,
    WriterError,
)
from dbxops.metastore.publisher import (
    MetadataPublisher,
    dedupe_records,
)
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    LineageEdge,
    PublishContext,
    PublishCounts,
    RunRecord,
)
from dbxops.metastore.runtime_collectors import RunCollection
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from tests.fixtures.metastore import (
    CONTRACT_ID,
    SQUAD,
    contract_record,
    deployment,
    product_record,
)
from tests.unit_tests.metastore.test_sqlalchemy_repository import rows


@pytest.fixture
def publisher(repo) -> MetadataPublisher:
    """Return a publisher for the example squad on SQLite."""
    return MetadataPublisher(repo, PublishContext(SQUAD, "ci", "c1", "dev"))


def log(engine) -> list[tuple]:
    """Return (what, status, inserted, unchanged) of the publish log in write order."""
    found = rows(
        engine,
        "SELECT what, status, inserted, unchanged, repo_id, target FROM md_publish_log ORDER BY recorded_at, status DESC",
    )
    return sorted((row["what"], row["status"], row["inserted"], row["unchanged"]) for row in found)


def test__a_publish_writes_started_and_complete_log_entries_with_counts(publisher, sqlite_engine, contract_doc):
    """Each publish is bracketed by a started and a complete row that carries the counts."""
    counts = publisher.publish_definitions([contract_record(contract_doc)])
    assert counts == PublishCounts(inserted=1)
    assert log(sqlite_engine) == [("definitions", "complete", 1, 0), ("definitions", "started", 0, 0)]
    stored = rows(sqlite_engine, "SELECT repo_id, target, git_sha FROM md_publish_log")
    assert {tuple(row.values()) for row in stored} == {(SQUAD, "dev", "c1")}


def test__publishing_twice_is_idempotent(publisher, sqlite_engine, contract_doc, product_doc):
    """The second identical publish inserts nothing and counts everything as unchanged."""
    records = [contract_record(contract_doc), product_record(product_doc)]
    publisher.publish_definitions(records)
    second = publisher.publish_definitions(records)
    assert second == PublishCounts(unchanged=2)
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_data_contract")[0]["n"] == 1


def test__a_failed_publish_is_logged_and_re_raised(publisher, sqlite_engine, contract_doc):
    """A rule violation raises to the caller and leaves a failed log entry with the message."""
    publisher.publish_definitions([contract_record(contract_doc)])
    edited = copy.deepcopy(contract_doc)
    edited["description"]["purpose"] = "no bump"
    with pytest.raises(PublishError):
        publisher.publish_definitions([contract_record(edited)])
    failed = rows(sqlite_engine, "SELECT message FROM md_publish_log WHERE status = 'failed'")
    assert len(failed) == 1 and "bump the version" in failed[0]["message"]


def test__the_publisher_is_guarded_for_its_repo_id(publisher, sqlite_engine, contract_doc):
    """A record for another repo is rejected before it reaches the repository, and the failure is logged."""
    with pytest.raises(WriterError):
        publisher.publish_definitions([contract_record(contract_doc, repo_id="someone_else")])
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_data_contract")[0]["n"] == 0
    assert any(status == "failed" for _, status, _, _ in log(sqlite_engine))


def test__retire_missing_retires_only_this_repos_entities_that_left_git(publisher, repo, contract_doc):
    """With retire_missing, a contract absent from the new set is retired; present ones are untouched."""
    other = copy.deepcopy(contract_doc)
    other["id"] = "dc_claims_fraud_second"
    publisher.publish_definitions([contract_record(contract_doc), contract_record(other)])
    publisher.publish_definitions([contract_record(contract_doc)], retire_missing=["data_contract"])
    assert repo.get_current_definition("data_contract", "dc_claims_fraud_second").status == "retired"
    assert repo.get_current_definition("data_contract", CONTRACT_ID).status == "active"
    again = publisher.publish_definitions([contract_record(contract_doc)], retire_missing=["data_contract"])
    assert again == PublishCounts(unchanged=1)


def test__links_and_tags_are_replace_sets_only_when_given(publisher, sqlite_engine, contract_doc):
    """Passing links and tags replaces them; passing None leaves what is there."""
    record = contract_record(contract_doc)
    publisher.publish_definitions([record], links=semantic_link_rows(contract_doc), tags=tag_rows(contract_doc))
    active = rows(sqlite_engine, "SELECT count(*) AS n FROM md_tag WHERE is_active")[0]["n"]
    assert active == 4
    publisher.publish_definitions([record])
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_tag WHERE is_active")[0]["n"] == 4
    publisher.publish_definitions([record], links=[], tags=[])
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_tag WHERE is_active")[0]["n"] == 0
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_semantic_link WHERE is_active")[0]["n"] == 0


def test__the_same_asset_from_a_contract_and_a_product_is_published_once(contract_doc):
    """Duplicates with equal content collapse; a conflicting duplicate is a CollectError."""
    options = {"repo_id": SQUAD, "source_path": "x", "git_commit": "c"}
    first = asset_records(contract_doc, **options)
    assert len(dedupe_records(first + first)) == 1
    conflicting = replace(first[0], payload={**first[0].payload, "fqn_template": "other"})
    with pytest.raises(CollectError, match="twice"):
        dedupe_records([first[0], conflicting])


def test__deployments_lineage_and_runs_are_published_with_log_entries(publisher, sqlite_engine):
    """Facts and lineage go through the same logged path; a retried deployment inserts nothing."""
    assert publisher.publish_deployment(deployment()).inserted == 3
    assert publisher.publish_deployment(deployment()).inserted == 0
    assert publisher.publish_lineage([LineageEdge("a.x", "a.y")], ORIGIN_SPEC).inserted == 1
    assert publisher.publish_run(RunCollection(RunRecord("dev", "r1", "uc_job/x/y", "SUCCESS"))).inserted == 1
    whats = {what for what, _, _, _ in log(sqlite_engine)}
    assert whats == {"deployments", "lineage", "runs"}
    assert publisher.cleanup_orphans() == 0


def test__a_publisher_can_ensure_the_schema(sqlite_engine):
    """ensure_schema goes through the guard to the repository."""
    MetadataPublisher(SqlAlchemyRepository(sqlite_engine), PublishContext(SQUAD, target="dev")).ensure_schema()
    assert rows(sqlite_engine, "SELECT version FROM md_schema_version") == [{"version": 2}]
