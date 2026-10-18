"""Tests of the pure collectors, including the document -> database -> document round trip."""

import json
from dataclasses import replace

import pytest

from dbxops.hashing import content_hash
from dbxops.metastore.collectors import (
    SEMANTIC_ASSIGNMENT,
    asset_id_from_physical_name,
    asset_records,
    dataflow_spec_records,
    domain_records,
    glossary_records,
    project_tag_rows,
    registration_records,
    semantic_link_rows,
    strip_id_prefix,
    tag_rows,
)
from dbxops.metastore.errors import CollectError
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
    TagRow,
)
from tests.fixtures.metastore import (
    CONTRACT_ID,
    IRI,
    PRODUCT_ID,
    SQUAD,
    contract_record,
    product_record,
    spec_info_for_tests,
)

OPTIONS = {"repo_id": SQUAD, "source_path": "x.yaml", "git_commit": "c1"}


def test__a_contract_maps_to_a_record_with_extracted_columns(contract_doc):
    """Name, api version, domain, product, purpose and the semver are extracted from the document."""
    record = contract_record(contract_doc)
    assert (record.kind, record.entity_id, record.semver, record.status) == (
        "data_contract",
        CONTRACT_ID,
        "1.0.0",
        "active",
    )
    assert record.columns["domain_id"] == "claims__litigation__claim_litigation"
    assert record.columns["product_id"] == "dp_claims_fraud_fraud_detection"
    assert (record.columns["purpose"], record.columns["limitations"]) == ("Score claims", "Synthetic")
    assert record.payload is contract_doc and record.source_path == "contracts/x.odcs.yaml"


def test__nested_and_array_properties_get_dotted_paths_and_flags(contract_doc):
    """Nested properties use '.', array items use '[]', and key, PII and position flags are read."""
    properties = {row["property_path"]: row for row in contract_record(contract_doc).children["md_contract_property"]}
    assert list(properties) == ["claim_id", "score", "holder", "holder.email", "labels", "labels.[]"]
    assert properties["holder.email"]["parent_path"] == "holder" and properties["holder.email"]["is_pii"] is True
    assert properties["claim_id"]["pk_position"] == 1 and properties["claim_id"]["primary_key"] is True
    assert properties["score"]["pk_position"] is None and properties["labels.[]"]["logical_type"] == "string"


def test__quality_rules_use_empty_property_path_for_dataset_rules_and_read_dbx_properties(contract_doc):
    """Dataset-level rules get '' as path; action, origin and spec come from customProperties with defaults."""
    rules = {row["rule_id"]: row for row in contract_record(contract_doc).children["md_quality_rule"]}
    assert rules["has_rows"]["property_path"] == "" and rules["has_rows"]["origin"] == "manual"
    assert rules["has_rows"]["expression"].startswith("SELECT count")
    assert (rules["claim_id_not_null"]["action"], rules["claim_id_not_null"]["origin"]) == ("drop", "expectation")
    assert (
        rules["claim_id_not_null"]["spec_id"] == "spec_scores"
        and rules["claim_id_not_null"]["expression"] == "claim_id IS NOT NULL"
    )


def test__objects_servers_and_assets_follow_the_physical_name(contract_doc):
    """The logical asset id drops the catalog; the server environment falls back to the server name."""
    record = contract_record(contract_doc)
    assert record.children["md_contract_object"][0]["asset_id"] == "claims_fraud_ml.fraud_scores"
    assert record.children["md_contract_server"][0]["environment"] == "prod"
    assert asset_id_from_physical_name("cat.schema.t") == "schema.t"
    assert asset_id_from_physical_name("schema.t") == "schema.t"
    assert strip_id_prefix("dp_x_y") == "x_y" and strip_id_prefix("dc_x_y") == "x_y" and strip_id_prefix("zz") == "zz"


def test__rules_without_an_id_get_a_stable_derived_id_and_duplicates_are_rejected(contract_doc):
    """An unnamed rule is identified by object, path and position; a repeated id is a CollectError."""
    contract_doc["schema"][0]["quality"][0].pop("name")
    ids = [row["rule_id"] for row in contract_record(contract_doc).children["md_quality_rule"]]
    assert "fraud_scores._.0" in ids
    contract_doc["schema"][0]["properties"][1]["quality"] = [{"name": "claim_id_not_null"}]
    with pytest.raises(CollectError, match="duplicate quality rule id"):
        contract_record(contract_doc)


