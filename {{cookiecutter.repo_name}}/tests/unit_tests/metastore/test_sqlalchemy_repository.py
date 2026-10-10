"""Tests of SqlAlchemyRepository on SQLite: SCD2 rules, invariants, derived rows, facts and upkeep."""

import copy
import re
import time
from dataclasses import replace
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Engine,
    event,
)

from dbxops.hashing import content_hash
from dbxops.metastore.collectors import registration_records
from dbxops.metastore.errors import (
    PublishError,
    SchemaError,
    WriterError,
)
from dbxops.metastore.model import ENTITIES
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    ORIGIN_UC_SYSTEM,
    DatasetMetricsRecord,
    EvalGate,
    EvalResultRecord,
    EvalSubject,
    LineageEdge,
    PipelineUpdateRecord,
    PublishCounts,
    PublishLogEntry,
    QualityResultRecord,
    RegistrationRecord,
    RuleCounts,
    RunOrigin,
    RunRecord,
    SemanticLinkRow,
    TagRow,
    Timing,
)
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from tests.fixtures.metastore import (
    CONTRACT_ID,
    OTHER_SQUAD,
    SQUAD,
    contract_record,
    deployment,
    product_record,
)


def rows(engine: Engine, sql: str, **params) -> list[dict]:
    """Run raw SQL and return the stored values as dicts."""
    with engine.connect() as conn:
        return [dict(row) for row in conn.exec_driver_sql(sql, params or ()).mappings()]


def versions(engine: Engine, entity_id: str = CONTRACT_ID) -> list[tuple]:
    """Return (record_version, is_current, status) of every version of an entity."""
    found = rows(
        engine,
        f"SELECT record_version, is_current, status FROM md_data_contract WHERE entity_id = '{entity_id}' ORDER BY 1",
    )
    return [(row["record_version"], row["is_current"], row["status"]) for row in found]


def assert_one_current_everywhere(engine: Engine) -> None:
    """Assert the at-most-one-current-row invariant on every parent table."""
    for spec in ENTITIES.values():
        found = rows(engine, f"SELECT entity_id, count(*) AS n FROM {spec.table} WHERE is_current GROUP BY entity_id")
        assert all(row["n"] == 1 for row in found), spec.table


def changed(doc: dict, version: str = "1.1.0") -> dict:
    """Return a copy of a contract document with different content and a new semver."""
    other = copy.deepcopy(doc)
    other["version"] = version
    other["description"]["purpose"] = "Score claims daily"
    return other


def test__unchanged_hash_publishes_nothing_new(repo, sqlite_engine, contract_doc, ctx):
    """Publishing the same document twice leaves one version and counts the second as unchanged."""
    first = repo.upsert_definitions([contract_record(contract_doc)], ctx)
    second = repo.upsert_definitions([contract_record(contract_doc)], ctx)
    assert first == PublishCounts(inserted=1)
    assert second == PublishCounts(unchanged=1)
    assert versions(sqlite_engine) == [(1, 1, "active")]


def test__changed_hash_closes_the_current_row_and_inserts_the_next_version(repo, sqlite_engine, contract_doc, ctx):
    """A new document version closes version 1 and inserts version 2, with a full child snapshot each."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    counts = repo.upsert_definitions([contract_record(changed(contract_doc))], ctx)
    assert counts == PublishCounts(inserted=1, closed=1)
    assert versions(sqlite_engine) == [(1, 0, "active"), (2, 1, "active")]
    per_version = rows(
        sqlite_engine, "SELECT record_version, count(*) AS n FROM md_contract_property GROUP BY 1 ORDER BY 1"
    )
    assert [row["n"] for row in per_version] == [6, 6]
    closed = rows(sqlite_engine, "SELECT valid_to FROM md_data_contract WHERE record_version = 1")[0]
    assert closed["valid_to"] is not None


def test__a_hash_change_without_a_semver_bump_is_rejected(repo, sqlite_engine, contract_doc, ctx):
    """Changing a contract's content while keeping its semver raises PublishError and writes nothing."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    edited = copy.deepcopy(contract_doc)
    edited["description"]["purpose"] = "edited without bump"
    with pytest.raises(PublishError, match="bump the version"):
        repo.upsert_definitions([contract_record(edited)], ctx)
    assert versions(sqlite_engine) == [(1, 1, "active")]


