"""
Validate squad registrations: the registry repo's `projects/<project_id>.yaml` files.

`validate_registry(root)` is what the registry's pull-request check runs (`make validate-registry`). It checks each
registration against the JSON schema and the platform's reference data (domain taxonomy, required tag keys,
AD group naming), and the whole set for uniqueness. Placeholder values (`PLACEHOLDER-<what>`) are accepted
unless `strict` is set, because a squad may register before its service principal exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft201909Validator

from dbxops.findings import Finding
from dbxops.registry.domains import (
    DomainError,
    DomainTaxonomy,
)

PLACEHOLDER = "PLACEHOLDER-"
GROUP_ROLES = {"developers": "DEV", "readers": "READ", "admins": "ADMIN"}


@dataclass(frozen=True)
class RegistryContext:
    """The platform reference data a registration is validated against."""

    taxonomy: DomainTaxonomy
    required_tags: tuple[str, ...]


def load_context(root: Path) -> RegistryContext:
    """Load `domains.yaml` and `required_tags.yaml` from a registry checkout."""
    tags = yaml.safe_load((root / "required_tags.yaml").read_text(encoding="utf-8")) or {}
    return RegistryContext(DomainTaxonomy.from_yaml(root / "domains.yaml"), tuple(tags.get("required", [])))


def group_name(project_id: str, role: str) -> str:
    """Return the AD group name for a project and role (`developers`/`readers`/`admins`): `AAD-DBX-<ID>-<ROLE>`."""
    return f"AAD-DBX-{project_id.replace('_', '').upper()}-{GROUP_ROLES[role]}"


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PLACEHOLDER)


def _walk_values(node: Any, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_values(value, f"{path}/{key}")
    else:
        yield path, node


def _schema_findings(doc: dict[str, Any], where: str) -> list[Finding]:
    schema = json.loads(
        (resources.files("dbxops.registry") / "schemas" / "registration.schema.json").read_text("utf-8")
    )
    errors = sorted(Draft201909Validator(schema).iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [
        Finding("registration", where, f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}")
        for e in errors
    ]


def _content_findings(doc: dict[str, Any], ctx: RegistryContext, where: str) -> list[Finding]:
    findings = []
    try:
        ctx.taxonomy.resolve(doc["domain"])
    except DomainError as error:
        findings.append(Finding("registration", where, str(error)))
    missing = [key for key in ctx.required_tags if key not in doc["tags"]]
    if missing:
        findings.append(Finding("registration", where, f"missing required tag(s): {', '.join(missing)}"))
    for role, group in doc["groups"].items():
        expected = group_name(doc["project_id"], role)
        if group != expected and not _is_placeholder(group):
            findings.append(Finding("registration", where, f"groups/{role}: expected {expected!r}, got {group!r}"))
    return findings


def validate_registration(
    doc: dict[str, Any], ctx: RegistryContext, where: str, strict: bool = False
) -> list[Finding]:
    """Validate one registration against the schema and the platform reference data."""
    findings = _schema_findings(doc, where)
    if findings:
        return findings
    findings = _content_findings(doc, ctx, where)
    if strict:
        findings += [
            Finding("registration", where, f"{path}: placeholder value {value!r}")
            for path, value in _walk_values(doc)
            if _is_placeholder(value)
        ]
    return findings


def _duplicates(docs: dict[str, dict[str, Any]], key: Any, label: str) -> list[Finding]:
    seen: dict[str, str] = {}
    findings = []
    for where, doc in docs.items():
        value = key(doc)
        if value in seen:
            findings.append(Finding("registry", where, f"{label} {value!r} is already used by {seen[value]}"))
        seen[value] = where
    return findings


def validate_registry(root: Path, strict: bool = False) -> list[Finding]:
    """Validate every registration under `root/projects` and their uniqueness; return all findings."""
    ctx = load_context(root)
    docs = {
        str(path.relative_to(root)): yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for path in sorted((root / "projects").glob("*.yaml"))
    }
    findings: list[Finding] = []
    for where, doc in docs.items():
        findings += validate_registration(doc, ctx, where, strict)
        if Path(where).stem != doc.get("project_id"):
            findings.append(Finding("registry", where, "file name must be <project_id>.yaml"))
    valid = {where: doc for where, doc in docs.items() if not _schema_findings(doc, where)}
    findings += _duplicates(valid, lambda d: d["project_id"], "project_id")
    findings += _duplicates(valid, lambda d: d["project_id"].replace("_", ""), "project_id (ignoring '_')")
    findings += _duplicates(valid, lambda d: d["repo"]["name"], "repo name")
    findings += _duplicates(valid, lambda d: d["squad"]["github_team"], "github_team")
    findings += _duplicates(valid, lambda d: d["squad"]["name"], "squad name")
    return findings
