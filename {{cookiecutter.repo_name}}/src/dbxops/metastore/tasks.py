"""
Task entry points for jobs: the post-run ``publish_run`` step (plan D8).

The step records the run, its pipeline updates, dataset metrics and quality results (pinned to the rule versions
current now), then deletes orphan child rows left by any crashed publish. The UC tag/comment sync is the
``MetadataSyncReconciler`` of a later phase and is not part of this library.
"""

from __future__ import annotations

from typing import (
    Callable,
    Mapping,
)

from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    PublishContext,
    PublishCounts,
)
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.runtime_collectors import (
    InMemoryRuleCatalog,
    JobsClient,
    PipelineEventLog,
    RunCollection,
    RunCollector,
    RunSources,
)


def default_asset_resolver(dataset_name: str) -> str:
    """Return the logical asset id (``<schema>.<name>``) of a Unity Catalog table name."""
    return ".".join(dataset_name.split(".")[-2:])


def publish_run(
    run_id: str,
    *,
    repository: MetadataRepository,
    context: PublishContext,
    readers: tuple[JobsClient, PipelineEventLog],
    asset_resolver: Callable[[str], str] = default_asset_resolver,
) -> tuple[PublishCounts, RunCollection]:
    """
    Collect and record one job run, then clean orphans; return the counts and what was collected.

    ``context`` names the writer, target and commit. ``readers`` is the ``(jobs client, event-log reader)`` pair.
    """
    rules = InMemoryRuleCatalog(repository.current_quality_rules(context.repo_id))
    collector = RunCollector(RunSources(readers[0], readers[1], rules), asset_resolver, context.repo_id)
    collection = collector.collect(run_id, context.target, context.git_commit or "")
    publisher = MetadataPublisher(repository, context)
    counts = publisher.publish_run(collection)
    return counts + PublishCounts(orphans_cleaned=publisher.cleanup_orphans()), collection


class WorkspaceJobsClient:
    """A ``JobsClient`` over ``WorkspaceClient.jobs``."""

    def __init__(self, workspace_client):
        self._jobs = workspace_client.jobs

    def get_run(self, run_id: str) -> Mapping:
        """Return the Jobs API run as a dict."""
        return self._jobs.get_run(run_id=int(run_id)).as_dict()