def test__a_contract_without_a_semver_is_rejected(repo, contract_doc, ctx):
    """Contracts and products must carry a semver."""
    del contract_doc["version"]
    with pytest.raises(PublishError, match="needs a semver"):
        repo.upsert_definitions([contract_record(contract_doc)], ctx)


def test__same_hash_on_a_new_commit_only_moves_last_seen_commit(repo, sqlite_engine, contract_doc, make_ctx):
    """An unchanged document on a later commit keeps its version and only updates last_seen_commit."""
    repo.upsert_definitions([contract_record(contract_doc, commit="c1")], make_ctx(commit="c1"))
    repo.upsert_definitions([contract_record(contract_doc, commit="c2")], make_ctx(commit="c2"))
    found = rows(sqlite_engine, "SELECT record_version, introduced_commit, last_seen_commit FROM md_data_contract")
    assert found == [{"record_version": 1, "introduced_commit": "c1", "last_seen_commit": "c2"}]


def test__retire_inserts_a_retired_version_that_stays_current(repo, sqlite_engine, contract_doc, ctx):
    """Retiring a contract creates a new version with status retired that is still the current row."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    repo.retire_definitions("data_contract", [CONTRACT_ID], ctx, git_commit="c9")
    assert versions(sqlite_engine) == [(1, 0, "active"), (2, 1, "retired")]
    shown = rows(
        sqlite_engine, f"SELECT status, retired_commit FROM v_data_contract_current WHERE entity_id = '{CONTRACT_ID}'"
    )
    assert shown == [{"status": "retired", "retired_commit": "c9"}]
    assert repo.list_current_entity_ids("data_contract", SQUAD) == []
    assert repo.list_current_entity_ids("data_contract", SQUAD, include_retired=True) == [CONTRACT_ID]


def test__a_retired_entity_that_reappears_gets_a_new_version(repo, sqlite_engine, contract_doc, ctx):
    """Re-opening a retired contract appends version 3 as active, so history stays continuous."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    repo.retire_definitions("data_contract", [CONTRACT_ID], ctx)
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    assert versions(sqlite_engine) == [(1, 0, "active"), (2, 0, "retired"), (3, 1, "active")]
    reopened = rows(sqlite_engine, "SELECT retired_commit FROM md_data_contract WHERE record_version = 3")[0]
    assert reopened["retired_commit"] is None


def test__retiring_an_unknown_or_foreign_entity_is_refused(repo, contract_doc, ctx, make_ctx):
    """Only the owning repo can retire an entity, and only if it exists."""
    with pytest.raises(PublishError, match="unknown"):
        repo.retire_definitions("data_contract", ["dc_missing"], ctx)
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    with pytest.raises(WriterError, match="owned by repo"):
        repo.retire_definitions("data_contract", [CONTRACT_ID], make_ctx(repo_id=OTHER_SQUAD))


def test__another_repo_cannot_take_over_an_entity(repo, contract_doc, ctx, make_ctx):
    """Publishing an existing entity id under a different repo_id raises WriterError."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    with pytest.raises(WriterError, match="owned by repo"):
        repo.upsert_definitions([contract_record(contract_doc, repo_id=OTHER_SQUAD)], make_ctx(repo_id=OTHER_SQUAD))


def test__there_is_at_most_one_current_row_after_every_publish(repo, sqlite_engine, contract_doc, product_doc, ctx):
    """After a sequence of publish, change, retire and re-open the invariant holds on every table."""
    repo.upsert_definitions([contract_record(contract_doc), product_record(product_doc)], ctx)
    assert_one_current_everywhere(sqlite_engine)
    repo.upsert_definitions([contract_record(changed(contract_doc))], ctx)
    assert_one_current_everywhere(sqlite_engine)
    repo.retire_definitions("data_contract", [CONTRACT_ID], ctx)
    repo.upsert_definitions([contract_record(changed(contract_doc))], ctx)
    assert_one_current_everywhere(sqlite_engine)


def test__a_broken_current_row_invariant_is_detected(repo, sqlite_engine, contract_doc, ctx):
    """If two rows are current (UC does not enforce this), the next publish refuses to continue."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_md_data_contract_current")
        conn.exec_driver_sql(
            "INSERT INTO md_data_contract (entity_id, record_version, repo_id, status, content_hash, payload, valid_from, "
            f"is_current, published_at, published_by) VALUES ('{CONTRACT_ID}', 2, 'x', 'active', 'h', '{{}}', 't', 1, 't', 'x')"
        )
    with pytest.raises(PublishError, match="invariant is broken"):
        repo.upsert_definitions([contract_record(contract_doc)], ctx)


