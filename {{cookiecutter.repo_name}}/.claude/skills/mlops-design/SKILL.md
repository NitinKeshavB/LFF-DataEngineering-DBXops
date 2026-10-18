---
name: mlops-design
description: Design or change MLOps on this platform — feature materialized views, model training and logging to Unity Catalog, challenger and champion aliases, promotion gates, batch scoring and serving endpoints. Use WHENEVER the user adds a model, changes how one is trained, scored, gated or served, or asks how a model reaches production here. Do NOT use for data pipelines (dataops-design), agents and RAG (aiops-design), or contracts and products (metadata-ontology).
---

# MLOps design

The example is `bundles/mlops/`: a feature MV over silver tables, `train_model.py` (logs to Unity Catalog, sets `challenger`, logs AUC),
a gate (`src/gates/*.gate.yml`) applied by `dbxops.lifecycle promote`, batch scoring that writes `claims_fraud_scores`, and a serving
config reconciled only in uat, preprod and prod. For model API details use the vendored `databricks-ml-training`,
`databricks-model-serving` and `databricks-mlflow-evaluation` skills.

## Conventions

- Models live in `<catalog>.<project_id>_ml.<model>`; aliases are `challenger` then `champion`. A model is promoted **only** by a gate decision
  (`dbxops.promotion`); never set `champion` directly.
- Thresholds live in git (the gate file) and are copied onto every `md_eval_result` fact with the gate's source path.
- Serving config is desired state for `ServingEndpointReconciler`; it never deletes what it does not own.
- Library-heavy code (mlflow, scikit-learn) stays in job scripts and job environments, with a pure testable core; none of it enters the package dependencies.
- The feature MV is a spec target with a contract; a table the job writes gets a hand-written contract with `dbx.producer: job`.
- Every asset (model, endpoint, scores table) is a port of the bundle's product.

## Known risk

`FeatureLookup` may need a declared Unity Catalog primary key on the feature table, which the contract tooling cannot express from a JSON schema yet (`.claude/context/06_Operations/Known_Gaps.md`).
