"""Command line for the registry tooling: `python -m dbxops.registry <validate|render>`."""

from __future__ import annotations

import argparse
from pathlib import Path

from dbxops.findings import has_errors
from dbxops.registry.intake import (
    IntakeError,
    intake,
)
from dbxops.registry.registration import validate_registry
from dbxops.registry.snapshots import render_registry_project


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = argparse.ArgumentParser(prog="python -m dbxops.registry")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_cmd = commands.add_parser("validate", help="validate every registration in a registry checkout")
    validate_cmd.add_argument("root", type=Path)
    validate_cmd.add_argument("--strict", action="store_true", help="reject PLACEHOLDER-<what> values")
    render_cmd = commands.add_parser("render", help="write a project's metadata/_platform snapshots")
    render_cmd.add_argument("root", type=Path, help="registry checkout")
    render_cmd.add_argument("project_id")
    render_cmd.add_argument("out", type=Path, help="directory to write the four snapshot files into")
    intake_cmd = commands.add_parser("intake", help="turn an issue-form body (a file) into projects/<id>.yaml")
    intake_cmd.add_argument("registry", type=Path)
    intake_cmd.add_argument("--body", type=Path, required=True, help="file holding the issue body")
    intake_cmd.add_argument("--mode", choices=["onboard", "change"], default="onboard")
    args = parser.parse_args(argv)
    if args.command == "intake":
        try:
            path = intake(args.body.read_text(encoding="utf-8"), args.registry, args.mode)
        except IntakeError as error:
            print(f"The request cannot be accepted yet:\n{error}")
            return 1
        print(f"wrote {path}")
        return 0
    if args.command == "validate":
        findings = validate_registry(args.root, args.strict)
        print("\n".join(str(f) for f in findings) if findings else f"{args.root}: registry is valid")
        return 1 if has_errors(findings) else 0
    files = render_registry_project(args.root, args.project_id)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.out / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} snapshot files to {args.out}")
    return 0
