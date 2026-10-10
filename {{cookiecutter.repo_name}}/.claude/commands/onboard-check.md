---
description: "Check whether this repository is ready to deploy, and list what is still a placeholder. Triggers: /onboard-check, \"are we ready to deploy\", \"what is left to fill in\"."
allowed-tools: Read, Glob, Grep, Bash
---

# /onboard-check — deploy readiness

Read-only. Run, in order, and report each result with its real output:

1. `make check-placeholders` — group the `PLACEHOLDER-<what>` values by file and say who supplies each (platform team: host, tenant, catalogs, warehouses; squad: owners, service
   principal, tags, descriptions).
2. `make validate-metadata`, `make contracts CHECK=1`, `make sync-substitutions CHECK=1`, `make validate-specs`.
3. `make onboard-check` (the strict gate used before every deploy) and state plainly whether it passed.

Finish with the shortest list of what blocks a deploy. Do not deploy, and do not edit any file.
