"""Fixtures for the metadata database: an in-memory SQLite repository, ODCS/ODPS documents and a recording executor."""

import copy
import json
import re
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterator,
)

import pytest
from sqlalchemy import (
    Engine,
    create_engine,
)

from dbxops.metadata.repo import SpecInfo
from dbxops.metadata.specs import (
    load_specs,
    load_tokens,
)
from dbxops.metastore.collectors import (
    SEMANTIC_ASSIGNMENT,
    contract_records,
    dataflow_spec_records,
    product_records,
)
from dbxops.metastore.records import (
    CiContext,
    DatasetMetricsRecord,
    DefinitionRecord,
    DeployedAssetRecord,
    DeploymentRecord,
    PublishContext,
    RuleCounts,
    Timing,
)
from dbxops.metastore.runtime_collectors import (
    ExpectationInfo,
    PipelineUpdateInfo,
)
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository

SQUAD = "claims_fraud"
OTHER_SQUAD = "policy_core"
CONTRACT_ID = "dc_claims_fraud_fraud_scores"
PRODUCT_ID = "dp_claims_fraud_fraud_detection"
IRI = "https://example.org/insurance#Claim"

CONTRACT_DOC: dict[str, Any] = {
    "apiVersion": "v3.1.0",
    "kind": "DataContract",
    "id": CONTRACT_ID,
    "version": "1.0.0",
    "status": "active",
    "name": "fraud_scores",
    "domain": "claims__litigation__claim_litigation",
    "dataProduct": PRODUCT_ID,
    "description": {"purpose": "Score claims", "usage": "BI", "limitations": "Synthetic"},
    "servers": [
        {
            "server": "prod",
            "type": "databricks",
            "environment": "prod",
            "catalog": "prod_cat",
            "schema": "claims_fraud_ml",
        }
    ],
    "authoritativeDefinitions": [{"type": SEMANTIC_ASSIGNMENT, "url": IRI}],
    "schema": [
        {
            "name": "fraud_scores",
            "physicalName": "prod_cat.claims_fraud_ml.fraud_scores",
            "physicalType": "table",
            "businessName": "Fraud scores",
            "authoritativeDefinitions": [{"type": SEMANTIC_ASSIGNMENT, "url": IRI + "Score"}],
            "quality": [
                {
                    "name": "has_rows",
                    "type": "sql",
                    "dimension": "completeness",
                    "query": "SELECT count(*) > 0 FROM fraud_scores",
                    "customProperties": [{"property": "dbx.action", "value": "warn"}],
                }
            ],
            "properties": [
                {
                    "name": "claim_id",
                    "logicalType": "string",
                    "physicalType": "STRING",
                    "required": True,
                    "primaryKey": True,
                    "primaryKeyPosition": 1,
                    "classification": "internal",
                    "authoritativeDefinitions": [{"type": SEMANTIC_ASSIGNMENT, "url": IRI + "Id"}],
                    "quality": [
                        {
                            "name": "claim_id_not_null",
                            "type": "library",
                            "metric": "nullValues",
                            "rule": "claim_id IS NOT NULL",
                            "dimension": "completeness",
                            "customProperties": [
                                {"property": "dbx.action", "value": "drop"},
                                {"property": "dbx.origin", "value": "expectation"},
                                {"property": "dbx.spec_id", "value": "spec_scores"},
                            ],
                        }
                    ],
                },
                {"name": "score", "logicalType": "number", "physicalType": "DOUBLE"},
                {
                    "name": "holder",
                    "logicalType": "object",
                    "properties": [
                        {
                            "name": "email",
                            "logicalType": "string",
                            "classification": "restricted",
                            "customProperties": [{"property": "pii", "value": True}],
                        }
                    ],
                },
                {"name": "labels", "logicalType": "array", "items": {"logicalType": "string"}},
            ],
        }
    ],
}

PRODUCT_DOC: dict[str, Any] = {
    "apiVersion": "v1.0.0",
    "kind": "DataProduct",
    "id": PRODUCT_ID,
    "name": "claims_fraud_fraud_detection",
    "version": "1.0.0",
    "status": "active",
    "domain": "claims__litigation__claim_litigation",
    "inputPorts": [{"name": "policies", "version": "1.0.0", "contractId": "dc_policy_core_policy"}],
    "outputPorts": [
        {
            "name": "scores",
            "version": "1.0.0",
            "contractId": CONTRACT_ID,
            "containsPii": False,
            "inputContracts": [{"id": "dc_policy_core_policy", "version": "1.0.0"}],
        },
        {
            "name": "scorer",
            "version": "1.0.0",
            "assetType": "serving_endpoint",
            "assetId": "serving_endpoint/claims_fraud/fraud_scorer",
            "customProperties": [{"property": "dbx.assetSubtype", "value": "custom_model"}],
        },
    ],
}

