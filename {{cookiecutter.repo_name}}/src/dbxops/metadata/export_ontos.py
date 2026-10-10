"""
Export contracts, products and the glossary as files for Ontos's upload endpoints.

The database is not the Ontos database, so alignment is this function, not a shared shape. It reads the
source documents (never lossy column copies) and applies the transforms of the plan's section D6:

1. domain: the contract's domain path is written as the Ontos domain name (unique across levels);
2. nested properties stay nested as authored (ODCS supports it natively);
3. team and support come from the *owning project's* contacts, so project-scoped owners survive;
4. a port whose asset type has no Ontos type is exported as `generic`, with the original in
   `customProperties` `dbx.assetSubtype`;
5. the Ontos name is the id without `dp_`/`dc_`, and it is added as the `data_product_<name>` /
   `data_contract_<name>` tag;
6. semantic assignments are already ODCS `authoritativeDefinitions`; the glossary is exported as Turtle;
7. `version` is the semver, which the validator guarantees is 1:1 with content.

Environment specifics (catalog) are resolved for one target. Files are `.yaml`: Ontos's product endpoint
rejects `.yml`.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from dbxops.metadata.contracts import dump_yaml
from dbxops.metadata.repo import RepoContext
from dbxops.registry.snapshots import strip_header

ONTOS_NATIVE_TYPES = frozenset(
    {
        "uc_table",
        "uc_view",
        "uc_materialized_view",
        "uc_streaming_table",
        "uc_volume",
        "uc_function",
        "uc_model",
        "uc_job",
        "uc_pipeline",
        "uc_metric",
    }
)
ROLE_KEYS = ("technical_owner", "data_owner", "executive_owner", "data_steward")


class ExportError(ValueError):
    """Raised when an export cannot be produced (for example an unknown target)."""


def _squad(ctx: RepoContext) -> dict[str, Any]:
    teams = ctx.teams.get("teams", [])
    if len(teams) != 1:
        raise ExportError(f"metadata/_platform/teams.yaml must define exactly one squad, found {len(teams)}")
    return teams[0]


def team_members(ctx: RepoContext) -> list[dict[str, Any]]:
    """Return the owning project's people as `{username, role}` entries (transform 3)."""
    contacts = _squad(ctx)["contacts"]
    return [{"username": contacts[role], "role": role} for role in ROLE_KEYS]


def _support(ctx: RepoContext) -> list[dict[str, Any]]:
    support = _squad(ctx)["contacts"]["support"]
    return [
        {"channel": "email", "url": f"mailto:{support['email']}"},
        {"channel": "chat", "url": support["channel"]},
    ]


def _add_tag(doc: dict[str, Any], tag: str) -> None:
    doc["tags"] = list(dict.fromkeys([*doc.get("tags", []), tag]))


def export_contract(ctx: RepoContext, doc: dict[str, Any], target: str) -> dict[str, Any]:
    """Return one contract resolved for `target`, with team and Ontos tags added."""
    cfg = ctx.environments["targets"][target]
    out = copy.deepcopy(doc)
    out["servers"] = [s for s in out.get("servers", []) if s.get("environment") == target]
    for obj in out.get("schema", []):
        parts = str(obj.get("physicalName", "")).split(".")
        if len(parts) == 3:
            obj["physicalName"] = ".".join([cfg["catalog"], *parts[1:]])
    out["team"] = team_members(ctx)
    _add_tag(out, f"data_contract_{out['id'].removeprefix('dc_')}")
    _add_tag(out, f"data_domain_{out['domain']}")
    return out


def _export_port(port: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(port)
    original = out.get("type")
    if original and original not in ONTOS_NATIVE_TYPES:
        out["type"] = "generic"
        out["customProperties"] = [
            *out.get("customProperties", []),
            {"property": "dbx.assetSubtype", "value": original},
        ]
    return out


def export_product(ctx: RepoContext, doc: dict[str, Any]) -> dict[str, Any]:
    """Return one product with team, support, Ontos tag and generic-typed ports."""
    out = copy.deepcopy(doc)
    out["outputPorts"] = [_export_port(p) for p in out.get("outputPorts", [])]
    out["team"] = {"name": _squad(ctx)["name"], "members": team_members(ctx)}
    out["support"] = _support(ctx)
    _add_tag(out, f"data_product_{out['id'].removeprefix('dp_')}")
    return out


def export_ontos(ctx: RepoContext, target: str, out_dir: Path) -> list[Path]:
    """Write the Ontos upload files for `target` under `out_dir` and return their paths."""
    if target not in ctx.environments.get("targets", {}):
        raise ExportError(f"unknown target {target!r}; known: {', '.join(ctx.environments.get('targets', {}))}")
    written = []
    for doc in ctx.contracts:
        path = out_dir / "contracts" / f"{doc.doc_id}.yaml"
        _write(path, dump_yaml(export_contract(ctx, doc.data, target)))
        written.append(path)
    for doc in ctx.products:
        path = out_dir / "products" / f"{doc.doc_id}.yaml"
        _write(path, dump_yaml(export_product(ctx, doc.data)))
        written.append(path)
    glossary = out_dir / "semantic-models" / f"{ctx.project_id}.ttl"
    _write(glossary, strip_header((ctx.root / "metadata/_platform/glossary.ttl").read_text(encoding="utf-8")))
    return [*written, glossary]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
