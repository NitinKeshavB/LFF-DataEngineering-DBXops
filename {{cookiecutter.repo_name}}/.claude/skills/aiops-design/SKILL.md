---
name: aiops-design
description: Design or change AIOps on this platform — document ingestion with ai_parse_document, chunk tables, Vector Search indexes, UC function tools, ResponsesAgent/LangGraph agents with MLflow tracing, mlflow.genai evaluation gates, agent deployment, AI Gateway and Agent Bricks Knowledge Assistants. Use WHENEVER the user adds or changes an agent, retrieval index, evaluation set or LLM serving configuration. Do NOT use for ordinary data pipelines (dataops-design) or classical ML (mlops-design).
---

# AIOps design

The example is `bundles/aiops/`: synthetic policy PDFs, bronze `ai_parse_document` with quarantine, silver chunks (change data feed on), a
Delta-Sync index reconciled by `VectorSearchIndexReconciler`, a UC function tool, a LangGraph agent with a pure testable core
(`policy_assistant_core.py`) and a thin framework wrapper, `mlflow.genai.evaluate` with a committed eval set, an `EvalPassRate` gate, deployment, and an optional Knowledge Assistant
behind `enable_agent_bricks`. For API details use the vendored `databricks-ai-functions`, `databricks-vector-search`, `databricks-agent-bricks`,
`databricks-mlflow-evaluation`, `agent-evaluation` and `instrumenting-with-mlflow-tracing` skills.

## Conventions

- Keep agent logic importable without langgraph or mlflow (inject fakes in tests); the framework-bound wrapper stays thin.
- Tracing is turned on before the framework imports (`mlflow.langchain.autolog()`).
- An agent is deployed only after the evaluation gate passes; thresholds live in git and are recorded with every result.
- Flow groups are `ai_bronze` and `ai_silver`, schema `<project_id>_ai`; the index, agent, serving endpoint and assistant are ports of the bundle's product.
- Library versions are lower bounds until a first live run; pin them then.

## Known risks

The Knowledge Assistant REST paths and the agent wrapper have not run against a workspace; if `agents.deploy` creates more than one served entity, the reconciler
would replace the deployed config, so run `--dry-run` after the first deployment (`context/06_Operations/Known_Gaps.md`).
