"""Document source for the CLI: the contracts, products and dataflow specs of a repo, via the ``dbxops.metadata`` loaders."""

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

from dbxops.metadata.repo import (
    RepoContext,
    SpecInfo,
)
from dbxops.metadata.specs import SpecError
from dbxops.metastore.errors import ConfigError
from dbxops.metastore.records import CiContext
from dbxops.registry.domains import DomainError

REPO_FILE = Path("metadata/_platform/repo.yaml")


@dataclass
class Discovered:
    """The documents a publish needs: contracts and products as parsed YAML, and the expanded dataflow specs."""

    contracts: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    products: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    specs: list[SpecInfo] = field(default_factory=list)


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
    """Load contracts, products and (template-expanded) dataflow specs with the ``dbxops.metadata`` loaders."""
    try:
        ctx = RepoContext.load(root)
    except (OSError, SpecError, DomainError) as error:
        raise ConfigError(f"cannot load {root}: {error}") from error
    return Discovered(
        contracts=[(doc.path.as_posix(), doc.data) for doc in ctx.contracts],
        products=[(doc.path.as_posix(), doc.data) for doc in ctx.products],
        specs=ctx.specs,
    )


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
