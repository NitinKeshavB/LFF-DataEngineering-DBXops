# Project Overview — cookie-cutter-dbx

Keep "Layout", "Toolchain" and "Tasks" accurate as the project changes. Architecture detail is in `../03_Architecture/`,
operations in `../06_Operations/`, and what is **not** yet verified in `../06_Operations/Known_Gaps.md`.

---

## Domain

`cookie-cutter-dbx` is a **repository for a metadata-driven Databricks platform** (an insurance client's DataOps,
MLOps and AIOps), generated from the cookiecutter-dbxops template. Pipelines are dataflow specs run by the pinned Lakeflow Framework; every table, product, model and endpoint is
described in Ontos-ready metadata (ODCS contracts, ODPS products, a SKOS glossary, a domain taxonomy) which is published to one
shared database, `platform_meta`. Squads are onboarded through a registry repository, and each squad generates its repository from the template. It also ships the installable Python library `dbxops` that does the validating, generating, publishing and lifecycle work.

**Users:** the platform team (maintains this template, the registry and the framework deployment) and squads (build bundles in
their own repositories made from it).
**Not in scope:** SQL Server as a metadata sink; two-way sync with Ontos; column-level lineage; dashboards, apps and notebooks as
catalogued asset types; row-level security on `platform_meta`; creating Unity Catalog schemas, grants and tags (workspace setup).

---

## Layout

| Path | Role |
|---|---|
| `src/dbxops/` | the library: `metadata` (specs, contracts, rules, export, scaffold), `registry` (registrations, snapshots, taxonomy, intake), `metastore` (the database), `config`, `workspace`, `reconcile`, `promotion`, `lifecycle` |
| `src/dbxops/__init__.py` | the public API; pinned by `tests/unit_tests/test_public_api.py` |
| `bundles/{dataops,mlops,aiops}/` | example bundles with specs, contracts, products, jobs and pipelines |
| `framework/` | the pinned Lakeflow Framework (read-only submodule), our overlay `local/`, the wrapper bundle |
| `conf/` | `environments.yml` (targets) and `metastore.yml` (database sinks) |
| `metadata/_platform/` | registry-generated, hash-protected snapshots (read-only) |
| `templates/registry-repo/` | scaffold of the registry repository |
| `scripts/` | framework staging and upgrade, skill vendoring |
| `.github/workflows/` | CI, CD, framework deploy, upstream check |
| `.claude/` | commands and skills, including vendored Databricks, MLflow and framework skills (`VENDORED.json` locks them) |
| `tests/` | `unit_tests/` mirrors `src/`; `bundles/` checks the example bundles |
| `.claude/context/` | this spec layer |

**Import name vs distribution name.** Installed as `cookie-cutter-dbx`, imported as `dbxops`. Jobs run `dbxops-publish-run` from the
wheel with `python_wheel_task` (package `cookie-cutter-dbx`).

---

## Toolchain

| Concern | Tool | Notes |
|---|---|---|
| Packaging | `setuptools` + `pip` | `[project.scripts]` installs `dbxops-publish-run` |
| Version | `version.txt` | read dynamically; never hardcode elsewhere |
| Formatting / linting | `black`, `isort`, `flake8` (+ docstrings, `radon`), `pylint` | line length **119**; complexity capped at **10**; no `noqa` |
| Types | `mypy` | no `[tool.mypy]` section; `types-PyYAML` is added to the hook |
| Dead code | `autoflake` | removes unused imports and variables |
| Tests | `pytest` + `pytest-cov` | markers `slow`, `integration` (PostgreSQL via `DBXOPS_TEST_PG_URL`) |
| Orchestration | `pre-commit` | blocks commits to `main`; pinned `<4.6.1` for git 2.25; vendored and generated files are excluded |
| Offline validation | Databricks CLI (`bundle schema`), the framework's `validate_dataflows.py`, `actionlint`, `shellcheck` | tests skip a tool that is not installed |

**Python floor:** `>=3.11` (the CI matrix is 3.11 to 3.13).

---

## Tasks

| Command | Effect |
|---|---|
| `make install` / `test` / `test-quick` / `lint` / `lint-ci` / `build` / `clean` | the standard developer loop |
| `make test-wheel-locally` | build a wheel, install it in a fresh venv, test the installed package |
| `make validate-metadata [BASELINE=ref]` | every metadata rule (offline) |
| `make contracts [CHECK=1]` / `sync-substitutions [CHECK=1]` | generate or check contracts and per-bundle config |
| `make validate-specs` / `validate-domains` / `validate-registry [STRICT=1]` | spec, taxonomy and registry checks |
| `make new-bundle NAME= DOMAIN=` | scaffold a valid bundle |
| `make export-ontos TARGET=` | Ontos upload files |
| `make check-placeholders [STRICT=1]` / `onboard-check` | list tracked placeholders; fail while any remain |
| `make stage-framework` / `deploy-framework TARGET= PROFILE=` | stage or deploy the framework (platform team; approval) |
| `make check-upstream` / `upgrade-lakeflow TAG=` | check for or move to a framework release |
| `make check-skills` / `sync-skills SOURCE=` | verify or refresh vendored skills |
| `make render-platform` / `metadata-dictionary` | regenerate `metadata/_platform` or the data dictionary |
| `make publish-test` / `publish-prod` | PyPI — **irreversible**, needs approval |

---

## Architecture notes

See `../03_Architecture/Platform_Architecture.md`, `Metadata_Model.md`, `Metadata_Dictionary.md` and `Design_Patterns_Map.md`.
Invariants worth knowing: only `dbxops.__init__` is public; the submodule and `metadata/_platform` are never edited by hand;
generated files (contracts' machine-owned parts, substitutions, config copies, the data dictionary) are regenerated by a command
and checked in CI; a squad only writes rows carrying its own `repo_id`.

## Dependencies

**Runtime:** `pyyaml`, `jsonschema` (validation), `rdflib` (the glossary), `sqlalchemy>=2` (the metadata model), `databricks-sdk>=0.64`
(0.64 is the first release with entity tag assignments). **Extras:** `uc` (`databricks-sql-connector`, `databricks-sqlalchemy`),
`postgres` (`psycopg[binary]`, also used for Lakebase). **Test:** `faker`. Job-only libraries (mlflow, scikit-learn, langgraph, ...) are
declared in job environments, never in `[project].dependencies`.
