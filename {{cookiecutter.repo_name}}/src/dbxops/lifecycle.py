"""
Task entry points for the lifecycle library, runnable as job tasks.

    python -m dbxops.lifecycle reconcile --target dev --config conf/lifecycle.yml [--dry-run [true|false]] [--catalog C]
    python -m dbxops.lifecycle promote --model cat.schema.model --version 3 --gate conf/gates/fraud.yml \
        [--metric auc=0.91 ...]

Authentication is the Databricks SDK default (a job's own identity, or ``DATABRICKS_HOST`` plus credentials or a
profile when run elsewhere). Exit codes: 0 success, 1 reconcile errors or a gate that did not pass, 2 bad usage or
bad config.

Reconcile config (YAML). Every section is optional; the string ``{catalog}`` in any value is replaced by the
target's catalog from ``conf/environments.yml``. ``reconcile`` applies changes unless ``--dry-run`` is given.

    warehouse_id: "<sql warehouse id>"        # needed for table and column comments
    vector_search_indexes:                     # DesiredVectorSearchIndex fields
      - {name: ..., endpoint_name: ..., primary_key: ..., delta_sync_index_spec: {...}}
    serving_endpoints:                         # DesiredServingEndpoint fields
      - {name: ..., served_entities: [...], traffic_routes: [...], rate_limits: [...],
         inference_table: {...}, tags: {k: v}}
    monitors:                                  # DesiredMonitor fields
      - {table_name: ..., output_schema_name: ..., assets_dir: ..., settings: {...}, create_options: {...}}
    knowledge_assistants:                      # DesiredKnowledgeAssistant fields
      - {display_name: ..., description: ..., sources: [{display_name: ..., source_type: files, files: {...}}]}
    metadata_sync:
      owned_extra_keys: [cost_center, region]  # project tag keys the sync may write and remove
      tags: [{entity_type: table, entity_id: c.s.t, tag_key: classification, tag_value: pii, is_active: true}]
      comments: [{entity_type: column, entity_id: c.s.t.col, comment: "..."}]

Gate config (YAML), read by ``PromotionGate.from_yaml``:

    eval_kind: model                  # model | agent (default model)
    challenger_alias: challenger      # default challenger
    champion_alias: champion          # default champion
    policies:                         # all must pass; at least one
      - {type: metric_threshold, metric: auc, comparator: ">=", threshold: 0.80}   # > >= < <= ==
      - {type: eval_pass_rate, threshold: 0.90, metric: pass_rate}                  # metric is optional

``promote`` evaluates the gate on the metrics of the run behind the model version (or on ``--metric`` values when
given), prints one JSON ``md_eval_result`` fact per decision, and moves the champion alias only if every policy
passes and the version holds the challenger alias.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import (
    Any,
    Callable,
    Sequence,
)

import yaml
from databricks.sdk import WorkspaceClient

from dbxops.config import (
    ConfigError,
    load_environment,
)
from dbxops.promotion import (
    GateConfigError,
    ModelPromoter,
    PromotionGate,
    PromotionRefused,
)
from dbxops.reconcile import (
    DesiredComment,
    DesiredKnowledgeAssistant,
    DesiredMonitor,
    DesiredServingEndpoint,
    DesiredTag,
    DesiredVectorSearchIndex,
    KnowledgeAssistantReconciler,
    MetadataSyncInput,
    MetadataSyncReconciler,
    MonitorReconciler,
    Reconciler,
    ReconcileResult,
    ServingEndpointReconciler,
    VectorSearchIndexReconciler,
)
from dbxops.workspace import (
    GatewayError,
    SdkWorkspaceGateway,
    WorkspaceGateway,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
_SECTIONS = {
    "vector_search_indexes",
    "serving_endpoints",
    "monitors",
    "knowledge_assistants",
    "metadata_sync",
    "warehouse_id",
}
GatewayFactory = Callable[[str | None], WorkspaceGateway]


def default_gateway(warehouse_id: str | None) -> WorkspaceGateway:
    """Build the SDK gateway with the SDK's default authentication."""
    return SdkWorkspaceGateway(WorkspaceClient(), warehouse_id=warehouse_id)


def _substitute(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        for name, text in variables.items():
            value = value.replace("{" + name + "}", text)
        return value
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, variables) for key, item in value.items()}
    return value


