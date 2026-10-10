"""
Generate and refresh ODCS data contracts from dataflow specs.

Each table a spec writes has exactly one contract at `<layer dir>/contracts/<table>.odcs.yaml`.

Machine-owned, regenerated from the specs: `servers[]`, the schema object's `name`, `physicalName`,
`logicalType` and `customProperties` (`dbx.asset_id`, `dbx.spec_id`), each property's physical fields
(`name`, `logicalType`, `physicalType`, `required`) and the quality rules that come from expectations
(`dbx.origin: expectation`). Everything else is human-owned and kept as written.

A scaffolded contract carries `PLACEHOLDER-<what>` text for the fields only a person can write, so
`make check-placeholders` blocks a deploy until they are filled in.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from dbxops.metadata.repo import (
    Doc,
    RepoContext,
    SpecInfo,
)
from dbxops.metadata.specs import (
    Expectation,
    SpecError,
    SpecTarget,
    asset_id,
    load_expectations,
    load_properties,
    schema_name,
)

API_VERSION = "v3.1.0"
ASSET_ID = "dbx.asset_id"
SPEC_ID = "dbx.spec_id"
ORIGIN = "dbx.origin"
ACTION = "dbx.action"
CONSTRAINT = "dbx.constraint"
QUARANTINE_ASSET_ID = "dbx.quarantine_asset_id"
EXPECTATION = "expectation"
_DIMENSIONS = {"accuracy", "completeness", "conformity", "consistency", "coverage", "timeliness", "uniqueness"}
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """Return a lowercase `[a-z0-9_]` slug of `text`."""
    return _SLUG.sub("_", text.lower()).strip("_")


def get_custom(obj: dict[str, Any], prop: str) -> Any:
    """Return the value of a `customProperties` entry, or None."""
    for entry in obj.get("customProperties", []):
        if entry.get("property") == prop:
            return entry.get("value")
    return None


def _custom(**props: Any) -> list[dict[str, Any]]:
    return [
        {"property": name.replace("__", "."), "value": value} for name, value in props.items() if value is not None
    ]


def contract_id(project_id: str, layer: str, table: str) -> str:
    """Return the contract id `dc_<project_id>_<layer>_<table>`."""
    return f"dc_{project_id}_{layer}_{table}"


def layer_of(schema: str, project_id: str) -> str:
    """Return the layer of a `<project_id>_<layer>` schema name; the whole name if it has another shape."""
    return schema.removeprefix(f"{project_id}_")


def _dimension(tag: str | None) -> str:
    candidate = (tag or "").lower()
    return candidate if candidate in _DIMENSIONS else "conformity"


def quality_rule(expectation: Expectation, schema: str, target: SpecTarget) -> dict[str, Any]:
    """Return the ODCS quality rule for one SDP expectation."""
    quarantined = expectation.action == "drop" and target.quarantine_table is not None
    action = "quarantine" if quarantined else expectation.action
    quarantine_id = f"{schema}.{target.quarantine_table}" if quarantined else None
    props = _custom(dbx__origin=EXPECTATION, dbx__action=action, dbx__constraint=expectation.constraint)
    if quarantine_id:
        props.append({"property": QUARANTINE_ASSET_ID, "value": quarantine_id})
    return {
        "id": f"{target.table}_{slug(expectation.name)}",
        "name": expectation.name,
        "type": "sql",
        "dimension": _dimension(expectation.tag),
        "severity": "warning" if expectation.action == "warn" else "error",
        "query": f"SELECT COUNT(*) FROM ${{table}} WHERE NOT ({expectation.constraint})",
        "mustBe": 0,
        "customProperties": props,
    }


def machine_view(ctx: RepoContext, info: SpecInfo, target: SpecTarget) -> dict[str, Any]:
    """Return the machine-owned parts of a contract for one spec target."""
    schema = schema_name(target.database, info.tokens)
    targets_cfg = ctx.environments.get("targets", {})
    prod_catalog = targets_cfg.get("prod", {}).get("catalog", "")
    if target.schema_path is None:
        raise SpecError(f"{info.spec.path}: target '{target.table}' has no schema_path; a contract needs a schema")
    expectations = load_expectations(target.expectations_path) if target.expectations_path else []
    return {
        "servers": [
            {
                "server": env,
                "type": "databricks",
                "environment": env,
                "catalog": cfg.get("catalog", ""),
                "schema": schema,
            }
            for env, cfg in targets_cfg.items()
        ],
        "object": {
            "name": target.table,
            "physicalName": f"{prod_catalog}.{schema}.{target.table}",
            "logicalType": "object",
            "properties": load_properties(target.schema_path),
            "quality": [quality_rule(e, schema, target) for e in expectations],
            "asset_id": asset_id(target.database, target.table, info.tokens),
            "spec_id": info.spec.spec_id,
        },
    }


def scaffold(ctx: RepoContext, info: SpecInfo, target: SpecTarget) -> dict[str, Any]:
    """Return a new contract with human-owned fields set to `PLACEHOLDER-<what>` text."""
    tags = info.spec.tags
    for key in ("data_domain", "data_product"):
        if key not in tags:
            raise SpecError(f"{info.spec.path}: spec 'tags' must define '{key}' to scaffold a contract")
    schema = schema_name(target.database, info.tokens)
    return {
        "apiVersion": API_VERSION,
        "kind": "DataContract",
        "id": contract_id(ctx.project_id, layer_of(schema, ctx.project_id), target.table),
        "name": target.table,
        "version": "1.0.0",
        "status": "draft",
        "domain": tags["data_domain"],
        "dataProduct": tags["data_product"],
        "description": {
            "purpose": "PLACEHOLDER-purpose",
            "usage": "PLACEHOLDER-usage",
            "limitations": "PLACEHOLDER-limitations",
        },
    }


def _merge_properties(generated: list[dict], existing: list[dict]) -> list[dict]:
    by_name = {p["name"]: p for p in existing}
    return [{**by_name.get(p["name"], {}), **p} for p in generated]


def _merge_quality(generated: list[dict], existing: list[dict]) -> list[dict]:
    manual = [q for q in existing if get_custom(q, ORIGIN) != EXPECTATION]
    return generated + manual


def _merge_object(machine: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(existing)
    merged.update(
        name=machine["name"],
        physicalName=machine["physicalName"],
        logicalType=machine["logicalType"],
        properties=_merge_properties(machine["properties"], existing.get("properties", [])),
        quality=_merge_quality(machine["quality"], existing.get("quality", [])),
    )
    human = [c for c in existing.get("customProperties", []) if c.get("property") not in (ASSET_ID, SPEC_ID)]
    merged["customProperties"] = _custom(dbx__asset_id=machine["asset_id"], dbx__spec_id=machine["spec_id"]) + human
    if not merged["quality"]:
        del merged["quality"]
    return merged


def merge(base: dict[str, Any], machine: dict[str, Any]) -> dict[str, Any]:
    """Return `base` (a scaffold or an existing contract) with the machine-owned parts refreshed."""
    contract = copy.deepcopy(base)
    contract["servers"] = machine["servers"]
    objects = contract.get("schema", [])
    index = next((i for i, o in enumerate(objects) if get_custom(o, ASSET_ID) == machine["object"]["asset_id"]), None)
    merged = _merge_object(machine["object"], objects[index] if index is not None else {})
    if index is None:
        contract["schema"] = [merged]
    else:
        objects[index] = merged
    return contract


@dataclass(frozen=True)
class ContractPlan:
    """What `make contracts` would do for one spec target."""

    path: Path
    asset_id: str
    action: str  # create | update | unchanged
    contract: dict[str, Any]


def contract_path(info: SpecInfo, target: SpecTarget) -> Path:
    """Return where a target's contract lives."""
    return info.spec.layer_dir / "contracts" / f"{target.table}.odcs.yaml"


