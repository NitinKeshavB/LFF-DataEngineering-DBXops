"""
Command line: ``python -m dbxops.metastore publish|ensure-schema|tags-plan``.

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

import yaml

from dbxops.metastore import collectors
from dbxops.metastore.dictionary import render_dictionary
from dbxops.metastore.errors import (
    ConfigError,
    MetastoreError,
)
from dbxops.metastore.factory import RepositoryFactory
from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    PLATFORM_REPO_ID,
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
from dbxops.registry.snapshots import merge_glossary

WHAT_CHOICES = ("definitions", "deployments", "lineage")
DEFAULT_CONFIG = "conf/metastore.yml"

RepositoryBuilder = Callable[[str, str], MetadataRepository]


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m dbxops.metastore", description="Publish to the metadata database."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "ensure-schema", "publish-registry"):
        command = commands.add_parser(name)
        command.add_argument("--target", required=True)
        command.add_argument("--config", default=DEFAULT_CONFIG)
        command.add_argument("--root", default=".", help="repo root holding metadata/ and the bundles")
    commands.add_parser("dictionary", help="print the data dictionary (markdown) generated from the model")
    plan = commands.add_parser(
        "tags-plan", help="print the governance tags of every contract table as a metadata_sync section"
    )
    plan.add_argument("--target", help="a target of conf/environments.yml (not needed with --bundle)")
    plan.add_argument("--root", default=".", help="repo root holding metadata/ and the bundles")
    plan.add_argument(
        "--catalog", help="catalog of the tables (default: the target's catalog in conf/environments.yml)"
    )
    plan.add_argument("--bundle", help="only this bundle's contracts; the catalog stays the {catalog} token")
    registry = commands.choices["publish-registry"]
    registry.add_argument("--registry", required=True, help="registry checkout: projects/, domains.yaml, glossary/")
    registry.add_argument("--git-sha", default=os.environ.get("GITHUB_SHA"))
    registry.add_argument("--published-by", default=os.environ.get("GITHUB_ACTOR", "dbxops"))
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
    for info in found.specs:
        records += collectors.dataflow_spec_records(
            info,
            repo_id=context.repo_id,
            source_path=info.spec.path.relative_to(info.bundle_dir.parent.parent).as_posix(),
            git_commit=context.git_commit,
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
        tags += collectors.tag_rows(doc, found.owners)
    records = collectors.apply_hierarchy(records, found.hierarchy, repo_id=publisher.repo_id, git_commit=args.git_sha)
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
        print(f"asset edges: {publisher.publish_asset_edges(collectors.asset_edge_rows(found.hierarchy))}")
    if "deployments" in what:
        print(f"deployments: {_publish_deployments(publisher, args, runner)}")
    if "lineage" in what:
        edges = LineageCollector.spec_edges(_spec_records(found, context))
        print(f"lineage: {publisher.publish_lineage(edges, ORIGIN_SPEC)}")
    return 0


def _target_catalog(root: Path, target: str | None) -> str:
    environments = yaml.safe_load((root / "conf/environments.yml").read_text(encoding="utf-8")) or {}
    catalog = (environments.get("targets") or {}).get(target, {}).get("catalog", "")
    if not catalog or "PLACEHOLDER" in catalog:
        raise ConfigError(f"target {target!r} has no real catalog ({catalog!r}); pass --catalog")
    return catalog


def _with_logical_env(asset_id: str) -> str:
    """Return ``<schema>{logical_env}.<table>``: a developer's schemas carry a suffix in dev."""
    schema, _, table = asset_id.partition(".")
    return f"{schema}{{logical_env}}.{table}"


def run_tags_plan(args: argparse.Namespace) -> int:
    """
    Print the ``metadata_sync`` section that applies the governance tags of every contract table.

    Save it in the reconcile config and run ``python -m dbxops.lifecycle reconcile --dry-run`` to see the changes.
    With ``--bundle`` only that bundle's tables are listed and the catalog stays the ``{catalog}`` token, which the
    bundle's job task fills from the target. Nothing is written to a workspace here.
    """
    root = Path(args.root)
    catalog = args.catalog or ("{catalog}" if args.bundle else _target_catalog(root, args.target))
    found = discover(root)
    if args.bundle:
        found.contracts = [(path, doc) for path, doc in found.contracts if f"bundles/{args.bundle}/" in path]
    tags = [
        {
            "entity_type": "table",
            "entity_id": f"{catalog}.{_with_logical_env(row.entity_id) if args.bundle else row.entity_id}",
            "tag_key": row.tag_key,
            "tag_value": row.tag_value,
            "is_active": True,
        }
        for _, doc in found.contracts
        for row in collectors.tag_rows(doc, found.owners)
    ]
    print(yaml.safe_dump({"metadata_sync": {"tags": tags}}, sort_keys=False), end="")
    return 0


def run_publish_registry(args: argparse.Namespace, build: RepositoryBuilder) -> int:
    """Publish the registry's platform-owned records (projects, squads, domains, glossary) as ``_platform``."""
    root = Path(args.registry)
    context = PublishContext(PLATFORM_REPO_ID, args.published_by, args.git_sha, args.target)
    required = (yaml.safe_load((root / "required_tags.yaml").read_text(encoding="utf-8")) or {}).get("required", [])
    publisher = MetadataPublisher(build(args.config, args.target), context, required_tag_keys=required)
    options = {"repo_id": PLATFORM_REPO_ID, "git_commit": args.git_sha}
    records: list[DefinitionRecord] = []
    tags: list = []
    for path in sorted((root / "projects").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        records += collectors.registration_records(doc, source_path=f"projects/{path.name}", **options)
        tags += collectors.project_tag_rows(doc)
    domains = yaml.safe_load((root / "domains.yaml").read_text(encoding="utf-8"))
    records += collectors.domain_records(domains, source_path="domains.yaml", **options)
    records += collectors.glossary_records(merge_glossary(root / "glossary"), source_path="glossary", **options)
    print(f"registry: {publisher.publish_definitions(records, tags=tags)}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    build: RepositoryBuilder | None = None,
    runner: BundleRunner | None = None,
) -> int:
    """Run the command line and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "dictionary":
        print(render_dictionary(), end="")
        return 0
    builder = build or RepositoryFactory.from_config
    try:
        if args.command == "tags-plan":
            return run_tags_plan(args)
        if args.command == "ensure-schema":
            builder(args.config, args.target).ensure_schema()
            print("schema ready")
            return 0
        if args.command == "publish-registry":
            return run_publish_registry(args, builder)
        return run_publish(args, builder, runner)
    except MetastoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
