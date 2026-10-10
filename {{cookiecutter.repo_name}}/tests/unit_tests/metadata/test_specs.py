"""Tests for reading nodespec dataflow specs, schemas and expectations."""

import json
from pathlib import Path

import pytest

from dbxops.metadata.specs import (
    SpecError,
    asset_id,
    find_specs,
    load_expectations,
    load_properties,
    load_specs,
    load_tokens,
    logical_type,
    schema_name,
)
from tests.consts import PROJECT_DIR
from tests.fixtures.metadata_repo import (
    PROJECT_ID,
    write_json,
)

SPEC_PATH = "bundles/dataops/src/dataflows/silver/dataflowspec/orders_main.json"
UPSTREAM_SAMPLES = PROJECT_DIR / "framework/lakeflow_framework/samples/tpch_sample"


def test__spec_targets_sources_and_resolved_paths(repo_root: Path):
    """A spec yields its target table, quarantine table, source tables and schema/expectation paths."""
    (spec,) = load_specs(repo_root / SPEC_PATH)
    (target,) = spec.targets
    assert (spec.spec_id, spec.flow_group) == ("orders", "silver")
    assert (target.table, target.quarantine_table, target.sources) == ("orders", "orders_quarantine", ("v_orders",))
    assert target.schema_path == spec.layer_dir / "schemas/orders_schema.json"
    assert target.expectations_path == spec.layer_dir / "expectations/orders_dqe.json"
    assert spec.sources["v_orders"].table == "orders_raw"


def test__find_specs_returns_every_main_json(repo_root: Path):
    """Only `*_main.json` files under a dataflowspec directory are specs."""
    write_json(repo_root / "bundles/dataops/src/dataflows/silver/schemas/not_a_spec_main.json", {})
    assert [p.name for p in find_specs(repo_root / "bundles")] == ["orders_main.json"]


def test__tokens_resolve_to_a_bare_schema_name(repo_root: Path):
    """A token such as `{silver_schema}` resolves to the schema name only: catalog and `{logical_env}` are dropped."""
    tokens = load_tokens(repo_root / "bundles/dataops")
    assert schema_name("{silver_schema}", tokens) == f"{PROJECT_ID}_silver"
    assert asset_id("{silver_schema}", "main.sub.orders", tokens) == f"{PROJECT_ID}_silver.orders"
    assert schema_name("literal_schema", tokens) == "literal_schema"


def test__an_unknown_token_names_the_known_ones(repo_root: Path):
    """A misspelt token is an error that lists the tokens the bundle defines."""
    tokens = load_tokens(repo_root / "bundles/dataops")
    with pytest.raises(
        SpecError,
        match=r"unknown substitution token \{silvr_schema\}; known: ai_schema, bronze_schema, gold_schema, landing_path, ml_schema, silver_schema",
    ):
        schema_name("{silvr_schema}", tokens)


def test__a_bundle_without_substitutions_is_an_error(tmp_path: Path):
    """A bundle must define substitution tokens for specs to resolve."""
    with pytest.raises(SpecError, match="no \\*_substitutions.json"):
        load_tokens(tmp_path)


def test__spark_types_map_to_odcs_logical_types():
    """Spark primitives, decimals and nested types map to ODCS logical types."""
    assert [logical_type(t) for t in ("long", "decimal(10,2)", "timestamp_ntz", "boolean", "date", "string")] == [
        "integer",
        "number",
        "timestamp",
        "boolean",
        "date",
        "string",
    ]
    assert logical_type({"type": "array"}) == "array"
    assert logical_type({"type": "struct"}) == "object"


def test__properties_carry_name_types_and_required(repo_root: Path):
    """Each schema field becomes a property with its logical and physical type; non-nullable means required."""
    props = load_properties(repo_root / "bundles/dataops/src/dataflows/silver/schemas/orders_schema.json")
    assert props[0] == {"name": "order_key", "logicalType": "integer", "physicalType": "long", "required": True}
    assert props[1] == {
        "name": "total_price",
        "logicalType": "number",
        "physicalType": "decimal(18,2)",
        "required": False,
    }


def test__expectations_keep_their_action_and_tag(repo_root: Path):
    """The actions expect, expect_or_drop and expect_or_fail map to warn, drop and fail."""
    found = load_expectations(repo_root / "bundles/dataops/src/dataflows/silver/expectations/orders_dqe.json")
    assert [(e.name, e.action, e.tag) for e in found] == [
        ("price is non-negative", "warn", "Accuracy"),
        ("order_key not null", "drop", "Validity"),
    ]


