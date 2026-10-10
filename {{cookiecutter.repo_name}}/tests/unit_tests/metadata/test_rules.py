"""Tests for the metadata validation rule chain: the valid repo is clean, and each rule catches its own breakage."""

import copy
import subprocess
from pathlib import Path
from typing import Any

import yaml

from dbxops.findings import (
    Finding,
    has_errors,
)
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import (
    GitBaseline,
    Options,
    validate,
)
from tests.fixtures.metadata_repo import (
    CONTRACT_ID,
    LITIGATION_DOMAIN,
    PROJECT_ID,
    SPEC,
    build_repo,
    product,
    write_json,
    write_platform,
    write_yaml,
)

SILVER = "bundles/dataops/src/dataflows/silver"
SUBSTITUTIONS = "bundles/dataops/src/pipeline_configs/dev_substitutions.json"


def check(root: Path, options: Options | None = None) -> list[Finding]:
    """Load the repo and run every rule."""
    return validate(RepoContext.load(root), options)


def errors(root: Path, rule: str, options: Options | None = None) -> list[str]:
    """Return the messages of the error findings of one rule."""
    return [f.message for f in check(root, options) if f.rule == rule and f.severity == "error"]


def edit_yaml(path: Path, change) -> None:
    """Load a YAML file, apply `change(doc)`, and write it back."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(doc)
    write_yaml(path, doc)


def test__a_generated_repo_has_no_errors(repo_root: Path):
    """The repo `make contracts` produces passes every rule (PLACEHOLDER text is for check-placeholders, not here)."""
    assert not has_errors(check(repo_root))


def test__the_json_schema_rule_reports_the_path_of_each_violation(
    repo_root: Path, contract_file: Path, product_file: Path
):
    """A contract without `status` and a product with a bad `kind` are reported against their files."""
    edit_yaml(contract_file, lambda d: d.pop("status"))
    edit_yaml(product_file, lambda d: d.update(kind="Dataset"))
    messages = errors(repo_root, "json-schema")
    assert any("'status' is a required property" in m for m in messages)
    assert any("kind: 'Dataset' is not one of ['DataProduct']" in m for m in messages)


def test__only_the_pinned_standard_versions_are_accepted(repo_root: Path, contract_file: Path, product_file: Path):
    """ODCS v3.0.2 is a valid older version for the schema, but the platform pins v3.1.0 (and ODPS v1.0.0)."""
    edit_yaml(contract_file, lambda d: d.update(apiVersion="v3.0.2"))
    edit_yaml(product_file, lambda d: d.update(apiVersion="v0.9.0"))
    assert errors(repo_root, "api-version") == ["apiVersion must be v3.1.0", "apiVersion must be v1.0.0"]


def test__ids_need_the_project_prefix_to_be_unique_and_a_product_name_matches_its_id(
    repo_root: Path, contract_file: Path, product_file: Path
):
    """A foreign-prefixed contract id, a duplicate product id and a mismatched product name are each caught."""
    edit_yaml(contract_file, lambda d: d.update(id="dc_other_silver_orders"))
    edit_yaml(product_file, lambda d: d.update(name="orders"))
    write_yaml(product_file.parent / "copy.odps.yaml", {**product(), "name": product()["name"]})
    messages = errors(repo_root, "id")
    assert any("must start with 'dc_example_claims_'" in m for m in messages)
    assert any("duplicate id 'dp_example_claims_orders'" in m for m in messages)
    assert any("product name must equal its id" in m for m in messages)


def test__a_misspelt_contract_domain_suggests_the_closest_path(repo_root: Path, contract_file: Path):
    """The domain error carries the 'did you mean' suggestion from the taxonomy."""
    edit_yaml(contract_file, lambda d: d.update(domain="claims__litigation__claim_litigaton"))
    assert any("did you mean: claims__litigation__claim_litigation" in m for m in errors(repo_root, "domain"))


def test__a_contract_domain_must_be_a_data_domain_not_a_business_domain(repo_root: Path, contract_file: Path):
    """`claims` is a business domain; a contract has to name one of its data domains."""
    edit_yaml(contract_file, lambda d: d.update(domain="claims"))
    assert any("a data_domain path is required" in m for m in errors(repo_root, "domain"))


def test__a_domain_repo_keeps_contracts_and_products_inside_its_domain(tmp_path: Path):
    """In a domain repo, a contract outside the repo's domain and a product of another domain are rejected."""
    root = build_repo(tmp_path, kind="domain", domain="claims")
    assert not has_errors(check(root))
    contract = root / f"{SILVER}/contracts/orders.odcs.yaml"
    edit_yaml(contract, lambda d: d.update(domain="customer__party_profile__client"))
    edit_yaml(root / "bundles/dataops/src/products/orders.odps.yaml", lambda d: d.update(domain="policy"))
    messages = errors(root, "domain")
    assert any("is outside this domain repo's 'claims'" in m for m in messages)
    assert any("product domain 'policy' must equal the repo domain 'claims'" in m for m in messages)


