"""Ontos-ready metadata: dataflow-spec parsing, ODCS contracts, ODPS products, validation and export."""

from __future__ import annotations

from pathlib import Path

from dbxops.findings import Finding
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import (
    GitBaseline,
    Options,
    validate,
)


def validate_metadata(root: str | Path = ".", baseline: str | None = None) -> list[Finding]:
    """
    Run every metadata rule over the repository at `root` and return all findings.

    `baseline` is a git ref (for example `origin/main`); with it, a changed contract or product must also carry
    a higher semver than it had at that ref.
    """
    path = Path(root).resolve()
    return validate(RepoContext.load(path), Options(baseline=GitBaseline(path, baseline) if baseline else None))
