"""
Reject answers that would produce a broken project, before anything is generated.

The rules mirror `dbxops.registry`: the same patterns a registry registration is held to, so a project
generated here can later be registered without renaming anything.
"""

import re
import sys

RAW_PROJECT_NAME = "{{ cookiecutter.project_name }}"
# "Finance Data-Hub" and "finance-data-hub" both become finance_data_hub
PROJECT_ID = re.sub(r"[\s_-]+", "_", RAW_PROJECT_NAME.strip().lower())
REPO_NAME = "{{ cookiecutter.repo_name }}"
SQUAD_TITLE = "{{ cookiecutter.squad_title }}"
DOMAIN = "{{ cookiecutter.domain }}"

RULES = (
    (
        PROJECT_ID,
        r"[a-z][a-z0-9_]{2,39}",
        "project_name must be 3-40 letters, digits, spaces, '-' or '_', starting with a letter, e.g. claims_fraud "
        "(it is turned into lowercase with '_')",
    ),
    (
        REPO_NAME,
        r"[a-z][a-z0-9-]{2,60}",
        "repo_name must be lowercase letters, digits and '-' (no '_'), starting with a letter, e.g. dbx-claims-fraud",
    ),
    (
        SQUAD_TITLE,
        r"[A-Za-z0-9][A-Za-z0-9 &'.,()-]{0,79}",
        "squad_title must be 1-80 plain characters (no quotes or braces)",
    ),
    (
        DOMAIN,
        r"[a-z][a-z0-9_]*(__[a-z][a-z0-9_]*)*",
        "domain must be a domain path such as claims or claims__litigation",
    ),
)


def main() -> int:
    """Check every answer; print each problem and return a non-zero exit code if there is one."""
    problems = [message for value, pattern, message in RULES if not re.fullmatch(pattern, value)]
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
