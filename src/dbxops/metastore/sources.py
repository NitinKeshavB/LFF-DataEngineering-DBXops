"""
A minimal document source for the CLI: find contracts, products and dataflow specs under a repo root.

This is an interim adapter. Discovery and validation belong to the ``dbxops.metadata`` loaders; the CLI only needs
parsed dicts, so this module globs and parses and nothing more. It does not validate documents.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Any,
    Mapping,
)

import yaml

from dbxops.metastore.errors import ConfigError
from dbxops.metastore.records import CiContext

_SKIPPED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "framework", "build", "dist", ".claude"})
REPO_FILE = Path("metadata/_platform/repo.yaml")


@dataclass
class Discovered:
    """Parsed documents with the path they came from, relative to the repo root."""

    contracts: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    products: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    specs: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)


def _files(root: Path, pattern: str) -> list[Path]:
    return sorted(
        path for path in root.rglob(pattern) if not _SKIPPED_DIRS.intersection(path.relative_to(root).parts[:-1])
    )


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def read_repo_id(root: Path) -> str:
    """Return the ``project_id`` of ``metadata/_platform/repo.yaml``, which is this repo's ``repo_id``."""
    path = root / REPO_FILE
    if not path.is_file():
        raise ConfigError(f"{path} not found; pass --repo-id explicitly")
    project_id = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("project_id")
    if not project_id:
        raise ConfigError(f"{path} has no project_id; pass --repo-id explicitly")
    return str(project_id)


def discover(root: Path) -> Discovered:
    """Find ``*.odcs.yaml`` contracts, ``*.odps.yaml`` products and ``dataflowspec`` documents under ``root``."""
    found = Discovered()
    for path in _files(root, "*.odcs.yaml"):
        found.contracts.append((path.relative_to(root).as_posix(), _load(path)))
    for path in _files(root, "*.odps.yaml"):
        found.products.append((path.relative_to(root).as_posix(), _load(path)))
    for pattern in ("*.json", "*.yaml", "*.yml"):
        for path in _files(root, pattern):
            if "dataflowspec" in path.relative_to(root).parts[:-1]:
                document = _load(path)
                if isinstance(document, dict) and "dataFlowId" in document:
                    found.specs.append((path.relative_to(root).as_posix(), document))
    return found


class SubprocessBundleRunner:
    """Runs ``databricks bundle summary -o json -t <target>`` in a bundle directory."""

    def __init__(self, bundle_dir: Path, executable: str = "databricks"):
        self._bundle_dir = bundle_dir
        self._executable = executable

    def summary(self, target: str) -> Mapping[str, Any]:
        """Return the parsed bundle summary."""
        result = subprocess.run(
            [self._executable, "bundle", "summary", "-o", "json", "-t", target],
            cwd=self._bundle_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ConfigError(f"databricks bundle summary failed: {result.stderr.strip()}")
        return json.loads(result.stdout)


def ci_context_from_env(environ: Mapping[str, str], local_id: str) -> CiContext:
    """Build the CI context from GitHub Actions variables; outside CI the run id is ``local-<uuid>``."""
    server, repository = environ.get("GITHUB_SERVER_URL", ""), environ.get("GITHUB_REPOSITORY", "")
    run_id = environ.get("GITHUB_RUN_ID") or f"local-{local_id}"
    url = (
        f"{server}/{repository}/actions/runs/{run_id}" if server and repository and "GITHUB_RUN_ID" in environ else ""
    )
    return CiContext(
        ci_run_id=run_id,
        ci_run_attempt=int(environ.get("GITHUB_RUN_ATTEMPT", "1")),
        ci_run_url=url,
        git_sha=environ.get("GITHUB_SHA", ""),
        deployed_by=environ.get("GITHUB_ACTOR", ""),
    )
