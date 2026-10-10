# Platform architecture

## What this is

A template repository for metadata-driven DataOps, MLOps and AIOps on Databricks. Pipelines are **dataflow specs** run
by the Lakeflow Framework; every table, product, model and endpoint is described in **Ontos-ready metadata**; and all of
that metadata is published to one shared database, `platform_meta`.

## Three kinds of repository

| Repository | Owned by | Holds | Writes to `platform_meta` |
|---|---|---|---|
| **This template** (`cookie-cutter-dbx`) | platform team | the framework pin, `dbxops` library, example bundles, CI/CD, the registry scaffold | its own example project |
| **`dbx-platform-registry`** (made from `templates/registry-repo/`) | platform team | one registration per squad, the domain taxonomy, required tags, the glossary, the onboarding workflows | organisation records: projects, squads, domains, glossary (`repo_id = _platform`) |
| **A squad repository** (made from the template by the registry) | a squad | its bundles, contracts, products | only its own records (`repo_id = <project_id>`) |

A squad repository carries a read-only snapshot of its registration (`metadata/_platform/`), so it can validate offline.

## How a change flows

```
spec / contract / product edited in git
        |  make validate-metadata, contracts, sync-substitutions   (offline, in CI)
        v
 PR merged -> CD: build wheel -> upload to volume -> databricks bundle deploy -> dbxops publish
        |                                                                         |
        v                                                                         v
 pipelines & jobs run in the workspace                              platform_meta (definitions, deployments, lineage)
        |  last job task: dbxops-publish-run
        v
 platform_meta (runs, pipeline updates, row counts, quality results pinned to contract rule versions)
```

The framework itself is deployed **once per target by the platform team** to a shared, versioned path
(`/Workspace/Shared/.bundle/lakeflow_framework/<target>/<version>/files/src`). Squad bundles only reference it.

## Environments

Five targets (`dev`, `test`, `uat`, `preprod`, `prod`) in one workspace, each with its own catalog; defined once in
`conf/environments.yml`. In `dev`, `logical_env` suffixes schema names per developer. Non-dev targets run as the project's
service principal. See `Environments_And_Deploy.md`.

## Layers and names

`<project_id>_<layer>` schemas with layer in `bronze, silver, gold, ml, ai`. A table's logical id is
`<schema>.<table>`; its contract is `dc_<project_id>_<layer>_<table>`. Every table rolls up to a **data domain**:
the path `business__sub__data` (e.g. `claims__litigation__claim_litigation`).

## Where things live

| Path | What |
|---|---|
| `framework/lakeflow_framework` | the pinned framework (read-only submodule); `framework/local` is our overlay |
| `bundles/{dataops,mlops,aiops}` | example bundles, built from the same conventions |
| `src/dbxops/` | the installable library (see the public API in `dbxops/__init__.py`) |
| `conf/` | targets and metadata-database sinks |
| `metadata/_platform/` | registry-generated, hash-protected snapshots (read-only) |
| `templates/registry-repo/` | scaffold of the registry repository |
| `scripts/` | staging, framework upgrade, skill vendoring |
| `.claude/` | Claude Code commands and skills, including vendored Databricks skills |
