"""
Fixtures that build a small but complete squad repository in a temp directory.

The repo has platform snapshots (rendered from the real registry scaffold, so the hash headers are
genuine), one silver `orders` dataflow spec with a schema and expectations, a quarantine table, and
one product that exposes the contract `make contracts` generates for it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbxops.metadata.contracts import write_contracts
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.substitutions import write_substitutions
from dbxops.registry.snapshots import render_snapshots
from tests.consts import PROJECT_DIR

REGISTRY = PROJECT_DIR / "templates/registry-repo"
PROJECT_ID = "example_claims"
CLAIM_DOMAIN = "claims__lodgement_assessment_liability__claim"
LITIGATION_DOMAIN = "claims__litigation__claim_litigation"
PRODUCT_ID = f"dp_{PROJECT_ID}_orders"
CONTRACT_ID = f"dc_{PROJECT_ID}_silver_orders"

ENVIRONMENTS = {
    "workspace": {"host": "https://example.invalid"},
    "targets": {"dev": {"catalog": "dev_cat"}, "prod": {"catalog": "prod_cat"}},
}

SPEC: dict[str, Any] = {
    "data_flow_id": "orders",
    "data_flow_group": "silver",
    "data_flow_type": "nodespec",
    "tags": {"data_domain": CLAIM_DOMAIN, "data_product": PRODUCT_ID},
    "nodes": [
        {
            "name": "v_orders",
            "node_type": "source",
            "source_type": "delta",
            "config": {"mode": "stream", "database": "{bronze_schema}", "table": "orders_raw"},
        },
        {
            "name": "target_orders",
            "node_type": "target",
            "config": {
                "database": "{silver_schema}",
                "table": "orders",
                "schema_path": "orders_schema.json",
                "data_quality": {
                    "enabled": True,
                    "expectations_path": "./orders_dqe.json",
                    "quarantine": {
                        "mode": "table",
                        "target": {"target_format": "delta", "database": "{silver_schema}"},
                    },
                },
                "sources": ["v_orders"],
            },
        },
    ],
}

ORDERS_SCHEMA = {
    "type": "struct",
    "fields": [
        {"name": "order_key", "type": "long", "nullable": False, "metadata": {}},
        {"name": "total_price", "type": "decimal(18,2)", "nullable": True, "metadata": {}},
        {"name": "order_date", "type": "date", "nullable": True, "metadata": {}},
    ],
}

ORDERS_DQE = {
    "expect_or_drop": [{"name": "order_key not null", "constraint": "order_key IS NOT NULL", "tag": "Validity"}],
    "expect": [{"name": "price is non-negative", "constraint": "total_price >= 0", "tag": "Accuracy"}],
}


def registration(kind: str = "project", domain: str = "claims") -> dict[str, Any]:
    """Return the example registration, optionally as a domain repo for `domain`."""
    doc = yaml.safe_load((REGISTRY / "projects/example_claims.yaml").read_text(encoding="utf-8"))
    doc.update(kind=kind, domain=domain)
    return doc


def write_platform(root: Path, kind: str = "project", domain: str = "claims") -> None:
    """Render the four `metadata/_platform` snapshots for a registration into `root`."""
    settings = yaml.safe_load((REGISTRY / "settings.yaml").read_text(encoding="utf-8"))
    domains = (REGISTRY / "domains.yaml").read_text(encoding="utf-8")
    glossary = (PROJECT_DIR / "metadata/_platform/glossary.ttl").read_text(encoding="utf-8")
    out = root / "metadata/_platform"
    out.mkdir(parents=True, exist_ok=True)
    for name, text in render_snapshots(registration(kind, domain), settings, domains, glossary).items():
        (out / name).write_text(text, encoding="utf-8")


def write_json(path: Path, doc: Any) -> None:
    """Write a JSON file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def write_yaml(path: Path, doc: Any) -> None:
    """Write a YAML file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def product(contract_id: str = CONTRACT_ID) -> dict[str, Any]:
    """Return a valid product exposing one contract."""
    return {
        "apiVersion": "v1.0.0",
        "kind": "DataProduct",
        "id": PRODUCT_ID,
        "name": PRODUCT_ID.removeprefix("dp_"),
        "version": "1.0.0",
        "status": "active",
        "domain": "claims",
        "outputPorts": [
            {
                "name": "orders",
                "version": "1.0.0",
                "type": "uc_table",
                "contractId": contract_id,
                "customProperties": [{"property": "dbx.assetId", "value": f"{PROJECT_ID}_silver.orders"}],
            }
        ],
    }


def build_repo(root: Path, kind: str = "project", domain: str = "claims") -> Path:
    """Create a complete valid repo (contracts generated, product written) under `root` and return it."""
    write_platform(root, kind, domain)
    write_yaml(root / "conf/environments.yml", ENVIRONMENTS)
    write_yaml(root / "conf/metastore.yml", {"targets": {"dev": {"sinks": []}}})
    bundle = root / "bundles/dataops/src"
    (bundle / "pipeline_configs").mkdir(parents=True)
    write_substitutions(RepoContext.load(root))
    layer = bundle / "dataflows/silver"
    write_json(layer / "dataflowspec/orders_main.json", SPEC)
    write_json(layer / "schemas/orders_schema.json", ORDERS_SCHEMA)
    write_json(layer / "expectations/orders_dqe.json", ORDERS_DQE)
    write_contracts(RepoContext.load(root))
    write_yaml(bundle / "products/orders.odps.yaml", product())
    return root


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Return a freshly built valid project repo."""
    return build_repo(tmp_path)


@pytest.fixture
def contract_file(repo_root: Path) -> Path:
    """Return the path of the generated `orders` contract in `repo_root`."""
    return repo_root / "bundles/dataops/src/dataflows/silver/contracts/orders.odcs.yaml"


@pytest.fixture
def product_file(repo_root: Path) -> Path:
    """Return the path of the `orders` product in `repo_root`."""
    return repo_root / "bundles/dataops/src/products/orders.odps.yaml"


@pytest.fixture
def real_conf_root(tmp_path: Path) -> Path:
    """Return a temp repo holding a copy of this repo's real conf/ and metadata/_platform, with no bundles."""
    shutil.copytree(PROJECT_DIR / "conf", tmp_path / "conf")
    shutil.copytree(PROJECT_DIR / "metadata/_platform", tmp_path / "metadata/_platform")
    return tmp_path
