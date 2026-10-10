"""
Turn a GitHub issue form (onboard or change a project) into a registration file.

The issue body is untrusted input. It is only ever parsed here, as data: every value is checked against the
registration schema before anything is written, and the workflow passes the body in a file, never through a shell.

GitHub renders an issue form as markdown: one `### <label>` heading per field followed by the answer, or
`_No response_` when left empty. `FIELDS` maps those labels to registration paths and is tied to
`.github/ISSUE_TEMPLATE/*.yml` by a test.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from dbxops.findings import Finding
from dbxops.registry.registration import (
    GROUP_ROLES,
    RegistryContext,
    group_name,
    load_context,
    validate_registration,
)

NO_RESPONSE = "_No response_"
SP_PLACEHOLDER = "PLACEHOLDER-project-sp-application-id"
FIELDS = {
    "Project id": "project_id",
    "Repository kind": "kind",
    "Squad name": "squad_name",
    "Squad title": "squad_title",
    "GitHub team": "github_team",
    "Data domain": "domain",
    "Technical owner email": "technical_owner",
    "Data owner email": "data_owner",
    "Executive owner email": "executive_owner",
    "Data steward email": "data_steward",
    "Support email": "support_email",
    "Support channel": "support_channel",
    "Service principal application id": "sp",
    "Tags": "tags",
    "Repository name": "repo_name",
}
REQUIRED_FIELDS = tuple(
    label for label in FIELDS if label not in ("Service principal application id", "Repository name")
)
_HEADING = re.compile(r"^### (.+?)\s*$", re.MULTILINE)
_TAG_LINE = re.compile(r"^([a-z][a-z0-9_]*)\s*:\s*(.+)$")


class IntakeError(ValueError):
    """Raised when an issue cannot be turned into a valid registration; the message lists every problem."""


def parse_issue_form(body: str) -> dict[str, str]:
    """Return `{label: answer}` for each `### label` section of a rendered issue form; empty answers are ''."""
    matches = list(_HEADING.finditer(body))
    fields = {}
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        start = match.end()
        answer = body[start:end].strip()
        fields[match.group(1)] = "" if answer == NO_RESPONSE else answer
    return fields


def _parse_tags(text: str) -> tuple[dict[str, str], list[str]]:
    tags: dict[str, str] = {}
    problems: list[str] = []
    for line in filter(None, (raw.strip() for raw in text.splitlines())):
        match = _TAG_LINE.match(line)
        if match:
            tags[match.group(1)] = match.group(2).strip()
        else:
            problems.append(f"Tags: {line!r} is not 'key: value' with a lowercase key")
    return tags, problems


def registration_from_form(fields: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Build a registration document from form answers; also return the problems found while reading them."""
    problems = [f"{label}: required" for label in REQUIRED_FIELDS if not fields.get(label)]
    values = {key: fields.get(label, "") for label, key in FIELDS.items()}
    tags, tag_problems = _parse_tags(values["tags"])
    project_id = values["project_id"]
    doc = {
        "project_id": project_id,
        "kind": values["kind"],
        "squad": {"name": values["squad_name"], "title": values["squad_title"], "github_team": values["github_team"]},
        "domain": values["domain"],
        "contacts": {
            "technical_owner": values["technical_owner"],
            "data_owner": values["data_owner"],
            "executive_owner": values["executive_owner"],
            "data_steward": values["data_steward"],
            "support": {"email": values["support_email"], "channel": values["support_channel"]},
        },
        "groups": {role: group_name(project_id, role) if project_id else "" for role in GROUP_ROLES},
        "service_principal": {"application_id": values["sp"] or SP_PLACEHOLDER},
        "tags": tags,
        "repo": {"name": values["repo_name"] or f"dbx-{project_id.replace('_', '-')}"},
    }
    return doc, problems + tag_problems


def _messages(findings: list[Finding]) -> list[str]:
    return [finding.message for finding in findings if finding.severity == "error"]


def intake(body: str, registry: Path, mode: str = "onboard", context: RegistryContext | None = None) -> Path:
    """
    Validate an issue body and write `projects/<project_id>.yaml`; return the path.

    `mode` is `onboard` (the project must be new) or `change` (it must already exist). Raises `IntakeError`
    listing every problem; nothing is written in that case.
    """
    doc, problems = registration_from_form(parse_issue_form(body))
    where = f"projects/{doc['project_id']}.yaml"
    if not problems:
        problems += _messages(validate_registration(doc, context or load_context(registry), where))
        exists = (registry / where).exists()
        if mode == "onboard" and exists:
            problems.append(f"project {doc['project_id']!r} is already registered; use the change form")
        if mode == "change" and not exists:
            problems.append(f"project {doc['project_id']!r} is not registered; use the onboarding form")
    if problems:
        raise IntakeError("\n".join(f"- {problem}" for problem in problems))
    path = registry / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=119), encoding="utf-8")
    return path
