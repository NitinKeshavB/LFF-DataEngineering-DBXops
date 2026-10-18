# What is built, and what is not verified

**Nothing in this repository has run against a real workspace, database, GitHub organisation or Ontos instance.** Everything is
verified offline: unit and integration tests with fakes and SQLite, the Databricks CLI's own JSON schema for bundle
configuration, the Lakeflow Framework's own validator for specs, `actionlint` and `shellcheck` for workflows and scripts.

## Believed correct but unverified live

- Unity Catalog SQL of the metadata sink (the one-statement MERGE, VARIANT, parameter style) and the PostgreSQL / Lakebase
  statements; the PostgreSQL integration tests exist and skip unless `DBXOPS_TEST_PG_URL` is set.
- Event-log reading: the event types and JSON paths in `metastore/event_log.py` follow the Databricks documentation as understood;
  `dropped` and `quarantined` counts are not separately available from the event log.
- Knowledge Assistant REST paths and registered-model tag REST paths in `workspace.py` (the SDK has neither).
- The AIOps agent wrapper, evaluation, `agents.deploy`, `ai_parse_document` on a serverless pipeline, PDF generation; the MLOps
  training, scoring and `condition_task` behaviour. The MLOps feature MV declares no Unity Catalog primary key, which
  `FeatureLookup` may require.
- GitHub provisioning (repository from template, environments, ruleset) and the `github-oidc` deploy authentication.
- Ontos accepting the exported shapes unchanged.
- **Lakeflow Connect scaffolding** (`make new-bundle ... CONNECTOR=sqlserver|postgresql|sharepoint MODE=lakeflow`, or `CONNECTOR=sftp:lakeflow`). The
  `gateway_definition`/`ingestion_definition` pipeline YAML and the SFTP Auto Loader script are verified against the Databricks CLI's own bundle schema
  (`cli_bundle_validator`) and the current public docs, but no gateway, ingestion pipeline, or UC `CONNECTION` from this path has ever run against a real
  workspace. The `databricks connections create --json` body in each `src/config/land_<name>_connection.md` note is a best-effort starting point, not a
  verified shape for sqlserver/postgresql credentials -- confirm the field names against `databricks connections create --help` or the connector reference
  before running it. SharePoint's connector is Preview-stage and may change before GA.

## Not built

- Workspace setup: creating schemas, applying the privilege matrix, grants on `platform_meta`, applying tags in Unity Catalog. The
  reconciler exists (`MetadataSyncReconciler`) and the dataops job applies the table tags in dev (`apply_table_tags`, dry run elsewhere), but nothing has run
  against a workspace, so whether Unity Catalog accepts tags on streaming tables and materialized views is unverified. The mlops and aiops
  bundles have no tag task. Column-level tags and the `budget_policy_id` job setting are not built.
- Recording registrations (`md_registration`) from the provisioning workflow.
- A scheduler for the retention purge; SQL Server; observed-lineage reader.
- Consolidating the MLOps and AIOps lifecycle wrappers into the package (a `--catalog` option on `dbxops.lifecycle reconcile` would remove them).
- A model monitor; contracts for inference, quarantine and trace tables.
- **Schema migration.** `platform_meta` is schema version 2 (asset tree columns, `md_job_task`, `md_asset_edge`, `md_task_run`). `CREATE TABLE IF NOT EXISTS`
  does not add columns, so a database created at version 1 must be recreated or altered by hand; there is no migration step.
- **The shared library is still carried in each repository.** `conf/upstream.yml` records the pin and `.github/workflows/template-sync.yml` runs `cruft update`,
  but `dbxops` is not yet published as a wheel to an index and generated repos still build their own copy from `src/dbxops`.
- **Asset discovery.** Models, agents and Unity Catalog functions are placed in the tree only when a bundle resource, a `src/vector_search`, `src/serving` or
  `src/functions` file or a product port declares them. A model registered by job code alone (`register_agent.py`) has no asset until it is declared.
- **Notifications.** Every job notifies Teams and email on failure and on a duration warning; `on_success` for prod releases, `on_streaming_backlog_exceeded`
  (no streaming task exists), the SQL alerts and the Teams deployment message are not built. The destination ids are placeholders and untested against a workspace.
- **Review agent in CI.** `/review` and the `code-reviewer` agent run locally; CI does not yet post its report as a PR comment or block on its decisions.
- **Run-to-asset joins.** `v_asset_tree` joins runs to assets through the workspace object ids that a deployment records (`md_deployed_asset`), so an asset
  shows `running` only after `publish --what deployments` has run for that target. `deployed, not running` is "deployed with no run recorded", not "no run
  inside the schedule window", because job schedules are not stored in `platform_meta`.

## Decisions to confirm

The framework is deployed once per target by the platform team, not by each squad. Squad bundles reference its shared path.
