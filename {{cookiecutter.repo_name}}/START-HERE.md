# START HERE

The repo has many folders because it is a whole platform. For day-to-day DataOps work you touch about five of them.
This page says which ones, and which to ignore until you need them.

## The five folders you edit

| Folder | What you do there |
|---|---|
| `bundles/dataops/src/dataflows/{bronze,silver,gold}/` | Add a table: a dataflow spec (`dataflowspec/`), a schema (`schemas/`), a data contract (`contracts/`), and for silver the expectations (`expectations/`) |
| `bundles/dataops/src/config/` | Per-target settings: catalogs, ingestion method per table |
| `bundles/dataops/src/products/` | The data product that exposes your tables |
| `bundles/dataops/resources/` | Jobs and pipelines (only when you add a layer or a schedule) |
| `conf/` | Targets (dev to prod) and the `platform_meta` sinks. Replace the `PLACEHOLDER-` values |

## Folders you can ignore at first

| Folder | Why it is there |
|---|---|
| `src/dbxops/` | The platform library. Changes here are API changes for every squad |
| `framework/` | The pinned Lakeflow Framework. Read-only |
| `metadata/_platform/` | Snapshot owned by the registry. Read-only |
| `bundles/mlops/`, `bundles/aiops/` | Example ML and AI bundles. Not needed for DataOps |
| `templates/registry-repo/` | Scaffold for the registry repository. Only for the platform team |
| `.claude/` | Claude Code instructions, skills, the `context/` spec layer, `LOCAL-MEMORY/`, licences. Plus `CLAUDE.md` at the root |
| `tests/`, `scripts/`, `run.sh`, `.github/` | Tests, helpers, CI. They run for you |

## First day: the commands

```bash
git add -A && git commit -m "Initial commit"   # then work on a branch, not main
git switch -c feat/my-first-table
make install                # once
make test                   # proves the fresh repo is green
make validate-metadata      # contracts, products, specs: 0 errors expected
make check-placeholders     # the list of config values you still have to fill in
make tags-plan TARGET=dev   # the governance tags your tables will get in Unity Catalog (needs a real catalog)
make sync-table-tags BUNDLE=dataops   # after adding a table or changing a classification: refresh the file the job applies
make new-bundle NAME=<name> DOMAIN=<data-domain-path>   # only if you need a bundle beyond the examples
```

## Tags you must set

| Where | Tag |
|---|---|
| each dataflow spec | `tags.data_domain` and `tags.data_product` |
| each contract column | `classification`: `public`, `internal`, `confidential` or `restricted` (replace `PLACEHOLDER-classification`) |
| jobs and pipelines | set for you from the bundle: `cost_center`, `region`, `project_id`, `bundle`, `target`, `workload`, `git_*` |
| owners | `technical_owner`, `data_owner`, `data_steward` come from the registry team, so ask the registry to change them |

`make validate-metadata` fails when one is missing or wrong. The full list is in `.claude/context/04_Spec_Reference/Spec_Conventions.md`.

## Add a source, step by step

1. Copy the closest example under `bundles/dataops/src/dataflows/bronze/` (spec, schema and contract) and rename it.
2. Do the same in `silver/`, adding expectations and a CDC setting if rows change.
3. Run `make contracts` (fills the machine-owned contract fields), then `make validate-specs` and `make validate-metadata`.
4. Run `make test`, open a pull request and let CI run the same checks.

When a command fails, its message names the file and rule. Fix that and re-run it.

Rules for working with Claude Code are in `CLAUDE.md`. Full detail is in `README.md` and `.claude/context/`.
