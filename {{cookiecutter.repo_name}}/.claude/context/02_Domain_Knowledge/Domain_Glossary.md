# Domain Glossary — cookie-cutter-dbx

**This is the highest-ROI file in the repository, and it starts empty.**

One agreed name per concept, with the synonyms to avoid. Without it, the same idea
quietly becomes `user`, `account`, `customer` and `principal` across four modules, and
every reader afterwards pays for it. Retrofitting consistent vocabulary onto a package
that already has public API is expensive; adding a row here costs a minute.

Fill it in as soon as this package has its first domain concept — not later.

## How to use it

1. One row per term. Canonical name in **Title Case**.
2. `(use)` marks an acceptable synonym; `(avoid)` marks one that is forbidden.
3. The canonical name is what appears in code, docstrings, errors and commit messages.
4. For a term that needs more than a sentence, add `Glossary_Details/<term-slug>.md`
   and link it here.
5. When you need a term that is missing, **add the row rather than coining usage
   ad hoc.**

Under the `full` doctrine this file is binding: canonical terms only, and any domain
term used must be cited back to a row here (`CLAUDE.md` §11). Under `lean` it is
advisory but still read.

## Terms

| Term | Definition | Synonyms / Notes |
|---|---|---|
| Domain Taxonomy | The platform-owned three-level hierarchy Business Domain → Sub-domain → Data Domain, held in `metadata/_platform/domains.yaml` (client source, 2026-10-10). | (avoid) domain list, domain model |
| Business Domain | Top level of the Domain Taxonomy, e.g. Claims, Finance. | (avoid) area, LOB |
| Sub-domain | Second level, grouping related Data Domains within a Business Domain, e.g. Claims > Litigation. | (use) Subdomain · (avoid) category |
| Data Domain | Leaf level of the Domain Taxonomy. Every table (ODCS contract) belongs to exactly one Data Domain. | (avoid) "domain" on its own when a level matters |
| Domain Path | The identifier of any taxonomy node: its ancestors' slugs and its own joined with `__`, e.g. `claims__litigation__claim_litigation`. Shows what a table rolls up to. | (avoid) domain id, domain code |
| Display Path | The human-readable form of a Domain Path, e.g. "Claims > Litigation > Claim Litigation". Never used as a key. | |
| Restricted | Sensitivity flag on a Data Domain (e.g. health, legal-privilege, PCI data). | |
| Data Contract | An ODCS v3.1.0 document describing one table: schema, quality rules, servers. Id `dc_<project_id>_<layer>_<table>`. | (avoid) schema file, table spec |
| Data Product | An ODPS v1.0.0 document grouping the assets a squad offers, with input and output ports. Id `dp_<project_id>_<name>`. | (avoid) dataset |
| Port | An input or output of a Data Product: a contract (or an asset such as a model or endpoint) it consumes or exposes. | |
| Dataflow Spec | A `nodespec` JSON file the Lakeflow Framework turns into a pipeline; a template invocation expands to several. | (avoid) pipeline config |
| Squad | A team that owns a project and its repository; the Ontos *team*. | (avoid) group (that means an AD group) |
| Project | One squad repository: its registration, bundles and metadata; the Ontos *project*. `kind` is `domain` or `project`. | |
| Registration | A squad's entry in the registry (`projects/<project_id>.yaml`). Also the audit table `md_registration` (not yet written by provisioning; see `Known_Gaps.md`). | |
| Registry | The `dbx-platform-registry` repository: registrations, the taxonomy, required tags, the glossary, the onboarding workflows. | |
| Technical Owner / Data Owner / Executive Owner / Data Steward / Support Contact | The five contact roles of a registration, each an email address. | (avoid) product owner, sponsor |
| Project Id | The slug that prefixes every schema, product and contract id, and is the `repo_id` of a squad's rows. | (avoid) project name |
| Layer | `bronze`, `silver`, `gold`, `ml` or `ai`; the suffix of a schema name `<project_id>_<layer>`. | (avoid) tier, zone |
| Logical Asset Id | Environment-free id of an asset: `<schema>.<table>` for Unity Catalog objects, `<type>/<project_id>/<name>` otherwise. | |
| Physical Name | An asset's fully qualified name in one target: `<catalog>.<schema>.<table>`. | |
| Target | One of `dev`, `test`, `uat`, `preprod`, `prod`; one catalog each. | (avoid) environment (that means a GitHub environment) |
| Platform Snapshot | The registry-generated, hash-protected files in `metadata/_platform/`; read-only in a squad repo. | |
| Writer | A publisher to `platform_meta`, identified by `repo_id`: the registry (`_platform`) or a squad. | |
| Record Version | The integer that increases each time a definition's content changes; with the entity id it is the primary key. | (avoid) revision |
| Semver | The `version` of a contract or product; must increase when content changes. Distinct from Record Version. | |
| Publish | Writing definitions, deployments, lineage or a run to `platform_meta`. Idempotent. | |
| Retire | Publishing a new version with `status = retired` that stays current. | (avoid) delete |
| Owned Tag Key | A Unity Catalog tag key this platform may write and remove: `data_domain_*`, `data_product_*`, `data_contract_*`, `classification`, the required project tags. | |
| Placeholder | A tracked value written `PLACEHOLDER-<what>` in `conf/`, `metadata/` or `bundles/`; blocks deployment until replaced. | (avoid) TODO |
| Quarantine | Rows failing a drop expectation, written to `<table>_quarantine` instead of the target. | |
| Gate | A threshold a model or agent must meet to be promoted to `@champion`. | |
| Asset | Anything deployable or queryable that the platform tracks, with the id `<asset_type>/<repo_id>/<resource_key>` (a dataset keeps `<schema>.<table>`). Every asset has exactly one parent, a Workload and a Layer. | (avoid) object, artifact, resource (a *resource* is a bundle YAML entry) |
| Workload | The class of work an asset belongs to: `de` (data engineering), `ml`, `ai` (generative AI and agents) or `platform` (shared platform jobs). Declared once per bundle as the `workload` variable; shown on every job and pipeline as a tag. | (avoid) type, family, tier |
| Layer | Where a dataset, flow or pipeline sits in the medallion: bronze, silver, gold, publish, or the `ml` and `ai` schemas. | (avoid) stage, zone |
| Bundle | The deploy unit of one workload: `bundles/<name>/databricks.yml` with its resources and source. | (avoid) project (a *project* is one repository) |
| Workflow | A Lakeflow **Job**: a schedule or trigger, tasks and notifications. Never a pipeline. | (use) job where the Databricks API name is meant · (avoid) DAG, orchestration |
| Job Task | One step of a Workflow: a pipeline, wheel, notebook, SQL, sub-workflow (`run_job`) or condition task. | (avoid) step, stage |
| ETL Pipeline | A Lakeflow declarative pipeline that runs one flow group (a layer). | (avoid) DLT job, workflow |
| Ingestion Pipeline | A Lakeflow Connect managed ingestion pipeline (has an `ingestion_definition`). | (avoid) connector job |
| Flow | One source-to-target transformation of a Dataflow Spec: `append`, `cdc_scd1`, `cdc_scd2`, `cdc_snapshot`, `mv` or `view`. | (avoid) transform, mapping |
| Dataset | The table or view a Flow writes, with its quarantine table. | (avoid) entity |
| Model | A machine-learning model in Unity Catalog, with versions and aliases. | (avoid) artifact |
| Agent | A model logged as an agent (ResponsesAgent or LangGraph), tagged `model_kind=agent`. | (avoid) bot, assistant (a Knowledge Assistant is a different asset) |
| Serving Endpoint | Serves a Model, an Agent or a foundation model. | (avoid) deployment |
| MCP Server | An app or managed endpoint that exposes tools over MCP, tagged `app_kind=mcp_server`. | (avoid) tool server |
| Tool | A Unity Catalog function an Agent calls. | (avoid) skill (a *skill* is a Claude Code file) |
| Governance Tag | A tag the platform requires and publishes: owners, `classification`, data domain and product, and the provenance and `workload` tags on jobs and pipelines. | (avoid) label, metadata field |

<!-- Example rows from a hypothetical order-management package — delete these and
     write your own:

| Order | A confirmed customer request to purchase goods | (use) Purchase Order · (avoid) Cart, Request |
| Invoice | A document issued to a customer requesting payment | (use) Bill · (avoid) Receipt |
| Customer | A person or organisation that purchases | (avoid) User, Client, Account |
-->

## Naming conventions

Project-wide conventions that are not single terms:

- Public functions and modules: `snake_case`. Classes: `PascalCase`.
- A leading underscore (`_name`) means internal — not part of the public API (`CLAUDE.md` P0 #5).
- Package exceptions: name them `<Something>Error`.
- Boolean parameters read as a question: `include_archived`, not `archived_flag`.