def test__optional_key_parts_use_empty_string_and_a_duplicate_publish_adds_no_rows(
    repo, sqlite_engine, contract_doc, ctx
):
    """Dataset-level quality rules store '' for the property path, and republishing duplicates nothing."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    repo.upsert_definitions([contract_record(contract_doc, commit="c2")], ctx)
    stored = rows(sqlite_engine, "SELECT rule_id, property_path FROM md_quality_rule ORDER BY rule_id")
    assert stored == [
        {"rule_id": "claim_id_not_null", "property_path": "claim_id"},
        {"rule_id": "has_rows", "property_path": ""},
    ]
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_quality_rule")[0]["n"] == 2


def test__a_null_in_a_text_child_column_is_stored_as_the_empty_sentinel(repo, sqlite_engine, contract_doc, ctx):
    """None in any text column of a child row becomes '' so key columns can never hold NULL."""
    record = contract_record(contract_doc)
    children = dict(record.children)
    children["md_contract_server"] = [{"environment": "prod", "type": None, "catalog": "c", "schema": None}]
    repo.upsert_definitions([replace(record, children=children)], ctx)
    found = rows(sqlite_engine, "SELECT type, schema FROM md_contract_server")
    assert found == [{"type": "", "schema": ""}]


@pytest.mark.parametrize(
    "case",
    [("empty key", {"rule_id": ""}), ("no column", {"rule_id": "x", "nope": 1})],
    ids=["empty-key", "unknown-column"],
)
def test__malformed_child_rows_are_rejected_before_writing(repo, sqlite_engine, contract_doc, ctx, case):
    """An empty key column or an unknown column raises PublishError and leaves the database untouched."""
    message, row = case
    record = contract_record(contract_doc)
    bad = replace(record, children={**record.children, "md_quality_rule": [row]})
    with pytest.raises(PublishError, match=message):
        repo.upsert_definitions([bad], ctx)
    assert versions(sqlite_engine) == []


def test__duplicate_child_keys_in_one_record_are_rejected(repo, contract_doc, ctx):
    """Two child rows with the same key in one record raise PublishError."""
    record = contract_record(contract_doc)
    rule = record.children["md_quality_rule"][0]
    bad = replace(record, children={**record.children, "md_quality_rule": [rule, rule]})
    with pytest.raises(PublishError, match="duplicate"):
        repo.upsert_definitions([bad], ctx)


def test__unknown_kinds_columns_and_child_tables_are_rejected(repo, contract_doc, ctx):
    """The repository refuses records that do not match the model."""
    record = contract_record(contract_doc)
    with pytest.raises(SchemaError, match="unknown entity kind"):
        repo.upsert_definitions([replace(record, ref=replace(record.ref, kind="nonsense"))], ctx)
    with pytest.raises(PublishError, match="unknown data_contract column"):
        repo.upsert_definitions([replace(record, columns={"bogus": 1})], ctx)
    with pytest.raises(PublishError, match="unknown child table"):
        repo.upsert_definitions([replace(record, children={"md_asset": []})], ctx)


def test__quality_results_stay_pinned_to_the_rule_version_after_a_later_edit(repo, sqlite_engine, contract_doc, ctx):
    """A result recorded against rule version 1 keeps pointing at version 1 after the contract becomes version 2."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    rule = next(r for r in repo.current_quality_rules(SQUAD) if r.rule_id == "claim_id_not_null")
    assert (rule.record_version, rule.asset_id) == (1, "claims_fraud_ml.fraud_scores")
    counts = RuleCounts(passed=9, failed=1)
    result = QualityResultRecord(
        "dev", "u1", rule.asset_id, rule.rule_id, rule.contract_id, rule.record_version, counts
    )
    repo.record_quality([result], ctx)

    edited = changed(contract_doc)
    edited["schema"][0]["properties"][0]["quality"][0]["rule"] = "claim_id IS NOT NULL AND claim_id <> ''"
    repo.upsert_definitions([contract_record(edited)], ctx)

    assert {r.record_version for r in repo.current_quality_rules(SQUAD)} == {2}
    stored = rows(sqlite_engine, "SELECT rule_record_version FROM md_quality_result")
    assert stored == [{"rule_record_version": 1}]
    pinned = rows(
        sqlite_engine,
        "SELECT r.expression FROM md_quality_result q JOIN md_quality_rule r ON r.contract_id = q.contract_id "
        "AND r.record_version = q.rule_record_version AND r.rule_id = q.rule_id",
    )
    assert pinned == [{"expression": "claim_id IS NOT NULL"}]


