"""Tests for the XLSX connector: sheet-to-Parquet conversion, header handling, and idempotent reruns."""

from pathlib import Path

import pytest

from dbxops.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    InMemoryState,
    LandingTarget,
)
from dbxops.connectors.xlsx_files import (
    XlsxConnector,
    _headers,
    _slug,
    sheet_table,
)


def _write_workbook(path: Path, sheets: dict[str, list[tuple]]):
    from openpyxl import Workbook  # pylint: disable=import-outside-toplevel

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    workbook.save(path)


def test__slug_replaces_non_alphanumerics_and_lowercases():
    """Non-alphanumeric characters become underscores and the result is lowercased."""
    assert _slug("Customer Name!") == "customer_name"


def test__slug_falls_back_to_sheet_for_an_empty_result():
    """A header with no alphanumeric characters slugs to ``sheet``."""
    assert _slug("###") == "sheet"


def test__headers_fills_blanks_and_deduplicates_repeats():
    """A blank header becomes ``col_n`` and a repeated name gets a numeric suffix."""
    assert _headers(["Name", "", "Name"]) == ["name", "col_2", "name_2"]


def test__sheet_table_returns_none_when_there_are_fewer_rows_than_the_header():
    """A sheet with fewer rows than the header row returns ``None``."""
    assert sheet_table([], header_row=1, source="s.xlsx", sheet="Sheet1") is None


def test__sheet_table_returns_none_when_the_body_is_empty():
    """A sheet whose only body row is entirely blank returns ``None``."""
    assert sheet_table([("a", "b"), (None, None)], header_row=1, source="s.xlsx", sheet="Sheet1") is None


def test__sheet_table_builds_a_table_with_source_and_sheet_columns():
    """The table carries the header columns plus ``_source_file`` and ``_sheet``, all as text."""
    rows = [("name", "age"), ("Alice", 30)]
    table = sheet_table(rows, header_row=1, source="s.xlsx", sheet="People")
    assert table.num_rows == 1
    assert table.column_names == ["name", "age", "_source_file", "_sheet"]
    assert table.column("_source_file").to_pylist() == ["s.xlsx"]
    assert table.column("_sheet").to_pylist() == ["People"]
    assert table.column("age").to_pylist() == ["30"]


def test__validate_requires_source_path():
    """A config missing source_path is rejected."""
    assert "source_path is required" in XlsxConnector().validate({})


def test__validate_rejects_a_non_positive_header_row():
    """A header_row of 0 or less is rejected."""
    assert any("header_row" in p for p in XlsxConnector().validate({"source_path": "/x", "header_row": 0}))


def test__validate_passes_a_well_formed_config():
    """A config with only source_path has no problems."""
    assert XlsxConnector().validate({"source_path": "/x"}) == []


def test__land_converts_each_sheet_to_its_own_parquet_file(tmp_path: Path):
    """Each non-empty sheet lands as its own Parquet file; an empty sheet is noted and skipped."""
    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    source = tmp_path / "source"
    source.mkdir()
    _write_workbook(
        source / "book.xlsx",
        {"People": [("name", "age"), ("Alice", 30)], "Empty": [("a",)]},
    )
    target = tmp_path / "landing"
    target.mkdir()
    result = XlsxConnector().land({"source_path": str(source)}, LandingTarget(target), InMemoryState(), ConnectorEnv())
    assert len(result.files) == 1
    assert result.rows == 1
    assert any("Empty" in note for note in result.notes)
    written = pq.read_table(target / result.files[0])
    assert written.column("name").to_pylist() == ["Alice"]


def test__land_skips_a_workbook_already_landed(tmp_path: Path):
    """A workbook whose content hash was already landed is skipped on rerun."""
    source = tmp_path / "source"
    source.mkdir()
    _write_workbook(source / "book.xlsx", {"People": [("name",), ("Alice",)]})
    target = tmp_path / "landing"
    target.mkdir()
    state = InMemoryState()
    connector = XlsxConnector()
    connector.land({"source_path": str(source)}, LandingTarget(target), state, ConnectorEnv())
    result = connector.land({"source_path": str(source)}, LandingTarget(target), state, ConnectorEnv())
    assert result.skipped == 1
    assert result.files == []


def test__land_raises_connector_error_for_a_corrupt_workbook(tmp_path: Path):
    """A file that cannot be read as a workbook raises ``ConnectorError`` naming it."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.xlsx").write_bytes(b"not a real workbook")
    target = tmp_path / "landing"
    target.mkdir()
    with pytest.raises(ConnectorError, match="cannot read broken.xlsx"):
        XlsxConnector().land({"source_path": str(source)}, LandingTarget(target), InMemoryState(), ConnectorEnv())