REGISTRATION_DOC: dict[str, Any] = {
    "project_id": "claims_fraud",
    "kind": "project",
    "squad": {"name": "claims_fraud_squad", "title": "Claims Fraud Squad", "github_team": "claims-fraud"},
    "domain": "claims",
    "contacts": {
        "technical_owner": "a@corp.com",
        "data_owner": "b@corp.com",
        "executive_owner": "c@corp.com",
        "data_steward": "d@corp.com",
        "support": {"email": "support@corp.com", "channel": "https://teams.example/ch"},
    },
    "groups": {
        "developers": "AAD-DBX-CLAIMSFRAUD-DEV",
        "readers": "AAD-DBX-CLAIMSFRAUD-READ",
        "admins": "AAD-DBX-CLAIMSFRAUD-ADMIN",
    },
    "service_principal": {"application_id": "sp-1"},
    "tags": {"cost_center": "cc-1", "region": "au"},
    "repo": {"name": "dbx-claims-fraud"},
}


@pytest.fixture
def contract_doc() -> dict[str, Any]:
    """Return a fresh ODCS contract document that tests can modify."""
    return copy.deepcopy(CONTRACT_DOC)


@pytest.fixture
def product_doc() -> dict[str, Any]:
    """Return a fresh ODPS product document that tests can modify."""
    return copy.deepcopy(PRODUCT_DOC)


@pytest.fixture
def registration_doc() -> dict[str, Any]:
    """Return a fresh registry registration document that tests can modify."""
    return copy.deepcopy(REGISTRATION_DOC)


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """Return an in-memory SQLite engine."""
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


@pytest.fixture
def repo(sqlite_engine: Engine) -> SqlAlchemyRepository:
    """Return a SQLite-backed repository with the schema created."""
    repository = SqlAlchemyRepository(sqlite_engine)
    repository.ensure_schema()
    return repository


@pytest.fixture
def make_ctx() -> Callable[..., PublishContext]:
    """Return a factory for publish contexts."""

    def build(repo_id: str = SQUAD, commit: str | None = "c1", target: str = "dev") -> PublishContext:
        return PublishContext(repo_id=repo_id, git_commit=commit, target=target, published_by="tester")

    return build


@pytest.fixture
def ctx(make_ctx: Callable[..., PublishContext]) -> PublishContext:
    """Return the publish context of the example squad."""
    return make_ctx()


def contract_record(doc: dict[str, Any], repo_id: str = SQUAD, commit: str | None = "c1") -> DefinitionRecord:
    """Return the contract record of ``doc``."""
    return contract_records(doc, repo_id=repo_id, source_path="contracts/x.odcs.yaml", git_commit=commit)[0]


def product_record(doc: dict[str, Any], repo_id: str = SQUAD, commit: str | None = "c1") -> DefinitionRecord:
    """Return the product record of ``doc``."""
    return product_records(doc, repo_id=repo_id, source_path="products/x.odps.yaml", git_commit=commit)[0]


_CLASSIFIERS = (
    (r"^SET TIME ZONE", lambda m: "session"),
    (r"^DELETE FROM `[^`]+`\.`[^`]+`\.`(\w+)` WHERE NOT EXISTS", lambda m: f"orphans:{m.group(1)}"),
    (r"^SELECT entity_id, record_version, repo_id", lambda m: "current"),
    (r"^SELECT count\(\*\)", lambda m: "invariant"),
    (r"^SELECT .*t\.repo_id FROM .* JOIN \(VALUES", lambda m: "foreign-check"),
    (r"^INSERT INTO `[^`]+`\.`[^`]+`\.`(\w+)`", lambda m: f"insert:{m.group(1)}"),
    (r"^MERGE INTO `[^`]+`\.`[^`]+`\.`(\w+)` AS t USING \(SELECT k\.merge_key", lambda m: f"scd2-merge:{m.group(1)}"),
    (r"^MERGE INTO `[^`]+`\.`[^`]+`\.`(\w+)`", lambda m: f"merge:{m.group(1)}"),
    (r"^UPDATE `[^`]+`\.`[^`]+`\.`(\w+)`", lambda m: f"update:{m.group(1)}"),
    (r"^CREATE OR REPLACE VIEW", lambda m: "view"),
    (r"^CREATE", lambda m: "create"),
    (r"^ALTER TABLE", lambda m: "alter"),
)


def classify(sql: str) -> str:
    """Return a short tag describing the shape of a recorded statement."""
    for pattern, tag in _CLASSIFIERS:
        found = re.match(pattern, sql)
        if found:
            return tag(found)
    return "other:" + sql[:40]


