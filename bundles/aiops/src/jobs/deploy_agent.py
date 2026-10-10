"""
Deploy the champion version of the policy assistant to a Model Serving endpoint with `databricks.agents.deploy`.

This follows the databricks-ml-training skill (references/genai-agents.md): the endpoint name is always passed
explicitly, because the derived name is unpredictable, and deploying a new version updates the existing endpoint in
place. The call blocks for about 15 minutes. Rate limits, the inference table and tags of the endpoint are applied
afterwards by the `reconcile` task of `src/serving/policy_assistant_endpoint.yml`.

Run as a job task (needs databricks-agents and mlflow):
    deploy_agent.py --model C.S.policy_assistant --endpoint-name NAME [--alias champion]
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from types import ModuleType
from typing import Sequence


class DeployError(RuntimeError):
    """Raised when there is nothing to deploy."""


def deploy(args: argparse.Namespace, mlflow: ModuleType, agents: ModuleType) -> dict[str, str]:
    """Deploy the version that holds the alias; return the endpoint name and its query endpoint."""
    client = mlflow.tracking.MlflowClient(registry_uri="databricks-uc")
    try:
        version = client.get_model_version_by_alias(args.model, args.alias).version
    except mlflow.exceptions.MlflowException as error:
        raise DeployError(f"{args.model} has no version with the alias @{args.alias}: {error}") from error
    deployment = agents.deploy(args.model, version, endpoint_name=args.endpoint_name)
    return {
        "model": args.model,
        "version": str(version),
        "endpoint_name": deployment.endpoint_name,
        "query_endpoint": deployment.query_endpoint,
    }


def main(argv: Sequence[str] | None = None, mlflow: ModuleType | None = None, agents: ModuleType | None = None) -> int:
    """Deploy the champion and print the result as JSON; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    parser.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    parser.add_argument("--endpoint-name", required=True)
    parser.add_argument("--alias", default="champion")
    args = parser.parse_args(argv)
    mlflow = mlflow or importlib.import_module("mlflow")
    agents = agents or importlib.import_module("databricks.agents")
    try:
        result = deploy(args, mlflow, agents)
    except DeployError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