def test__a_contract_in_a_restricted_domain_must_say_so(repo_root: Path, contract_file: Path):
    """Litigation is Restricted, so the contract must carry classification: restricted; once it does, it passes."""
    edit_yaml(contract_file, lambda d: d.update(domain=LITIGATION_DOMAIN))
    assert any("is Restricted" in m for m in errors(repo_root, "restricted"))
    edit_yaml(
        contract_file, lambda d: d.update(customProperties=[{"property": "classification", "value": "restricted"}])
    )
    assert errors(repo_root, "restricted") == []


def test__a_spec_target_without_a_contract_is_reported_with_the_fix(repo_root: Path, contract_file: Path):
    """Deleting the contract leaves the target uncovered, and the message says to run `make contracts`."""
    contract_file.unlink()
    messages = errors(repo_root, "spec-coverage")
    assert any(
        "example_claims_silver.orders has 0 contracts; expected exactly 1 (run: make contracts)" in m for m in messages
    )


def test__a_contract_without_a_spec_target_is_an_orphan(repo_root: Path, contract_file: Path):
    """A contract whose table no spec writes, and a second contract for the same table, are both caught."""
    doc = yaml.safe_load(contract_file.read_text(encoding="utf-8"))
    orphan = copy.deepcopy(doc)
    orphan["id"] = f"dc_{PROJECT_ID}_silver_ghost"
    orphan["schema"][0]["customProperties"][0]["value"] = f"{PROJECT_ID}_silver.ghost"
    write_yaml(contract_file.parent / "ghost.odcs.yaml", orphan)
    duplicate = {**doc, "id": f"dc_{PROJECT_ID}_silver_orders_two"}
    write_yaml(contract_file.parent / "orders_two.odcs.yaml", duplicate)
    messages = errors(repo_root, "spec-coverage")
    assert any("contract for example_claims_silver.ghost has no spec target" in m for m in messages)
    assert any("example_claims_silver.orders has 2 contracts" in m for m in messages)


def test__schemas_must_be_the_project_id_plus_a_known_layer(repo_root: Path):
    """A spec that writes to a schema outside `<project_id>_<layer>` breaks the naming rule."""
    write_json(
        repo_root / SUBSTITUTIONS,
        {"tokens": {"bronze_schema": f"dev_cat.{PROJECT_ID}_bronze", "silver_schema": "dev_cat.shared_stuff"}},
    )
    assert any("schema 'shared_stuff' must be <project_id>_<layer>" in m for m in errors(repo_root, "spec-coverage"))


def test__an_unknown_substitution_token_is_reported_not_raised(repo_root: Path):
    """A spec using an undefined token yields a finding naming the token, instead of crashing the run."""
    spec = copy.deepcopy(SPEC)
    spec["nodes"][1]["config"]["database"] = "{nope_schema}"
    write_json(repo_root / f"{SILVER}/dataflowspec/orders_main.json", spec)
    assert any("unknown substitution token {nope_schema}" in m for m in errors(repo_root, "spec-coverage"))
    assert any("cannot compute contracts" in m for m in errors(repo_root, "drift"))


