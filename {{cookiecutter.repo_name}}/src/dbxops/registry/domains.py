"""
Domain taxonomy: Business Domain -> Sub-domain -> Data Domain.

Every node is addressed by its domain path: the slugs of its ancestors and itself
joined with ``__``, for example ``claims__litigation__claim_litigation``. A table
references a data-domain path, so the path alone shows what the table rolls up to.

Run ``python -m dbxops.registry.domains <domains.yaml>`` to validate a taxonomy file.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

import yaml

PATH_SEPARATOR = "__"
LEVELS = ("business_domain", "sub_domain", "data_domain")
STATUSES = frozenset({"existing", "new", "renamed"})
SENSITIVITIES = frozenset({"restricted"})
# 128-character identifier cap minus the 12-character "data_domain_" UC tag prefix.
MAX_PATH_LENGTH = 116

_CHILD_KEYS = ("business_domains", "sub_domains", "data_domains")
_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
_BRANCH_KEYS = frozenset({"name", "slug", "tentative"})
_LEAF_KEYS = frozenset({"name", "slug", "status", "sensitivity", "previous_name", "notes"})


class DomainError(ValueError):
    """Raised when a taxonomy is invalid or a domain path does not resolve."""


@dataclass(frozen=True)
class DomainDetails:
    """Taxonomy annotations carried by a node; status and sensitivity apply to data domains only."""

    status: str | None = None
    sensitivity: str | None = None
    previous_name: str | None = None
    notes: str | None = None
    tentative: bool = False


@dataclass(frozen=True)
class DomainNode:
    """One node of the domain taxonomy."""

    level: str
    slug: str
    name: str
    path: str
    display_path: str
    parent_path: str | None
    details: DomainDetails = field(default_factory=DomainDetails)


class DomainTaxonomy:
    """A validated domain taxonomy, indexed by domain path."""

    def __init__(self, nodes: dict[str, DomainNode]):
        self._nodes = nodes

    @classmethod
    def from_yaml(cls, path: str | Path) -> DomainTaxonomy:
        """Load and validate a taxonomy file."""
        with open(path, encoding="utf-8") as file:
            return cls.from_dict(yaml.safe_load(file))

    @classmethod
    def from_dict(cls, data: Any) -> DomainTaxonomy:
        """
        Build a taxonomy from parsed YAML.

        Every problem found is reported together in a single ``DomainError``.
        """
        builder = _TaxonomyBuilder()
        if isinstance(data, dict):
            builder.add_children(data.get(_CHILD_KEYS[0]), parent=None, depth=0)
        else:
            builder.errors.append("<root>: the taxonomy must be a mapping")
        if builder.errors:
            raise DomainError("invalid domain taxonomy:\n  " + "\n  ".join(builder.errors))
        return cls(builder.nodes)

    def __len__(self) -> int:
        """Return the number of nodes at all levels."""
        return len(self._nodes)

    def __contains__(self, path: object) -> bool:
        """Return whether ``path`` is a known domain path."""
        return path in self._nodes

    def nodes(self, level: str | None = None) -> list[DomainNode]:
        """Return all nodes, or only those at ``level``, in file order."""
        return [node for node in self._nodes.values() if level is None or node.level == level]

    def children(self, path: str) -> list[DomainNode]:
        """Return the direct children of ``path``."""
        return [node for node in self._nodes.values() if node.parent_path == path]

    def resolve(self, ref: Any, level: str | None = None) -> DomainNode:
        """
        Return the node for a domain path, optionally requiring a level.

        Raises ``DomainError`` naming the closest valid paths when ``ref`` is unknown,
        and the valid child paths when ``ref`` is at the wrong level.
        """
        if level is not None and level not in LEVELS:
            raise ValueError(f"unknown level {level!r}; expected one of {', '.join(LEVELS)}")
        node = self._nodes.get(ref) if isinstance(ref, str) else None
        if node is None:
            raise DomainError(f"unknown domain path {ref!r}{self._suggest(ref, level)}")
        if level is not None and node.level != level:
            options = [descendant.path for descendant in self.nodes(level) if self.is_within(descendant.path, ref)]
            hint = f"; choose one of: {', '.join(options)}" if options else ""
            raise DomainError(f"{ref!r} is a {node.level}, but a {level} path is required{hint}")
        return node

    def rollup(self, path: str) -> list[DomainNode]:
        """Return the chain from the business domain down to ``path``."""
        chain = [self.resolve(path)]
        while chain[0].parent_path is not None:
            chain.insert(0, self._nodes[chain[0].parent_path])
        return chain

    def is_within(self, path: str, ancestor: str) -> bool:
        """Return whether ``path`` is ``ancestor`` or one of its descendants."""
        self.resolve(path)
        self.resolve(ancestor)
        return path == ancestor or path.startswith(ancestor + PATH_SEPARATOR)

    def _suggest(self, ref: Any, level: str | None) -> str:
        if not isinstance(ref, str):
            return ""
        by_display = {node.display_path.lower(): node.path for node in self.nodes(level)}
        matches = [by_display[ref.strip().lower()]] if ref.strip().lower() in by_display else []
        matches = matches or difflib.get_close_matches(ref, [node.path for node in self.nodes(level)], n=3)
        return f"; did you mean: {', '.join(matches)}" if matches else ""


class _TaxonomyBuilder:
    """Walks parsed YAML, collecting nodes and every validation error."""

    def __init__(self):
        self.nodes: dict[str, DomainNode] = {}
        self.errors: list[str] = []

    def add_children(self, entries: Any, parent: DomainNode | None, depth: int) -> None:
        where = parent.path if parent else "<root>"
        if not isinstance(entries, list) or not entries:
            self.errors.append(f"{where}: '{_CHILD_KEYS[depth]}' must be a non-empty list")
            return
        for entry in entries:
            node = self._add_node(entry, parent, depth)
            if node is not None and depth < len(LEVELS) - 1:
                self.add_children(entry.get(_CHILD_KEYS[depth + 1]), node, depth + 1)

    def _add_node(self, entry: Any, parent: DomainNode | None, depth: int) -> DomainNode | None:
        error = self._identity_error(entry, parent)
        if error:
            self.errors.append(error)
            return None
        name, slug = entry["name"], entry["slug"]
        path = slug if parent is None else parent.path + PATH_SEPARATOR + slug
        if len(path) > MAX_PATH_LENGTH:
            self.errors.append(f"{path}: domain path is longer than {MAX_PATH_LENGTH} characters")
        node = DomainNode(
            level=LEVELS[depth],
            slug=slug,
            name=name,
            path=path,
            display_path=name if parent is None else f"{parent.display_path} > {name}",
            parent_path=parent.path if parent else None,
            details=self._details(path, entry, depth, parent),
        )
        self.nodes[path] = node
        return node

    def _identity_error(self, entry: Any, parent: DomainNode | None) -> str | None:
        where = parent.path if parent else "<root>"
        if not isinstance(entry, dict):
            return f"{where}: entry {entry!r} must be a mapping"
        name, slug = entry.get("name"), entry.get("slug")
        if not isinstance(name, str) or not name.strip():
            return f"{where}: entry with slug {slug!r} needs a non-empty 'name'"
        if not isinstance(slug, str) or not _SLUG_PATTERN.match(slug):
            return f"{where}: slug {slug!r} for {name!r} must be lowercase words joined by single '_'"
        path = slug if parent is None else parent.path + PATH_SEPARATOR + slug
        if path in self.nodes:
            return f"{path}: duplicate domain path"
        return None

    def _details(self, path: str, entry: dict, depth: int, parent: DomainNode | None) -> DomainDetails:
        is_leaf = depth == len(LEVELS) - 1
        allowed = _LEAF_KEYS if is_leaf else _BRANCH_KEYS | {_CHILD_KEYS[depth + 1]}
        unknown = sorted(set(entry) - allowed)
        if unknown:
            self.errors.append(f"{path}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}")
        if is_leaf:
            self._check_leaf(path, entry)
        inherited = parent.details.tentative if parent else False
        return DomainDetails(
            status=entry.get("status"),
            sensitivity=entry.get("sensitivity"),
            previous_name=entry.get("previous_name"),
            notes=entry.get("notes"),
            tentative=bool(entry.get("tentative", inherited)),
        )

    def _check_leaf(self, path: str, entry: dict) -> None:
        status = entry.get("status")
        if status not in STATUSES:
            self.errors.append(f"{path}: status {status!r} must be one of {', '.join(sorted(STATUSES))}")
        sensitivity = entry.get("sensitivity")
        if sensitivity is not None and sensitivity not in SENSITIVITIES:
            self.errors.append(
                f"{path}: sensitivity {sensitivity!r} must be one of {', '.join(sorted(SENSITIVITIES))}"
            )
        if status == "renamed" and not entry.get("previous_name"):
            self.errors.append(f"{path}: a renamed data domain needs 'previous_name'")


def main(argv: list[str] | None = None) -> int:
    """Validate a taxonomy file, print a summary, and return a process exit code."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m dbxops.registry.domains <domains.yaml>", file=sys.stderr)
        return 2
    try:
        taxonomy = DomainTaxonomy.from_yaml(args[0])
    except (DomainError, OSError, yaml.YAMLError) as error:
        print(error, file=sys.stderr)
        return 1
    counts = ", ".join(f"{len(taxonomy.nodes(level))} {level}s" for level in LEVELS)
    restricted = sum(1 for node in taxonomy.nodes(LEVELS[-1]) if node.details.sensitivity == "restricted")
    print(f"{args[0]}: valid ({counts}; {restricted} restricted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
