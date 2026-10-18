# Before the first real deployment

Everything below must exist; none of it is created by this repository.

**Values to replace** (`make check-placeholders` lists them all; `STRICT=1` fails while any remain): workspace host and tenant,
the five catalog names, SQL warehouse ids, the Lakebase instance (prod sink), the project service principal id and
tag values, owner emails and the ontology IRI host (`metadata/_platform/` comes from the registry).

**Workspace and Unity Catalog:** one catalog per target, owned by the platform service principal; the `<project>_<layer>`
schemas per project; a `platform` schema with a `wheels` volume in each catalog; a `platform_meta` schema and a SQL warehouse for
the metadata sink; predictive optimisation enabled on the catalogs.

**Identity:** AD groups created by IAM (`AAD-DBX-<PROJECT>-DEV|READ|ADMIN`); a deployment service principal per target with a
Databricks federation policy for the repository; the project service principal registered in the workspace.

**GitHub:** an organisation; a GitHub App with repository-administration rights (for provisioning); teams; per-target
environments with `DEPLOY_SP_CLIENT_ID`; repository variables `ORG`, `TEMPLATE_REPO`, `DBXOPS_VERSION`, `APP_ID`,
`PLATFORM_RELEASE_TEAM`; secret `APP_PRIVATE_KEY`; the `dbxops` package published (the registry installs it by version).

**Local:** Python 3.11+, the Databricks CLI, git 2.31+ if you want a newer `pre-commit` (the pin in `pyproject.toml` works with older git).

**Order:** workspace and Unity Catalog setup, then identities, then GitHub, then fill placeholders, then `make deploy-framework`, then
bundles.