def test__editing_the_spec_without_regenerating_is_drift(repo_root: Path):
    """After the schema file changes, the contract no longer matches and the finding says to run `make contracts`."""
    write_json(
        repo_root / f"{SILVER}/schemas/orders_schema.json",
        {"type": "struct", "fields": [{"name": "k", "type": "long"}]},
    )
    assert any("contract is out of date" in m and "run: make contracts" in m for m in errors(repo_root, "drift"))


def test__every_contract_is_exposed_by_exactly_one_product_port(repo_root: Path, product_file: Path):
    """A contract with no product port, or exposed twice, is caught; a port to a missing contract too."""
    edit_yaml(product_file, lambda d: d["outputPorts"].append({**d["outputPorts"][0], "name": "again"}))
    assert any(f"{CONTRACT_ID} is exposed by 2 output ports" in m for m in errors(repo_root, "product"))
    edit_yaml(
        product_file,
        lambda d: d["outputPorts"].__setitem__(
            slice(None), [{**d["outputPorts"][0], "contractId": "dc_example_claims_silver_nope"}]
        ),
    )
    messages = errors(repo_root, "product")
    assert any("contract 'dc_example_claims_silver_nope' is not in this repo" in m for m in messages)
    assert any(f"{CONTRACT_ID} is exposed by 0 output ports" in m for m in messages)


def test__input_ports_must_name_a_contract_and_external_ones_warn_offline(repo_root: Path, product_file: Path):
    """A malformed input port is an error; a well-formed id from another repo is only a warning (checked at publish)."""
    edit_yaml(
        product_file,
        lambda d: d.update(
            inputPorts=[
                {"name": "bad", "version": "1.0.0", "contractId": "orders"},
                {"name": "ext", "version": "1.0.0", "contractId": "dc_other_gold_x"},
            ]
        ),
    )
    assert any("contractId must be a contract id" in m for m in errors(repo_root, "product"))
    warnings = [f for f in check(repo_root) if f.rule == "product" and f.severity == "warning"]
    assert [w.message for w in warnings] == [
        "input port 'ext': dc_other_gold_x is not in this repo; verified when published"
    ]


def test__a_project_repo_reading_another_projects_data_needs_an_input_port(repo_root: Path, product_file: Path):
    """Reading `other_bronze` from a project repo requires the product to declare an input port."""
    write_json(
        repo_root / SUBSTITUTIONS,
        {"tokens": {"bronze_schema": "dev_cat.other_bronze", "silver_schema": f"dev_cat.{PROJECT_ID}_silver"}},
    )
    assert any("reads other_bronze.orders_raw from another project" in m for m in errors(repo_root, "product"))
    edit_yaml(
        product_file,
        lambda d: d.update(
            inputPorts=[{"name": "bronze", "version": "1.0.0", "contractId": "dc_other_bronze_orders_raw"}]
        ),
    )
    assert not any("another project" in m for m in errors(repo_root, "product"))


def definition(url: str) -> list[dict[str, str]]:
    """Return a semantic-assignment authoritativeDefinitions list pointing at `url`."""
    return [{"type": "http://databricks.com/ontology/uc/semanticAssignment", "url": url}]


def test__semantic_assignments_must_be_glossary_concepts(repo_root: Path, contract_file: Path):
    """An IRI not in the glossary is rejected; one that is passes."""
    known = "https://PLACEHOLDER-ontology-host/insurance#Claim"
    edit_yaml(contract_file, lambda d: d.update(authoritativeDefinitions=definition(known)))
    assert errors(repo_root, "semantics") == []
    edit_yaml(
        contract_file,
        lambda d: d["schema"][0]["properties"][0].update(
            authoritativeDefinitions=definition("https://PLACEHOLDER-ontology-host/insurance#Nonsense")
        ),
    )
    assert any("insurance#Nonsense' is not a concept" in m for m in errors(repo_root, "semantics"))