def find_existing(ctx: RepoContext, info: SpecInfo, target: SpecTarget) -> Doc | None:
    """Return the contract for a spec target, matched by its `dbx.asset_id`, or None."""
    wanted = asset_id(target.database, target.table, info.tokens)
    for doc in ctx.contracts:
        if any(get_custom(o, ASSET_ID) == wanted for o in doc.data.get("schema", [])):
            return doc
    return None


def plan_contracts(ctx: RepoContext) -> list[ContractPlan]:
    """Compute the create/update/unchanged action for every spec target."""
    plans = []
    for info in ctx.specs:
        for target in info.spec.targets:
            existing = find_existing(ctx, info, target)
            machine = machine_view(ctx, info, target)
            base = existing.data if existing else scaffold(ctx, info, target)
            contract = merge(base, machine)
            action = "create" if existing is None else ("unchanged" if contract == existing.data else "update")
            path = ctx.root / existing.path if existing else contract_path(info, target)
            plans.append(ContractPlan(path, machine["object"]["asset_id"], action, contract))
    return plans


def write_contracts(ctx: RepoContext) -> list[ContractPlan]:
    """Write every created or updated contract and return the plans that changed something."""
    changed = [p for p in plan_contracts(ctx) if p.action != "unchanged"]
    for plan in changed:
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        plan.path.write_text(dump_yaml(plan.contract), encoding="utf-8")
    return changed


def dump_yaml(document: dict[str, Any]) -> str:
    """Serialise a document with its keys in the order they were built."""
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=119)
