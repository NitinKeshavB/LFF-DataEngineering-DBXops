"""Tests for the Spark pipeline event-log reader, using a fake Spark that answers by query."""

import json
from datetime import (
    datetime,
    timezone,
)

import pytest

from dbxops.metadata.contracts import expectation_rule_id
from dbxops.metastore.errors import CollectError
from dbxops.metastore.event_log import SparkPipelineEventLog

PIPELINE = "11111111-2222-3333-4444-555555555555"
T0 = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 10, 1, 5, tzinfo=timezone.utc)
RUN = {
    "start_time": 1_000,
    "end_time": 2_000,
    "tasks": [{"task_key": "silver", "pipeline_task": {"pipeline_id": PIPELINE}}, {"task_key": "gen"}],
}


class FakeResult:
    """The `.collect()` of a Spark DataFrame."""

    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        """Return the canned rows."""
        return self._rows


class FakeSpark:
    """Answers the updates query and the flows query with canned rows, and records the SQL it was given."""

    def __init__(self, updates, flows):
        self.updates, self.flows, self.sql_seen = updates, flows, []

    def sql(self, statement):
        """Return rows for whichever query this is."""
        self.sql_seen.append(statement)
        return FakeResult(self.updates if "update_progress" in statement else self.flows)


EXPECTATIONS = json.dumps(
    [
        {"name": "premium_id not null", "dataset": "fact_premium", "passed_records": 98, "failed_records": 2},
        {
            "name": "written premium non-negative",
            "dataset": "fact_premium",
            "passed_records": 100,
            "failed_records": 0,
        },
    ]
)
UPDATE_ROW = {"update_id": "abcd1234ef", "started_at": T0, "ended_at": T1, "state": "COMPLETED", "full_refresh": 0}
FLOW_ROW = {
    "dataset": "dev_cat.example_claims_silver.fact_premium",
    "rows_written": "98",
    "rows_upserted": None,
    "rows_deleted": None,
    "rows_dropped": "2",
    "expectations": EXPECTATIONS,
}


def test__updates_carry_state_timing_dataset_metrics_and_expectations():
    """One update per pipeline update; metrics and expectation counts are read from its completed flows."""
    (update,) = SparkPipelineEventLog(FakeSpark([UPDATE_ROW], [FLOW_ROW])).updates_for_run(RUN)
    assert (update.update_id, update.state, update.full_refresh) == ("abcd1234ef", "COMPLETED", False)
    assert update.pipeline_asset_id == f"uc_pipeline/{PIPELINE}"
    assert (update.timing.started_at, update.timing.ended_at) == (T0, T1)
    metrics = update.datasets[0]
    assert (metrics.asset_id, metrics.rows_written, metrics.rows_upserted, metrics.rows_dropped) == (
        "dev_cat.example_claims_silver.fact_premium",
        98,
        0,
        2,
    )


def test__expectation_names_become_the_contracts_rule_ids():
    """The reader maps `premium_id not null` on table fact_premium to the id the contract generator gave that rule."""
    (update,) = SparkPipelineEventLog(FakeSpark([UPDATE_ROW], [FLOW_ROW])).updates_for_run(RUN)
    first = update.expectations[0]
    assert (
        first.name == expectation_rule_id("fact_premium", "premium_id not null") == "fact_premium_premium_id_not_null"
    )
    assert (first.counts.passed, first.counts.failed) == (98, 2)


def test__only_pipeline_tasks_are_read_and_the_sql_is_scoped_to_the_run_window():
    """Tasks without a pipeline are skipped; the updates query is bounded by the run's start and end."""
    spark = FakeSpark([], [])
    assert SparkPipelineEventLog(spark).updates_for_run(RUN) == []
    assert len(spark.sql_seen) == 1
    assert f"event_log('{PIPELINE}')" in spark.sql_seen[0]
    assert "timestamp_millis(1000)" in spark.sql_seen[0] and "timestamp_millis(2000)" in spark.sql_seen[0]


def test__a_full_refresh_update_is_flagged():
    """The create_update event's full_refresh marker comes through."""
    (update,) = SparkPipelineEventLog(FakeSpark([{**UPDATE_ROW, "full_refresh": 1}], [])).updates_for_run(RUN)
    assert update.full_refresh is True and update.datasets == [] and update.expectations == []


@pytest.mark.parametrize(
    "run, updates, message",
    [
        ({**RUN, "tasks": [{"pipeline_task": {"pipeline_id": "x'; DROP TABLE t; --"}}]}, [], "is not a pipeline id"),
        (RUN, [{**UPDATE_ROW, "update_id": "x' OR '1'='1"}], "is not an update id"),
    ],
)
def test__ids_that_could_inject_sql_are_rejected(run, updates, message):
    """Pipeline and update ids are validated before they reach SQL."""
    with pytest.raises(CollectError, match=message):
        SparkPipelineEventLog(FakeSpark(updates, [])).updates_for_run(run)


def test__a_run_still_in_progress_is_read_up_to_now(monkeypatch):
    """The post-run task reads its own live run (no end time): the window ends at the current time."""
    monkeypatch.setattr("dbxops.metastore.event_log.time.time", lambda: 5.0)
    spark = FakeSpark([], [])
    SparkPipelineEventLog(spark).updates_for_run({**RUN, "end_time": 0})
    assert "timestamp_millis(5000)" in spark.sql_seen[0]