def test__a_document_without_an_id_is_rejected(contract_doc, product_doc):
    """Contracts and products need an id."""
    del contract_doc["id"]
    del product_doc["id"]
    with pytest.raises(CollectError, match="id"):
        contract_record(contract_doc)
    with pytest.raises(CollectError, match="id"):
        product_record(product_doc)


def test__a_product_maps_ports_and_input_contracts(product_doc):
    """Input and output ports, their contract ids, asset types and the input contracts of an output port."""
    record = product_record(product_doc)
    ports = {(row["direction"], row["port_name"]): row for row in record.children["md_product_port"]}
    assert ports[("input", "policies")]["contract_id"] == "dc_policy_core_policy"
    assert ports[("output", "scores")]["asset_type"] == "table" and ports[("output", "scores")]["asset_id"] == ""
    assert ports[("output", "scorer")]["asset_type"] == "serving_endpoint"
    assert record.children["md_port_input_contract"] == [
        {"port_name": "scores", "contract_id": "dc_policy_core_policy", "contract_semver": "1.0.0"}
    ]
    assert record.columns["project_id"] == SQUAD and record.semver == "1.0.0"


def test__assets_come_from_contract_tables_and_non_table_ports(contract_doc, product_doc):
    """Contract objects become uc tables; a serving endpoint becomes a generic asset with its subtype."""
    table = asset_records(contract_doc, **OPTIONS)[0]
    assert (table.entity_id, table.columns["asset_type"], table.columns["fqn_template"]) == (
        "claims_fraud_ml.fraud_scores",
        "uc_table",
        "{catalog}.claims_fraud_ml.fraud_scores",
    )
    endpoint = asset_records(product_doc, **OPTIONS)[0]
    assert endpoint.columns["asset_type"] == "serving_endpoint" and endpoint.columns["ontos_asset_type"] == "generic"
    assert endpoint.payload["subtype"] == "custom_model" and endpoint.columns["product_id"] == PRODUCT_ID
    with pytest.raises(CollectError, match="kind"):
        asset_records({"kind": "Other"}, **OPTIONS)


def test__view_physical_types_map_to_ontos_native_asset_types(contract_doc):
    """A materialized view stays Ontos-native instead of becoming generic."""
    contract_doc["schema"][0]["physicalType"] = "materialized_view"
    asset = asset_records(contract_doc, **OPTIONS)[0]
    assert (asset.columns["asset_type"], asset.columns["ontos_asset_type"]) == (
        "uc_materialized_view",
        "uc_materialized_view",
    )


def test__semantic_links_use_composite_ids(contract_doc, product_doc):
    """Contract, schema and property links use {contract}, {contract}#{object} and {contract}#{object}#{path} ids."""
    links = {(row.entity_type, row.entity_id): row.iri for row in semantic_link_rows(contract_doc)}
    assert links == {
        ("data_contract", CONTRACT_ID): IRI,
        ("data_contract_schema", f"{CONTRACT_ID}#fraud_scores"): IRI + "Score",
        ("data_contract_property", f"{CONTRACT_ID}#fraud_scores#claim_id"): IRI + "Id",
    }
    product_doc["authoritativeDefinitions"] = [
        {"type": SEMANTIC_ASSIGNMENT, "url": IRI},
        {"type": "http://other/type", "url": "ignored"},
    ]
    assert [(r.entity_type, r.entity_id) for r in semantic_link_rows(product_doc)] == [("data_product", PRODUCT_ID)]


def test__owned_tags_follow_the_naming_rule_and_classification_is_the_highest_level(contract_doc, product_doc):
    """Tags are data_domain_<path>, data_product_<id without dp_>, data_contract_<id without dc_>, classification."""
    tags = {row.tag_key: row for row in tag_rows(contract_doc)}
    assert set(tags) == {
        "data_domain_claims__litigation__claim_litigation",
        "data_product_claims_fraud_fraud_detection",
        "data_contract_claims_fraud_fraud_scores",
        "classification",
    }
    assert (
        tags["classification"].tag_value == "restricted"
        and tags["classification"].entity_id == "claims_fraud_ml.fraud_scores"
    )
    endpoint_tags = tag_rows(product_doc)
    assert {t.entity_id for t in endpoint_tags} == {"serving_endpoint/claims_fraud/fraud_scorer"}
    assert "data_product_claims_fraud_fraud_detection" in {t.tag_key for t in endpoint_tags}


