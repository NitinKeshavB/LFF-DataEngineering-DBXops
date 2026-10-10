"""
Reject answers that would produce a broken project, before anything is generated.

The rules mirror `dbxops.registry`: the same patterns a registry registration is held to, so a project
generated here can later be registered without renaming anything.
"""

import re
import sys

PROJECT_ID = "{{ cookiecutter.project_id }}"
REPO_NAME = "{{ cookiecutter.repo_name }}"
SQUAD_TITLE = "{{ cookiecutter.squad_title }}"
DOMAIN = "{{ cookiecutter.domain }}"
KIND = "{{ cookiecutter.kind }}"
EXAMPLES = "{{ cookiecutter.include_examples }}"

RULES = (
    (PROJECT_ID, r"[a-z][a-z0-9_]{2,39}", "project_id must be lowercase letters, digits and '_', 3-40 characters"),
    (REPO_NAME, r"dbx-[a-z0-9-]{3,60}", "repo_name must start with 'dbx-' and use lowercase letters, digits and '-'"),
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
    if KIND == "domain" and EXAMPLES == "yes":
        problems.append(
            "kind 'domain' needs include_examples 'no': the example bundles span several business domains, "
            "and a domain repository may only hold data in its own domain"
        )
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
