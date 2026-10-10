# Spec, contract and product conventions

## Dataflow specs

The pinned framework's default spec type is `nodespec`: a `nodes` list of `source` and `target` nodes. Our tooling reads
nodespecs only (legacy `standard`/`flow`/`materialized_view` specs are not supported). Specs are `*_main.json` files in
`<layer dir>/dataflowspec/`; schemas are Spark struct JSON in `<layer dir>/schemas/`; expectations are
`expect | expect_or_drop | expect_or_fail` lists in `<layer dir>/expectations/`; materialized-view SQL is in `<layer dir>/dml/`.
A file may be a **template invocation** (`template` + `parameter_sets`), expanded the way the framework expands it
(`${param.x}`, defaults, whole-string placeholders keep their type); the expanded specs are what get validated and published.

Every spec carries `tags.data_domain` (a data-domain path) and `tags.data_product` (the product that exposes its tables).
Every target needs a `schema_path`; a quarantine table is derived from its parent (`<table>_quarantine`) and has no contract.

Schema names come from substitution tokens (`{silver_schema}`) defined per target in
`<bundle>/src/pipeline_configs/<target>_substitutions.json`, **generated** by `make sync-substitutions` from
`conf/environments.yml` and the project id. The resolved schema must be `<project_id>_<layer>`.

## Contracts (ODCS v3.1.0)

One per table, at `<layer dir>/contracts/<table>.odcs.yaml`, id `dc_<project_id>_<layer>_<table>`.

| Machine-owned (`make contracts` rewrites) | Human-owned (kept as written) |
|---|---|
| `servers[]`; the schema object's `name`, `physicalName` (prod FQN), `logicalType`, `dbx.asset_id`/`dbx.spec_id` | `description` (purpose, usage, limitations), `status`, `version`, `domain`, `dataProduct` |
| each property's `name`, `logicalType`, `physicalType`, `required` | property `primaryKey`, `businessName`, `description`, `classification`, semantic assignments, tags |
| quality rules whose `dbx.origin` is `expectation` | any other quality rule |

New contracts carry `PLACEHOLDER-purpose|usage|limitations` until a person writes them; `make check-placeholders` then blocks
deployment. A table written by a **job** rather than a spec has a hand-authored schema and `dbx.producer: job` on its schema
object; the spec-coverage and drift rules skip it.

Quality-rule ids are `<table>_<slug of the expectation name>`; the event-log reader derives the same id, which is how run
results are matched to rules. Custom properties used: `dbx.asset_id`, `dbx.spec_id`, `dbx.origin`, `dbx.action` (warn, drop,
fail, quarantine), `dbx.constraint`, `dbx.quarantine_asset_id`, `dbx.producer`, `dbx.assetId` (on ports), `dbx.assetSubtype`.

A table in a **Restricted** data domain is created classified `restricted`, and the validator requires it.

## Products (ODPS v1.0.0)

Id `dp_<project_id>_<name>`; `name` is the id without `dp_`. Every contract is an output port of exactly one product. An input
port names a contract (`dc_...`); one from another repository is only a warning offline and is verified at publish time. Output
port `type` is the asset type (`uc_streaming_table`, `uc_materialized_view`, `uc_model`, `serving_endpoint`, `agent`, ...);
`dbx.assetId` carries the logical asset id.

## Versioning

A changed contract or product needs a higher semver; removing or retyping a column needs a major bump (checked against a git
ref: `BASELINE=origin/main make validate-metadata`).

## Rules, in one place

`make validate-metadata` runs: platform snapshot integrity, repo kind, generated-file drift, JSON schemas, pinned
`apiVersion`s, ids and prefixes, domains (data-domain level; inside the repo's domain for a domain repo), restricted
classification, one contract per spec target, contract/spec drift, product ports, semantic assignments against the glossary,
and semver.
