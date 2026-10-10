# Getting this metadata into Databricks Labs Ontos

```bash
make export-ontos TARGET=dev          # writes build/ontos/dev/{contracts,products,semantic-models}
```

Upload (Ontos 1.0.2 endpoints, as read from its source):

| Files | Endpoint | Notes |
|---|---|---|
| `contracts/*.yaml` | `/api/data-contracts/upload` | ODCS YAML or JSON |
| `products/*.yaml` | `/api/data-products/upload` | the endpoint accepts `.yaml` or `.json` only; **`.yml` is rejected** |
| `semantic-models/*.ttl` | `/api/semantic-models/upload` | Turtle |

## What the export changes (it reads the source documents, not the database)

1. the contract's `domain` is the data-domain **path** (unique across the three taxonomy levels, where names repeat);
2. nested properties stay nested as authored;
3. `team[]` and `support[]` are built from the owning **project's** contacts, so owners stay attached to the right project;
4. a port whose asset type Ontos does not know is typed `generic`, with the original in `customProperties` `dbx.assetSubtype`;
5. the Ontos name is the id without `dp_`/`dc_`, and `data_product_<name>` / `data_contract_<name>` / `data_domain_<path>` are added as tags;
6. semantic assignments are ODCS `authoritativeDefinitions`; the glossary is exported as Turtle;
7. `version` is the semver, which the validator keeps 1:1 with content.

Catalog names in `servers[]` and `physicalName` are resolved for the chosen target.

## Not verified against a live Ontos

Whether Ontos keeps `customProperties` such as `dbx.assetSubtype`; whether product names must be unique across domains; how it
imports the tag names above. Check these on the first real upload. Two-way sync and an id-mapping table are out of scope for v1.
