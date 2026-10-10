"""The template's own shape: what cookiecutter.json promises and what the template tree must and must not hold."""

import json

from tests.consts import (
    PROJECT_TEMPLATE,
    TEMPLATE_DIR,
)


def test__the_prompts_are_the_documented_ones():
    """The README documents these prompts, in this order; the example squad is the default."""
    config = json.loads((TEMPLATE_DIR / "cookiecutter.json").read_text(encoding="utf-8"))
    prompts = [key for key in config if not key.startswith("_")]
    assert prompts == ["project_id", "repo_name", "squad_title", "kind", "domain", "include_examples"]
    assert config["project_id"] == "example_claims"
    assert config["kind"] == ["project", "domain"]
    assert config["include_examples"] == ["yes", "no"]


def test__nothing_is_rendered_so_jinja_syntax_in_the_template_survives():
    """Workflows, skills and job files are full of `${{ }}`; a single rendered file would corrupt them."""
    config = json.loads((TEMPLATE_DIR / "cookiecutter.json").read_text(encoding="utf-8"))
    assert config["_copy_without_render"] == ["*"]


def test__the_framework_is_added_by_the_hook_not_shipped_in_the_template():
    """The submodule is created at generation time from the pin in framework/UPSTREAM.lock."""
    assert not (PROJECT_TEMPLATE / "framework/lakeflow_framework").exists()
    assert not (PROJECT_TEMPLATE / ".gitmodules").exists()
    lock = (PROJECT_TEMPLATE / "framework/UPSTREAM.lock").read_text(encoding="utf-8")
    assert {line.split("=")[0] for line in lock.split() if "=" in line} >= {"repo", "tag", "commit"}


def test__the_template_holds_the_whole_platform():
    """Everything a squad needs is under the template directory, not beside it."""
    expected = ["src/dbxops", "bundles", "conf", "metadata/_platform", "framework/local", "templates/registry-repo"]
    expected += [".github/workflows", ".claude/skills", "context", "tests", "CLAUDE.md", "Makefile", "pyproject.toml"]
    assert [path for path in expected if not (PROJECT_TEMPLATE / path).exists()] == []
