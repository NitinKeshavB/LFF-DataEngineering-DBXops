"""
Evaluate the challenger version of the policy assistant and record its pass rate for the promotion gate.

The version that holds the `challenger` alias is loaded from Unity Catalog and run over the committed evaluation
cases with `mlflow.genai.evaluate` (databricks-mlflow-evaluation skill: nested `inputs` and `expectations`,
`predict_fn` receiving the inputs as keyword arguments, metrics named `<scorer>/mean`). The pass rate is the mean of
the scorers' pass rates, and it is logged as the `pass_rate` metric on the run that logged the model version, which
is where `dbxops.lifecycle promote` reads it. The threshold is not decided here; it lives in `src/gates/`.

Tracing goes to a Unity Catalog trace location when a trace schema and a SQL warehouse are given (the
instrumenting-with-mlflow-tracing skill requires mlflow 3.11.1 or newer for that); otherwise the experiment's own
storage is used and a warning is printed.

Run as a job task:
    evaluate_agent.py --model C.S.policy_assistant --cases <cases.json> --experiment /Shared/x
        [--alias challenger] [--trace-schema C.S] [--warehouse-id ID]
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import (
    Any,
    Callable,
    MutableMapping,
    Sequence,
)

SCORER_NAMES = ("correctness", "safety", "grounded")
PASS_RATE_METRIC = "pass_rate"
GROUNDED_GUIDELINES = (
    "The answer relies only on policy wording passages or claim data that the tools returned. When they do not "
    "contain the answer, the answer says so instead of guessing."
)


class EvaluationError(RuntimeError):
    """Raised when the evaluation cases are invalid or the evaluation did not produce the expected metrics."""


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """Read the evaluation cases; every case needs an id, a question and at least one expected fact."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not cases:
        raise EvaluationError(f"{path}: expected an object with a non-empty 'cases' list")
    for case in cases:
        facts = case.get("expectations", {}).get("expected_facts")
        if not case.get("id") or not case.get("inputs", {}).get("question") or not facts:
            raise EvaluationError(f"{path}: case {case.get('id')!r} needs an id, inputs.question and expected_facts")
    return cases


def build_eval_data(cases: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the cases in the shape `mlflow.genai.evaluate` takes: `inputs` and `expectations` only."""
    return [{"inputs": case["inputs"], "expectations": case["expectations"]} for case in cases]


def extract_answer(response: Any) -> str:
    """Return the text of the last message in a ResponsesAgent response (a dict or an object with `model_dump`)."""
    data = response.model_dump() if hasattr(response, "model_dump") else response
    texts = [
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    return texts[-1] if texts else ""


def pass_rate_from_metrics(metrics: dict[str, float], names: Sequence[str] = SCORER_NAMES) -> float:
    """Return the mean of the scorers' `<name>/mean` values; raise `EvaluationError` when a scorer has no metric."""
    missing = [name for name in names if f"{name}/mean" not in metrics]
    if missing:
        raise EvaluationError(
            f"evaluation metrics lack {', '.join(f'{name}/mean' for name in missing)}; got {sorted(metrics)}"
        )
    return sum(float(metrics[f"{name}/mean"]) for name in names) / len(names)


@dataclass(frozen=True)
class TraceDestination:
    """Where traces should be stored: a `catalog.schema` and the SQL warehouse MLflow uses to write there."""

    schema: str = ""
    warehouse_id: str = ""

    @property
    def enabled(self) -> bool:
        """Return whether both parts are given, which Unity Catalog trace storage needs."""
        return bool(self.schema and self.warehouse_id)


def configure_tracking(
    mlflow: ModuleType,
    experiment: str,
    destination: TraceDestination,
    trace_location: ModuleType,
    environ: MutableMapping[str, str],
) -> str:
    """Select the experiment, binding it to a Unity Catalog trace location when possible; return where traces go."""
    mlflow.set_registry_uri("databricks-uc")
    if destination.enabled:
        catalog, schema = destination.schema.split(".")
        environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = destination.warehouse_id
        location = trace_location.UnityCatalog(catalog_name=catalog, schema_name=schema)
        mlflow.set_experiment(experiment_name=experiment, trace_location=location)
        return "unity_catalog"
    print("warning: no trace schema or SQL warehouse given; traces use the experiment's own storage", file=sys.stderr)
    mlflow.set_experiment(experiment)
    return "experiment"


def build_scorers(scorers: ModuleType) -> list[Any]:
    """Return the scorers: correctness against the expected facts, safety, and a groundedness guideline."""
    return [
        scorers.Correctness(),
        scorers.Safety(),
        scorers.Guidelines(name="grounded", guidelines=GROUNDED_GUIDELINES),
    ]


def make_predict_fn(model: Any) -> Callable[[str], dict[str, str]]:
    """Return the `predict_fn` for `mlflow.genai.evaluate`: it receives the case's `question` as a keyword argument."""

    def predict(question: str) -> dict[str, str]:
        response = model.predict({"input": [{"role": "user", "content": question}]})
        return {"response": extract_answer(response)}

    return predict


def evaluate(args: argparse.Namespace, mlflow: ModuleType, scorers: ModuleType, trace_location: ModuleType) -> dict:
    """Evaluate the aliased version and log its pass rate on the run that logged it; return the summary."""
    destination = configure_tracking(
        mlflow, args.experiment, TraceDestination(args.trace_schema, args.warehouse_id), trace_location, os.environ
    )
    cases = load_cases(args.cases)
    client = mlflow.tracking.MlflowClient(registry_uri="databricks-uc")
    version = client.get_model_version_by_alias(args.model, args.alias)
    model = mlflow.pyfunc.load_model(f"models:/{args.model}@{args.alias}")
    results = mlflow.genai.evaluate(
        data=build_eval_data(cases), predict_fn=make_predict_fn(model), scorers=build_scorers(scorers)
    )
    rate = pass_rate_from_metrics(results.metrics)
    client.log_metric(version.run_id, PASS_RATE_METRIC, rate)
    for name in SCORER_NAMES:
        client.log_metric(version.run_id, f"eval_{name}_mean", float(results.metrics[f"{name}/mean"]))
    return {
        "model": args.model,
        "version": version.version,
        "cases": len(cases),
        PASS_RATE_METRIC: rate,
        "traces": destination,
        "evaluation_run_id": results.run_id,
    }


def main(
    argv: Sequence[str] | None = None,
    mlflow: ModuleType | None = None,
    scorers: ModuleType | None = None,
    trace_location: ModuleType | None = None,
) -> int:
    """Run the evaluation and print the summary as JSON; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    parser.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    parser.add_argument("--cases", required=True, help="evaluation cases JSON")
    parser.add_argument("--experiment", required=True, help="absolute workspace path of the MLflow experiment")
    parser.add_argument("--alias", default="challenger")
    parser.add_argument("--trace-schema", default="", help="catalog.schema for Unity Catalog traces")
    parser.add_argument("--warehouse-id", default="", help="SQL warehouse for Unity Catalog traces")
    args = parser.parse_args(argv)
    summary = evaluate(
        args,
        mlflow or importlib.import_module("mlflow"),
        scorers or importlib.import_module("mlflow.genai.scorers"),
        trace_location or importlib.import_module("mlflow.entities.trace_location"),
    )
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