def load_reconcile_config(path: str, catalog: str, variables: dict[str, str] | None = None) -> dict:
    """
    Read a reconcile config file and substitute ``{catalog}`` and any ``variables``.

    Raises ``ConfigError`` when the file is invalid.
    """
    try:
        with open(path, encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot load reconcile config {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping")
    unknown = sorted(set(data) - _SECTIONS)
    if unknown:
        raise ConfigError(f"{path}: unknown section(s) {', '.join(unknown)}; allowed: {', '.join(sorted(_SECTIONS))}")
    return _substitute(data, {**(variables or {}), "catalog": catalog})


def _build(factory: Callable[..., Any], entries: Any, section: str) -> list:
    try:
        return [factory(**entry) for entry in entries]
    except (TypeError, AttributeError) as error:
        raise ConfigError(f"{section}: invalid entry: {error}") from error


def _metadata_input(section: dict) -> MetadataSyncInput:
    return MetadataSyncInput(
        tags=_build(DesiredTag, section.get("tags", []), "metadata_sync.tags"),
        comments=_build(DesiredComment, section.get("comments", []), "metadata_sync.comments"),
    )


def build_tasks(config: dict, gateway: WorkspaceGateway) -> list[tuple[str, Reconciler, Any]]:
    """Return ``(section, reconciler, desired)`` for every section present in the config."""
    tasks: list[tuple[str, Reconciler, Any]] = []
    simple = (
        ("vector_search_indexes", VectorSearchIndexReconciler, DesiredVectorSearchIndex),
        ("serving_endpoints", ServingEndpointReconciler, DesiredServingEndpoint),
        ("monitors", MonitorReconciler, DesiredMonitor),
        ("knowledge_assistants", KnowledgeAssistantReconciler, DesiredKnowledgeAssistant),
    )
    for section, reconciler_class, desired in simple:
        if section in config:
            tasks.append((section, reconciler_class(gateway), _build(desired, config[section], section)))
    if "metadata_sync" in config:
        sync = config["metadata_sync"]
        reconciler = MetadataSyncReconciler(gateway, owned_extra_keys=sync.get("owned_extra_keys", []))
        tasks.append(("metadata_sync", reconciler, _metadata_input(sync)))
    return tasks


def _print_result(section: str, result: ReconcileResult) -> None:
    mode = "applied" if result.applied else "dry run"
    print(f"[{section}] {mode}: {len(result.changes)} change(s), {len(result.unchanged)} unchanged")
    for change in result.changes:
        print(f"  {change.action} {change.resource} {change.detail}".rstrip())
    for error in result.errors:
        print(f"  ERROR {error}", file=sys.stderr)


def run_reconcile(args: argparse.Namespace, gateway_factory: GatewayFactory) -> int:
    """Reconcile every section of the config against the workspace."""
    catalog = args.catalog or load_environment(args.target, args.environments).catalog
    config = load_reconcile_config(args.config, catalog, _parse_vars(args.var))
    tasks = build_tasks(config, gateway_factory(config.get("warehouse_id")))
    failed = False
    for section, reconciler, desired in tasks:
        result = reconciler.reconcile(desired, dry_run=args.dry_run)
        _print_result(section, result)
        failed = failed or not result.ok
    return EXIT_FAILED if failed else EXIT_OK


def _parse_metrics(pairs: Sequence[str]) -> dict[str, float]:
    metrics = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise ConfigError(f"--metric expects NAME=VALUE, got {pair!r}")
        try:
            metrics[name] = float(value)
        except ValueError as error:
            raise ConfigError(f"--metric {name} must be a number, got {value!r}") from error
    return metrics


def run_promote(args: argparse.Namespace, gateway_factory: GatewayFactory) -> int:
    """Evaluate the gate for a model version and promote it when every policy passes."""
    gate = PromotionGate.from_yaml(args.gate)
    gateway = gateway_factory(None)
    metrics = _parse_metrics(args.metric) or gateway.get_model_version_metrics(args.model, args.version)
    report = gate.evaluate(args.model, args.version, metrics)
    print(json.dumps(report.eval_results(), indent=2))
    if not report.passed:
        for decision in report.decisions:
            if not decision.passed:
                print(f"gate failed: {decision.reason}", file=sys.stderr)
        return EXIT_FAILED
    outcome = ModelPromoter(gateway).promote(report)
    state = "promoted" if outcome.promoted else "already the champion"
    print(f"{args.model} version {args.version}: {state} (@{report.champion_alias})")
    return EXIT_OK


def _parse_vars(pairs: Sequence[str]) -> dict[str, str]:
    variables = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise ConfigError(f"--var expects name=value, got {pair!r}")
        variables[name] = value
    return variables


def _flag(text: str) -> bool:
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(prog="python -m dbxops.lifecycle", description=__doc__.split("\n")[1])
    commands = parser.add_subparsers(dest="command", required=True)
    reconcile = commands.add_parser("reconcile", help="reconcile workspace objects with a desired-state config")
    reconcile.add_argument("--target", required=True, help="a target of conf/environments.yml")
    reconcile.add_argument("--config", required=True, help="path to the reconcile config")
    reconcile.add_argument("--environments", default="conf/environments.yml", help="path to environments.yml")
    reconcile.add_argument("--catalog", help="catalog of the target; when given, conf/environments.yml is not read")
    reconcile.add_argument("--var", action="append", default=[], help="name=value for a {name} token; may repeat")
    reconcile.add_argument(
        "--dry-run",
        nargs="?",
        const=True,
        default=False,
        type=_flag,
        help="plan and print the changes without applying them; accepts true or false so a bundle variable can set it",
    )
    promote = commands.add_parser("promote", help="promote a model version from challenger to champion")
    promote.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    promote.add_argument("--version", required=True, type=int, help="model version")
    promote.add_argument("--gate", required=True, help="path to the gate config")
    promote.add_argument("--metric", action="append", default=[], help="NAME=VALUE; replaces the run's metrics")
    return parser


def main(argv: Sequence[str] | None = None, gateway_factory: GatewayFactory = default_gateway) -> int:
    """Run a lifecycle task and return the process exit code."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return EXIT_OK if error.code in (None, 0) else EXIT_USAGE
    runner = run_reconcile if args.command == "reconcile" else run_promote
    try:
        return runner(args, gateway_factory)
    except (ConfigError, GateConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except (PromotionRefused, GatewayError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
