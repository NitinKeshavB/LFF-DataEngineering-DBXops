"""Tests that the agent and command files the harness ships are well formed and the reviewer stays read-only."""

from pathlib import Path

import pytest
import yaml

from tests.consts import PROJECT_DIR

CLAUDE = PROJECT_DIR / ".claude"
FILES = [
    pytest.param(path, id=path.name)
    for path in sorted([*(CLAUDE / "agents").glob("*.md"), *(CLAUDE / "commands").glob("*.md")])
]


def frontmatter(path: Path) -> dict:
    """Return the YAML front matter of a markdown file."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no front matter"
    return yaml.safe_load(text.split("---\n", 2)[1])


@pytest.mark.parametrize("path", FILES)
def test__every_agent_and_command_has_a_description(path: Path):
    """Front matter parses and carries a description, so the harness can route to the file."""
    assert frontmatter(path).get("description")


def test__the_code_reviewer_can_read_and_run_but_never_edit():
    """The reviewer's tools exclude Edit, Write and NotebookEdit."""
    tools = {tool.strip() for tool in frontmatter(CLAUDE / "agents/code-reviewer.md")["tools"].split(",")}
    assert {"Read", "Grep", "Bash"} <= tools and not tools & {"Edit", "Write", "NotebookEdit"}


@pytest.mark.parametrize("name", ["review", "review-runs"])
def test__the_review_commands_cannot_edit_files(name: str):
    """/review and /review-runs are read-only commands."""
    tools = {tool.strip() for tool in frontmatter(CLAUDE / f"commands/{name}.md")["allowed-tools"].split(",")}
    assert not tools & {"Edit", "Write", "NotebookEdit"}