def test__owner_tags_use_the_team_contacts_and_skip_placeholders(contract_doc, product_doc):
    """Each real contact becomes an owner tag on every asset; a PLACEHOLDER- or missing contact is not published."""
    owners = {"technical_owner": "t@x.com", "data_owner": "PLACEHOLDER-data-owner-email"}
    rows = {(r.entity_id, r.tag_key): r.tag_value for r in tag_rows(contract_doc, owners)}
    assert rows[("claims_fraud_ml.fraud_scores", "technical_owner")] == "t@x.com"
    assert not any(key in {"data_owner", "data_steward"} for _, key in rows)
    assert ("serving_endpoint/claims_fraud/fraud_scorer", "technical_owner") in {
        (r.entity_id, r.tag_key) for r in tag_rows(product_doc, owners)
    }
    assert not any(r.tag_key == "technical_owner" for r in tag_rows(contract_doc))


def test__registration_maps_team_project_contacts_and_groups(registration_doc):
    """A registration yields a team with members and a project with five contacts and three groups."""
    team, project = registration_records(
        registration_doc, repo_id=PLATFORM_REPO_ID, source_path="p.yaml", git_commit="r1"
    )
    assert (team.kind, team.entity_id, project.kind, project.entity_id) == (
        "team",
        "claims_fraud_squad",
        "project",
        "claims_fraud",
    )
    assert {(m["member_type"], m["role"]) for m in team.children["md_team_member"]} >= {
        ("user", "data_owner"),
        ("group", "admins"),
    }
    roles = {row["role"]: row for row in project.children["md_project_contact"]}
    assert set(roles) == {"technical_owner", "data_owner", "executive_owner", "data_steward", "support"}
    assert roles["support"]["channel"] == "https://teams.example/ch"
    assert {g["access_role"]: g["ad_group"] for g in project.children["md_project_group"]}[
        "readers"
    ] == "AAD-DBX-CLAIMSFRAUD-READ"
    assert project.columns["repo_kind"] == "project" and project.columns["service_principal_app_id"] == "sp-1"
    assert project.payload["tags"] == {"cost_center": "cc-1", "region": "au"}


def test__project_tags_are_free_form_values(registration_doc):
    """Each tag of the registration becomes a project tag row."""
    assert project_tag_rows(registration_doc) == [
        TagRow("project", "claims_fraud", "cost_center", "cc-1"),
        TagRow("project", "claims_fraud", "region", "au"),
    ]


def test__a_string_support_contact_is_accepted(registration_doc):
    """The support contact may be a plain email."""
    registration_doc["contacts"]["support"] = "s@corp.com"
    project = registration_records(registration_doc, repo_id=PLATFORM_REPO_ID, source_path="p", git_commit=None)[1]
    assert {r["role"]: r["email"] for r in project.children["md_project_contact"]}["support"] == "s@corp.com"


def test__domains_map_one_record_per_taxonomy_node(minimal_taxonomy_doc):
    """Every node is keyed by its domain path and linked to its parent."""
    records = domain_records(minimal_taxonomy_doc, source_path="d.yaml", git_commit="r1")
    assert [(r.entity_id, r.columns["parent_domain_id"]) for r in records] == [
        ("claims", None),
        ("claims__litigation", "claims"),
        ("claims__litigation__claim_litigation", "claims__litigation"),
    ]
    assert all(r.repo_id == PLATFORM_REPO_ID for r in records)


def test__glossary_concepts_come_from_skos_turtle():
    """Each skos:Concept becomes a record with label, definition, scheme and broader IRIs."""
    turtle = """
    @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix ex: <https://example.org/insurance#> .
    ex:Scheme a skos:ConceptScheme .
    ex:Claim a skos:Concept ; rdfs:label "Claim" ; skos:definition "A request" ; skos:inScheme ex:Scheme ; skos:broader ex:Loss .
    ex:Loss a skos:Concept ; skos:prefLabel "Loss" .
    """
    records = glossary_records(turtle, source_path="g.ttl", git_commit="r1")
    by_id = {r.entity_id: r for r in records}
    claim = by_id["https://example.org/insurance#Claim"]
    assert claim.columns["label"] == "Claim" and claim.columns["broader_iris"] == [
        "https://example.org/insurance#Loss"
    ]
    assert by_id["https://example.org/insurance#Loss"].columns["label"] == "Loss"


