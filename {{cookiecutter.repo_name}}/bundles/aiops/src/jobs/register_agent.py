"""
Log the policy assistant to MLflow, register it in Unity Catalog and give the new version the `challenger` alias.

This follows the databricks-ml-training skill (references/genai-agents.md): the agent file is logged as code, with
`resources` so the deployed endpoint gets passthrough access to the chat endpoint, the vector index and the UC
function, then the model is checked with `mlflow.models.predict` before it can be promoted. The core module is
shipped next to the agent file with `code_paths`, and the agent's settings travel as the model configuration.

Run as a job task (needs the agent libraries of `AGENT_PIP_REQUIREMENTS`):
    register_agent.py --model C.S.policy_assistant --agent-file <policy_assistant.py> --experiment /Shared/x
        --llm-endpoint E --vector-index C.S.I --claim-status-function C.S.F
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import (
    Any,
    Sequence,
)

AGENT_PIP_REQUIREMENTS = ("mlflow>=3.11.1", "databricks-langchain", "langgraph", "databricks-agents", "pydantic>=2")
INPUT_EXAMPLE = {
    "input": [{"role": "user", "content": "What is the standard excess on the Motor Comprehensive policy?"}]
}
CORE_MODULE = "policy_assistant_core.py"
CHALLENGER_ALIAS = "challenger"


def build_resources(resources: ModuleType, config: dict[str, Any]) -> list[Any]:
    """Return the MLflow resources the endpoint needs passthrough access to (`mlflow.models.resources`)."""
    return [
        resources.DatabricksServingEndpoint(endpoint_name=config["llm_endpoint"]),
        resources.DatabricksVectorSearchIndex(index_name=config["vector_index"]),
        resources.DatabricksFunction(function_name=config["claim_status_function"]),
    ]


def latest_version(client: Any, model: str) -> str:
    """Return the highest registered version number of the model."""
    return max(client.search_model_versions(f"name='{model}'"), key=lambda version: int(version.version)).version


def register(args: argparse.Namespace, mlflow: ModuleType, resources: ModuleType, config: dict[str, Any]) -> dict:
    """Log and register the agent, validate it, set the challenger alias, and return what was registered."""
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(args.experiment)
    agent_file = Path(args.agent_file)
    with mlflow.start_run():
        info = mlflow.pyfunc.log_model(
            name="agent",
            python_model=str(agent_file),
            code_paths=[str(agent_file.with_name(CORE_MODULE))],
            model_config=config,
            resources=build_resources(resources, config),
            input_example=INPUT_EXAMPLE,
            pip_requirements=list(AGENT_PIP_REQUIREMENTS),
            registered_model_name=args.model,
        )
    mlflow.models.predict(model_uri=info.model_uri, input_data=INPUT_EXAMPLE, env_manager="uv")
    client = mlflow.tracking.MlflowClient(registry_uri="databricks-uc")
    version = latest_version(client, args.model)
    client.set_registered_model_alias(args.model, CHALLENGER_ALIAS, version)
    return {"model": args.model, "version": version, "alias": CHALLENGER_ALIAS, "model_uri": info.model_uri}


def settings_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Return the agent's model configuration (see `AgentSettings.to_dict`) from the command line."""
    return {
        "llm_endpoint": args.llm_endpoint,
        "vector_index": args.vector_index,
        "claim_status_function": args.claim_status_function,
    }


def main(
    argv: Sequence[str] | None = None, mlflow: ModuleType | None = None, resources: ModuleType | None = None
) -> int:
    """Register the agent; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    parser.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    parser.add_argument("--agent-file", required=True, help="path of policy_assistant.py")
    parser.add_argument("--experiment", required=True, help="absolute workspace path of the MLflow experiment")
    parser.add_argument("--llm-endpoint", required=True)
    parser.add_argument("--vector-index", required=True)
    parser.add_argument("--claim-status-function", required=True)
    args = parser.parse_args(argv)
    result = register(
        args,
        mlflow or importlib.import_module("mlflow"),
        resources or importlib.import_module("mlflow.models.resources"),
        settings_from_args(args),
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
