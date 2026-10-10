"""
Read Lakeflow Framework `nodespec` dataflow specs, the default spec type of the pinned framework.

Only what the metadata layer needs is extracted: the delta tables a spec writes (target nodes), the
tables it reads (source nodes), each target's Spark schema and its data-quality expectations.

Path conventions follow the framework (`PipelineBundlePaths`): a spec lives in
`<layer dir>/dataflowspec/<id>_main.json`; `schema_path` resolves against `<layer dir>/schemas/` and
`expectations_path` against `<layer dir>/expectations/`.
"""

from __future__ import annotations

import json
import re
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

SPEC_SUFFIX = "_main.json"
_TOKEN = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_LOGICAL_ENV = "{logical_env}"
EXPECTATION_ACTIONS = {"expect": "warn", "expect_or_drop": "drop", "expect_or_fail": "fail"}


class SpecError(ValueError):
    """Raised when a dataflow spec cannot be read or interpreted."""


@dataclass(frozen=True)
class SpecSource:
    """A table a spec reads, as written in the spec (database may be a substitution token)."""

    node: str
    database: str
    table: str


@dataclass(frozen=True)
class SpecTarget:
    """A delta table a spec writes."""

    node: str
    database: str
    table: str
    schema_path: Path | None
    expectations_path: Path | None
    quarantine_table: str | None
    sources: tuple[str, ...]


@dataclass(frozen=True)
class DataflowSpec:
    """The metadata-relevant content of one `*_main.json` nodespec."""

    path: Path
    spec_id: str
    flow_group: str
    layer_dir: Path
    targets: tuple[SpecTarget, ...]
    sources: dict[str, SpecSource] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tags(self) -> dict[str, Any]:
        """Return the spec's `tags` mapping (domain and product tags live here)."""
        return self.raw.get("tags", {})


def find_specs(root: Path) -> list[Path]:
    """Return every `*_main.json` under a bundle's `src/dataflows`, sorted."""
    return sorted(root.glob(f"**/src/dataflows/**/dataflowspec/*{SPEC_SUFFIX}"))


def _target(node: dict, layer_dir: Path) -> SpecTarget | None:
    config = node.get("config", {})
    if node.get("node_type") != "target" or "table" not in config:
        return None
    quarantine = config.get("data_quality", {}).get("quarantine", {})
    quarantined = quarantine.get("mode") == "table"
    schema_path = config.get("schema_path")
    expectations = config.get("data_quality", {}).get("expectations_path")
    enabled = config.get("data_quality", {}).get("enabled", False)
    return SpecTarget(
        node=node["name"],
        database=config.get("database", ""),
        table=config["table"],
        schema_path=layer_dir / "schemas" / schema_path if schema_path else None,
        expectations_path=layer_dir / "expectations" / expectations if enabled and expectations else None,
        quarantine_table=(
            (quarantine.get("target", {}).get("table") or f"{config['table']}_quarantine") if quarantined else None
        ),
        sources=tuple(config.get("sources", [])),
    )


def _source(node: dict) -> SpecSource | None:
    config = node.get("config", {})
    if node.get("node_type") != "source" or "table" not in config:
        return None
    return SpecSource(node=node["name"], database=config.get("database", ""), table=config["table"])


_PARAM = re.compile(r"\$\{param\.([A-Za-z0-9_]+)\}")


def _substitute(node: Any, params: dict[str, Any], where: str) -> Any:
    """Replace `${param.x}` in a template body: a whole-string placeholder keeps the parameter's type."""
    if isinstance(node, dict):
        return {_substitute(k, params, where): _substitute(v, params, where) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(item, params, where) for item in node]
    if not isinstance(node, str):
        return node

    def value(name: str) -> Any:
        if name not in params:
            raise SpecError(f"{where}: parameter '{name}' not found in params")
        return params[name]

    whole = _PARAM.fullmatch(node)
    return value(whole.group(1)) if whole else _PARAM.sub(lambda m: str(value(m.group(1))), node)


def _template_params(definition: dict[str, Any], given: dict[str, Any], where: str) -> dict[str, Any]:
    params = dict(given)
    missing = []
    for name, spec in definition.get("parameters", {}).items():
        if name in given:
            continue
        if spec.get("required", True):
            missing.append(name)
        elif spec.get("default") is not None:
            params[name] = spec["default"]
    if missing:
        raise SpecError(f"{where}: missing required template parameters: {', '.join(missing)}")
    return params


def expand_template(doc: dict[str, Any], templates_dir: Path, where: str) -> list[dict[str, Any]]:
    """Expand a `{template, parameter_sets}` spec into one concrete spec per parameter set, as the framework does."""
    name = doc["template"]
    path = templates_dir / f"{name}.json"
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SpecError(f"{where}: cannot read template {path}: {error}") from error
    sets = doc.get("parameter_sets", doc.get("parameterSets", []))
    if not sets:
        raise SpecError(f"{where}: template '{name}' has no parameter_sets")
    return [_substitute(definition["template"], _template_params(definition, given, where), where) for given in sets]


