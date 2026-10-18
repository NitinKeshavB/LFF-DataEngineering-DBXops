"""Tests for the JDBC connector: ranged partitioning, snapshot and watermark landing."""

from datetime import (
    date,
    datetime,
)
from pathlib import Path
from typing import Any

from dbxops.connectors.base import (
    ConnectorEnv,
    InMemoryState,
    LandingTarget,
)
from dbxops.connectors.jdbc import (
    FetchRequest,
    JdbcConnector,
    partition_ranges,
)


class FakeBatch:
    """A ``Batch`` of fixed row count and watermark value, for tests."""

    def __init__(self, rows: int, max_watermark: Any = None):
        self.rows = rows
        self._max_watermark = max_watermark

    def write_parquet(self, path: str) -> int:
        """Write a placeholder file so the connector finds something at ``path``, and return the row count."""
        if self.rows:
            Path(path).write_text("fake-parquet", encoding="utf-8")
        return self.rows

    def max_value(self, _column: str) -> Any:
        """Return the fixed watermark value."""
        return self._max_watermark


class FakeJdbcSource:
    """A ``JdbcSource`` returning fixed bounds and one batch per requested range."""

    def __init__(self, bounds: tuple[Any, Any] | None, batch_for_range):
        self._bounds = bounds
        self._batch_for_range = batch_for_range
        self.requests: list[FetchRequest] = []

    def bounds(self, _table: str, _column: str) -> tuple[Any, Any] | None:
        """Return the fixed bounds."""
        return self._bounds

    def fetch(self, request: FetchRequest):
        """Record the request and return the configured batch for its range."""
        self.requests.append(request)
        return self._batch_for_range(request.lower, request.upper)


def test__partition_ranges_splits_integers_into_half_open_ranges_covering_the_upper_bound():
    """Integer bounds split into consecutive ranges, with the last one including the upper bound."""
    assert partition_ranges(0, 9, 3) == [(0, 3), (3, 6), (6, 10)]


def test__partition_ranges_splits_floats():
    """Float bounds split into ranges whose last upper edge is just past the given upper bound."""
    ranges = partition_ranges(0.0, 10.0, 2)
    assert ranges[0][0] == 0.0
    assert ranges[-1][1] > 10.0


def test__partition_ranges_splits_dates():
    """Date bounds split into the requested number of ranges covering the span."""
    ranges = partition_ranges(date(2024, 1, 1), date(2024, 1, 10), 3)
    assert len(ranges) == 3
    assert ranges[0][0] == date(2024, 1, 1)
    assert ranges[-1][1] == date(2024, 1, 10)


def test__partition_ranges_splits_datetimes():
    """Datetime bounds split into the requested number of ranges."""
    lower, upper = datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 12, 0)
    ranges = partition_ranges(lower, upper, 2)
    assert len(ranges) == 2
    assert ranges[0][0] == lower


def _connector():
    return JdbcConnector(lambda config, env: config["_source"])


def test__validate_requires_url_table_and_partition_column():
    """A config missing url, table and partition_column is rejected for all three."""
    problems = _connector().validate({"num_partitions": 4})
    assert "url is required" in problems
    assert "table is required" in problems
    assert "partition_column is required" in problems


def test__validate_rejects_fewer_than_two_partitions():
    """A single partition is rejected: an unpartitioned JDBC read runs on one task."""
    config = {"url": "u", "table": "t", "partition_column": "id", "num_partitions": 1}
    assert any("num_partitions" in p for p in _connector().validate(config))


def test__validate_rejects_an_unknown_mode():
    """A mode outside snapshot/watermark is rejected."""
    config = {"url": "u", "table": "t", "partition_column": "id", "num_partitions": 2, "mode": "bogus"}
    assert any("mode must be one of" in p for p in _connector().validate(config))


def test__validate_requires_watermark_column_in_watermark_mode():
    """Watermark mode without a watermark_column is rejected."""
    config = {"url": "u", "table": "t", "partition_column": "id", "num_partitions": 2, "mode": "watermark"}
    assert any("watermark_column is required" in p for p in _connector().validate(config))


def test__validate_passes_a_well_formed_snapshot_config():
    """A complete snapshot config has no problems."""
    config = {"url": "u", "table": "t", "partition_column": "id", "num_partitions": 2}
    assert _connector().validate(config) == []


def test__land_notes_an_empty_table_and_does_not_write_anything(tmp_path: Path):
    """An empty table (no bounds) is noted and lands no files."""
    source = FakeJdbcSource(None, lambda lower, upper: FakeBatch(0))
    config = {"table": "t", "partition_column": "id", "num_partitions": 2, "_source": source}
    result = _connector().land(config, LandingTarget(tmp_path), InMemoryState(), ConnectorEnv())
    assert result.notes == ["t is empty"]
    assert result.files == []


def test__land_snapshot_writes_one_file_per_non_empty_partition(tmp_path: Path):
    """Each requested partition lands as its own Parquet file, with rows summed across them."""
    source = FakeJdbcSource((0, 9), lambda lower, upper: FakeBatch(5))
    config = {"table": "t", "partition_column": "id", "num_partitions": 2, "_source": source}
    result = _connector().land(config, LandingTarget(tmp_path), InMemoryState(), ConnectorEnv())
    assert len(result.files) == 2
    assert result.rows == 10
    assert result.watermark is None


def test__land_snapshot_skips_an_empty_partition_and_removes_its_file(tmp_path: Path):
    """A partition that returns no rows is skipped and leaves no file behind."""
    source = FakeJdbcSource((0, 9), lambda lower, upper: FakeBatch(0 if lower == 0 else 3))
    config = {"table": "t", "partition_column": "id", "num_partitions": 2, "_source": source}
    result = _connector().land(config, LandingTarget(tmp_path), InMemoryState(), ConnectorEnv())
    assert len(result.files) == 1
    assert result.rows == 3


def test__land_watermark_mode_advances_the_stored_watermark(tmp_path: Path):
    """Landing in watermark mode stores and returns the newest watermark seen."""
    source = FakeJdbcSource((0, 9), lambda lower, upper: FakeBatch(5, max_watermark="2024-01-02"))
    config = {
        "table": "t",
        "partition_column": "id",
        "num_partitions": 2,
        "mode": "watermark",
        "watermark_column": "updated_at",
        "_source": source,
    }
    state = InMemoryState()
    result = _connector().land(config, LandingTarget(tmp_path), state, ConnectorEnv())
    assert result.watermark == "2024-01-02"
    assert state.get("jdbc:t:watermark") == "2024-01-02"


def test__land_watermark_mode_passes_the_previous_watermark_into_each_request(tmp_path: Path):
    """Every fetch request in watermark mode carries the previously stored watermark."""
    source = FakeJdbcSource((0, 9), lambda lower, upper: FakeBatch(1, max_watermark="2024-01-03"))
    config = {
        "table": "t",
        "partition_column": "id",
        "num_partitions": 2,
        "mode": "watermark",
        "watermark_column": "updated_at",
        "_source": source,
    }
    state = InMemoryState({"jdbc:t:watermark": "2024-01-01"})
    _connector().land(config, LandingTarget(tmp_path), state, ConnectorEnv())
    assert all(request.watermark == "2024-01-01" for request in source.requests)