def test__tag_publish_adds_owned_keys_and_removes_only_owned_keys(repo, sqlite_engine, ctx):
    """Dropping a tag from the set deactivates it, while a non-owned key already in the table is never touched."""
    domain, product = "data_domain_claims__litigation__claim_litigation", "data_product_claims_fraud_fraud_detection"
    repo.upsert_tags(
        [
            TagRow("asset", "s.t", domain),
            TagRow("asset", "s.t", product),
            TagRow("asset", "s.t", "classification", "internal"),
        ],
        ctx,
    )
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO md_tag (entity_type, entity_id, tag_key, tag_value, repo_id, is_active, first_seen_at, last_seen_at) "
            f"VALUES ('asset', 's.t', 'owner_note', 'keep', '{SQUAD}', 1, 't', 't')"
        )
    repo.upsert_tags([TagRow("asset", "s.t", domain)], ctx)
    state = {row["tag_key"]: row["is_active"] for row in rows(sqlite_engine, "SELECT tag_key, is_active FROM md_tag")}
    assert state == {domain: 1, product: 0, "classification": 0, "owner_note": 1}
    repo.upsert_tags([TagRow("asset", "s.t", product)], ctx)
    reactivated = rows(sqlite_engine, f"SELECT is_active FROM md_tag WHERE tag_key = '{product}'")
    assert reactivated == [{"is_active": 1}]


def test__publishing_a_tag_with_a_non_owned_key_is_refused(repo, ctx):
    """Only owned keys can be published, so a publish can never claim an arbitrary UC tag."""
    with pytest.raises(PublishError, match="not an owned key"):
        repo.upsert_tags([TagRow("asset", "s.t", "pii_note")], ctx)
    repo.upsert_tags([TagRow("project", "claims_fraud", "cost_center", "cc-1")], ctx, required_keys=["cost_center"])


def test__required_project_tag_keys_are_owned_and_removable(repo, sqlite_engine, make_ctx):
    """Project tags are owned through the required keys, so removing one deactivates it."""
    platform = make_ctx(repo_id="_platform")
    rows_in = [TagRow("project", "p", "cost_center", "1"), TagRow("project", "p", "region", "au")]
    repo.upsert_tags(rows_in, platform, required_keys=["cost_center", "region"])
    repo.upsert_tags(rows_in[:1], platform, required_keys=["cost_center", "region"])
    state = {row["tag_key"]: row["is_active"] for row in rows(sqlite_engine, "SELECT tag_key, is_active FROM md_tag")}
    assert state == {"cost_center": 1, "region": 0}


def test__a_repo_cannot_overwrite_another_repos_derived_rows(repo, ctx, make_ctx):
    """A tag or link row keyed like another repo's row is rejected instead of being taken over."""
    repo.upsert_tags([TagRow("asset", "s.t", "classification", "internal")], ctx)
    with pytest.raises(WriterError, match="owned by repo"):
        repo.upsert_tags([TagRow("asset", "s.t", "classification", "restricted")], make_ctx(repo_id=OTHER_SQUAD))


