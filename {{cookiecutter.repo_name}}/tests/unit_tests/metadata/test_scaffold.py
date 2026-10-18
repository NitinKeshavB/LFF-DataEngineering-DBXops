"""Tests for scaffolding a new bundle: it must be valid, consistent with conf/, and blocked from deploy until filled in."""

import json
from pathlib import Path

import pytest
import yaml

from dbxops.config import load_repo_config
from dbxops.findings import has_errors
from dbxops.metadata.__main__ import main
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import validate
from dbxops.metadata.scaffold import (
    CONNECTOR_KINDS,
    ScaffoldError,
    scaffold_bundle,
)
from dbxops.registry.domains import DomainTaxonomy
from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import load_bundle


def data_domain_of_this_repo() -> str:
    """Return a data domain a bundle in this repository may use: the first one within the registered domain."""
    taxonomy = DomainTaxonomy.from_yaml(PROJECT_DIR / "metadata/_platform/domains.yaml")
    registered = load_repo_config(PROJECT_DIR / "metadata/_platform/repo.yaml").domain
    return next(node.path for node in taxonomy.nodes("data_domain") if taxonomy.is_within(node.path, registered))


DOMAIN = data_domain_of_this_repo()


def test__a_scaffolded_bundle_passes_every_metadata_rule(real_conf_root: Path):
    """The new bundle's specs, generated contract, product, substitutions and config copies are all consistent."""
    files = scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    findings = validate(RepoContext.load(real_conf_root))
    assert not has_errors(findings), [str(f) for f in findings]
    names = {p.relative_to(real_conf_root).as_posix() for p in files}
    assert "bundles/sandbox/databricks.yml" in names
    assert "bundles/sandbox/src/dataflows/silver/contracts/example.odcs.yaml" in names
    assert "bundles/sandbox/src/pipeline_configs/prod_substitutions.json" in names


def test__the_bundle_follows_conf_for_targets_catalogs_and_permissions(real_conf_root: Path):
    """Targets, catalogs, framework path, run-as and AD group permissions come from conf/ and the registration."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    doc = load_bundle(real_conf_root / "bundles/sandbox")
    env = yaml.safe_load((real_conf_root / "conf/environments.yml").read_text())
    assert list(doc["targets"]) == list(env["targets"])
    for name, target in doc["targets"].items():
        assert target["variables"]["catalog"] == env["targets"][name]["catalog"]
        assert target["variables"]["framework_source_path"].endswith(f"/{name}/{env['framework_version']}/files/src")
        assert ("run_as" in target) == env["targets"][name]["run_as_sp"]
    assert {p["level"] for p in doc["permissions"]} == {"CAN_MANAGE", "CAN_RUN", "CAN_VIEW"}
    assert doc["variables"]["project_id"]["default"] == "example_claims"


def test__the_bundle_validates_against_the_cli_schema(real_conf_root: Path, cli_bundle_validator):
    """The generated databricks.yml and resources satisfy the Databricks CLI's own bundle schema."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    errors = list(cli_bundle_validator.iter_errors(load_bundle(real_conf_root / "bundles/sandbox")))
    assert not errors, [e.message[:160] for e in errors]


def test__the_job_records_its_run_last_and_the_spec_carries_domain_and_product(real_conf_root: Path):
    """The example job ends with publish_run_metadata, and the spec's tags name the domain and the product."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    job = yaml.safe_load((real_conf_root / "bundles/sandbox/resources/sandbox.jobs.yml").read_text())["resources"][
        "jobs"
    ]
    tasks = job["sandbox_run"]["tasks"]
    assert [t["task_key"] for t in tasks] == ["silver", "publish_run_metadata"] and tasks[-1]["run_if"] == "ALL_DONE"
    spec = json.loads(
        (real_conf_root / "bundles/sandbox/src/dataflows/silver/dataflowspec/example_main.json").read_text()
    )
    assert spec["tags"] == {"data_domain": DOMAIN, "data_product": "dp_example_claims_sandbox"}


def test__people_must_fill_in_the_descriptions_before_a_deploy(real_conf_root: Path):
    """The contract and product carry PLACEHOLDER text, which `check-placeholders --strict` reports."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    contract = (real_conf_root / "bundles/sandbox/src/dataflows/silver/contracts/example.odcs.yaml").read_text()
    product = (real_conf_root / "bundles/sandbox/src/products/sandbox.odps.yaml").read_text()
    assert "PLACEHOLDER-purpose" in contract and "PLACEHOLDER-purpose" in product


@pytest.mark.parametrize(
    "name, domain, message",
    [
        ("Bad Name", DOMAIN, "must match"),
        ("ab", DOMAIN, "must match"),
        ("sandbox", "claims__litigaton__claim_litigation", "did you mean"),
        ("sandbox", "claims", "a data_domain path is required"),
    ],
)
def test__invalid_names_and_domains_are_rejected_before_anything_is_written(
    real_conf_root: Path, name, domain, message
):
    """A bad name, a misspelt domain or a non-data-domain path stops the scaffold and leaves no bundle behind."""
    with pytest.raises(ScaffoldError, match=message):
        scaffold_bundle(real_conf_root, name, domain)
    assert not (real_conf_root / "bundles").exists()


