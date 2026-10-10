"""
Job task: record this run in the metadata database (platform_meta). Installed as the `dbxops-publish-run` command.

Runs last (`run_if: ALL_DONE`, so it also records failed runs). It reads the run from the Jobs API and each
pipeline's event log, then writes the run, its pipeline updates, per-dataset row counts and per-rule quality
results (pinned to the contract rule versions that are current), and clears any orphan rows from a crashed publish.

    dbxops-publish-run --run-id {{job.run_id}} --target <target> --repo-id <project_id> --config <metastore.yml>

Bundles run it as a `python_wheel_task` (package `cookie-cutter-dbx`, entry point `dbxops-publish-run`), with the
dbxops wheel (variable `dbxops_wheel_path`) in the task's serverless environment. It is idempotent: a retried task
writes nothing twice.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from dbxops.metastore.event_log import SparkPipelineEventLog
from dbxops.metastore.factory import RepositoryFactory
from dbxops.metastore.records import PublishContext
from dbxops.metastore.tasks import (
    WorkspaceJobsClient,
    publish_run,
)


def _live_readers() -> tuple[Any, Any]:
    """Return the Jobs API client and the event-log reader, built from the task's workspace identity and Spark."""
    from databricks.sdk import WorkspaceClient  # pylint: disable=import-outside-toplevel
    from pyspark.sql import SparkSession  # pylint: disable=import-outside-toplevel

    return WorkspaceJobsClient(WorkspaceClient()), SparkPipelineEventLog(SparkSession.builder.getOrCreate())


def main(argv: list[str] | None = None, *, build: Any = RepositoryFactory.from_config, readers: Any = None) -> int:
    """Publish one finished (or finishing) run; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--repo-id", required=True, help="the project id this job belongs to")
    parser.add_argument("--config", required=True, help="path of the metastore.yml copied into the bundle")
    args = parser.parse_args(argv)
    context = PublishContext(args.repo_id, "dataops-job", os.environ.get("GIT_COMMIT"), args.target)
    counts, collection = publish_run(
        args.run_id,
        repository=build(args.config, args.target),
        context=context,
        readers=readers or _live_readers(),
    )
    print(
        f"run {args.run_id}: {counts}; {len(collection.quality)} quality results, {len(collection.unmapped)} unmapped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