def test__editing_a_platform_snapshot_by_hand_is_detected(repo_root: Path):
    """A change to metadata/_platform/repo.yaml without regenerating its hash fails the platform rule."""
    path = repo_root / "metadata/_platform/repo.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("kind: project", "kind: domain"), encoding="utf-8")
    assert any("edited by hand" in m for m in errors(repo_root, "platform"))


def test__a_repo_whose_domain_is_not_in_the_taxonomy_is_rejected(tmp_path: Path):
    """A snapshot naming an unknown home domain is reported by the repo rule."""
    root = build_repo(tmp_path)
    write_platform(root, "project", "claimz")
    assert any("domain 'claimz' is not in domains.yaml" in m for m in errors(root, "repo"))


def _baseline(old_contract: dict[str, Any]):
    return lambda path: old_contract if path.endswith("orders.odcs.yaml") else None


def test__changed_content_needs_a_higher_semver(repo_root: Path, contract_file: Path):
    """The same content passes; changed content with the same version fails; a bumped version passes."""
    old = yaml.safe_load(contract_file.read_text(encoding="utf-8"))
    options = Options(baseline=_baseline(old))
    assert errors(repo_root, "semver", options) == []
    edit_yaml(contract_file, lambda d: d.update(status="active"))
    assert any("content changed but version is still 1.0.0" in m for m in errors(repo_root, "semver", options))
    edit_yaml(contract_file, lambda d: d.update(version="1.0.1"))
    assert errors(repo_root, "semver", options) == []


def test__removing_or_retyping_a_column_needs_a_major_bump(repo_root: Path, contract_file: Path):
    """A minor bump is not enough when a column was removed; a major bump is. A malformed version is reported."""
    old = yaml.safe_load(contract_file.read_text(encoding="utf-8"))
    options = Options(baseline=_baseline(old))
    edit_yaml(contract_file, lambda d: (d["schema"][0]["properties"].pop(), d.update(version="1.1.0")))
    assert any("needs a MAJOR version bump" in m for m in errors(repo_root, "semver", options))
    edit_yaml(contract_file, lambda d: d.update(version="2.0.0"))
    assert errors(repo_root, "semver", options) == []
    edit_yaml(contract_file, lambda d: d.update(version="two"))
    assert any("not MAJOR.MINOR.PATCH" in m for m in errors(repo_root, "semver", options))


def test__git_baseline_reads_a_file_as_it_was_at_a_ref(tmp_path: Path):
    """Reading through GitBaseline returns the committed document, and None for a path that did not exist at the ref."""

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    write_yaml(tmp_path / "a.yaml", {"version": "1.0.0"})
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "x")
    write_yaml(tmp_path / "a.yaml", {"version": "9.9.9"})
    baseline = GitBaseline(tmp_path, "HEAD")
    assert baseline("a.yaml") == {"version": "1.0.0"}
    assert baseline("missing.yaml") is None


def test__a_contract_for_a_table_a_job_writes_is_not_an_orphan(repo_root: Path, contract_file: Path):
    """A hand-authored contract marked `dbx.producer: job` has no spec target and that is allowed; unmarked, it is not."""
    doc = yaml.safe_load(contract_file.read_text(encoding="utf-8"))
    scores = copy.deepcopy(doc)
    scores["id"] = f"dc_{PROJECT_ID}_silver_scores"
    scores["schema"][0]["customProperties"] = [{"property": "dbx.asset_id", "value": f"{PROJECT_ID}_silver.scores"}]
    path = contract_file.parent / "scores.odcs.yaml"
    write_yaml(path, scores)
    assert any(
        "contract for example_claims_silver.scores has no spec target" in m for m in errors(repo_root, "spec-coverage")
    )
    scores["schema"][0]["customProperties"].append({"property": "dbx.producer", "value": "job"})
    write_yaml(path, scores)
    assert not any("scores" in m for m in errors(repo_root, "spec-coverage"))
