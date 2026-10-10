"""
Job task wrapper around `python -m dbxops.lifecycle`, for a job that has the dbxops wheel but not `conf/`.

`dbxops.lifecycle` reads the target's catalog from `conf/environments.yml`, which is not deployed with a bundle.
This wrapper therefore takes the catalog from the bundle and writes a minimal environments file for the call, and it
fills the `{name}` tokens of a reconcile config (the lifecycle library itself only fills `{catalog}`).

    lifecycle_task.py reconcile --config F --target T --catalog C --workspace-host H [--var name=value ...] [--dry-run]
    lifecycle_task.py wait-index --index NAME [--timeout-minutes 30] [--poll-seconds 30]
    lifecycle_task.py promote --model M --alias challenger --gate F

`promote` finds the model version that holds the alias and runs the gate on the metrics of the run that logged it;
`wait-index` polls until a vector search index reports ready. Exit codes follow `dbxops.lifecycle`: 0 success,
1 a failed reconcile, gate or wait, 2 bad usage or configuration.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import (
    Callable,
    Mapping,
    Sequence,
)

import yaml

from dbxops.lifecycle import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    GatewayFactory,
    default_gateway,
)
from dbxops.lifecycle import main as lifecycle_main

TOKEN = re.compile(r"\{([a-z_]+)\}")
LEFT_FOR_LIFECYCLE = frozenset({"catalog"})


class TaskError(ValueError):
    """Raised for bad arguments or an unresolved token in a config."""


def render_config(text: str, variables: Mapping[str, str]) -> str:
    """Replace each `{name}` token that has a variable; raise `TaskError` for any other token except `{catalog}`."""
    unknown = sorted({name for name in TOKEN.findall(text) if name not in variables} - LEFT_FOR_LIFECYCLE)
    if unknown:
        raise TaskError(f"the config uses token(s) without a --var value: {', '.join(unknown)}")
    return TOKEN.sub(lambda match: str(variables.get(match.group(1), match.group(0))), text)


def parse_variables(pairs: Sequence[str]) -> dict[str, str]:
    """Parse repeated `name=value` arguments; a value may be empty (for example an empty logical environment)."""
    variables = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not TOKEN.fullmatch(f"{{{name}}}"):
            raise TaskError(f"--var expects name=value with a lowercase name, got {pair!r}")
        variables[name] = value
    return variables


def environments_document(target: str, catalog: str, workspace_host: str) -> dict:
    """Return the smallest `conf/environments.yml` that `dbxops.config.load_environment` accepts for one target."""
    return {
        "workspace": {"host": workspace_host, "tenant_id": ""},
        "framework_version": "",
        "targets": {
            target: {
                "catalog": catalog,
                "mode": "production",
                "github_environment": target,
                "requires_approval": False,
                "run_as_sp": False,
            }
        },
    }


def run_reconcile(args: argparse.Namespace, gateway_factory: GatewayFactory) -> int:
    """Render the config, write the environments file and run `dbxops.lifecycle reconcile`."""
    rendered = render_config(Path(args.config).read_text(encoding="utf-8"), parse_variables(args.var))
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "lifecycle.yml"
        environments = Path(folder) / "environments.yml"
        config.write_text(rendered, encoding="utf-8")
        environments.write_text(
            yaml.safe_dump(environments_document(args.target, args.catalog, args.workspace_host)), encoding="utf-8"
        )
        argv = ["reconcile", "--target", args.target, "--config", str(config), "--environments", str(environments)]
        return lifecycle_main(argv + (["--dry-run"] if args.dry_run else []), gateway_factory)


def run_promote(args: argparse.Namespace, gateway_factory: GatewayFactory) -> int:
    """Run the gate on the version that holds the alias, and promote it when the gate passes."""
    version = gateway_factory(None).get_model_alias_version(args.model, args.alias)
    if version is None:
        print(f"error: {args.model} has no version with the alias @{args.alias}", file=sys.stderr)
        return EXIT_FAILED
    return lifecycle_main(
        ["promote", "--model", args.model, "--version", str(version), "--gate", args.gate], gateway_factory
    )


def run_wait_index(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Poll the index until it reports ready; return a failure code when the time runs out."""
    gateway = gateway_factory(None)
    deadline = clock() + args.timeout_minutes * 60
    while True:
        found = gateway.get_vector_search_index(args.index)
        if found is not None and found.get("status", {}).get("ready"):
            print(f"{args.index} is ready ({found['status'].get('indexed_row_count', 'unknown')} rows indexed)")
            return EXIT_OK
        if clock() >= deadline:
            print(f"error: {args.index} was not ready after {args.timeout_minutes} minutes", file=sys.stderr)
            return EXIT_FAILED
        sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    commands = parser.add_subparsers(dest="command", required=True)
    reconcile = commands.add_parser("reconcile", help="render a reconcile config and apply it")
    reconcile.add_argument("--config", required=True)
    reconcile.add_argument("--target", required=True)
    reconcile.add_argument("--catalog", required=True)
    reconcile.add_argument("--workspace-host", default="")
    reconcile.add_argument("--var", action="append", default=[], help="name=value for a {name} token")
    reconcile.add_argument("--dry-run", action="store_true")
    promote = commands.add_parser("promote", help="gate and promote the version that holds an alias")
    promote.add_argument("--model", required=True)
    promote.add_argument("--alias", default="challenger")
    promote.add_argument("--gate", required=True)
    wait = commands.add_parser("wait-index", help="wait until a vector search index is ready")
    wait.add_argument("--index", required=True)
    wait.add_argument("--timeout-minutes", type=float, default=30)
    wait.add_argument("--poll-seconds", type=float, default=30)
    return parser


def main(argv: Sequence[str] | None = None, gateway_factory: GatewayFactory = default_gateway) -> int:
    """Run one task and return its exit code."""
    args = build_parser().parse_args(argv)
    runners: dict[str, Callable[[argparse.Namespace, GatewayFactory], int]] = {
        "reconcile": run_reconcile,
        "promote": run_promote,
        "wait-index": run_wait_index,
    }
    try:
        return runners[args.command](args, gateway_factory)
    except (TaskError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
