"""Convert Excel workbooks to Parquet, one file per sheet, so Auto Loader can read them."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import (
    Any,
    Iterable,
    Mapping,
)

from dbxops.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    LandingState,
    LandingTarget,
    LandResult,
    literal_secret_problems,
    missing_keys,
)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "sheet"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _headers(row: Iterable[Any]) -> list[str]:
    """Return unique, non-empty column names from a header row; blanks become ``col_<n>`` and repeats get a suffix."""
    seen: dict[str, int] = {}
    names = []
    for index, value in enumerate(row, start=1):
        name = _slug(str(value)) if value not in (None, "") else f"col_{index}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


def sheet_table(rows: list[tuple[Any, ...]], header_row: int, source: str, sheet: str):
    """Return a pyarrow table of one sheet (all columns text, plus ``_source_file`` and ``_sheet``), or ``None`` if it is empty."""
    import pyarrow as pa  # pylint: disable=import-outside-toplevel  # imported here so the rest of dbxops does not need pyarrow

    if len(rows) < header_row:
        return None
    names = _headers(rows[header_row - 1])
    body = [row for row in rows[header_row:] if any(cell not in (None, "") for cell in row)]
    if not body:
        return None
    columns = {name: [_text(row[i]) if i < len(row) else None for row in body] for i, name in enumerate(names)}
    columns["_source_file"] = [source] * len(body)
    columns["_sheet"] = [sheet] * len(body)
    return pa.table(columns)


def _parquet_bytes(table: Any) -> bytes:
    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


class XlsxConnector:
    """Convert each ``*.xlsx`` under ``source_path``: a sheet becomes ``<workbook>__<sheet>.parquet``; empty sheets are skipped."""

    kind = "xlsx"
    landable = True

    def validate(self, config: Mapping[str, Any]) -> list[str]:
        """Return what is wrong with ``config``: ``source_path`` is required and ``header_row`` must be 1 or more."""
        problems = missing_keys(config, ("source_path",)) + literal_secret_problems(config)
        if "header_row" in config and not (isinstance(config["header_row"], int) and config["header_row"] >= 1):
            problems.append("header_row must be a whole number of 1 or more")
        return problems

    def land(
        self, config: Mapping[str, Any], target: LandingTarget, state: LandingState, _env: ConnectorEnv
    ) -> LandResult:
        """Convert every workbook not seen before (by content hash); a workbook that cannot be read stops the run."""
        from openpyxl import load_workbook  # pylint: disable=import-outside-toplevel

        result = LandResult()
        for book in sorted(Path(config["source_path"]).rglob("*.xlsx")):
            digest = hashlib.sha256(book.read_bytes()).hexdigest()
            if state.get(f"xlsx:{digest}"):
                result.skipped += 1
                continue
            try:
                workbook = load_workbook(book, read_only=True, data_only=True)
            except Exception as error:  # openpyxl raises several unrelated types for a corrupt file
                raise ConnectorError(f"cannot read {book.name}: {error}") from error
            for sheet in workbook.worksheets:
                table = sheet_table(
                    list(sheet.iter_rows(values_only=True)), config.get("header_row", 1), book.name, sheet.title
                )
                if table is None:
                    result.notes.append(f"{book.name}/{sheet.title}: empty, skipped")
                    continue
                name = f"{book.stem}__{_slug(sheet.title)}__{digest[:8]}.parquet"
                result.files.append(target.write_bytes(name, _parquet_bytes(table)))
                result.rows += table.num_rows
            workbook.close()
            state.set(f"xlsx:{digest}", "done")
        return result
