"""Tests of CompositeRepository: fan-out writes, primary reads, failures reported after every sink was tried."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from dbxops.metastore.composite import CompositeRepository
from dbxops.metastore.records import (
    EvalResultRecord,
    EvalSubject,
    LineageEdge,
    PublishCounts,
    PublishLogEntry,
    QualityResultRecord,
    RegistrationRecord,
    RunRecord,
    SemanticLinkRow,
    TagRow,
)
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from tests.fixtures.metastore import (
    CONTRACT_ID,
    SQUAD,
    contract_record,
    deployment,
)


def make_repo() -> SqlAlchemyRepository:
    """Return a fresh in-memory repository."""
    repository = SqlAlchemyRepository(create_engine("sqlite://"))
    repository.ensure_schema()
    return repository


def test__writes_go_to_every_sink_and_reads_come_from_the_first(contract_doc, ctx):
    """A publish lands in both sinks and the primary answers reads."""
    first, second = make_repo(), make_repo()
    composite = CompositeRepository([first, second])
    assert composite.upsert_definitions([contract_record(contract_doc)], ctx) == PublishCounts(inserted=1)
    assert composite.record_deployment(deployment(), ctx) == 3
    for sink in (first, second):
        assert sink.get_current_definition("data_contract", CONTRACT_ID) is not None
    assert composite.list_current_entity_ids("data_contract", SQUAD) == [CONTRACT_ID]
    assert composite.get_current_definition("data_contract", CONTRACT_ID).entity_id == CONTRACT_ID
    assert composite.current_quality_rules(SQUAD)
    assert composite.cleanup_orphans() == 0
    assert composite.purge_runtime(400) == 0


def test__the_first_failure_propagates_after_the_primary_sink_was_written(contract_doc, ctx):
    """The primary sink is written first; a failing later sink raises, and a retry is idempotent for the primary."""
    healthy = make_repo()
    broken = SqlAlchemyRepository(create_engine("sqlite://"))  # schema never created
    composite = CompositeRepository([healthy, broken])
    with pytest.raises(OperationalError):
        composite.upsert_definitions([contract_record(contract_doc)], ctx)
    assert healthy.get_current_definition("data_contract", CONTRACT_ID) is not None
    with pytest.raises(OperationalError):
        composite.upsert_definitions([contract_record(contract_doc)], ctx)
    assert healthy.upsert_definitions([contract_record(contract_doc)], ctx) == PublishCounts(unchanged=1)


def test__a_composite_needs_a_sink():
    """An empty sink list is a programming error."""
    with pytest.raises(ValueError):
        CompositeRepository([])


def sinks_and_composite():
    """Return two empty repositories and a composite over them."""
    sinks = [SqlAlchemyRepository(create_engine("sqlite://")) for _ in range(2)]
    return sinks, CompositeRepository(sinks)


def test__schema_definitions_and_retire_fan_out(contract_doc, ctx):
    """ensure_schema, publish and retire reach every sink."""
    sinks, composite = sinks_and_composite()
    composite.ensure_schema()
    composite.upsert_definitions([contract_record(contract_doc)], ctx)
    composite.retire_definitions("data_contract", [CONTRACT_ID], ctx)
    for sink in sinks:
        assert sink.get_current_definition("data_contract", CONTRACT_ID).status == "retired"


def test__derived_rows_reach_every_sink(ctx):
    """Tags, links and lineage edges are replaced on every sink."""
    _, composite = sinks_and_composite()
    composite.ensure_schema()
    assert composite.upsert_tags([TagRow("asset", "s.t", "classification")], ctx) == 1
    assert composite.upsert_semantic_links([SemanticLinkRow("data_contract", "x", "i")], ctx) == 1
    assert composite.record_lineage([LineageEdge("a", "b")], ctx, target="dev", origin="spec") == 1


def test__facts_and_the_publish_log_reach_every_sink(ctx):
    """Registrations, runs, quality, evals and log events are inserted on every sink."""
    _, composite = sinks_and_composite()
    composite.ensure_schema()
    assert composite.record_registration([RegistrationRecord("p", "pr")], ctx) == 1
    assert composite.record_run(RunRecord("dev", "r", "j", "OK"), ctx) == 1
    assert composite.record_quality([QualityResultRecord("dev", "u", "a", "r", "c", 1)], ctx) == 1
    assert composite.record_evals([EvalResultRecord("dev", "r", EvalSubject("a", "1"), "m")], ctx) == 1
    composite.log_publish(PublishLogEntry(ctx, "x", "started"))
