"""
Job tasks that run the dbxops lifecycle library for the claims fraud model.

The lifecycle CLI needs a model version and, for reconcile, conf/environments.yml; a job task has neither. This
script resolves the version from a model alias and takes the target's catalog as an argument, then uses
dbxops.lifecycle for the work. The dbxops wheel must be installed in the job environment.

    mlops_lifecycle_task.py promote --model <catalog.schema.model> --gate <gate.yml>
    mlops_lifecycle_task.py reconcile --model <catalog.schema.model> --config <endpoint.yml> --catalog <catalog> \
        --project-id <project_id> --target <target> [--dry-run]

``promote`` promotes the version that holds @challenger. ``reconcile`` serves the version that holds @champion: the
tokens {project_id}, {target} and {champion_version} are replaced in every string of the config ({catalog} is
replaced by dbxops.lifecycle). Exit codes follow dbxops.lifecycle: 0 success, 1 failure, 2 bad usage or config.
"""

from __future__ import annotations

import argparse
import sys
from typing import (
    Any,
    Callable,
    Sequence,
)

from dbxops import lifecycle
from dbxops.config import ConfigError
from dbxops.workspace import (
    GatewayError,
    WorkspaceGateway,
)

CHALLENGER = "challenger"
CHAMPION = "champion"


class AliasNotSet(LookupError):
    """Raised when a registered model has no version under the requested alias."""


def alias_version(gateway: WorkspaceGateway, model: str, alias: str) -> int:
    """Return the version a model alias points at, or raise ``AliasNotSet``."""
    version = gateway.get_model_alias_version(model, alias)
    if version is None:
        raise AliasNotSet(f"{model} has no @{alias} alias")
    return version


def fill_tokens(value: Any, tokens: dict[str, str]) -> Any:
    """Return ``value`` with every ``{token}`` in its strings replaced, recursing through lists and mappings."""
    if isinstance(value, str):
        for name, replacement in tokens.items():
            value = value.replace("{" + name + "}", replacement)
        return value
    if isinstance(value, list):
        return [fill_tokens(item, tokens) for item in value]
    if isinstance(value, dict):
        return {key: fill_tokens(item, tokens) for key, item in value.items()}
    return value


def run_promote(args: argparse.Namespace, gateway_factory: lifecycle.GatewayFactory) -> int:
    """Promote the challenger version of the model through the gate."""
    version = alias_version(gateway_factory(None), args.model, CHALLENGER)
    argv = ["promote", "--model", args.model, "--version", str(version), "--gate", args.gate]
    return lifecycle.main(argv, gateway_factory)


def run_reconcile(args: argparse.Namespace, gateway_factory: lifecycle.GatewayFactory) -> int:
    """Reconcile the desired state with the champion version filled in."""
    config = lifecycle.load_reconcile_config(args.config, args.catalog)
    gateway = gateway_factory(config.get("warehouse_id"))
    tokens = {
        "project_id": args.project_id,
        "target": args.target,
        "champion_version": str(alias_version(gateway, args.model, CHAMPION)),
    }
    failed = False
    for section, reconciler, desired in lifecycle.build_tasks(fill_tokens(config, tokens), gateway):
        result = reconciler.reconcile(desired, dry_run=args.dry_run)
        print(f"[{section}] {'applied' if result.applied else 'dry run'}: {len(result.changes)} change(s)")
        for error in result.errors:
            print(f"  ERROR {error}", file=sys.stderr)
        failed = failed or not result.ok
    return lifecycle.EXIT_FAILED if failed else lifecycle.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(prog="mlops_lifecycle_task.py", description=__doc__.split("\n")[1])
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser("promote", help="promote the challenger version through the gate")
    promote.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    promote.add_argument("--gate", required=True, help="path to the gate config")
    reconcile = commands.add_parser("reconcile", help="reconcile the serving endpoint with the champion version")
    reconcile.add_argument("--model", required=True, help="full UC model name, catalog.schema.model")
    reconcile.add_argument("--config", required=True, help="path to the desired-state config")
    reconcile.add_argument("--catalog", required=True, help="catalog of the target")
    reconcile.add_argument("--project-id", required=True, help="project id, e.g. example_claims")
    reconcile.add_argument("--target", required=True, help="target name, e.g. prod")
    reconcile.add_argument("--dry-run", action="store_true", help="plan and print the changes without applying them")
    return parser


def main(
    argv: Sequence[str] | None = None, gateway_factory: Callable[..., WorkspaceGateway] = lifecycle.default_gateway
) -> int:
    """Run one task and return the process exit code."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return lifecycle.EXIT_OK if error.code in (None, 0) else lifecycle.EXIT_USAGE
    runner = run_promote if args.command == "promote" else run_reconcile
    try:
        return runner(args, gateway_factory)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return lifecycle.EXIT_USAGE
    except (AliasNotSet, GatewayError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return lifecycle.EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
