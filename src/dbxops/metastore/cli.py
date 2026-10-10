"""
Command line: ``python -m dbxops.metastore publish|ensure-schema``.

A thin wrapper: parse arguments, build the repository with ``RepositoryFactory``, discover documents with
``sources``, map them with the collectors and hand them to ``MetadataPublisher``.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import (
    Callable,
    Sequence,
)

from dbxops.metastore import collectors
from dbxops.metastore.errors import MetastoreError
from dbxops.metastore.factory import RepositoryFactory
from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    DefinitionRecord,
    PublishContext,
    PublishCounts,
)
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.runtime_collectors import (
    BundleRunner,
    DeploymentCollector,
    LineageCollector,
)
from dbxops.metastore.sources import (
    Discovered,
    SubprocessBundleRunner,
    ci_context_from_env,
    discover,
    read_repo_id,
)

WHAT_CHOICES = ("definitions", "deployments", "lineage")
DEFAULT_CONFIG = "conf/metastore.yml"

RepositoryBuilder = Callable[[str, str], MetadataRepository]


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m dbxops.metastore", description="Publish to the metadata database."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "ensure-schema"):
        command = commands.add_parser(name)
        command.add_argument("--target", required=True)
        command.add_argument("--config", default=DEFAULT_CONFIG)
        command.add_argument("--root", default=".", help="repo root holding metadata/ and the bundles")
    publish = commands.choices["publish"]
    publish.add_argument("--what", default=",".join(WHAT_CHOICES), help="comma-separated: " + ", ".join(WHAT_CHOICES))
    publish.add_argument("--repo-id", help="default: project_id of metadata/_platform/repo.yaml")
    publish.add_argument("--git-sha", default=os.environ.get("GITHUB_SHA"))
    publish.add_argument("--published-by", default=os.environ.get("GITHUB_ACTOR", "dbxops"))
    publish.add_argument("--bundle", default="", help="bundle name (default: from `bundle summary`)")
    publish.add_argument("--bundle-dir", default=".", help="directory to run `databricks bundle summary` in")
    publish.add_argument("--retire-missing", action="store_true", help="retire contracts/products no longer in git")
    return parser


def _spec_records(found: Discovered, context: PublishContext) -> list[DefinitionRecord]:
    records: list[DefinitionRecord] = []
    for path, doc in found.specs:
        records += collectors.dataflow_spec_records(
            doc, repo_id=context.repo_id, source_path=path, git_commit=context.git_commit
        )
    return records


def _publish_definitions(publisher: MetadataPublisher, found: Discovered, args: argparse.Namespace) -> PublishCounts:
    options = {"repo_id": publisher.repo_id, "git_commit": args.git_sha}
    records = _spec_records(found, publisher.context)
    links: list = []
    tags: list = []
    for path, doc in found.contracts:
        records += collectors.contract_records(doc, source_path=path, **options)
        records += collectors.asset_records(doc, source_path=path, **options)
    for path, doc in found.products:
        records += collectors.product_records(doc, source_path=path, **options)
        records += collectors.asset_records(doc, source_path=path, **options)
    for _, doc in [*found.contracts, *found.products]:
        links += collectors.semantic_link_rows(doc)
        tags += collectors.tag_rows(doc)
    retire = ("data_contract", "data_product") if args.retire_missing else ()
    return publisher.publish_definitions(records, links=links, tags=tags, retire_missing=retire)


def _publish_deployments(publisher: MetadataPublisher, args: argparse.Namespace, runner: BundleRunner | None):
    runner = runner or SubprocessBundleRunner(Path(args.bundle_dir))
    ci = ci_context_from_env(os.environ, uuid.uuid4().hex)
    record = DeploymentCollector(runner, publisher.repo_id).collect(args.target, ci, bundle=args.bundle)
    return publisher.publish_deployment(record)


def run_publish(args: argparse.Namespace, build: RepositoryBuilder, runner: BundleRunner | None = None) -> int:
    """Run the ``publish`` command."""
    what = [item.strip() for item in args.what.split(",") if item.strip()]
    unknown = sorted(set(what) - set(WHAT_CHOICES))
    if unknown:
        print(f"unknown --what value(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    root = Path(args.root)
    context = PublishContext(args.repo_id or read_repo_id(root), args.published_by, args.git_sha, args.target)
    publisher = MetadataPublisher(build(args.config, args.target), context)
    found = discover(root)
    if "definitions" in what:
        print(f"definitions: {_publish_definitions(publisher, found, args)}")
    if "deployments" in what:
        print(f"deployments: {_publish_deployments(publisher, args, runner)}")
    if "lineage" in what:
        edges = LineageCollector.spec_edges(_spec_records(found, context))
        print(f"lineage: {publisher.publish_lineage(edges, ORIGIN_SPEC)}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    build: RepositoryBuilder | None = None,
    runner: BundleRunner | None = None,
) -> int:
    """Run the command line and return the process exit code."""
    args = build_parser().parse_args(argv)
    builder = build or RepositoryFactory.from_config
    try:
        if args.command == "ensure-schema":
            builder(args.config, args.target).ensure_schema()
            print("schema ready")
            return 0
        return run_publish(args, builder, runner)
    except MetastoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