def test__semantic_links_are_a_replace_set(repo, sqlite_engine, ctx):
    """A link missing from the next publish is deactivated, and one that returns is reactivated."""
    repo.upsert_semantic_links(
        [SemanticLinkRow("data_contract", "dc_x", "iri:a"), SemanticLinkRow("data_contract", "dc_x", "iri:b")], ctx
    )
    repo.upsert_semantic_links([SemanticLinkRow("data_contract", "dc_x", "iri:a", "A")], ctx)
    state = rows(sqlite_engine, "SELECT iri, label, is_active FROM md_semantic_link ORDER BY iri")
    assert state == [{"iri": "iri:a", "label": "A", "is_active": 1}, {"iri": "iri:b", "label": "", "is_active": 0}]


def test__lineage_replace_sets_are_scoped_by_origin_and_target(repo, sqlite_engine, ctx):
    """Replacing spec edges never touches observed edges or another target, and the views compare them."""
    spec = [LineageEdge("a.t1", "a.t2", ORIGIN_SPEC, via="spec1"), LineageEdge("a.t2", "a.t3", ORIGIN_SPEC)]
    seen = [LineageEdge("a.t1", "a.t2", ORIGIN_UC_SYSTEM), LineageEdge("a.t1", "a.t9", ORIGIN_UC_SYSTEM)]
    repo.record_lineage(spec, ctx, target="dev", origin=ORIGIN_SPEC)
    repo.record_lineage(seen, ctx, target="dev", origin=ORIGIN_UC_SYSTEM)
    repo.record_lineage(spec, ctx, target="test", origin=ORIGIN_SPEC)
    repo.record_lineage(spec[:1], ctx, target="dev", origin=ORIGIN_SPEC)
    active = rows(
        sqlite_engine,
        "SELECT target, source_asset_id, target_asset_id, origin FROM md_lineage_edge WHERE is_active ORDER BY 1,2,3,4",
    )
    assert [(r["target"], r["target_asset_id"], r["origin"]) for r in active] == [
        ("dev", "a.t2", "spec"),
        ("dev", "a.t2", "uc_system"),
        ("dev", "a.t9", "uc_system"),
        ("test", "a.t2", "spec"),
        ("test", "a.t3", "spec"),
    ]
    assert rows(sqlite_engine, "SELECT target_asset_id, observed FROM v_lineage WHERE target = 'dev'") == [
        {"target_asset_id": "a.t2", "observed": 1}
    ]
    assert rows(sqlite_engine, "SELECT target_asset_id FROM v_lineage_drift") == [{"target_asset_id": "a.t9"}]


def test__lineage_edges_must_match_the_declared_origin(repo, ctx):
    """An edge whose origin differs from the call, or an unknown origin, is rejected."""
    with pytest.raises(PublishError, match="does not have origin"):
        repo.record_lineage([LineageEdge("a", "b", ORIGIN_SPEC)], ctx, target="dev", origin=ORIGIN_UC_SYSTEM)
    with pytest.raises(PublishError, match="unknown lineage origin"):
        repo.record_lineage([], ctx, target="dev", origin="guess")


def test__a_retried_deployment_creates_no_duplicate(repo, sqlite_engine, ctx):
    """Recording the same CI run twice inserts the deployment and its assets only once."""
    assert repo.record_deployment(deployment(), ctx) == 3
    assert repo.record_deployment(deployment(), ctx) == 0
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_deployment")[0]["n"] == 1
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_deployed_asset")[0]["n"] == 2
    assert repo.record_deployment(deployment(attempt=2), ctx) == 3


def test__runs_pipeline_updates_metrics_and_evals_are_idempotent_facts(repo, sqlite_engine, ctx):
    """A run with updates and metrics, plus eval results, inserts once and ignores a replay."""
    started = datetime(2026, 10, 10, 1, 2, 3, tzinfo=timezone(timedelta(hours=10)))
    update = PipelineUpdateRecord(
        "u1", "uc_pipeline/claims_fraud/silver", "COMPLETED", metrics=[DatasetMetricsRecord("s.t", 10, 2, 1, 3)]
    )
    run = RunRecord(
        "dev",
        "r1",
        "uc_job/claims_fraud/main",
        "SUCCESS",
        Timing(started, started + timedelta(minutes=5)),
        RunOrigin("manual"),
        [update],
    )
    subject, gate = EvalSubject("uc_model/claims_fraud/m", "3", "model", "challenger"), EvalGate(
        0.8, ">=", True, "gates.yml"
    )
    evals = [EvalResultRecord("dev", "r1", subject, "auc", 0.91, gate)]
    assert repo.record_run(run, ctx) == 3
    assert repo.record_run(run, ctx) == 0
    assert repo.record_evals(evals, ctx) == 1
    assert repo.record_evals(evals, ctx) == 0
    stored = rows(sqlite_engine, "SELECT started_at FROM md_run")[0]["started_at"]
    assert stored == "2026-10-09T15:02:03.000Z"
    assert rows(sqlite_engine, "SELECT rows_dropped FROM md_dataset_metrics") == [{"rows_dropped": 3}]


