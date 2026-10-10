---
description: "Move the Lakeflow Framework pin to a release and bring everything in step. Triggers: /upgrade-lakeflow, \"upgrade the framework\", \"new lakeflow release\"."
argument-hint: "[tag]"
allowed-tools: Read, Glob, Grep, Bash
---

# /upgrade-lakeflow — move the framework pin

1. No tag in `$ARGUMENTS`: run `make check-upstream` and report `current`, `latest`, `update`. Stop if `update=no`.
2. With a tag: this is **Tier 3** (CLAUDE.md §3) — state what will change (the submodule pin, `framework/UPSTREAM.lock`, the version in `conf/`, `framework/databricks.yml` and every bundle's
   `databricks.yml`, the vendored dataflow-spec skill, the generated config copies) and ask for confirmation before running `make upgrade-lakeflow TAG=<tag>`.
3. After it runs, run `make test` and `make validate-metadata`; paste the real results. The script already validated every spec with the new framework's validator.
4. Remind the user that the platform team must deploy the new framework version (`make deploy-framework`) before squads merge the upgrade.

Never edit files inside `framework/lakeflow_framework/`.