def test__an_existing_bundle_is_never_overwritten(real_conf_root: Path):
    """Scaffolding the same name twice fails the second time and leaves the first untouched."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    before = (real_conf_root / "bundles/sandbox/databricks.yml").read_text()
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    assert (real_conf_root / "bundles/sandbox/databricks.yml").read_text() == before


def test__cli_new_bundle_reports_success_and_errors(real_conf_root: Path, capsys):
    """`new-bundle` prints what to do next on success and `error: ...` with exit 1 on a bad domain."""
    assert main(["--root", str(real_conf_root), "new-bundle", "--name", "sandbox", "--data-domain", DOMAIN]) == 0
    assert "created bundles/sandbox" in capsys.readouterr().out
    assert main(["--root", str(real_conf_root), "new-bundle", "--name", "other", "--data-domain", "nope"]) == 1
    assert "error:" in capsys.readouterr().out


def test__a_scaffolded_bundle_tags_its_jobs_and_pipelines_for_governance(real_conf_root: Path):
    """Registry tags, provenance from the bundle's git substitutions and the workload reach every job and pipeline."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    doc = load_bundle(real_conf_root / "bundles/sandbox")
    expected = {"project_id", "bundle", "target", "workload", "git_repo", "git_ref", "git_sha"}
    tagged = [*doc["resources"]["jobs"].values(), *doc["resources"]["pipelines"].values()]
    assert tagged and all(expected <= set(item["tags"]) for item in tagged)
    assert doc["variables"]["workload"]["default"] == "de"


@pytest.mark.parametrize("workload", ["de", "ml", "ai", "platform"])
def test__a_scaffolded_bundle_declares_the_workload_it_was_asked_for(real_conf_root: Path, workload: str):
    """`make new-bundle ... WORKLOAD=` becomes the bundle's workload variable, and the bundle still validates."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN, workload)
    assert load_bundle(real_conf_root / "bundles/sandbox")["variables"]["workload"]["default"] == workload
    assert not has_errors(validate(RepoContext.load(real_conf_root)))


def test__an_unknown_workload_is_refused(real_conf_root: Path):
    """Only de, ml, ai and platform are workloads."""
    with pytest.raises(ScaffoldError, match="workload"):
        scaffold_bundle(real_conf_root, "sandbox", DOMAIN, "etl")


def _task(tasks: list, key: str) -> dict:
    return next(t for t in tasks if t["task_key"] == key)


@pytest.mark.parametrize("connector", CONNECTOR_KINDS)
def test__a_connector_option_adds_a_land_task_that_runs_dbxops_land(real_conf_root: Path, connector: str):
    """With a connector, the job gains a land_<name> task running dbxops-land for that connector."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN, connector=connector)
    tasks = load_bundle(real_conf_root / "bundles/sandbox")["resources"]["jobs"]["sandbox_run"]["tasks"]
    assert {t["task_key"] for t in tasks} == {"land_sandbox", "silver", "publish_run_metadata"}
    land = _task(tasks, "land_sandbox")
    assert land["python_wheel_task"]["entry_point"] == "dbxops-land"
    assert land["python_wheel_task"]["parameters"][:2] == ["--connector", connector]


@pytest.mark.parametrize("connector", CONNECTOR_KINDS)
def test__a_connector_option_keeps_the_bundle_valid_and_waited_on(
    real_conf_root: Path, connector: str, cli_bundle_validator
):
    """publish_run_metadata waits for the land task too, and the bundle still validates with a connector."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN, connector=connector)
    doc = load_bundle(real_conf_root / "bundles/sandbox")
    publish = _task(doc["resources"]["jobs"]["sandbox_run"]["tasks"], "publish_run_metadata")
    assert {d["task_key"] for d in publish["depends_on"]} == {"silver", "land_sandbox"}
    assert not has_errors(validate(RepoContext.load(real_conf_root)))
    assert not list(cli_bundle_validator.iter_errors(doc))


@pytest.mark.parametrize("connector", CONNECTOR_KINDS)
def test__a_connector_option_writes_a_placeholder_config_for_dbxops_land(real_conf_root: Path, connector: str):
    """The land task's config file is PLACEHOLDER-blocked until a person fills in the real source."""
    scaffold_bundle(real_conf_root, "sandbox", DOMAIN, connector=connector)
    config = yaml.safe_load((real_conf_root / "bundles/sandbox/src/config/land_sandbox.yml").read_text())
    assert any("PLACEHOLDER-" in str(value) for value in config.values())


def test__no_connector_leaves_the_job_exactly_as_before(real_conf_root: Path):
    """Without a connector, the job keeps its original two tasks and no landing config is written."""
    files = scaffold_bundle(real_conf_root, "sandbox", DOMAIN)
    assert not any("land_" in p.name for p in files)
    assert not (real_conf_root / "bundles/sandbox/src/config/land_sandbox.yml").exists()


def test__an_unknown_connector_is_refused(real_conf_root: Path):
    """Only sftp, xlsx and zip are offered; jdbc needs a hand-wired JdbcSource, so it is not one of them."""
    with pytest.raises(ScaffoldError, match="connector"):
        scaffold_bundle(real_conf_root, "sandbox", DOMAIN, connector="jdbc")
    assert not (real_conf_root / "bundles").exists()


def test__cli_new_bundle_accepts_the_connector_flag(real_conf_root: Path, capsys):
    """`new-bundle --connector` reaches scaffold_bundle and the land config lands on disk."""
    assert (
        main(
            [
                "--root",
                str(real_conf_root),
                "new-bundle",
                "--name",
                "sandbox",
                "--data-domain",
                DOMAIN,
                "--connector",
                "zip",
            ]
        )
        == 0
    )
    assert "created bundles/sandbox" in capsys.readouterr().out
    assert (real_conf_root / "bundles/sandbox/src/config/land_sandbox.yml").exists()