def test__registrations_and_publish_log_are_append_only(repo, sqlite_engine, make_ctx):
    """A registration is keyed by project and PR, and each publish status is its own log row."""
    platform = make_ctx(repo_id="_platform")
    registration = RegistrationRecord("claims_fraud", "https://pr/1", requested_by="x")
    assert repo.record_registration([registration], platform) == 1
    assert repo.record_registration([registration], platform) == 0
    entry = PublishLogEntry(make_ctx(), "definitions", "started")
    repo.log_publish(entry)
    repo.log_publish(replace(entry, status="complete", counts=PublishCounts(inserted=2)))
    log = rows(
        sqlite_engine,
        "SELECT status, inserted, started_at IS NOT NULL AS s, finished_at IS NOT NULL AS f FROM md_publish_log ORDER BY status",
    )
    assert log == [
        {"status": "complete", "inserted": 2, "s": 0, "f": 1},
        {"status": "started", "inserted": 0, "s": 1, "f": 0},
    ]


def test__stored_timestamps_are_utc_text_from_the_database_clock(repo, sqlite_engine, contract_doc, ctx, monkeypatch):
    """valid_from, valid_to and published_at are ISO-8601 Z values the database wrote, whatever the local time zone."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        repo.upsert_definitions([contract_record(contract_doc)], ctx)
        repo.upsert_definitions([contract_record(changed(contract_doc))], ctx)
        db_now = rows(sqlite_engine, "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS now")[0]["now"]
    finally:
        monkeypatch.undo()
        time.tzset()
    stored = rows(
        sqlite_engine, "SELECT valid_from, valid_to, published_at FROM md_data_contract ORDER BY record_version"
    )
    pattern = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
    for row in stored:
        for value in row.values():
            assert value is None or pattern.match(value)
    parsed = datetime.fromisoformat(stored[1]["valid_from"].replace("Z", "+00:00"))
    assert abs(parsed - datetime.fromisoformat(db_now.replace("Z", "+00:00"))) < timedelta(seconds=30)
    assert stored[0]["valid_to"] <= stored[1]["valid_from"]
    assert repo.get_current_definition("data_contract", CONTRACT_ID) is not None


def test__no_runner_timestamp_is_bound_into_a_definition_write(sqlite_engine, contract_doc, ctx):
    """The statements that write valid_from, valid_to and published_at use the SQL clock, not a bound value."""
    seen: list[tuple[str, object]] = []

    @event.listens_for(sqlite_engine, "before_cursor_execute")
    def capture(*args):
        seen.append((args[2], args[3]))

    repository = SqlAlchemyRepository(sqlite_engine)
    repository.ensure_schema()
    seen.clear()
    repository.upsert_definitions([contract_record(contract_doc)], ctx)
    repository.upsert_definitions([contract_record(changed(contract_doc))], ctx)
    writes = [(s, p) for s, p in seen if s.startswith(("INSERT INTO md_data_contract ", "UPDATE md_data_contract"))]
    assert len(writes) == 3
    assert all("strftime(" in statement for statement, _ in writes)
    iso = re.compile(r"\d{4}-\d\d-\d\dT")
    for _, parameters in writes:
        assert not any(isinstance(value, str) and iso.match(value) for value in tuple(parameters))


def test__views_hide_children_of_an_unfinished_publish_and_cleanup_removes_them(
    repo, sqlite_engine, contract_doc, ctx
):
    """Children written at version 2 without a parent row (a crash) are invisible in views and cleaned up."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    before = rows(sqlite_engine, "SELECT rule_id, record_version FROM v_quality_rule_current ORDER BY rule_id")
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO md_quality_rule (contract_id, record_version, rule_id, object_name) "
            f"VALUES ('{CONTRACT_ID}', 2, 'orphan_rule', 'fraud_scores')"
        )
        conn.exec_driver_sql(
            "INSERT INTO md_quality_rule (contract_id, record_version, rule_id, object_name) VALUES ('dc_never_published', 1, 'r', 'o')"
        )
    assert rows(sqlite_engine, "SELECT rule_id, record_version FROM v_quality_rule_current ORDER BY rule_id") == before
    assert repo.cleanup_orphans() == 2
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_quality_rule")[0]["n"] == 2
    assert repo.cleanup_orphans() == 0


