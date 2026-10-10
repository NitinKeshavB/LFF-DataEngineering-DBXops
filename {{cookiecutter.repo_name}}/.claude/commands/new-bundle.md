---
description: "Scaffold a new pipeline bundle that passes validation from the first commit. Triggers: /new-bundle, \"add a bundle\", \"new pipeline bundle\"."
argument-hint: "<name> <data-domain-path>"
allowed-tools: Read, Glob, Grep, Bash
---

# /new-bundle — scaffold a bundle

`$ARGUMENTS` is `<name> <data-domain-path>`. If either is missing, ask; do not invent a data domain. List candidates with
`grep -n 'slug' metadata/_platform/domains.yaml` or read the taxonomy; a table rolls up to a **data-domain** path (`business__sub__data`).

1. Run `make new-bundle NAME=<name> DOMAIN=<path>` (approval-listed). It creates `bundles/<name>/` from `conf/` and the registration,
   generates the substitution files and the example table's contract.
2. Run `make validate-metadata` and `make validate-specs`; both must pass.
3. Tell the user what to do next: replace `PLACEHOLDER-purpose|usage|limitations` in the contract and the product, point the example spec at the real source, and
   follow the `dataops-design` and `metadata-ontology` skills.

Never edit `metadata/_platform/` or the framework submodule.
