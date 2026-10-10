"""
Read what a job run's pipelines did from the Spark Declarative Pipelines event log.

For each pipeline task of a run this reads the pipeline's event log with the ``event_log('<pipeline id>')`` table
function: the updates the run caused, each completed flow's row metrics, and each expectation's passed and failed
record counts. Expectation names are turned into the contract's quality-rule ids (``expectation_rule_id``), so the
results line up with ``md_quality_rule``.

Unverified against a live workspace: the event types and the ``details`` JSON paths below (``update_progress.state``,
``flow_progress.metrics.*``, ``flow_progress.data_quality.expectations``) follow the Databricks event-log documentation
as understood when this was written. The event log reports passed and failed counts per expectation only, so
``dropped`` and ``quarantined`` stay 0 here (for a drop or quarantine rule they equal ``failed``).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import (
    Any,
    Mapping,
    Sequence,
)

from dbxops.metadata.contracts import expectation_rule_id
from dbxops.metastore.errors import CollectError
from dbxops.metastore.records import (
    DatasetMetricsRecord,
    RuleCounts,
    Timing,
)
from dbxops.metastore.runtime_collectors import (
    ExpectationInfo,
    PipelineUpdateInfo,
)

_PIPELINE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_UPDATE_ID = re.compile(r"^[0-9a-zA-Z-]{8,64}$")
_UPDATES_SQL = """
SELECT origin.update_id AS update_id,
       min(timestamp) AS started_at,
       max(timestamp) AS ended_at,
       max_by(get_json_object(details, '$.update_progress.state'), timestamp) AS state,
       max(CASE WHEN event_type = 'create_update'
                 AND get_json_object(details, '$.create_update.full_refresh') = 'true' THEN 1 ELSE 0 END) AS full_refresh
FROM event_log('{pipeline_id}')
WHERE event_type IN ('create_update', 'update_progress')
  AND timestamp >= timestamp_millis({start_ms})
  AND timestamp <= timestamp_millis({end_ms})
GROUP BY origin.update_id
"""
_FLOWS_SQL = """
SELECT origin.flow_name AS dataset,
       get_json_object(details, '$.flow_progress.metrics.num_output_rows') AS rows_written,
       get_json_object(details, '$.flow_progress.metrics.num_upserted_rows') AS rows_upserted,
       get_json_object(details, '$.flow_progress.metrics.num_deleted_rows') AS rows_deleted,
       get_json_object(details, '$.flow_progress.data_quality.dropped_records') AS rows_dropped,
       get_json_object(details, '$.flow_progress.data_quality.expectations') AS expectations
FROM event_log('{pipeline_id}')
WHERE event_type = 'flow_progress'
  AND origin.update_id = '{update_id}'
  AND get_json_object(details, '$.flow_progress.status') = 'COMPLETED'
"""


def _int(value: Any) -> int:
    return int(float(value)) if value not in (None, "") else 0


def _timestamp(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _expectations(dataset: str, raw: Any) -> list[ExpectationInfo]:
    items = json.loads(raw) if isinstance(raw, str) and raw else []
    table = dataset.split(".")[-1]
    return [
        ExpectationInfo(
            dataset,
            expectation_rule_id(table, item["name"]),
            RuleCounts(passed=_int(item.get("passed_records")), failed=_int(item.get("failed_records"))),
        )
        for item in items
    ]


class SparkPipelineEventLog:
    """A ``PipelineEventLog`` that reads each pipeline's event log through ``spark.sql``."""

    def __init__(self, spark: Any):
        self._spark = spark

    def updates_for_run(self, run: Mapping[str, Any]) -> Sequence[PipelineUpdateInfo]:
        """Return the updates of every pipeline task of ``run``, with dataset metrics and expectation counts."""
        updates: list[PipelineUpdateInfo] = []
        for task in run.get("tasks") or ():
            pipeline_id = (task.get("pipeline_task") or {}).get("pipeline_id")
            if pipeline_id:
                updates += self._pipeline_updates(pipeline_id, run)
        return updates

    def _query(self, sql: str) -> list[Any]:
        return list(self._spark.sql(sql).collect())

    def _pipeline_updates(self, pipeline_id: str, run: Mapping[str, Any]) -> list[PipelineUpdateInfo]:
        if not _PIPELINE_ID.match(pipeline_id):
            raise CollectError(f"{pipeline_id!r} is not a pipeline id")
        start = int(run.get("start_time") or 0)
        end = int(run.get("end_time") or 0) or int(time.time() * 1000)  # the post-run task reads its own live run
        sql = _UPDATES_SQL.format(pipeline_id=pipeline_id, start_ms=start, end_ms=end)
        return [self._update(pipeline_id, row) for row in self._query(sql)]

    def _update(self, pipeline_id: str, row: Any) -> PipelineUpdateInfo:
        update_id = str(row["update_id"])
        if not _UPDATE_ID.match(update_id):
            raise CollectError(f"{update_id!r} is not an update id")
        datasets, expectations = [], []
        for flow in self._query(_FLOWS_SQL.format(pipeline_id=pipeline_id, update_id=update_id)):
            datasets.append(
                DatasetMetricsRecord(
                    str(flow["dataset"]),
                    _int(flow["rows_written"]),
                    _int(flow["rows_upserted"]),
                    _int(flow["rows_deleted"]),
                    _int(flow["rows_dropped"]),
                )
            )
            expectations += _expectations(str(flow["dataset"]), flow["expectations"])
        return PipelineUpdateInfo(
            update_id=update_id,
            pipeline_asset_id=f"uc_pipeline/{pipeline_id}",
            state=str(row["state"] or "UNKNOWN"),
            full_refresh=bool(_int(row["full_refresh"])),
            timing=Timing(_timestamp(row["started_at"]), _timestamp(row["ended_at"])),
            datasets=datasets,
            expectations=expectations,
        )
