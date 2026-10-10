---
name: dataops-design
description: Design or change DataOps pipelines on this platform — Lakeflow Framework dataflow specs (nodespec), Auto Loader bronze, CDC silver (SCD1/SCD2), data-quality expectations and quarantine, gold materialized views, pipelines and jobs, and their performance settings. Use WHENEVER the user adds a bundle, a spec, a pipeline, a CDC flow, an expectation or a materialized view, asks how to structure layers, or asks for a performance or cost improvement to a pipeline. Do NOT use for contracts, products and tags (metadata-ontology), ML (mlops-design) or agents (aiops-design).
---

# DataOps design

Start from the working example: `bundles/dataops/` (bronze template with one parameter set per entity, silver SCD2/SCD1/append with
quarantine, gold materialized views, a job that ends by recording its run). Scaffold a new bundle with
`make new-bundle NAME=<n> DOMAIN=<data-domain-path>` rather than copying by hand. For spec syntax use the vendored
`dataflowspec-builder` skill and the framework's own validator (`make validate-specs`); for pipeline and job API details use the vendored
`databricks-pipelines`, `databricks-jobs` and `databricks-dabs` skills.

## Conventions

- One spec file per table (or a template invocation with one parameter set per entity). `data_flow_group` names the layer and is what a
  pipeline's `pipeline.dataFlowGroupFilter` selects; every group needs a pipeline and every pipeline a group.
- Schemas are `{<layer>_schema}` tokens resolving to `<project_id>_<layer>`; never hard-code a catalog or schema.
- Silver CDC: `cdc_settings` with `keys`, `sequence_by`, `apply_as_deletes`, `except_column_list`, and `track_history_column_list` for SCD2. Append-only facts get expectations plus
  `quarantine: {mode: table}`.
- Expectations: `expect` warns, `expect_or_drop` drops (quarantined when a quarantine table is configured), `expect_or_fail` fails the update.
- Gold materialized views: `table_type: mv`, `sql_path`, `schema_path` and a `comment`; SQL refers to tables as `{silver_schema}.<table>`.

## Performance defaults (keep them unless you can show why not)

Serverless pipelines, triggered not continuous; `cluster_by_auto: true`; `delta.enableRowTracking` so materialized views refresh incrementally;
`delta.enableChangeDataFeed` on tables that feed downstream streams; streaming tables for appends and CDC, materialized views for
aggregates; Auto Loader with schema provided and addNewColumns; job `performance_target` STANDARD below preprod; predictive optimisation on the catalog.

## After any change

`make validate-specs`, then the metadata loop (`metadata-ontology`), then `make test`. The bundle consistency tests
(`tests/bundles/test_bundles.py`) check targets, catalogs, permissions, tags and that every job ends with `publish_run_metadata`.
