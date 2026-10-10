"""Tests for generating and refreshing contracts from specs."""

import json
from pathlib import Path

import pytest
import yaml

from dbxops.metadata.contracts import (
    ASSET_ID,
    contract_id,
    get_custom,
    layer_of,
    plan_contracts,
    slug,
    write_contracts,
)
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.specs import SpecError
from tests.fixtures.metadata_repo import (
    CONTRACT_ID,
    SPEC,
    write_json,
)

SPEC_FILE = "bundles/dataops/src/dataflows/silver/dataflowspec/orders_main.json"
SCHEMA_FILE = "bundles/dataops/src/dataflows/silver/schemas/orders_schema.json"


def load(path: Path) -> dict:
    """Read a YAML file."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test__generated_contract_has_machine_sections_and_placeholder_text(contract_file: Path):
    """A scaffolded contract has the spec-derived sections and PLACEHOLDER text where a person must write."""
    doc = load(contract_file)
    assert (doc["id"], doc["version"], doc["status"]) == (CONTRACT_ID, "1.0.0", "draft")
    assert doc["description"] == {
        "purpose": "PLACEHOLDER-purpose",
        "usage": "PLACEHOLDER-usage",
        "limitations": "PLACEHOLDER-limitations",
    }
    assert [s["environment"] for s in doc["servers"]] == ["dev", "prod"]
    obj = doc["schema"][0]
    assert obj["physicalName"] == "prod_cat.example_claims_silver.orders"
    assert get_custom(obj, ASSET_ID) == "example_claims_silver.orders"
    assert [p["name"] for p in obj["properties"]] == ["order_key", "total_price", "order_date"]


def test__expectations_become_quality_rules_and_drops_become_quarantine(contract_file: Path):
    """Each expectation is a SQL quality rule; with a quarantine table a drop is recorded as a quarantine action."""
    rules = {q["name"]: q for q in load(contract_file)["schema"][0]["quality"]}
    drop = rules["order_key not null"]
    assert drop["query"] == "SELECT COUNT(*) FROM ${table} WHERE NOT (order_key IS NOT NULL)"
    assert (get_custom(drop, "dbx.action"), get_custom(drop, "dbx.quarantine_asset_id")) == (
        "quarantine",
        "example_claims_silver.orders_quarantine",
    )
    assert (rules["price is non-negative"]["severity"], rules["price is non-negative"]["dimension"]) == (
        "warning",
        "accuracy",
    )


def test__regenerating_an_up_to_date_repo_changes_nothing(repo_root: Path):
    """Generation is idempotent: a second run plans no changes and writes nothing."""
    ctx = RepoContext.load(repo_root)
    assert [p.action for p in plan_contracts(ctx)] == ["unchanged"]
    assert write_contracts(ctx) == []


def test__human_fields_survive_a_refresh_and_machine_fields_follow_the_spec(repo_root: Path, contract_file: Path):
    """A spec change updates properties and quality; descriptions, flags and manual rules written by people are kept."""
    doc = load(contract_file)
    doc["description"]["purpose"] = "Orders for claims analytics"
    doc["status"] = "active"
    doc["schema"][0]["properties"][0].update(primaryKey=True, businessName="Order key")
    doc["schema"][0]["quality"].append({"name": "manual check", "type": "text", "description": "reviewed monthly"})
    contract_file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    write_json(
        repo_root / SCHEMA_FILE,
        {
            "type": "struct",
            "fields": [
                {"name": "order_key", "type": "string", "nullable": False},
                {"name": "channel", "type": "string"},
            ],
        },
    )
    changed = write_contracts(RepoContext.load(repo_root))
    assert [p.action for p in changed] == ["update"]
    after = load(contract_file)
    assert after["description"]["purpose"] == "Orders for claims analytics" and after["status"] == "active"
    props = {p["name"]: p for p in after["schema"][0]["properties"]}
    assert list(props) == ["order_key", "channel"]
    assert (
        props["order_key"]["primaryKey"],
        props["order_key"]["businessName"],
        props["order_key"]["physicalType"],
    ) == (True, "Order key", "string")
    assert "manual check" in [q["name"] for q in after["schema"][0]["quality"]]


def test__dropping_an_expectation_removes_its_rule(repo_root: Path, contract_file: Path):
    """A rule whose expectation was deleted from the spec disappears from the contract."""
    path = repo_root / "bundles/dataops/src/dataflows/silver/expectations/orders_dqe.json"
    write_json(
        path, {"expect": [{"name": "price is non-negative", "constraint": "total_price >= 0", "tag": "Accuracy"}]}
    )
    write_contracts(RepoContext.load(repo_root))
    assert [q["name"] for q in load(contract_file)["schema"][0]["quality"]] == ["price is non-negative"]


def test__a_new_spec_target_gets_a_scaffolded_contract(repo_root: Path):
    """A second table written by a spec is given a new contract at <layer>/contracts/<table>.odcs.yaml."""
    spec = json.loads(json.dumps(SPEC))
    spec["data_flow_id"] = "customers"
    spec["nodes"][1]["config"].update(table="customers", schema_path="orders_schema.json")
    spec["nodes"][1]["config"]["data_quality"] = {"enabled": False}
    write_json(repo_root / "bundles/dataops/src/dataflows/silver/dataflowspec/customers_main.json", spec)
    created = [p for p in write_contracts(RepoContext.load(repo_root)) if p.action == "create"]
    assert [p.path.name for p in created] == ["customers.odcs.yaml"]
    doc = load(created[0].path)
    assert doc["id"] == contract_id("example_claims", "silver", "customers") and "quality" not in doc["schema"][0]


def test__scaffolding_needs_domain_and_product_tags(repo_root: Path):
    """Without `tags.data_domain`/`tags.data_product` on the spec a contract cannot be scaffolded."""
    spec = json.loads(json.dumps(SPEC))
    spec["data_flow_id"] = "customers"
    spec["tags"] = {}
    spec["nodes"][1]["config"]["table"] = "customers"
    write_json(repo_root / "bundles/dataops/src/dataflows/silver/dataflowspec/customers_main.json", spec)
    with pytest.raises(SpecError, match="must define 'data_domain'"):
        plan_contracts(RepoContext.load(repo_root))


def test__a_target_without_a_schema_is_an_error(repo_root: Path):
    """A contract needs a schema file; a target without `schema_path` is reported with the spec path."""
    spec = json.loads(json.dumps(SPEC))
    del spec["nodes"][1]["config"]["schema_path"]
    write_json(repo_root / SPEC_FILE, spec)
    with pytest.raises(SpecError, match="has no schema_path"):
        plan_contracts(RepoContext.load(repo_root))


def test__small_helpers():
    """Slugs, ids and layers follow the naming rules."""
    assert slug("order_key NOT null!") == "order_key_not_null"
    assert contract_id("p", "silver", "t") == "dc_p_silver_t"
    assert (layer_of("p_silver", "p"), layer_of("other", "p")) == ("silver", "other")


def test__a_contract_scaffolded_in_a_restricted_domain_is_classified_restricted(repo_root: Path):
    """New contracts in a Restricted data domain are created already classified, so the restricted rule passes."""
    spec = json.loads(json.dumps(SPEC))
    spec["data_flow_id"] = "claims_lit"
    spec["tags"]["data_domain"] = "claims__litigation__claim_litigation"
    spec["nodes"][1]["config"].update(table="litigation", schema_path="orders_schema.json")
    spec["nodes"][1]["config"]["data_quality"] = {"enabled": False}
    write_json(repo_root / "bundles/dataops/src/dataflows/silver/dataflowspec/lit_main.json", spec)
    created = [p for p in write_contracts(RepoContext.load(repo_root)) if p.path.name == "litigation.odcs.yaml"]
    assert get_custom(load(created[0].path), "classification") == "restricted"
    assert (
        get_custom(
            load(repo_root / "bundles/dataops/src/dataflows/silver/contracts/orders.odcs.yaml"), "classification"
        )
        is None
    )
