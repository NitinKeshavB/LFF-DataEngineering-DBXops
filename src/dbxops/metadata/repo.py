"""
A loaded view of one squad (or template) repository: platform snapshots, specs, contracts and products.

`RepoContext.load(root)` reads everything the validators and the Ontos export need, once, so rules are
plain functions over this object.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

import yaml
from rdflib import (
    RDF,
    Graph,
    Namespace,
)

from dbxops.metadata.specs import (
    DataflowSpec,
    find_specs,
    load_specs,
    load_tokens,
)
from dbxops.registry.domains import DomainTaxonomy

PLATFORM_DIR = "metadata/_platform"
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
LAYERS = ("bronze", "silver", "gold", "ml", "ai")


@dataclass(frozen=True)
class Doc:
    """A YAML document with the path it was read from (relative to the repo root)."""

    path: Path
    data: dict[str, Any]

    @property
    def doc_id(self) -> str:
        """Return the document's `id`, or the empty string if it has none."""
        return str(self.data.get("id", ""))


@dataclass(frozen=True)
class SpecInfo:
    """A parsed spec with its bundle directory and resolved substitution tokens."""

    spec: DataflowSpec
    bundle_dir: Path
    tokens: dict[str, str]


@dataclass(frozen=True)
class PlatformSnapshot:
    """The registry-owned snapshots under `metadata/_platform`, parsed."""

    repo: dict[str, Any]
    teams: dict[str, Any]
    taxonomy: DomainTaxonomy
    glossary_iris: frozenset[str]


@dataclass
class RepoContext:
    """Everything the metadata rules and exporters read, loaded once."""

    root: Path
    platform: PlatformSnapshot
    environments: dict[str, Any]
    specs: list[SpecInfo] = field(default_factory=list)
    contracts: list[Doc] = field(default_factory=list)
    products: list[Doc] = field(default_factory=list)

    @property
    def repo(self) -> dict[str, Any]:
        """Return the parsed `repo.yaml` snapshot."""
        return self.platform.repo

    @property
    def teams(self) -> dict[str, Any]:
        """Return the parsed `teams.yaml` snapshot."""
        return self.platform.teams

    @property
    def taxonomy(self) -> DomainTaxonomy:
        """Return the domain taxonomy snapshot."""
        return self.platform.taxonomy

    @property
    def glossary_iris(self) -> frozenset[str]:
        """Return the IRI of every concept in the glossary snapshot."""
        return self.platform.glossary_iris

    @property
    def project_id(self) -> str:
        """Return the repo's project id from the platform snapshot."""
        return self.repo["project_id"]

    @property
    def kind(self) -> str:
        """Return `domain` or `project`."""
        return self.repo["kind"]

    @classmethod
    def load(cls, root: Path) -> RepoContext:
        """Read the platform snapshots, environments, specs, contracts and products under `root`."""
        platform = root / PLATFORM_DIR
        ctx = cls(
            root=root,
            platform=PlatformSnapshot(
                repo=_read_yaml(platform / "repo.yaml"),
                teams=_read_yaml(platform / "teams.yaml"),
                taxonomy=DomainTaxonomy.from_yaml(platform / "domains.yaml"),
                glossary_iris=_glossary_iris(platform / "glossary.ttl"),
            ),
            environments=_read_yaml(root / "conf" / "environments.yml"),
        )
        ctx.specs = [info for path in find_specs(root / "bundles") for info in _spec_infos(path)]
        ctx.contracts = _docs(root, "bundles/**/src/dataflows/**/contracts/*.odcs.yaml")
        ctx.products = _docs(root, "bundles/**/src/products/*.odps.yaml")
        return ctx


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _docs(root: Path, pattern: str) -> list[Doc]:
    return [Doc(path.relative_to(root), _read_yaml(path)) for path in sorted(root.glob(pattern))]


def _glossary_iris(path: Path) -> frozenset[str]:
    graph = Graph().parse(path, format="turtle")
    return frozenset(str(subject) for subject in graph.subjects(RDF.type, SKOS.Concept))


def _spec_infos(path: Path) -> list[SpecInfo]:
    bundle_dir = next(parent for parent in path.parents if parent.name == "src").parent
    tokens = load_tokens(bundle_dir)
    return [SpecInfo(spec=spec, bundle_dir=bundle_dir, tokens=tokens) for spec in load_specs(path)]
