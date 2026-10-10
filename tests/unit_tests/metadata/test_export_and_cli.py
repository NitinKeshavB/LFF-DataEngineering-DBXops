"""Tests for the Ontos export and the metadata command line."""

import json
from dataclasses import replace
from importlib import resources
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft201909Validator

from dbxops.metadata.__main__ import main
from dbxops.metadata.export_ontos import (
    ExportError,
    export_ontos,
)
from dbxops.metadata.repo import RepoContext
from tests.fixtures.metadata_repo import (
    CONTRACT_ID,
    PRODUCT_ID,
    write_json,
    write_yaml,
)


def schema(name: str) -> Draft201909Validator:
    """Return a validator for one of the packaged Bitol schemas."""
    text = (resources.files("dbxops.metadata") / "schemas" / name).read_text(encoding="utf-8")
    return Draft201909Validator(json.loads(text))


def load(path: Path) -> dict:
    """Read a YAML file."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test__export_writes_contracts_products_and_the_glossary(repo_root: Path, tmp_path: Path):
    """One file per contract and product, as .yaml, plus the glossary as Turtle without its hash header."""
    out = tmp_path / "out"
    paths = export_ontos(RepoContext.load(repo_root), "dev", out)
    assert sorted(p.relative_to(out).as_posix() for p in paths) == [
        f"contracts/{CONTRACT_ID}.yaml",
        f"products/{PRODUCT_ID}.yaml",
        "semantic-models/example_claims.ttl",
    ]
    assert not (out / "semantic-models/example_claims.ttl").read_text(encoding="utf-8").startswith("# machine-owned")


def test__an_exported_contract_is_resolved_for_the_target_and_carries_team_and_tags(repo_root: Path, tmp_path: Path):
    """Only the target's server remains, the physical name uses the target catalog, and owners and Ontos tags are added."""
    export_ontos(RepoContext.load(repo_root), "dev", tmp_path)
    doc = load(tmp_path / f"contracts/{CONTRACT_ID}.yaml")
    assert [s["environment"] for s in doc["servers"]] == ["dev"]
    assert doc["schema"][0]["physicalName"] == "dev_cat.example_claims_silver.orders"
    assert [m["role"] for m in doc["team"]] == ["technical_owner", "data_owner", "executive_owner", "data_steward"]
    assert doc["tags"] == [
        "data_contract_example_claims_silver_orders",
        "data_domain_claims__lodgement_assessment_liability__claim",
    ]
    assert not list(schema("odcs-json-schema-v3.1.0.json").iter_errors(doc))


def test__an_exported_product_has_team_support_a_tag_and_generic_ports_for_non_ontos_assets(
    repo_root: Path, product_file: Path, tmp_path: Path
):
    """Ports whose type Ontos does not know become `generic` with the original kept as dbx.assetSubtype."""
    doc = load(product_file)
    doc["outputPorts"].append({"name": "endpoint", "version": "1.0.0", "type": "serving_endpoint"})
    write_yaml(product_file, doc)
    export_ontos(RepoContext.load(repo_root), "dev", tmp_path)
    out = load(tmp_path / f"products/{PRODUCT_ID}.yaml")
    table, endpoint = out["outputPorts"]
    assert table["type"] == "uc_table"
    assert endpoint["type"] == "generic"
    assert endpoint["customProperties"] == [{"property": "dbx.assetSubtype", "value": "serving_endpoint"}]
    assert out["tags"] == ["data_product_example_claims_orders"]
    assert out["team"]["name"] == "example_claims_squad" and len(out["team"]["members"]) == 4
    assert [s["channel"] for s in out["support"]] == ["email", "chat"]
    assert not list(schema("odps-json-schema-v1.0.0.json").iter_errors(out))


def test__export_is_idempotent_and_does_not_modify_the_sources(repo_root: Path, contract_file: Path, tmp_path: Path):
    """Exporting twice gives identical files and leaves the authored contract untouched."""
    before = contract_file.read_text(encoding="utf-8")
    ctx = RepoContext.load(repo_root)
    export_ontos(ctx, "dev", tmp_path / "a")
    export_ontos(ctx, "dev", tmp_path / "b")
    assert (tmp_path / "a" / f"contracts/{CONTRACT_ID}.yaml").read_text() == (
        tmp_path / "b" / f"contracts/{CONTRACT_ID}.yaml"
    ).read_text()
    assert contract_file.read_text(encoding="utf-8") == before


def test__an_unknown_target_lists_the_known_ones(repo_root: Path, tmp_path: Path):
    """Exporting for a target that is not in conf/environments.yml is an error naming the valid targets."""
    with pytest.raises(ExportError, match="unknown target 'qa'; known: dev, prod"):
        export_ontos(RepoContext.load(repo_root), "qa", tmp_path)


def test__export_needs_exactly_one_squad_in_the_platform_snapshot(repo_root: Path, tmp_path: Path):
    """A repo whose teams snapshot lists no squad cannot be exported (the team would be empty)."""
    ctx = RepoContext.load(repo_root)
    ctx.platform = replace(ctx.platform, teams={"teams": []})
    with pytest.raises(ExportError, match="exactly one squad, found 0"):
        export_ontos(ctx, "dev", tmp_path)


def test__cli_validate_passes_a_good_repo_and_fails_a_bad_one(repo_root: Path, contract_file: Path, capsys):
    """`validate` prints a summary and exits 0; with a broken contract it prints the finding and exits 1."""
    assert main(["--root", str(repo_root), "validate"]) == 0
    assert "1 contracts, 1 products, 1 specs: 0 error(s)" in capsys.readouterr().out
    contract_file.unlink()
    assert main(["--root", str(repo_root), "validate"]) == 1
    assert "[spec-coverage]" in capsys.readouterr().out


def test__cli_contracts_check_reports_drift_and_the_plain_run_fixes_it(repo_root: Path, capsys):
    """`contracts --check` exits 1 when a contract would change, and `contracts` rewrites it."""
    write_json(
        repo_root / "bundles/dataops/src/dataflows/silver/schemas/orders_schema.json",
        {"type": "struct", "fields": [{"name": "k", "type": "long"}]},
    )
    assert main(["--root", str(repo_root), "contracts", "--check"]) == 1
    assert "would update" in capsys.readouterr().out
    assert main(["--root", str(repo_root), "contracts"]) == 0
    assert main(["--root", str(repo_root), "contracts", "--check"]) == 0
    assert "contracts are up to date" in capsys.readouterr().out


def test__cli_contracts_reports_a_spec_error_without_a_traceback(repo_root: Path, capsys):
    """A spec that cannot be read is printed as `error: ...` with exit 1."""
    (repo_root / "bundles/dataops/src/dataflows/silver/schemas/orders_schema.json").write_text("{", encoding="utf-8")
    assert main(["--root", str(repo_root), "contracts"]) == 1
    assert "error:" in capsys.readouterr().out


def test__cli_export_writes_to_build_ontos_by_default(repo_root: Path, capsys):
    """`export-ontos --target dev` writes under build/ontos/dev and says how many files."""
    assert main(["--root", str(repo_root), "export-ontos", "--target", "dev"]) == 0
    assert (repo_root / "build/ontos/dev/contracts" / f"{CONTRACT_ID}.yaml").is_file()
    assert "wrote 3 files" in capsys.readouterr().out
