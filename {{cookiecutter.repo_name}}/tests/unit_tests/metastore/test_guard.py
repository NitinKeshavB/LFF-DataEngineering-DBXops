"""Tests of WriterGuard: the writer split of plan D1."""

import pytest

from dbxops.metastore.collectors import (
    registration_records,
    tag_rows,
)
from dbxops.metastore.errors import WriterError
from dbxops.metastore.guard import WriterGuard
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
    EvalResultRecord,
    EvalSubject,
    LineageEdge,
    PublishLogEntry,
    QualityResultRecord,
    RegistrationRecord,
    RunRecord,
    SemanticLinkRow,
    TagRow,
)
from tests.fixtures.metastore import (
    CONTRACT_ID,
    OTHER_SQUAD,
    SQUAD,
    contract_record,
    deployment,
)


@pytest.fixture
def squad(repo) -> WriterGuard:
    """Return a guard for the example squad."""
    return WriterGuard(repo, SQUAD)


@pytest.fixture
def platform(repo) -> WriterGuard:
    """Return a guard for the registry."""
    return WriterGuard(repo, PLATFORM_REPO_ID)


def org_records(registration_doc):
    """Return the team and project records of a registration."""
    return registration_records(registration_doc, repo_id=PLATFORM_REPO_ID, source_path="p.yaml", git_commit="r1")


def test__a_squad_publish_writes_its_own_rows(squad, contract_doc, ctx):
    """A squad can write its own contract."""
    assert squad.upsert_definitions([contract_record(contract_doc)], ctx).inserted == 1
    assert squad.get_current_definition("data_contract", CONTRACT_ID) is not None


def test__a_squad_cannot_write_rows_of_another_repo_id(squad, contract_doc, ctx):
    """A record carrying another repo_id, including the registry's, is rejected."""
    for repo_id in (OTHER_SQUAD, PLATFORM_REPO_ID):
        with pytest.raises(WriterError, match="may only write its own"):
            squad.upsert_definitions([contract_record(contract_doc, repo_id=repo_id)], ctx)


def test__a_squad_publish_never_writes_organisation_tables(squad, registration_doc, ctx):
    """Team, project and the other platform kinds are refused for squads, so org tables stay empty."""
    for record in org_records(registration_doc):
        with pytest.raises(WriterError, match="may not write"):
            squad.upsert_definitions([record], ctx)
    with pytest.raises(WriterError):
        squad.retire_definitions("project", ["claims_fraud"], ctx)
    with pytest.raises(WriterError, match="only _platform"):
        squad.record_registration([RegistrationRecord("claims_fraud", "pr")], ctx)
    with pytest.raises(WriterError, match="may not write 'project' tags"):
        squad.upsert_tags([TagRow("project", "claims_fraud", "cost_center", "1")], ctx, ["cost_center"])
    with pytest.raises(WriterError, match="only _platform"):
        squad.purge_runtime(10)


def test__the_registry_cannot_write_squad_tables(platform, contract_doc, make_ctx):
    """The registry's guard rejects squad kinds, squad facts, lineage, links and non-project tags."""
    ctx = make_ctx(repo_id=PLATFORM_REPO_ID)
    with pytest.raises(WriterError, match="may not write"):
        platform.upsert_definitions([contract_record(contract_doc, repo_id=PLATFORM_REPO_ID)], ctx)
    for call in (
        lambda: platform.record_deployment(deployment(), ctx),
        lambda: platform.record_run(RunRecord("dev", "r", "j", "OK"), ctx),
        lambda: platform.record_quality([QualityResultRecord("dev", "u", "a", "r", "c", 1)], ctx),
        lambda: platform.record_evals([EvalResultRecord("dev", "r", EvalSubject("a", "1"), "m")], ctx),
        lambda: platform.record_lineage([LineageEdge("a", "b")], ctx, target="dev", origin="spec"),
        lambda: platform.upsert_semantic_links([SemanticLinkRow("data_contract", "x", "iri")], ctx),
        lambda: platform.upsert_tags(tag_rows(contract_doc), ctx),
    ):
        with pytest.raises(WriterError):
            call()


def test__the_registry_writes_organisation_rows_registrations_and_project_tags(platform, registration_doc, make_ctx):
    """The registry guard accepts its own kinds, registration rows and project tags."""
    ctx = make_ctx(repo_id=PLATFORM_REPO_ID)
    assert platform.upsert_definitions(org_records(registration_doc), ctx).inserted == 2
    assert platform.record_registration([RegistrationRecord("claims_fraud", "pr")], ctx) == 1
    assert platform.upsert_tags([TagRow("project", "claims_fraud", "region", "au")], ctx, ["region"]) == 1


def test__a_squad_cannot_take_over_or_retire_another_squads_entity(repo, contract_doc, make_ctx):
    """Squad B can neither republish nor retire squad A's contract."""
    a, b = WriterGuard(repo, SQUAD), WriterGuard(repo, OTHER_SQUAD)
    a.upsert_definitions([contract_record(contract_doc)], make_ctx())
    with pytest.raises(WriterError, match="owned by repo"):
        b.upsert_definitions([contract_record(contract_doc, repo_id=OTHER_SQUAD)], make_ctx(repo_id=OTHER_SQUAD))
    with pytest.raises(WriterError, match="owned by repo"):
        b.retire_definitions("data_contract", [CONTRACT_ID], make_ctx(repo_id=OTHER_SQUAD))
    assert a.get_current_definition("data_contract", CONTRACT_ID).repo_id == SQUAD


def test__a_context_for_another_repo_is_rejected(squad, contract_doc, make_ctx):
    """The context repo_id must equal the guarded publisher."""
    with pytest.raises(WriterError, match="differs from the guarded publisher"):
        squad.upsert_definitions([contract_record(contract_doc)], make_ctx(repo_id=OTHER_SQUAD))


def test__the_publish_log_accepts_only_the_publishers_own_entries(squad, ctx, make_ctx):
    """An entry for another repo is rejected; the guard's own repo is accepted."""
    squad.log_publish(PublishLogEntry(ctx, "definitions", "started"))
    with pytest.raises(WriterError):
        squad.log_publish(PublishLogEntry(make_ctx(repo_id=OTHER_SQUAD), "definitions", "started"))


def test__reads_ensure_schema_and_orphan_cleanup_pass_through(squad, contract_doc, ctx):
    """Reads and the schema check are not restricted."""
    squad.ensure_schema()
    squad.upsert_definitions([contract_record(contract_doc)], ctx)
    assert squad.list_current_entity_ids("data_contract", SQUAD) == [CONTRACT_ID]
    assert squad.current_quality_rules(SQUAD)
    assert squad.cleanup_orphans() == 0