@pytest.mark.parametrize(
    "document, message",
    [
        ({"data_flow_group": "g", "nodes": []}, "missing 'data_flow_id'"),
        ({"data_flow_id": "x", "data_flow_group": "g", "nodes": [], "data_flow_type": "standard"}, "only nodespec"),
    ],
)
def test__malformed_specs_are_rejected(tmp_path: Path, document: dict, message: str):
    """A spec missing a required key, or of a legacy type, is a clear error naming the file."""
    path = tmp_path / "bad_main.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SpecError, match=message):
        load_specs(path)


def test__unreadable_files_are_spec_errors(tmp_path: Path):
    """Missing or non-JSON files raise SpecError rather than a raw OSError/JSONDecodeError."""
    with pytest.raises(SpecError, match="cannot read spec"):
        load_specs(tmp_path / "missing_main.json")
    (tmp_path / "s.json").write_text("{", encoding="utf-8")
    with pytest.raises(SpecError, match="cannot read Spark struct schema"):
        load_properties(tmp_path / "s.json")
    with pytest.raises(SpecError, match="cannot read expectations"):
        load_expectations(tmp_path / "e.json")


@pytest.mark.skipif(not UPSTREAM_SAMPLES.exists(), reason="framework submodule not checked out")
def test__parses_the_upstream_tpch_sample_specs():
    """Real upstream nodespecs, plain and template-driven, parse: the reader matches the pinned framework."""
    specs = [spec for p in find_specs(UPSTREAM_SAMPLES) if p.parent.parent.name == "silver" for spec in load_specs(p)]
    by_id = {s.spec_id: s for s in specs}
    orders = by_id["orders"]
    assert (orders.targets[0].table, orders.targets[0].quarantine_table) == ("orders", "orders_quarantine")
    assert load_properties(orders.targets[0].schema_path)[0]["name"] == "order_key"
    assert [e.action for e in load_expectations(orders.targets[0].expectations_path)] == ["drop", "drop"]
    expanded = by_id["lineitem"]  # expanded from silver_append_template
    assert {"lineitem", "partsupp"} <= set(by_id)
    assert (expanded.targets[0].table, expanded.sources["v_lineitem"].table) == ("lineitem", "lineitem")


TEMPLATE = {
    "name": "append_tpl",
    "parameters": {
        "id": {"type": "string", "required": True},
        "cols": {"type": "list", "required": True},
        "suffix": {"type": "string", "required": False, "default": "_v1"},
    },
    "template": {
        "data_flow_id": "${param.id}",
        "data_flow_group": "silver",
        "data_flow_type": "nodespec",
        "nodes": [
            {
                "name": "src",
                "node_type": "source",
                "source_type": "delta",
                "config": {"database": "{silver_schema}", "table": "raw_${param.id}", "select_exp": "${param.cols}"},
            },
            {
                "name": "t",
                "node_type": "target",
                "config": {"database": "{silver_schema}", "table": "${param.id}${param.suffix}", "sources": ["src"]},
            },
        ],
    },
}


def _template_spec(repo_root: Path, parameter_sets: list) -> Path:
    write_json(repo_root / "bundles/dataops/src/templates/append_tpl.json", TEMPLATE)
    path = repo_root / "bundles/dataops/src/dataflows/silver/dataflowspec/tpl_main.json"
    write_json(path, {"template": "append_tpl", "parameter_sets": parameter_sets})
    return path


def test__a_template_expands_to_one_spec_per_parameter_set_with_defaults(repo_root: Path):
    """Each parameter set yields a spec; whole-string placeholders keep their type; optional parameters take defaults."""
    path = _template_spec(repo_root, [{"id": "a", "cols": ["x", "y"]}, {"id": "b", "cols": ["z"], "suffix": "_v2"}])
    first, second = load_specs(path)
    assert [(s.spec_id, s.targets[0].table) for s in (first, second)] == [("a", "a_v1"), ("b", "b_v2")]
    assert first.sources["src"].table == "raw_a"


def test__template_errors_name_the_problem(repo_root: Path):
    """A missing required parameter, an unknown placeholder, a missing template and empty sets are reported."""
    with pytest.raises(SpecError, match="missing required template parameters: cols"):
        load_specs(_template_spec(repo_root, [{"id": "a"}]))
    with pytest.raises(SpecError, match="has no parameter_sets"):
        load_specs(_template_spec(repo_root, []))
    path = _template_spec(repo_root, [{"id": "a", "cols": []}])
    write_json(
        repo_root / "bundles/dataops/src/templates/append_tpl.json",
        {**TEMPLATE, "template": {"data_flow_id": "${param.nope}"}},
    )
    with pytest.raises(SpecError, match="parameter 'nope' not found"):
        load_specs(path)
    (repo_root / "bundles/dataops/src/templates/append_tpl.json").unlink()
    path = repo_root / "bundles/dataops/src/dataflows/silver/dataflowspec/tpl_main.json"
    with pytest.raises(SpecError, match="cannot read template"):
        load_specs(path)
