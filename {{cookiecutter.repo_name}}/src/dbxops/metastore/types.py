"""
Column types that hide the dialect differences of the metadata database.

``JsonType`` is JSONB on PostgreSQL/Lakebase, VARIANT on Databricks and JSON text on SQLite.
``UtcTimestamp`` is timestamptz on PostgreSQL/Lakebase, TIMESTAMP on Databricks and ISO-8601 ``Z`` text on
SQLite. Both always hand Python values back in a canonical form: a parsed JSON document and a
timezone-aware UTC ``datetime``.

``utc_now()`` is the database clock. ``valid_from``, ``valid_to``, ``published_at`` and the seen-at columns
use it inside the write statement, so the runner's own clock and time zone never reach the database.
"""

from __future__ import annotations

import importlib
import json
from datetime import (
    datetime,
    timezone,
)
from typing import Any

from sqlalchemy import (
    DateTime,
    Text,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement
from sqlalchemy.types import TypeDecorator

from dbxops.hashing import canonical_json

SQLITE_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%fZ"
_TEXT_JSON_DIALECTS = frozenset({"sqlite", "databricks"})


def to_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime; naive datetimes are rejected."""
    if value.tzinfo is None:
        raise ValueError(f"naive datetime {value!r} is not allowed; pass a timezone-aware value")
    return value.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    """Return the ISO-8601 ``Z`` text form (millisecond precision) of a datetime."""
    return to_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    """Parse the ISO-8601 text form back into a timezone-aware UTC datetime."""
    return to_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))


def _databricks_type(name: str) -> Any:
    """Return an instance of a ``databricks-sqlalchemy`` type; that package is optional (extra ``uc``)."""
    return getattr(importlib.import_module("databricks.sqlalchemy"), name)()


class JsonType(TypeDecorator):
    """A JSON document column: JSONB, VARIANT or JSON text depending on the dialect."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Pick the native JSON type of the dialect."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        if dialect.name == "databricks":
            return dialect.type_descriptor(_databricks_type("DatabricksVariant"))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        """Serialise to canonical JSON text where the dialect stores text; pass objects to JSONB."""
        if value is None or dialect.name not in _TEXT_JSON_DIALECTS:
            return value
        return canonical_json(value)

    def process_result_value(self, value, dialect):
        """Parse JSON text back into a document where the dialect stores text."""
        if dialect.name in _TEXT_JSON_DIALECTS and isinstance(value, (str, bytes)):
            return json.loads(value)
        return value


class UtcTimestamp(TypeDecorator):
    """A timestamp column that is always UTC: timestamptz, TIMESTAMP or ISO-8601 ``Z`` text."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Pick the native timestamp type of the dialect."""
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_TIMESTAMP(timezone=True))
        if dialect.name == "databricks":
            return dialect.type_descriptor(_databricks_type("TIMESTAMP"))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: Any, dialect):
        """Normalise to UTC; SQLite stores ISO-8601 text."""
        if value is None:
            return None
        if isinstance(value, str):
            value = parse_utc(value)
        value = to_utc(value)
        return format_utc(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        """Return a timezone-aware UTC datetime."""
        if value is None:
            return None
        if dialect.name == "sqlite" and isinstance(value, str):
            return parse_utc(value)
        return to_utc(value if value.tzinfo else value.replace(tzinfo=timezone.utc))


class UtcNow(FunctionElement):
    """The database clock, as a UTC timestamp."""

    type = UtcTimestamp()
    inherit_cache = True
    name = "utc_now"

    @staticmethod
    def sql(dialect_name: str) -> str:
        """Return the SQL expression of the clock in a dialect."""
        return _CLOCK_SQL.get(dialect_name, "CURRENT_TIMESTAMP")


class UtcNowMinusDays(FunctionElement):
    """The database clock minus a number of days; used for retention cut-offs."""

    type = UtcTimestamp()
    inherit_cache = False
    name = "utc_now_minus_days"

    def __init__(self, days: int):
        super().__init__()
        self.days = int(days)

    def sql(self, dialect_name: str) -> str:
        """Return the SQL expression of the cut-off in a dialect."""
        return _CUTOFF_SQL.get(dialect_name, _CUTOFF_SQL[""]).format(days=self.days)


_CLOCK_SQL = {
    "sqlite": f"strftime('{SQLITE_TIMESTAMP_FORMAT}', 'now')",
    "postgresql": "now()",
    "databricks": "current_timestamp()",
}
_CUTOFF_SQL = {
    "": "CURRENT_TIMESTAMP - INTERVAL '{days} days'",
    "sqlite": f"strftime('{SQLITE_TIMESTAMP_FORMAT}', 'now', '-{{days}} days')",
    "postgresql": "now() - INTERVAL '{days} days'",
    "databricks": "current_timestamp() - INTERVAL {days} DAYS",
}


@compiles(UtcNow)
def _compile_utc_now(element, compiler, **kwargs) -> str:
    return compiler.process(literal_column(element.sql(compiler.dialect.name)), **kwargs)


@compiles(UtcNowMinusDays)
def _compile_utc_now_minus_days(element, compiler, **kwargs) -> str:
    return compiler.process(literal_column(element.sql(compiler.dialect.name)), **kwargs)


def utc_now() -> UtcNow:
    """Return the database-clock expression to use inside a write statement."""
    return UtcNow()


def utc_now_minus_days(days: int) -> UtcNowMinusDays:
    """Return the database clock minus ``days`` days."""
    return UtcNowMinusDays(days)
