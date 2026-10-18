"""Read a JDBC table in parallel ranges and land it as Parquet, as a snapshot or above a stored high-water mark."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import (
    date,
    datetime,
)
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
)

from dbxops.connectors.base import (
    ConnectorEnv,
    LandingState,
    LandingTarget,
    LandResult,
    literal_secret_problems,
    missing_keys,
)

MODES = ("snapshot", "watermark")


@dataclass(frozen=True)
class FetchRequest:
    """One ranged read: the rows with ``lower <= partition_column < upper``, and above a watermark when one is set."""

    table: str
    columns: tuple[str, ...]
    partition_column: str
    lower: Any
    upper: Any
    watermark_column: str = ""
    watermark: Any = None


class Batch(Protocol):
    """The rows of one range, held wherever the source keeps them (a Spark frame for real, a list in tests)."""

    def write_parquet(self, path: str) -> int:
        """Write the rows as Parquet to ``path`` and return how many were written."""

    def max_value(self, column: str) -> Any:
        """Return the largest value of ``column`` in the batch, or ``None`` when it is empty."""


class JdbcSource(Protocol):
    """A database reached over JDBC."""

    def bounds(self, table: str, column: str) -> tuple[Any, Any] | None:
        """Return the smallest and largest value of ``column``, or ``None`` for an empty table."""

    def fetch(self, request: FetchRequest) -> Batch:
        """Return the rows of one range."""


def _number(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).timestamp()
    return float(value)


def _restore(like: Any, number: float) -> Any:
    if isinstance(like, datetime):
        return datetime.fromtimestamp(number, tz=like.tzinfo)
    if isinstance(like, date):
        return datetime.fromtimestamp(number).date()
    return int(number) if isinstance(like, int) else number


def partition_ranges(lower: Any, upper: Any, count: int) -> list[tuple[Any, Any]]:
    """
    Split ``[lower, upper]`` into ``count`` consecutive ranges ``[a, b)``; the last one includes ``upper``.

    Works for integers, floats, dates and datetimes. A range can be empty when there are fewer distinct values than
    ranges; the caller skips those.
    """
    low, high = _number(lower), _number(upper)
    step = (high - low) / count
    edges = [_restore(lower, low + step * index) for index in range(count)]
    closing = _restore(lower, high) if not isinstance(lower, (datetime, date)) else _restore(lower, high + 1)
    if isinstance(lower, int):
        closing = upper + 1
    elif isinstance(lower, float):
        closing = float(upper) + 1e-9
    edges.append(closing)
    return list(zip(edges[:-1], edges[1:]))


def _updated_watermark(previous: str | None, candidate: Any) -> str | None:
    """Return the larger of ``previous`` and ``candidate`` (compared as strings), or ``previous`` if ``candidate`` is ``None``."""
    if candidate is None:
        return previous
    text = str(candidate)
    return text if previous is None or text > previous else previous


class JdbcConnector:
    """Land ``table`` from ``source`` in ``num_partitions`` parallel ranges; ``mode`` is snapshot or watermark."""

    kind = "jdbc"
    landable = True

    def __init__(self, source_factory: Callable[[Mapping[str, Any], ConnectorEnv], JdbcSource]):
        self._source_factory = source_factory

    def validate(self, config: Mapping[str, Any]) -> list[str]:
        """Return what is wrong with ``config``; an unpartitioned read is refused, and a watermark mode needs its column."""
        problems = missing_keys(config, ("url", "table", "partition_column")) + literal_secret_problems(config)
        count = config.get("num_partitions")
        if not (isinstance(count, int) and count >= 2):
            problems.append(
                "num_partitions must be a whole number of 2 or more: an unpartitioned JDBC read runs on one task"
            )
        mode = config.get("mode", "snapshot")
        if mode not in MODES:
            problems.append(f"mode must be one of {', '.join(MODES)}")
        if mode == "watermark" and not config.get("watermark_column"):
            problems.append("watermark_column is required when mode is watermark")
        return problems

    def _write_range(
        self, source: JdbcSource, request: FetchRequest, target: LandingTarget, prefix: str, index: int
    ) -> tuple[str, int, Any] | None:
        """Fetch one range and write it to Parquet; return its relative path, row count and watermark candidate, or ``None`` if empty."""
        batch = source.fetch(request)
        path = target.path(request.table, prefix, f"part-{index:04d}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        written = batch.write_parquet(str(path))
        if not written:
            path.unlink(missing_ok=True)
            return None
        relative = path.relative_to(target.root.resolve()).as_posix()
        watermark_candidate = batch.max_value(request.watermark_column) if request.watermark_column else None
        return relative, written, watermark_candidate

    def _run_partitions(
        self,
        source: JdbcSource,
        target: LandingTarget,
        config: Mapping[str, Any],
        previous: str | None,
        env: ConnectorEnv,
    ) -> tuple[LandResult, str | None] | None:
        """Fetch and write every partition range; return the result and the newest watermark, or ``None`` if the table is empty."""
        bounds = source.bounds(config["table"], config["partition_column"])
        if bounds is None:
            return None
        prefix = f"{config.get('mode', 'snapshot')}-{env.now().strftime('%Y%m%dT%H%M%S')}"
        result = LandResult()
        newest = previous
        for index, (lower, upper) in enumerate(partition_ranges(*bounds, config["num_partitions"])):
            request = FetchRequest(
                config["table"], tuple(config.get("columns", ())), config["partition_column"], lower, upper,
                config.get("watermark_column", "") if config.get("mode", "snapshot") == "watermark" else "", previous,
            )  # fmt: skip
            outcome = self._write_range(source, request, target, prefix, index)
            if outcome is None:
                continue
            result.files.append(outcome[0])
            result.rows += outcome[1]
            newest = _updated_watermark(newest, outcome[2])
        return result, newest

    def land(
        self, config: Mapping[str, Any], target: LandingTarget, state: LandingState, env: ConnectorEnv
    ) -> LandResult:
        """Read each range into ``<table>/<mode>-<timestamp>/part-NNNN.parquet`` and move the watermark past what was read."""
        source = self._source_factory(config, env)
        mode = config.get("mode", "snapshot")
        key = f"jdbc:{config['table']}:watermark"
        previous = state.get(key) if mode == "watermark" else None
        outcome = self._run_partitions(source, target, config, previous, env)
        if outcome is None:
            return LandResult(watermark=previous, notes=[f"{config['table']} is empty"])
        result, newest = outcome
        result.watermark = previous
        if mode == "watermark" and newest is not None:
            state.set(key, newest)
            result.watermark = newest
        return result
