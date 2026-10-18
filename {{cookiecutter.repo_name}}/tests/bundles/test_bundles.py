"""
Every bundle under bundles/ agrees with conf/, the platform snapshots and its own dataflow specs.

These are the offline stand-ins for `databricks bundle validate`, which needs a workspace. They check the
bundle configuration against the CLI's own JSON schema, and that the names used in different files line up.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from dbxops.findings import has_errors
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import validate
from dbxops.metastore.cli import main as metastore_main
from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import (
    bundle_dirs,
    load_bundle,
)

ENVIRONMENTS = yaml.safe_load((PROJECT_DIR / "conf/environments.yml").read_text(encoding="utf-8"))
TEAM = yaml.safe_load((PROJECT_DIR / "metadata/_platform/teams.yaml").read_text(encoding="utf-8"))["teams"][0]
REPO = yaml.safe_load((PROJECT_DIR / "metadata/_platform/repo.yaml").read_text(encoding="utf-8"))
UPSTREAM_VALIDATOR = PROJECT_DIR / "framework/lakeflow_framework/scripts/validate_dataflows.py"
BUNDLES = [pytest.param(path, id=path.name) for path in bundle_dirs()]
GOVERNANCE_TAGS = {
    "cost_center",
    "region",
    "project_id",
    "bundle",
    "target",
    "workload",
    "git_repo",
    "git_ref",
    "git_sha",
}
WORKLOADS = {"dataops": "de", "mlops": "ml", "aiops": "ai"}


def unbracket(value: str) -> str:
    """Return a placeholder without the angle brackets used inside quoted config values."""
    return value.replace("<", "").replace(">", "")


def test__there_is_at_least_one_bundle():
    """The parametrised tests below would silently test nothing if bundle discovery broke."""
    assert [p.name for p in bundle_dirs()]


@pytest.mark.parametrize("bundle", BUNDLES)
def test__the_bundle_is_valid_against_the_cli_schema(bundle: Path, cli_bundle_validator):
    """databricks.yml plus its resource files satisfy the Databricks CLI's bundle JSON schema."""
    errors = sorted(cli_bundle_validator.iter_errors(load_bundle(bundle)), key=lambda e: list(e.absolute_path))
    assert not errors, [f"{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}" for e in errors]


@pytest.mark.parametrize("bundle", BUNDLES)
def test__targets_catalogs_hosts_and_framework_paths_follow_conf(bundle: Path):
    """The bundle has exactly conf/environments.yml's targets, each with the same catalog, host and framework path."""
    targets = load_bundle(bundle)["targets"]
    assert list(targets) == list(ENVIRONMENTS["targets"])
    for name, target in targets.items():
        assert target["variables"]["catalog"] == ENVIRONMENTS["targets"][name]["catalog"], name
        assert target["workspace"]["host"] == ENVIRONMENTS["workspace"]["host"], name
        assert target["mode"] == ENVIRONMENTS["targets"][name]["mode"], name
        expected = f"/Workspace/Shared/.bundle/lakeflow_framework/{name}/{ENVIRONMENTS['framework_version']}/files/src"
        assert target["variables"]["framework_source_path"] == expected, name
        assert ("run_as" in target) == ENVIRONMENTS["targets"][name]["run_as_sp"], name


@pytest.mark.parametrize("bundle", BUNDLES)
def test__project_identity_permissions_and_tags_match_the_platform_snapshot(bundle: Path):
    """project_id, AD group permissions, tag defaults and the run-as principal come from the registration."""
    doc = load_bundle(bundle)
    assert doc["variables"]["project_id"]["default"] == REPO["project_id"]
    assert {p["group_name"]: p["level"] for p in doc["permissions"]} == {
        TEAM["groups"]["admins"]: "CAN_MANAGE",
        TEAM["groups"]["developers"]: "CAN_RUN",
        TEAM["groups"]["readers"]: "CAN_VIEW",
    }
    prod = doc["targets"]["prod"]
    assert unbracket(prod["variables"]["cost_center"]) == REPO["tags"]["cost_center"]
    assert unbracket(prod["variables"]["region"]) == REPO["tags"]["region"]
    assert (
        unbracket(prod["variables"]["service_principal_application_id"]) == REPO["service_principal"]["application_id"]
    )


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_pipeline_filters_one_flow_group_and_every_flow_group_has_a_pipeline(bundle: Path):
    """A pipeline's dataFlowGroupFilter names a real flow group, and no group is left without a pipeline."""
    ctx = RepoContext.load(PROJECT_DIR)
    groups = {info.spec.flow_group for info in ctx.specs if info.bundle_dir == bundle}
    pipelines = load_bundle(bundle)["resources"]["pipelines"]
    filters = {p["configuration"]["pipeline.dataFlowGroupFilter"] for p in pipelines.values()}
    assert filters == groups


@pytest.mark.parametrize("bundle", BUNDLES)
def test__jobs_only_reference_pipelines_the_bundle_defines_and_are_tagged(bundle: Path):
    """Pipeline tasks point at defined pipelines, every task's dependencies exist, and jobs and pipelines carry tags."""
    resources = load_bundle(bundle)["resources"]
    for job in resources["jobs"].values():
        keys = {t["task_key"] for t in job["tasks"]}
        assert set(job["tags"]) == GOVERNANCE_TAGS
        for task in job["tasks"]:
            assert {d["task_key"] for d in task.get("depends_on", [])} <= keys
            if "pipeline_task" in task:
                name = re.fullmatch(
                    r"\$\{resources\.pipelines\.(\w+)\.id\}", task["pipeline_task"]["pipeline_id"]
                ).group(1)
                assert name in resources["pipelines"]
    assert all(set(p["tags"]) == GOVERNANCE_TAGS for p in resources["pipelines"].values())