class RecordingExecutor:
    """A ``SqlExecutor`` that records statements and answers from scripted responses; nothing is executed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._scripts: list[dict[str, Any]] = []

    def on(self, pattern: str, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        """Answer statements matching ``pattern`` with ``rows``, or raise ``error``; the first script wins."""
        self._scripts.append({"pattern": re.compile(pattern), "rows": rows or [], "error": error})

    def execute(self, sql: str, params: Any = None) -> list[dict]:
        """Record the statement and return the scripted rows."""
        self.calls.append((sql, dict(params or {})))
        for script in self._scripts:
            if script["pattern"].search(sql):
                if script["error"] is not None:
                    raise script["error"]
                return script["rows"]
        if "count(*) AS n" in sql:
            return [{"n": 1}]
        return []

    @property
    def tags(self) -> list[str]:
        """Return the classification of every recorded statement, in order."""
        return [classify(sql) for sql, _ in self.calls]

    def clear(self) -> None:
        """Forget the recorded statements (scripts stay)."""
        self.calls.clear()


@pytest.fixture
def executor() -> RecordingExecutor:
    """Return a fresh recording executor."""
    return RecordingExecutor()


SPEC_JSON: dict[str, Any] = {
    "data_flow_id": "spec_scores",
    "data_flow_group": "silver",
    "data_flow_type": "nodespec",
    "nodes": [
        {
            "name": "v_x",
            "node_type": "source",
            "source_type": "delta",
            "config": {"database": "{silver_schema}", "table": "x"},
        },
        {"name": "v_files", "node_type": "source", "source_type": "cloud_files", "config": {"path": "/Volumes/c/s/v"}},
        {
            "name": "t_y",
            "node_type": "target",
            "config": {
                "database": "{silver_schema}",
                "table": "y",
                "schema_path": "y_schema.json",
                "sources": ["v_x"],
            },
        },
        {
            "name": "t_z",
            "node_type": "target",
            "config": {"database": "{silver_schema}", "table": "z", "sources": ["v_files"]},
        },
    ],
}


def write_spec_bundle(root: Path) -> Path:
    """Write a one-spec bundle under ``root/bundles/dataops`` and return the spec file's path."""
    src = root / "bundles/dataops/src"
    (src / "pipeline_configs").mkdir(parents=True)
    (src / "pipeline_configs/dev_substitutions.json").write_text(
        json.dumps({"tokens": {"silver_schema": f"main.{SQUAD}_silver{{logical_env}}"}}), encoding="utf-8"
    )
    spec = src / "dataflows/silver/dataflowspec/scores_main.json"
    spec.parent.mkdir(parents=True)
    spec.write_text(json.dumps(SPEC_JSON), encoding="utf-8")
    return spec


def spec_info_for_tests(root: Path) -> SpecInfo:
    """Return the parsed example spec of ``write_spec_bundle`` as the loaders produce it."""
    spec = write_spec_bundle(root)
    bundle = root / "bundles/dataops"
    return SpecInfo(spec=load_specs(spec)[0], bundle_dir=bundle, tokens=load_tokens(bundle))


def dataflow_record_for_tests(root: Path) -> DefinitionRecord:
    """Return the dataflow spec record of the example spec: one table-to-table flow and one flow without a table source."""
    return dataflow_spec_records(spec_info_for_tests(root), repo_id=SQUAD, source_path="s.json", git_commit="c1")[0]


def deployment(run_id: str = "123", attempt: int = 1, assets: int = 2) -> DeploymentRecord:
    """Return a deployment of the example bundle with ``assets`` deployed assets."""
    deployed = [
        DeployedAssetRecord("uc_job/claims_fraud/main", 1, "main job", "11"),
        DeployedAssetRecord("uc_pipeline/claims_fraud/silver", 2, "silver", "22"),
    ][:assets]
    return DeploymentRecord("dev", "dataops", CiContext(run_id, attempt, git_sha="abc"), assets=deployed)


class FakeJobs:
    """A Jobs API stand-in that returns one finished run."""

    def get_run(self, run_id):
        """Return a successful run."""
        return {
            "run_id": int(run_id),
            "job_id": 7,
            "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
            "start_time": 1_760_000_000_000,
            "end_time": 1_760_000_060_000,
            "trigger": "PERIODIC",
            "git_source": {"git_commit": "abc123"},
        }


class FakeEventLog:
    """An event-log stand-in with one pipeline update, one mapped and one unmapped expectation."""

    def updates_for_run(self, _run):
        """Return one pipeline update on the example table."""
        table = "main.claims_fraud_ml.fraud_scores"
        return [
            PipelineUpdateInfo(
                "u1",
                "uc_pipeline/claims_fraud/silver",
                "COMPLETED",
                True,
                Timing(started_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                datasets=[DatasetMetricsRecord(table, 10, 1, 2, 3)],
                expectations=[
                    ExpectationInfo(table, "claim_id_not_null", RuleCounts(8, 2, 2, 0)),
                    ExpectationInfo(table, "unknown_expectation", RuleCounts(1)),
                ],
            )
        ]
