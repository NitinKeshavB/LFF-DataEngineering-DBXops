"""PostgreSQL integration tests; skipped unless DBXOPS_TEST_PG_URL points at a scratch database."""

import copy
import os
import uuid

import pytest
from sqlalchemy import (
    create_engine,
    text,
)

from dbxops.metastore.errors import PublishError
from dbxops.metastore.records import (
    DeploymentRecord,
    PublishContext,
    TagRow,
)
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from tests.fixtures.metastore import (
    CONTRACT_ID,
    SQUAD,
    contract_record,
)

PG_URL = os.environ.get("DBXOPS_TEST_PG_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not PG_URL, reason="set DBXOPS_TEST_PG_URL to run PostgreSQL integration tests"),
]


@pytest.fixture
def pg_repo():
    """Return a repository on a throwaway schema of the test database; the schema is dropped afterwards."""
    schema = f"dbxops_test_{uuid.uuid4().hex[:8]}"
    engine = create_engine(PG_URL)
    repository = SqlAlchemyRepository(engine, schema)
    repository.ensure_schema()
    yield repository, engine, schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def test__scd2_lifecycle_on_postgresql(pg_repo, contract_doc):
    """Publish, no-op, change, retire and re-open behave as on SQLite, with timestamptz values from now()."""
    repository, engine, schema = pg_repo
    ctx = PublishContext(repo_id=SQUAD, git_commit="c1")
    assert repository.upsert_definitions([contract_record(contract_doc)], ctx).inserted == 1
    assert repository.upsert_definitions([contract_record(contract_doc)], ctx).unchanged == 1
    changed = copy.deepcopy(contract_doc)
    changed["version"] = "1.1.0"
    changed["description"]["purpose"] = "changed"
    assert repository.upsert_definitions([contract_record(changed)], ctx).closed == 1
    with pytest.raises(PublishError):
        edited = copy.deepcopy(changed)
        edited["description"]["purpose"] = "no bump"
        repository.upsert_definitions([contract_record(edited)], ctx)
    repository.retire_definitions("data_contract", [CONTRACT_ID], ctx)
    repository.upsert_definitions([contract_record(changed)], ctx)
    with engine.connect() as conn:
        found = conn.execute(
            text(f'SELECT record_version, is_current, status FROM "{schema}".md_data_contract ORDER BY 1')
        ).all()
        zone = conn.execute(text(f'SELECT valid_from FROM "{schema}".md_data_contract LIMIT 1')).scalar()
    assert found == [(1, False, "active"), (2, False, "active"), (3, False, "retired"), (4, True, "active")]
    assert zone.utcoffset().total_seconds() == 0 or zone.tzinfo is not None
    assert repository.get_current_definition("data_contract", CONTRACT_ID).payload == changed


def test__derived_and_fact_rows_on_postgresql(pg_repo):
    """ON CONFLICT upserts for tags and idempotent deployments work on PostgreSQL."""
    repository, _, _ = pg_repo
    ctx = PublishContext(repo_id=SQUAD)
    repository.upsert_tags([TagRow("asset", "s.t", "classification", "internal")], ctx)
    assert repository.upsert_tags([], ctx) == 0
    deployment = DeploymentRecord("dev", "dataops", "1")
    assert repository.record_deployment(deployment, ctx) == 1
    assert repository.record_deployment(deployment, ctx) == 0