@pytest.mark.parametrize("bundle", BUNDLES)
def test__the_notification_destinations_are_placeholders_until_set(bundle: Path):
    """The destination ids and the alert address are bundle variables that stay tracked placeholders."""
    variables = load_bundle(bundle)["variables"]
    assert variables["teams_destination_id"]["default"] == "PLACEHOLDER-teams-destination-id"
    assert variables["email_destination_id"]["default"] == "PLACEHOLDER-email-destination-id"
    assert variables["alert_email"]["default"] == "PLACEHOLDER-alert-email"


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_job_notifies_teams_and_email_and_warns_on_duration(bundle: Path):
    """Each job has email and webhook notifications for failure and a duration rule, wired to bundle variables."""
    for job in load_bundle(bundle)["resources"]["jobs"].values():
        email, webhook = job["email_notifications"], job["webhook_notifications"]
        assert email["on_failure"] == ["${var.alert_email}"]
        assert [item["id"] for item in webhook["on_failure"]] == [
            "${var.teams_destination_id}",
            "${var.email_destination_id}",
        ]
        assert (
            "on_duration_warning_threshold_exceeded" in email and "on_duration_warning_threshold_exceeded" in webhook
        )
        rule = job["health"]["rules"][0]
        assert (rule["metric"], rule["value"]) == ("RUN_DURATION_SECONDS", "${var.duration_warning_seconds}")


@pytest.mark.parametrize("bundle", BUNDLES)
def test__the_workload_variable_matches_the_bundle_family(bundle: Path):
    """A bundle declares its workload once (de, ml or ai) and every job and pipeline tag takes it from there."""
    loaded = load_bundle(bundle)
    assert loaded["variables"]["workload"]["default"] == WORKLOADS[bundle.name]
    tagged = [*loaded["resources"]["jobs"].values(), *loaded["resources"]["pipelines"].values()]
    assert all(item["tags"]["workload"] == "${var.workload}" for item in tagged)


def test__the_repository_metadata_is_valid():
    """All contracts, products, specs, substitutions and snapshots in this repo pass every metadata rule (make validate-metadata)."""
    findings = validate(RepoContext.load(PROJECT_DIR))
    assert not has_errors(findings), [str(f) for f in findings if f.severity == "error"]


@pytest.mark.skipif(
    not BUNDLES or not UPSTREAM_VALIDATOR.exists(),
    reason="no bundle yet (run make new-bundle), or the framework submodule is not checked out",
)
def test__every_spec_passes_the_frameworks_own_validator():
    """The pinned framework's validate_dataflows.py accepts every dataflow spec under bundles/ (make validate-specs)."""
    result = subprocess.run(
        [sys.executable, "-I", str(UPSTREAM_VALIDATOR), str(PROJECT_DIR / "bundles")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-500:]
    assert "Failed: 0" in re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)


@pytest.mark.parametrize("bundle", BUNDLES)
def test__wheel_tasks_name_an_installed_package_and_a_real_entry_point(bundle: Path):
    """Every python_wheel_task uses this package's distribution name and a console script it declares."""
    project = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for job in load_bundle(bundle)["resources"]["jobs"].values():
        for task in job["tasks"]:
            wheel = task.get("python_wheel_task")
            if wheel:
                assert wheel["package_name"] == project["name"]
                assert wheel["entry_point"] in project["scripts"]
                assert task["environment_key"] in {e["environment_key"] for e in job["environments"]}


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_job_records_its_run_after_everything_else_even_on_failure(bundle: Path):
    """Each job ends with a publish_run_metadata task that runs whatever happened before it."""
    for job in load_bundle(bundle)["resources"]["jobs"].values():
        publish = next(t for t in job["tasks"] if t["task_key"] == "publish_run_metadata")
        assert publish["run_if"] == "ALL_DONE"
        assert not any(publish["task_key"] in [d["task_key"] for d in t.get("depends_on", [])] for t in job["tasks"])


def test__the_dataops_table_tags_file_matches_the_contracts(capsys):
    """bundles/dataops/src/config/table_tags.yml is what `make sync-table-tags BUNDLE=dataops` generates."""
    assert metastore_main(["tags-plan", "--root", str(PROJECT_DIR), "--bundle", "dataops"]) == 0
    committed = (PROJECT_DIR / "bundles/dataops/src/config/table_tags.yml").read_text(encoding="utf-8")
    assert capsys.readouterr().out == committed, "out of date (run: make sync-table-tags BUNDLE=dataops)"


def test__table_tags_are_applied_in_dev_only_and_planned_everywhere_else():
    """The dataops job runs the tag task after gold; only the dev target turns the dry run off."""
    doc = load_bundle(PROJECT_DIR / "bundles/dataops")
    task = next(t for t in doc["resources"]["jobs"]["dataops_run"]["tasks"] if t["task_key"] == "apply_table_tags")
    assert task["depends_on"] == [{"task_key": "gold"}]
    assert task["python_wheel_task"]["parameters"][-2:] == ["--dry-run", "${var.table_tags_dry_run}"]
    assert doc["variables"]["table_tags_dry_run"]["default"] == "true"
    applied = [
        name for name, target in doc["targets"].items() if target["variables"].get("table_tags_dry_run") == "false"
    ]
    assert applied == ["dev"]
