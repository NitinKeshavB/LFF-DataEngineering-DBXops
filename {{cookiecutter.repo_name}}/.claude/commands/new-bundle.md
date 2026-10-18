---
description: "Scaffold a new pipeline bundle that passes validation from the first commit. Triggers: /new-bundle, \"add a bundle\", \"new pipeline bundle\"."
argument-hint: "<name> <data-domain-path> [de|ml|ai|platform] [sftp|xlsx|zip|sqlserver|postgresql|sharepoint] [custom|lakeflow]"
allowed-tools: Read, Glob, Grep, Bash
---

# /new-bundle — scaffold a bundle

`$ARGUMENTS` is `<name> <data-domain-path> [workload] [connector] [mode]`. If the name or domain is missing, ask; do not invent a data domain. **Always ask which workload the
bundle is** (`de` data engineering, `ml`, `ai`, or `platform` for shared platform jobs); do not default silently. A bundle tests fail without one. List candidates with
`grep -n 'slug' metadata/_platform/domains.yaml` or read the taxonomy; a table rolls up to a **data-domain** path (`business__sub__data`).

If the squad has a real external source to land, ask whether to also scaffold a landing job for it, and which **connector** and **mode**:

| Connector | custom (a dbxops.connectors job task) | lakeflow (a managed Lakeflow Connect pipeline) |
|---|---|---|
| `sftp` | paramiko download job | Auto Loader reading `sftp://` directly (GA) |
| `xlsx` | openpyxl/pyarrow landing job | not offered |
| `zip` | stdlib zip-unpack job | not offered |
| `sqlserver` | not offered | gateway + ingestion pipeline (GA CDC) |
| `postgresql` | not offered | gateway + ingestion pipeline (Public Preview CDC) |
| `sharepoint` | not offered | ingestion pipeline, Preview channel (OAuth-only, UI setup) |

`jdbc` needs a hand-wired `JdbcSource`, so it is not offered either way. Leave the connector out if there is none yet; when one is picked, mode defaults to
whichever one it supports (no need to ask separately unless `sftp` is picked, which supports both).

1. Run `make new-bundle NAME=<name> DOMAIN=<path> WORKLOAD=<de|ml|ai|platform> CONNECTOR=<kind> MODE=<custom|lakeflow>` (approval-listed; omit `CONNECTOR`/`MODE`
   if none was asked for). It creates `bundles/<name>/` from `conf/` and the registration, generates the substitution files and the example table's contract,
   and — with `CONNECTOR` — a landing job/pipeline and a `PLACEHOLDER`-filled config or connection-setup note for it.
2. Run `make validate-metadata` and `make validate-specs`; both must pass.
3. Tell the user what to do next: replace `PLACEHOLDER-purpose|usage|limitations` in the contract and the product, create the connection named in
   `src/config/land_<name>_connection.md` (if a Lakeflow connector was chosen), point the example spec at the real source, and follow the `dataops-design` and
   `metadata-ontology` skills.

Never edit `metadata/_platform/` or the framework submodule.
