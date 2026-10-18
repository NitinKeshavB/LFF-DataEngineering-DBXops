"""
Pure collectors: map already-parsed YAML/TTL documents to repository records.

Nothing here reads files or talks to a service; discovery belongs to ``dbxops.metadata``. Every function takes
dicts (or text) and returns records, so the whole mapping is testable offline.

Conventions for fields the ODCS/ODPS standards leave open (all read from ``customProperties``, a list of
``{property, value}``):

* quality rules: ``dbx.action`` (warn|drop|fail|quarantine, default ``warn``), ``dbx.origin`` (expectation|manual,
  default ``manual``), ``dbx.spec_id`` (on the schema object, or the rule), ``dbx.quarantine_asset_id``, ``dbx.constraint``; ``rule_id`` is the rule's
  ``id``, else its ``name``, else ``<object>.<property>.<position>``;
* PII: a property custom property ``pii`` that is true;
* output port asset: the port's ``assetId`` (or ``dbx.assetId``) and ``assetType`` (default ``table``);
* nested properties: ``property_path`` joins names with ``.`` and array ``items`` appear as the segment ``[]``.

The runtime collectors (runs, deployments, lineage) live in ``runtime_collectors``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import (
    Any,
    Iterator,
    Mapping,
    Sequence,
)

from rdflib import (
    RDF,
    RDFS,
    Graph,
)
from rdflib.namespace import SKOS

from dbxops.metadata.export_ontos import ONTOS_NATIVE_TYPES
from dbxops.metadata.repo import SpecInfo
from dbxops.metadata.specs import (
    SpecError,
    asset_id,
)
from dbxops.metastore.errors import CollectError
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
    DefinitionRecord,
    EntityRef,
    Provenance,
    SemanticLinkRow,
    TagRow,
)
from dbxops.registry.domains import DomainTaxonomy

SEMANTIC_ASSIGNMENT = "http://databricks.com/ontology/uc/semanticAssignment"
_PHYSICAL_TO_ASSET_TYPE = {
    "table": "uc_table",
    "view": "uc_view",
    "materialized_view": "uc_materialized_view",
    "streaming_table": "uc_streaming_table",
}
CLASSIFICATION_ORDER = ("public", "internal", "confidential", "restricted")
_CONTACT_ROLES = ("technical_owner", "data_owner", "executive_owner", "data_steward")
OWNER_TAG_KEYS = ("technical_owner", "data_owner", "data_steward")


# ---------------------------------------------------------------------- helpers


def _custom(item: Mapping[str, Any], name: str, default: Any = "") -> Any:
    for entry in item.get("customProperties") or ():
        if entry.get("property") == name:
            return entry.get("value", default)
    return default


def _require(doc: Mapping[str, Any], key: str, what: str) -> Any:
    value = doc.get(key)
    if value in (None, ""):
        raise CollectError(f"{what} is missing required field {key!r}")
    return value


def strip_id_prefix(identifier: str) -> str:
    """Return an entity id without its ``dp_``/``dc_`` prefix."""
    return identifier.removeprefix("dp_").removeprefix("dc_")


def asset_id_from_physical_name(physical_name: str) -> str:
    """Return the logical asset id (``<schema>.<name>``) of a physical name ``catalog.schema.name``."""
    parts = physical_name.split(".")
    return ".".join(parts[-2:]) if len(parts) == 3 else physical_name


def _fqn_template(physical_name: str) -> str:
    parts = physical_name.split(".")
    return ".".join(["{catalog}", *parts[1:]]) if len(parts) == 3 else physical_name


def _iter_properties(
    properties: Sequence[Mapping[str, Any]], parent_path: str = ""
) -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    """Yield ``(property_path, parent_path, property)`` depth first; array ``items`` are the segment ``[]``."""
    for prop in properties or ():
        name = "[]" if prop.get("_items") else _require(prop, "name", "a property")
        path = f"{parent_path}.{name}" if parent_path else name
        yield path, parent_path, prop
        yield from _iter_properties(prop.get("properties") or (), path)
        if prop.get("items"):
            yield from _iter_properties([{**prop["items"], "_items": True}], path)


def _description(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    description = doc.get("description")
    if isinstance(description, str):
        return {"purpose": description}
    return description or {}


def _semver(doc: Mapping[str, Any]) -> str | None:
    return str(doc["version"]) if doc.get("version") is not None else None


# --------------------------------------------------------------------- contracts


def contract_records(
    doc: Mapping[str, Any],
    *,
    repo_id: str,
    source_path: str,
    git_commit: str | None,
    owner_team_id: str | None = None,
) -> list[DefinitionRecord]:
    """Map an ODCS data contract to its ``data_contract`` record with servers, objects, properties and rules."""
    contract_id = _require(doc, "id", "a data contract")
    description = _description(doc)
    objects, properties, rules = _schema_children(doc)
    children = {
        "md_contract_server": [
            {
                "environment": server.get("environment") or server.get("server") or "",
                "type": server.get("type"),
                "catalog": server.get("catalog"),
                "schema": server.get("schema"),
            }
            for server in doc.get("servers") or ()
        ],
        "md_contract_object": objects,
        "md_contract_property": properties,
        "md_quality_rule": rules,
    }
    columns = {
        "name": doc.get("name"),
        "api_version": doc.get("apiVersion"),
        "domain_id": doc.get("domain"),
        "product_id": doc.get("dataProduct"),
        "owner_team_id": owner_team_id,
        "purpose": description.get("purpose"),
        "usage": description.get("usage"),
        "limitations": description.get("limitations"),
    }
    return [
        DefinitionRecord(
            EntityRef("data_contract", contract_id, repo_id),
            doc,
            doc.get("status") or "draft",
            _semver(doc),
            columns,
            children,
            Provenance(source_path, git_commit),
        )
    ]


def _schema_children(doc: Mapping[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    objects: list[dict] = []
    properties: list[dict] = []
    rules: list[dict] = []
    for obj in doc.get("schema") or ():
        name = _require(obj, "name", "a schema object")
        physical = obj.get("physicalName") or ""
        objects.append(
            {
                "object_name": name,
                "physical_name": physical,
                "logical_type": obj.get("logicalType") or "object",
                "physical_type": obj.get("physicalType") or "",
                "business_name": obj.get("businessName"),
                "asset_id": asset_id_from_physical_name(physical) if physical else "",
            }
        )
        spec_id = _custom(obj, "dbx.spec_id") or ""
        rules += _rules(obj.get("quality"), name, "", spec_id)
        for path, parent_path, prop in _iter_properties(obj.get("properties") or ()):
            properties.append(_property_row(name, path, parent_path, prop))
            rules += _rules(prop.get("quality"), name, path, spec_id)
    _check_unique_rules(doc.get("id", ""), rules)
    return objects, properties, rules


def _property_row(object_name: str, path: str, parent_path: str, prop: Mapping[str, Any]) -> dict[str, Any]:
    position = prop.get("primaryKeyPosition")
    return {
        "object_name": object_name,
        "property_path": path,
        "parent_path": parent_path,
        "logical_type": prop.get("logicalType"),
        "physical_type": prop.get("physicalType"),
        "required": bool(prop.get("required", False)),
        "primary_key": bool(prop.get("primaryKey", False)),
        "pk_position": position if isinstance(position, int) and position > 0 else None,
        "unique": bool(prop.get("unique", False)),
        "partitioned": bool(prop.get("partitioned", False)),
        "classification": prop.get("classification"),
        "critical": bool(prop.get("criticalDataElement", False)),
        "is_pii": bool(_custom(prop, "pii", False)),
        "business_name": prop.get("businessName"),
        "description": prop.get("description"),
    }


def _rules(
    quality: Sequence[Mapping[str, Any]] | None, object_name: str, property_path: str, spec_id: str = ""
) -> list[dict]:
    rows = []
    for position, item in enumerate(quality or ()):
        rule_id = item.get("id") or item.get("name") or f"{object_name}.{property_path or '_'}.{position}"
        rows.append(
            {
                "rule_id": rule_id,
                "object_name": object_name,
                "property_path": property_path,
                "type": item.get("type"),
                "dimension": item.get("dimension"),
                "expression": _expression(item),
                "action": _custom(item, "dbx.action") or "warn",
                "quarantine_asset_id": _custom(item, "dbx.quarantine_asset_id"),
                "origin": _custom(item, "dbx.origin") or "manual",
                "spec_id": _custom(item, "dbx.spec_id") or spec_id,
            }
        )
    return rows


def _expression(item: Mapping[str, Any]) -> str:
    for candidate in (
        _custom(item, "dbx.constraint"),
        item.get("query"),
        item.get("rule"),
        item.get("implementation"),
    ):
        if candidate:
            return str(candidate)
    return ""


def _check_unique_rules(contract_id: str, rules: Sequence[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule["rule_id"] in seen:
            raise CollectError(f"{contract_id}: duplicate quality rule id {rule['rule_id']!r}; give each rule an id")
        seen.add(rule["rule_id"])


# ----------------------------------------------------------------------- products


def product_records(
    doc: Mapping[str, Any],
    *,
    repo_id: str,
    source_path: str,
    git_commit: str | None,
    owner_team_id: str | None = None,
) -> list[DefinitionRecord]:
    """Map an ODPS data product to its ``data_product`` record with ports and input contracts."""
    product_id = _require(doc, "id", "a data product")
    ports = [_port_row("input", port, "table") for port in doc.get("inputPorts") or ()]
    input_contracts: list[dict] = []
    for port in doc.get("outputPorts") or ():
        ports.append(_port_row("output", port, "table" if port.get("contractId") else ""))
        for contract in port.get("inputContracts") or ():
            input_contracts.append(
                {
                    "port_name": port["name"],
                    "contract_id": _require(contract, "id", f"an input contract of port {port['name']!r}"),
                    "contract_semver": contract.get("version"),
                }
            )
    columns = {
        "name": doc.get("name"),
        "api_version": doc.get("apiVersion"),
        "domain_id": doc.get("domain"),
        "project_id": repo_id,
        "owner_team_id": owner_team_id,
    }
    return [
        DefinitionRecord(
            EntityRef("data_product", product_id, repo_id),
            doc,
            doc.get("status") or "draft",
            _semver(doc),
            columns,
            {"md_product_port": ports, "md_port_input_contract": input_contracts},
            Provenance(source_path, git_commit),
        )
    ]


def _port_row(direction: str, port: Mapping[str, Any], default_type: str) -> dict[str, Any]:
    return {
        "direction": direction,
        "port_name": _require(port, "name", f"an {direction} port"),
        "port_version": port.get("version"),
        "contract_id": port.get("contractId"),
        "asset_id": port.get("assetId") or _custom(port, "dbx.assetId"),
        "asset_type": port.get("assetType") or default_type,
        "contains_pii": bool(port.get("containsPii", False)),
    }


# ------------------------------------------------------------------------ assets


def asset_records(
    doc: Mapping[str, Any], *, repo_id: str, source_path: str, git_commit: str | None
) -> list[DefinitionRecord]:
    """
    Map a contract or product to ``asset`` records.

    A contract yields one table-like asset per schema object; a product yields one asset per output port whose
    ``assetType`` is not a table. The same asset may come from several documents; the publisher de-duplicates.
    """
    kind = doc.get("kind")
    if kind == "DataContract":
        found = _contract_assets(doc)
    elif kind == "DataProduct":
        found = _product_assets(doc)
    else:
        raise CollectError(f"cannot derive assets from a document of kind {kind!r}")
    columns = ("asset_type", "ontos_asset_type", "fqn_template", "bundle", "resource_key", "product_id")
    return [
        DefinitionRecord(
            EntityRef("asset", asset["asset_id"], repo_id),
            asset,
            "active",
            columns={name: asset[name] for name in columns},
            provenance=Provenance(source_path, git_commit),
        )
        for asset in found
    ]


def _asset(asset_id: str, asset_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "ontos_asset_type": asset_type if asset_type in ONTOS_NATIVE_TYPES else "generic",
        "fqn_template": extra.pop("fqn_template", ""),
        "bundle": extra.pop("bundle", ""),
        "resource_key": extra.pop("resource_key", ""),
        "product_id": extra.pop("product_id", ""),
        **extra,
    }


def _contract_assets(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    assets = []
    for obj in doc.get("schema") or ():
        physical = obj.get("physicalName") or ""
        if not physical:
            continue
        asset_type = _PHYSICAL_TO_ASSET_TYPE.get(obj.get("physicalType") or "table", "uc_table")
        assets.append(
            _asset(
                asset_id_from_physical_name(physical),
                asset_type,
                fqn_template=_fqn_template(physical),
                product_id=doc.get("dataProduct") or "",
                contract_id=doc["id"],
            )
        )
    return assets


def _product_assets(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    assets = []
    for port in doc.get("outputPorts") or ():
        asset_type = port.get("assetType") or ("table" if port.get("contractId") else "")
        asset_id = port.get("assetId") or _custom(port, "dbx.assetId")
        if asset_type in ("", "table") or not asset_id:
            continue
        extra = {"subtype": _custom(port, "dbx.assetSubtype")} if asset_type not in ONTOS_NATIVE_TYPES else {}
        assets.append(
            _asset(
                asset_id,
                asset_type,
                bundle=_custom(port, "dbx.bundle"),
                resource_key=_custom(port, "dbx.resourceKey"),
                product_id=doc["id"],
                **extra,
            )
        )
    return assets


# --------------------------------------------------------------- semantic links


def semantic_link_rows(doc: Mapping[str, Any]) -> list[SemanticLinkRow]:
    """
    Map ``semanticAssignment`` authoritative definitions of a contract or product to link rows.

    Entity ids follow plan D4.3: the contract or product id, ``{contract}#{object}`` and
    ``{contract}#{object}#{property_path}``. No ``uc_table``/``uc_column`` links are emitted.
    """
    entity_id = _require(doc, "id", "a document")
    if doc.get("kind") == "DataProduct":
        return _links("data_product", entity_id, doc)
    links = _links("data_contract", entity_id, doc)
    for obj in doc.get("schema") or ():
        object_id = f"{entity_id}#{obj['name']}"
        links += _links("data_contract_schema", object_id, obj)
        for path, _, prop in _iter_properties(obj.get("properties") or ()):
            links += _links("data_contract_property", f"{object_id}#{path}", prop)
    return links


def _links(entity_type: str, entity_id: str, item: Mapping[str, Any]) -> list[SemanticLinkRow]:
    return [
        SemanticLinkRow(entity_type, entity_id, definition["url"], definition.get("description") or "")
        for definition in item.get("authoritativeDefinitions") or ()
        if definition.get("type") == SEMANTIC_ASSIGNMENT and definition.get("url")
    ]


# -------------------------------------------------------------------------- tags


def tag_rows(doc: Mapping[str, Any], owners: Mapping[str, str] | None = None) -> list[TagRow]:
    """
    Map a contract or product to the owned tags of its assets.

    Each asset gets ``data_domain_<path>``, ``data_product_<name>`` and ``data_contract_<name>`` (the id without its
    ``dp_``/``dc_`` prefix, value empty) and, for contract tables, ``classification`` set to the highest
    classification among the table's properties. ``owners`` maps ``technical_owner``, ``data_owner`` and
    ``data_steward`` to a contact; each one that is set (not a ``PLACEHOLDER-`` value) becomes a tag of that name.
    """
    if doc.get("kind") == "DataProduct":
        return _product_tags(doc, owners)
    rows: list[TagRow] = []
    for obj in doc.get("schema") or ():
        if not obj.get("physicalName"):
            continue
        asset_id = asset_id_from_physical_name(obj["physicalName"])
        rows += _asset_tags(asset_id, doc.get("domain"), doc.get("dataProduct"), doc["id"])
        level = _highest_classification(obj.get("properties") or ())
        if level:
            rows.append(TagRow("asset", asset_id, "classification", level))
        rows += _owner_tags(asset_id, owners)
    return rows


def _owner_tags(asset_id: str, owners: Mapping[str, str] | None) -> list[TagRow]:
    return [
        TagRow("asset", asset_id, role, str(owners[role]))
        for role in OWNER_TAG_KEYS
        if owners and owners.get(role) and not str(owners[role]).startswith("PLACEHOLDER-")
    ]


def _product_tags(doc: Mapping[str, Any], owners: Mapping[str, str] | None) -> list[TagRow]:
    rows: list[TagRow] = []
    for asset in _product_assets(doc):
        rows += _asset_tags(asset["asset_id"], doc.get("domain"), doc["id"], None)
        rows += _owner_tags(asset["asset_id"], owners)
    return rows


def _asset_tags(asset_id: str, domain: str | None, product_id: str | None, contract_id: str | None) -> list[TagRow]:
    rows = []
    if domain:
        rows.append(TagRow("asset", asset_id, f"data_domain_{domain}"))
    if product_id:
        rows.append(TagRow("asset", asset_id, f"data_product_{strip_id_prefix(product_id)}"))
    if contract_id:
        rows.append(TagRow("asset", asset_id, f"data_contract_{strip_id_prefix(contract_id)}"))
    return rows


def _highest_classification(properties: Sequence[Mapping[str, Any]]) -> str:
    levels = [
        CLASSIFICATION_ORDER.index(prop["classification"])
        for _, _, prop in _iter_properties(properties)
        if prop.get("classification") in CLASSIFICATION_ORDER
    ]
    return CLASSIFICATION_ORDER[max(levels)] if levels else ""


def project_tag_rows(doc: Mapping[str, Any]) -> list[TagRow]:
    """Map the free-form ``tags`` of a registration to project tags (owned by the registry)."""
    project_id = _require(doc, "project_id", "a registration")
    return [TagRow("project", project_id, key, str(value)) for key, value in (doc.get("tags") or {}).items()]


# ------------------------------------------------------------------ organisation


def registration_records(
    doc: Mapping[str, Any], *, repo_id: str = PLATFORM_REPO_ID, source_path: str, git_commit: str | None
) -> list[DefinitionRecord]:
    """Map a registry ``projects/<project_id>.yaml`` to its ``team`` and ``project`` records with children."""
    squad = _require(doc, "squad", "a registration")
    team_id = _require(squad, "name", "the squad")
    provenance = Provenance(source_path, git_commit)
    team = DefinitionRecord(
        EntityRef("team", team_id, repo_id),
        {**squad, "domain": doc.get("domain")},
        "active",
        columns={
            "name": team_id,
            "title": squad.get("title"),
            "domain_id": doc.get("domain"),
            "github_team": squad.get("github_team"),
        },
        children={"md_team_member": _team_members(doc)},
        provenance=provenance,
    )
    project = DefinitionRecord(
        EntityRef("project", _require(doc, "project_id", "a registration"), repo_id),
        doc,
        doc.get("status") or "approved",
        columns=_project_columns(doc, team_id),
        children={
            "md_project_contact": _contact_rows(doc.get("contacts") or {}),
            "md_project_group": [
                {"access_role": role, "ad_group": group} for role, group in (doc.get("groups") or {}).items()
            ],
        },
        provenance=provenance,
    )
    return [team, project]


def _team_members(doc: Mapping[str, Any]) -> list[dict[str, str]]:
    contacts, groups = doc.get("contacts") or {}, doc.get("groups") or {}
    members = [
        {"member_type": "user", "member_identifier": contacts[role], "role": role}
        for role in _CONTACT_ROLES
        if contacts.get(role)
    ]
    members += [
        {"member_type": "group", "member_identifier": group, "role": role} for role, group in groups.items() if group
    ]
    return members


def _contact_rows(contacts: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [{"role": role, "email": contacts[role]} for role in _CONTACT_ROLES if contacts.get(role)]
    support = contacts.get("support")
    if support:
        email, channel = (
            (support, None) if isinstance(support, str) else (support.get("email"), support.get("channel"))
        )
        rows.append({"role": "support", "email": email, "channel": channel})
    return rows


def _project_columns(doc: Mapping[str, Any], team_id: str) -> dict[str, Any]:
    return {
        "title": (doc.get("squad") or {}).get("title"),
        "project_type": "TEAM",
        "repo_kind": doc.get("kind"),
        "owner_team_id": team_id,
        "domain_id": doc.get("domain"),
        "repo_url": (doc.get("repo") or {}).get("url"),
        "service_principal_app_id": (doc.get("service_principal") or {}).get("application_id"),
    }


def domain_records(
    doc: Mapping[str, Any], *, repo_id: str = PLATFORM_REPO_ID, source_path: str, git_commit: str | None
) -> list[DefinitionRecord]:
    """Map the domain taxonomy (``domains.yaml``) to one ``domain`` record per node, keyed by domain path."""
    provenance = Provenance(source_path, git_commit)
    records = []
    for node in DomainTaxonomy.from_dict(doc).nodes():
        payload = {
            "path": node.path,
            "slug": node.slug,
            "name": node.name,
            "level": node.level,
            "display_path": node.display_path,
            "parent_path": node.parent_path,
            "details": asdict(node.details),
        }
        columns = {"name": node.name, "parent_domain_id": node.parent_path, "description": node.details.notes}
        records.append(
            DefinitionRecord(
                EntityRef("domain", node.path, repo_id), payload, "active", columns=columns, provenance=provenance
            )
        )
    return records


def glossary_records(
    turtle: str, *, repo_id: str = PLATFORM_REPO_ID, source_path: str, git_commit: str | None
) -> list[DefinitionRecord]:
    """Map a SKOS glossary (Turtle text) to one ``glossary_concept`` record per ``skos:Concept``."""
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    provenance = Provenance(source_path, git_commit)
    return [
        DefinitionRecord(
            EntityRef("glossary_concept", str(concept), repo_id),
            _concept_payload(graph, concept),
            "active",
            columns=_concept_payload(graph, concept),
            provenance=provenance,
        )
        for concept in sorted(graph.subjects(RDF.type, SKOS.Concept), key=str)
    ]


def _concept_payload(graph: Graph, concept: Any) -> dict[str, Any]:
    label = graph.value(concept, RDFS.label) or graph.value(concept, SKOS.prefLabel)
    definition = graph.value(concept, SKOS.definition)
    scheme = graph.value(concept, SKOS.inScheme)
    return {
        "iri": str(concept),
        "label": str(label) if label else "",
        "definition": str(definition) if definition else "",
        "scheme_iri": str(scheme) if scheme else "",
        "broader_iris": sorted(str(item) for item in graph.objects(concept, SKOS.broader)),
    }


# ------------------------------------------------------------------ dataflow specs


def _flow_rows(info: SpecInfo) -> list[dict[str, Any]]:
    """One flow per (source table node -> target table); sources that are not tables (files, SQL) have no asset."""
    rows = []
    for target in info.spec.targets:
        target_asset = asset_id(target.database, target.table, info.tokens)
        for name in target.sources:
            source = info.spec.sources.get(name)
            rows.append(
                {
                    "flow_name": f"{name}->{target.node}",
                    "flow_type": "nodespec",
                    "source_asset_id": asset_id(source.database, source.table, info.tokens) if source else "",
                    "target_asset_id": target_asset,
                    "target_kind": "table",
                }
            )
    return rows


def dataflow_spec_records(
    info: SpecInfo, *, repo_id: str, source_path: str, git_commit: str | None
) -> list[DefinitionRecord]:
    """
    Map a parsed nodespec (``dbxops.metadata`` loaders) to a ``dataflow_spec`` record with its flows.

    The record's payload is the expanded spec document; bundle and layer come from where the spec lives. A flow
    exists for each target's source node that reads a table; its source and target are logical asset ids.
    """
    try:
        flows = _flow_rows(info)
    except SpecError as error:
        raise CollectError(str(error)) from error
    spec = info.spec
    columns = {
        "bundle": info.bundle_dir.name,
        "flow_group": spec.flow_group,
        "layer": spec.layer_dir.name,
        "spec_type": "nodespec",
    }
    return [
        DefinitionRecord(
            EntityRef("dataflow_spec", spec.spec_id, repo_id),
            spec.raw,
            "active",
            columns=columns,
            children={"md_dataflow_flow": flows},
            provenance=Provenance(source_path, git_commit),
        )
    ]
