"""The validation finding shared by the metadata and registry validators."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One validation problem: which rule found it, where, and what is wrong."""

    rule: str
    path: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        """Return `severity [rule] path: message`."""
        return f"{self.severity} [{self.rule}] {self.path}: {self.message}"


def has_errors(findings: list[Finding]) -> bool:
    """Return whether any finding is an error (warnings do not fail a run)."""
    return any(f.severity == "error" for f in findings)
