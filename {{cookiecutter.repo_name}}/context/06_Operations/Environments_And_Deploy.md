# Environments and deployment

## Targets

`conf/environments.yml` is the single source of truth: `dev`, `test`, `uat`, `preprod`, `prod`, each with a catalog, a bundle
mode, a GitHub environment, whether approval is required, and whether bundles run as the project service principal. A test
(`tests/bundles/test_bundles.py`) keeps every bundle's targets, catalogs, host and framework path equal to it.

## What deploys what

| What | Who | How |
|---|---|---|
| Lakeflow Framework, once per target | platform team | `make deploy-framework TARGET=<t> PROFILE=<p>` or the *Deploy framework* workflow; needs approval |
| A bundle | squad CI | `cd.yml`: merge to `main` deploys dev then test; a `v*` tag deploys uat, preprod, prod behind environment reviewers |
| The dbxops wheel | the same deploy | built, uploaded to `/Volumes/<catalog>/platform/wheels/<version>/` (dev: `.../dev/<sha>/`), passed as `BUNDLE_VAR_dbxops_wheel_path` |
| Metadata | the same deploy and each job's last task | `python -m dbxops.metastore publish ...`, `dbxops-publish-run` |

Deploys refuse to run while any `PLACEHOLDER-<what>` remains (`make check-placeholders STRICT=1`). `databricks bundle deploy`
is run **without** `--auto-approve`, so a pipeline recreation stops for a human.

## Local checks (no workspace needed)

`make test`, `make lint`, `make validate-metadata`, `make contracts CHECK=1`, `make sync-substitutions CHECK=1`,
`make validate-specs`, `make check-skills`, `make validate-registry`, `make stage-framework`.

## Authentication

Never auto-selected: pass `--profile` locally (`make deploy-framework ... PROFILE=`); CI uses Databricks workload identity
federation for GitHub Actions (`DATABRICKS_AUTH_TYPE=github-oidc`, the environment variable `DEPLOY_SP_CLIENT_ID`, and a
federation policy on the service principal). The earlier plan named `github-oidc-azure`; the CLI documentation covers
`github-oidc`, so that is what the workflows use.