def test__the_next_publish_reports_the_orphans_it_cleaned(repo, sqlite_engine, contract_doc, ctx):
    """upsert_definitions removes leftover children of its kind first and counts them."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql(
            f"INSERT INTO md_contract_server (contract_id, record_version, environment) VALUES ('{CONTRACT_ID}', 2, 'x')"
        )
    counts = repo.upsert_definitions([contract_record(contract_doc)], ctx)
    assert counts == PublishCounts(unchanged=1, orphans_cleaned=1)


def test__get_current_definition_returns_the_stored_record_with_children(repo, contract_doc, ctx):
    """Reading back gives the payload, the extracted columns and every child row."""
    original = contract_record(contract_doc)
    repo.upsert_definitions([original], ctx)
    stored = repo.get_current_definition("data_contract", CONTRACT_ID)
    assert stored.payload == contract_doc
    assert stored.columns["name"] == "fraud_scores"
    assert len(stored.children["md_contract_property"]) == 6
    assert repo.get_current_definition("data_contract", "dc_missing") is None


def test__org_definitions_and_the_project_directory_view_hide_emails(repo, sqlite_engine, registration_doc, make_ctx):
    """Team, project and children publish for the registry, and the directory view exposes groups but no emails."""
    platform = make_ctx(repo_id="_platform")
    records = registration_records(
        registration_doc, repo_id="_platform", source_path="projects/claims_fraud.yaml", git_commit="r1"
    )
    repo.upsert_definitions(records, platform)
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_project_contact")[0]["n"] == 5
    directory = rows(sqlite_engine, "SELECT * FROM v_project_directory ORDER BY access_role")
    assert [row["access_role"] for row in directory] == ["admins", "developers", "readers"]
    assert directory[0]["ad_group"] == "AAD-DBX-CLAIMSFRAUD-ADMIN"
    assert not any("email" in column for column in directory[0])


def test__purge_removes_only_runtime_facts_past_retention(repo, sqlite_engine, ctx):
    """Runtime facts older than the retention window are deleted; deployments are kept."""
    repo.record_run(RunRecord("dev", "r1", "uc_job/x/y", "SUCCESS"), ctx)
    repo.record_deployment(deployment(), ctx)
    assert repo.purge_runtime(400) == 0
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql("UPDATE md_run SET recorded_at = '2020-01-01T00:00:00.000Z'")
        conn.exec_driver_sql("UPDATE md_deployment SET recorded_at = '2020-01-01T00:00:00.000Z'")
    assert repo.purge_runtime(400) == 1
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_deployment")[0]["n"] == 1


def test__ensure_schema_is_idempotent_and_refuses_a_newer_schema(repo, sqlite_engine):
    """Running ensure_schema again keeps one version row; a newer stored version is refused."""
    repo.ensure_schema()
    assert rows(sqlite_engine, "SELECT version FROM md_schema_version") == [{"version": 1}]
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO md_schema_version (version, applied_at) VALUES (99, 't')")
    with pytest.raises(SchemaError, match="newer"):
        repo.ensure_schema()


def test__only_sqlite_and_postgresql_engines_are_accepted():
    """An engine for another dialect is rejected with a SchemaError."""
    with pytest.raises(SchemaError, match="supports sqlite and postgresql"):
        SqlAlchemyRepository(SimpleNamespace(dialect=SimpleNamespace(name="mysql")))


def test__json_payload_round_trips_on_sqlite(repo, contract_doc, ctx):
    """The payload column returns the same document, so its content hash is unchanged."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    stored = repo.get_current_definition("data_contract", CONTRACT_ID).payload
    assert content_hash(stored) == content_hash(contract_doc)
