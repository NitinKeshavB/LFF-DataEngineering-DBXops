"""
Generate each bundle's per-target substitution files from `conf/environments.yml` and the platform snapshot.

A dataflow spec refers to schemas through tokens (`{silver_schema}`); the framework replaces them from
`<bundle>/src/pipeline_configs/<target>_substitutions.json`. Those files are generated, never hand-edited,
so a spec can only ever address `<project_id>_<layer>` schemas in the target's catalog. `{logical_env}`
is left in place for the framework to fill (it isolates developers in dev).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbxops.metadata.repo import (
    LAYERS,
    RepoContext,
)

LOGICAL_ENV = "{logical_env}"
LANDING_VOLUME = "landing"


def render_substitutions(project_id: str, catalog: str) -> dict[str, Any]:
    """Return the substitution document for one project in one target catalog."""
    tokens = {f"{layer}_schema": f"{catalog}.{project_id}_{layer}{LOGICAL_ENV}" for layer in LAYERS}
    tokens["landing_path"] = f"/Volumes/{catalog}/{project_id}_bronze{LOGICAL_ENV}/{LANDING_VOLUME}"
    return {"tokens": tokens}


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


@dataclass(frozen=True)
class SubstitutionPlan:
    """One substitution file and whether it already has the right content."""

    path: Path
    text: str
    current: bool


def plan_substitutions(ctx: RepoContext) -> list[SubstitutionPlan]:
    """Return a plan for every `<target>_substitutions.json` of every bundle under `bundles/`."""
    plans = []
    for configs in sorted((ctx.root / "bundles").glob("*/src/pipeline_configs")):
        for target, cfg in ctx.environments.get("targets", {}).items():
            path = configs / f"{target}_substitutions.json"
            text = _dump(render_substitutions(ctx.project_id, cfg["catalog"]))
            plans.append(SubstitutionPlan(path, text, path.is_file() and path.read_text(encoding="utf-8") == text))
    return plans


def write_substitutions(ctx: RepoContext) -> list[SubstitutionPlan]:
    """Write every missing or outdated substitution file; return the plans that changed."""
    changed = [plan for plan in plan_substitutions(ctx) if not plan.current]
    for plan in changed:
        plan.path.write_text(plan.text, encoding="utf-8")
    return changed