def test__a_dataflow_spec_maps_flows_and_skips_sources_that_are_not_tables(tmp_path):
    """A nodespec yields its id, group, layer and one flow per target source; a file source has no asset id."""
    record = dataflow_spec_records(spec_info_for_tests(tmp_path), **OPTIONS)[0]
    assert record.entity_id == "spec_scores" and record.columns == {
        "bundle": "dataops",
        "flow_group": "silver",
        "layer": "silver",
        "spec_type": "nodespec",
    }
    flows = {f["flow_name"]: f for f in record.children["md_dataflow_flow"]}
    assert flows["v_x->t_y"]["source_asset_id"] == "claims_fraud_silver.x"
    assert flows["v_x->t_y"]["target_asset_id"] == "claims_fraud_silver.y"
    assert (
        flows["v_files->t_z"]["source_asset_id"] == ""
        and flows["v_files->t_z"]["target_asset_id"] == "claims_fraud_silver.z"
    )
    assert record.payload["data_flow_id"] == "spec_scores"


def test__a_spec_with_an_unknown_token_is_a_collect_error(tmp_path):
    """A substitution token the bundle does not define is reported as a CollectError naming the token."""
    info = spec_info_for_tests(tmp_path)
    info = replace(info, tokens={})
    with pytest.raises(CollectError, match="unknown substitution token"):
        dataflow_spec_records(info, **OPTIONS)


def derived_property_paths(properties, parent=""):
    """Walk a payload independently of the collector and yield property paths."""
    for prop in properties:
        path = f"{parent}.{prop['name']}" if parent else prop["name"]
        yield path
        yield from derived_property_paths(prop.get("properties", []), path)
        if "items" in prop:
            yield from derived_property_paths([{"name": "[]", **prop["items"]}], path)


def stored_row(engine, table):
    """Return the single row of a table as a dict."""
    with engine.connect() as conn:
        return dict(conn.exec_driver_sql(f"SELECT * FROM {table}").mappings().one())


def test__round_trip_rebuilds_the_document_with_the_same_hash(repo, sqlite_engine, contract_doc, product_doc, ctx):
    """Collect -> publish -> read payload gives the identical content_hash, for contracts and products."""
    repo.upsert_definitions([contract_record(contract_doc), product_record(product_doc)], ctx)
    for table, original in (("md_data_contract", contract_doc), ("md_data_product", product_doc)):
        stored = stored_row(sqlite_engine, table)
        assert content_hash(json.loads(stored["payload"])) == stored["content_hash"] == content_hash(original)


def test__round_trip_contract_columns_equal_values_derived_from_the_payload(repo, sqlite_engine, contract_doc, ctx):
    """The extracted contract columns, properties and rules equal what the stored payload says."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    stored = stored_row(sqlite_engine, "md_data_contract")
    payload = json.loads(stored["payload"])
    assert (stored["name"], stored["api_version"], stored["domain_id"], stored["product_id"], stored["semver"]) == (
        payload["name"],
        payload["apiVersion"],
        payload["domain"],
        payload["dataProduct"],
        payload["version"],
    )
    description = payload["description"]
    assert (stored["purpose"], stored["usage"], stored["limitations"]) == (
        description["purpose"],
        description["usage"],
        description["limitations"],
    )
    with sqlite_engine.connect() as conn:
        properties = {r[0] for r in conn.exec_driver_sql("SELECT property_path FROM md_contract_property")}
        rule_ids = {r[0] for r in conn.exec_driver_sql("SELECT rule_id FROM md_quality_rule")}
    assert properties == set(derived_property_paths(payload["schema"][0]["properties"]))
    assert rule_ids == {"has_rows", "claim_id_not_null"}


def test__round_trip_product_columns_equal_values_derived_from_the_payload(repo, sqlite_engine, product_doc, ctx):
    """The extracted product columns and ports equal what the stored payload says."""
    repo.upsert_definitions([product_record(product_doc)], ctx)
    stored = stored_row(sqlite_engine, "md_data_product")
    payload = json.loads(stored["payload"])
    assert (stored["name"], stored["domain_id"], stored["semver"], stored["project_id"]) == (
        payload["name"],
        payload["domain"],
        payload["version"],
        SQUAD,
    )
    with sqlite_engine.connect() as conn:
        ports = {
            (r[0], r[1], r[2])
            for r in conn.exec_driver_sql("SELECT direction, port_name, contract_id FROM md_product_port")
        }
    assert ports == {
        ("input", "policies", "dc_policy_core_policy"),
        ("output", "scores", CONTRACT_ID),
        ("output", "scorer", ""),
    }
