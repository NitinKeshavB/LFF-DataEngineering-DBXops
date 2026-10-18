# Upgrading the Lakeflow Framework

The framework is a git submodule pinned in `framework/UPSTREAM.lock` (repo, tag, commit). Nothing under
`framework/lakeflow_framework/` is ever edited; our additions are in `framework/local/`.

```bash
make check-upstream            # current=v0.24.1 latest=... update=yes|no
make upgrade-lakeflow TAG=v0.25.0
make test
```

`upgrade-lakeflow` moves the submodule, rewrites `framework/UPSTREAM.lock`, the version in `conf/environments.yml`,
`framework/databricks.yml` and every bundle's `databricks.yml`, re-vendors the framework's dataflow-spec skill, regenerates the
per-bundle config copies, and validates every spec with the **new** framework's own validator. It was exercised with a real
v0.24.1 to v0.24.0 and back; the second run left the tree byte-identical.

CI fails when the submodule is not at the pinned commit. A weekly workflow (`lakeflow-upstream.yml`) opens a pull request when a
newer release exists. After merging, the platform team deploys the new framework version (`deploy-framework`), then each squad
merges the upgrade pull request that `upgrade-lakeflow` produces for its repository (bundles name the version in their
`framework_source_path`, so a bundle keeps using the framework it was deployed with until then).
