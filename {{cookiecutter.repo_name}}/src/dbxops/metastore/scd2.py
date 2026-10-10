"""
The SCD2 decision rules (plan D2), shared by every repository.

The repositories differ in how they write (a transaction on PostgreSQL and SQLite, ordered statements and one
MERGE on Unity Catalog) but they must agree on what a publish means. That agreement lives here, as pure functions
with no database access.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from enum import Enum
from typing import (
    Any,
    Mapping,
    Sequence,
)

from sqlalchemy import Text

from dbxops.metastore.errors import (
    PublishError,
    WriterError,
)
from dbxops.metastore.model import (
    EntitySpec,
    MetaModel,
)
from dbxops.metastore.records import (
    STATUS_RETIRED,
    DefinitionRecord,
    EntityRef,
    Provenance,
)

_STRUCTURAL_CHILD_COLUMNS = frozenset({"record_version"})


class Action(str, Enum):
    """What a publish does with one entity."""

    NEW = "new"
    CHANGE = "change"
    TOUCH = "touch"


@dataclass(frozen=True)
class CurrentRow:
    """The header of the current version of an entity, as read from the database."""

    entity_id: str
    record_version: int
    repo_id: str
    semver: str | None
    status: str
    content_hash: str


def decide(spec: EntitySpec, record: DefinitionRecord, current: CurrentRow | None) -> Action:
    """
    Decide how to publish ``record`` given the current row of the same entity.

    A new entity is ``NEW``. A changed content hash or status (retire and re-open change only the status) is a
    ``CHANGE``: close the current version and insert the next one. Anything else is a ``TOUCH``: only the
    last-seen commit moves. A changed hash on a contract or product without a changed semver is rejected, and so is
    an entity that already belongs to another repo.
    """
    if spec.semver_required and not record.semver:
        raise PublishError(f"{record.entity_id}: a {spec.kind} needs a semver")
    if current is None:
        return Action.NEW
    if current.repo_id != record.repo_id:
        raise WriterError(
            f"{record.entity_id} is owned by repo {current.repo_id!r}; repo {record.repo_id!r} cannot write it"
        )
    hash_changed = record.content_hash != current.content_hash
    if hash_changed and spec.semver_required and record.semver == current.semver:
        raise PublishError(
            f"{record.entity_id}: content changed but semver is still {current.semver!r}; bump the version"
        )
    if hash_changed or record.status != current.status:
        return Action.CHANGE
    return Action.TOUCH


def parent_values(spec: EntitySpec, record: DefinitionRecord, version: int, published_by: str) -> dict[str, Any]:
    """Return the non-clock column values of the parent row for ``version`` of ``record``."""
    unknown = sorted(set(record.columns) - set(spec.columns))
    if unknown:
        raise PublishError(f"{record.entity_id}: unknown {spec.kind} column(s) {', '.join(unknown)}")
    values: dict[str, Any] = {name: record.columns.get(name) for name in spec.columns}
    values.update(
        entity_id=record.entity_id,
        record_version=version,
        repo_id=record.repo_id,
        semver=record.semver,
        status=record.status,
        content_hash=record.content_hash,
        payload=dict(record.payload),
        introduced_commit=record.git_commit,
        last_seen_commit=record.git_commit,
        source_path=record.source_path,
        retired_commit=record.retired_commit,
        is_current=True,
        published_by=published_by,
    )
    return values


def normalised_children(model: MetaModel, spec: EntitySpec, record: DefinitionRecord) -> dict[str, list[dict]]:
    """
    Return the child rows of ``record`` per child table, validated and with ``''`` key sentinels.

    A missing text value becomes ``''`` so key columns never hold a NULL. A row with an unknown column, an empty
    required key, or a key that appears twice is rejected before anything is written.
    """
    unknown = sorted(set(record.children) - {child.table for child in spec.children})
    if unknown:
        raise PublishError(f"{record.entity_id}: unknown child table(s) {', '.join(unknown)} for {spec.kind}")
    return {child.table: _child_rows(model, spec, record, child.table) for child in spec.children}


def _child_rows(model: MetaModel, spec: EntitySpec, record: DefinitionRecord, table_name: str) -> list[dict]:
    child = spec.child(table_name)
    table = model.table(table_name)
    skipped = _STRUCTURAL_CHILD_COLUMNS | {child.parent_column}
    columns = [column for column in table.c if column.name not in skipped]
    seen: set[tuple] = set()
    rows = []
    for raw in record.children.get(table_name, ()):
        unknown = sorted(set(raw) - {column.name for column in columns})
        if unknown:
            raise PublishError(f"{record.entity_id}: {table_name} has no column(s) {', '.join(unknown)}")
        row = {column.name: _sentinel(raw.get(column.name), isinstance(column.type, Text)) for column in columns}
        key = tuple(row[name] for name in child.key_columns)
        if "" in key:
            raise PublishError(f"{record.entity_id}: {table_name} row {dict(raw)!r} has an empty key column")
        if key in seen:
            raise PublishError(f"{record.entity_id}: duplicate {table_name} key {key!r}")
        seen.add(key)
        rows.append(row)
    return rows


def _sentinel(value: Any, is_text: bool) -> Any:
    return "" if value is None and is_text else value


def record_from_rows(
    spec: EntitySpec,
    parent: Mapping[str, Any],
    children: Mapping[str, Sequence[Mapping[str, Any]]],
) -> DefinitionRecord:
    """Rebuild a ``DefinitionRecord`` from the stored parent row and child rows."""
    structural = {"record_version"}
    return DefinitionRecord(
        ref=EntityRef(spec.kind, parent["entity_id"], parent["repo_id"]),
        payload=parent["payload"],
        status=parent["status"],
        semver=parent["semver"],
        columns={name: parent[name] for name in spec.columns},
        children={
            table: [
                {key: value for key, value in row.items() if key not in structural | {spec.child(table).parent_column}}
                for row in rows
            ]
            for table, rows in children.items()
        },
        provenance=Provenance(parent["source_path"], parent["last_seen_commit"], parent["retired_commit"]),
    )


def retired_copy(
    current: DefinitionRecord | None, kind: str, entity_id: str, repo_id: str, git_commit: str | None
) -> DefinitionRecord:
    """
    Return the record that retires ``current``: the same document with status ``retired``.

    Raises ``PublishError`` for an unknown entity and ``WriterError`` when another repo owns it.
    """
    if current is None:
        raise PublishError(f"cannot retire unknown {kind} {entity_id!r}")
    if current.repo_id != repo_id:
        raise WriterError(f"{entity_id} is owned by repo {current.repo_id!r}, not {repo_id!r}")
    provenance = replace(current.provenance, git_commit=git_commit, retired_commit=git_commit)
    return replace(current, status=STATUS_RETIRED, provenance=provenance)
