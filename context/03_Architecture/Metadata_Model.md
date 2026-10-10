# Metadata model

The columns of every table are in `Metadata_Dictionary.md` (generated from the model). This page is the reasoning.

## Principles

1. **Git is the source of truth; the database is a versioned read model.** Every definition row carries its content hash and
   the commits that introduced and last confirmed it. Republishing unchanged files writes nothing but `last_seen_commit`.
2. **Natural keys, no UUIDs.** Slugs identify entities (`dp_<project>_<name>`, `dc_<project>_<layer>_<table>`, UC logical
   ids). Ontos assigns its own ids when files are uploaded.
3. **Three table patterns.** A: versioned definitions. B: derived current state. C: append-only facts.
4. **Hybrid storage.** The whole source document is stored as JSON (`payload`); the fields people query are columns.
5. **One model, many dialects.** SQLAlchemy Core with `JsonType` and `UtcTimestamp`; PostgreSQL and Lakebase share a
   dialect; Unity Catalog Delta is rendered with its own SQL.

## Pattern A: definitions (SCD2)

Domains, squads, projects, products, contracts, dataflow specs, assets, glossary concepts. Primary key
`(entity_id, record_version)`.

- **Unchanged content** (same hash): no new version; only `last_seen_commit` moves.
- **Changed content**: the current row is closed and `record_version + 1` is inserted. For contracts and products the
  semver must also increase (the validator and the repository both enforce it).
- **Retire**: a new version with `status = retired` that stays *current*, so views show retired entities. "No current row"
  is never a signal.
- **Re-open**: a retired id that reappears gets a new version. **Rename** is retire plus create (history splits).
- **Children** (contract objects, properties, quality rules, product ports, team members, ...) are a full snapshot per
  parent version, keyed `(parent_id, record_version, ...)`.
- **No NULLs in key columns**: optional key parts use `''`.
- At most one current row per entity is asserted after every publish (Unity Catalog does not enforce primary keys).

## Pattern B: derived current state

Semantic links, tags, lineage edges. Replaced as a set per publisher; a row missing from the next publish becomes
`is_active = false`. Only **owned tag keys** (`data_domain_*`, `data_product_*`, `data_contract_*`, `classification`, the
registry's required project tags) are ever written or removed. Lineage keeps the declared design (`origin = spec`) apart
from what Unity Catalog observed (`uc_system`); `v_lineage_drift` lists observed edges nobody declared.

## Pattern C: facts

Registrations, deployments, runs, pipeline updates, dataset metrics, quality results, evaluation results, the publish log.
Inserted once per idempotency key (a retried CI job or job task writes nothing twice). Quality results are pinned to the
`rule_record_version` current when they were recorded, so a later contract edit never makes a result ambiguous. Per-rule
counts overlap, so they must never be summed; dataset totals are in `md_dataset_metrics`.

## Writers

| Writer | `repo_id` | Owns |
|---|---|---|
| Registry repo | `_platform` | domains, squads and members, projects, contacts, groups, glossary, project tags, registrations |
| Squad repo | its `project_id` | products and ports, contracts and children, dataflow specs and flows, assets, semantic links, its tags, deployments and runs |

`WriterGuard` rejects any write outside the publisher's `repo_id` and tables. Isolation between squads in shared tables is
enforced by this code and audited in `md_publish_log`, **not by the database**; row-level security is a later hardening step.

## Publish atomicity

PostgreSQL and Lakebase: one transaction per entity. Unity Catalog: each statement is its own Delta commit, so children are
written at `N+1` first (invisible to the views), then **one MERGE** closes `N` and inserts `N+1`. A crash in between leaves
orphan children that the next publish deletes first. `md_publish_log` records started, complete or failed per publish.

## Ontos

The database is **not** the Ontos database. `dbxops.metadata.export_ontos` reads the source documents and applies seven
transforms (see `Ontos_Onboarding.md`). Upload to Ontos is file based in version 1.

## Access

Owner emails are personal data. The policy: squad groups read the `v_*_current` views and `v_project_directory` (roles to AD
groups, no emails); only the registry service principal and platform admins read contacts. **The grants that apply this
policy are part of workspace setup, which is not built yet**; until then nothing in the database enforces it. See
`Metadata_DB.md`.