def _src_dir(path: Path) -> Path:
    return next(parent for parent in path.parents if parent.name == "src")


def load_specs(path: Path) -> list[DataflowSpec]:
    """
    Parse a spec file into its concrete specs.

    A plain nodespec yields one spec; a template invocation yields one per parameter set. Raises
    `SpecError` for unsupported or malformed specs.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SpecError(f"{path}: cannot read spec: {error}") from error
    docs = expand_template(doc, _src_dir(path) / "templates", str(path)) if "template" in doc else [doc]
    return [_spec_from_doc(path, concrete) for concrete in docs]


def _spec_from_doc(path: Path, doc: dict[str, Any]) -> DataflowSpec:
    if doc.get("data_flow_type", "nodespec") != "nodespec":
        raise SpecError(f"{path}: only nodespec specs are supported, got {doc.get('data_flow_type')!r}")
    for key in ("data_flow_id", "data_flow_group", "nodes"):
        if key not in doc:
            raise SpecError(f"{path}: missing '{key}'")
    layer_dir = path.parent.parent
    targets = tuple(t for t in (_target(n, layer_dir) for n in doc["nodes"]) if t)
    sources = {s.node: s for s in (_source(n) for n in doc["nodes"]) if s}
    return DataflowSpec(
        path=path,
        spec_id=doc["data_flow_id"],
        flow_group=doc["data_flow_group"],
        layer_dir=layer_dir,
        targets=targets,
        sources=sources,
        raw=doc,
    )


def load_tokens(bundle_dir: Path, required: bool = True) -> dict[str, str]:
    """
    Return a bundle's substitution tokens from the first `*_substitutions.json` (dev preferred).

    With `required` false, a bundle that has none yet yields no tokens instead of an error; the
    substitutions rule reports the missing files.
    """
    configs = bundle_dir / "src" / "pipeline_configs"
    candidates = sorted(
        configs.glob("*_substitutions.json"), key=lambda p: (p.name != "dev_substitutions.json", p.name)
    )
    if not candidates and not required:
        return {}
    if not candidates:
        raise SpecError(f"{configs}: no *_substitutions.json found")
    return json.loads(candidates[0].read_text(encoding="utf-8")).get("tokens", {})


def schema_name(database: str, tokens: dict[str, str]) -> str:
    """
    Resolve a spec's `database` to the bare UC schema name.

    Tokens are substituted, the `{logical_env}` suffix and any catalog prefix are dropped, so
    `{silver_schema}` -> `main.proj_silver{logical_env}` -> `proj_silver`.
    """

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name == "logical_env":
            return match.group(0)
        if name not in tokens:
            raise SpecError(f"unknown substitution token {{{name}}}; known: {', '.join(sorted(tokens))}")
        return tokens[name]

    resolved = _TOKEN.sub(substitute, database).replace(_LOGICAL_ENV, "")
    return resolved.split(".")[-1]


def asset_id(database: str, table: str, tokens: dict[str, str]) -> str:
    """Return the logical asset id `<schema>.<table>` of a spec table."""
    return f"{schema_name(database, tokens)}.{table.split('.')[-1]}"


_LOGICAL_TYPES = {
    "string": "string",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "timestamp_ntz": "timestamp",
    "byte": "integer",
    "short": "integer",
    "integer": "integer",
    "long": "integer",
    "float": "number",
    "double": "number",
    "binary": "string",
}


def logical_type(spark_type: Any) -> str:
    """Map a Spark type (string or nested dict) to an ODCS logicalType."""
    if isinstance(spark_type, dict):
        return {"array": "array"}.get(spark_type.get("type", ""), "object")
    if spark_type.startswith("decimal"):
        return "number"
    return _LOGICAL_TYPES.get(spark_type, "string")


def physical_type(spark_type: Any) -> str:
    """Return a Spark type's physical form: the string itself, or `struct`/`array`/`map` for nested types."""
    return spark_type if isinstance(spark_type, str) else str(spark_type.get("type", "struct"))


def load_properties(schema_file: Path) -> list[dict[str, Any]]:
    """Return ODCS physical property dicts (name, types, required) from a Spark struct schema file."""
    try:
        fields = json.loads(schema_file.read_text(encoding="utf-8"))["fields"]
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise SpecError(f"{schema_file}: cannot read Spark struct schema: {error}") from error
    return [
        {
            "name": f["name"],
            "logicalType": logical_type(f["type"]),
            "physicalType": physical_type(f["type"]),
            "required": not f.get("nullable", True),
        }
        for f in fields
    ]


@dataclass(frozen=True)
class Expectation:
    """One SDP expectation from an expectations file."""

    name: str
    constraint: str
    action: str
    tag: str | None


def load_expectations(path: Path) -> list[Expectation]:
    """Return the expectations in an expectations file, in file order within each action."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SpecError(f"{path}: cannot read expectations: {error}") from error
    found = []
    for key, action in EXPECTATION_ACTIONS.items():
        for rule in doc.get(key, []):
            found.append(Expectation(rule["name"], rule["constraint"], action, rule.get("tag")))
    return found
