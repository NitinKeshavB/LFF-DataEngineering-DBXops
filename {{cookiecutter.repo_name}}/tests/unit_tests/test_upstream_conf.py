"""Tests for conf/upstream.yml: it names the template and the library pin, and the sync workflow reads the same repo."""

import yaml

from tests.consts import PROJECT_DIR


def test__upstream_conf_names_the_template_repo_and_a_library_pin():
    """The template repo is a GitHub URL, the pin is a version, and the index is a tracked placeholder until set."""
    conf = yaml.safe_load((PROJECT_DIR / "conf/upstream.yml").read_text(encoding="utf-8"))
    assert conf["template"]["repo"].startswith("https://github.com/")
    assert conf["template"]["ref"]
    assert conf["dbxops"]["pinned_version"].count(".") == 2
    assert conf["dbxops"]["index_url"].startswith("PLACEHOLDER-")


def test__the_template_sync_workflow_only_runs_with_a_cruft_file_and_never_merges():
    """The job stops when `.cruft.json` is missing, opens a pull request and does not merge it."""
    text = (PROJECT_DIR / ".github/workflows/template-sync.yml").read_text(encoding="utf-8")
    assert ".cruft.json" in text and "gh pr create" in text
    assert "gh pr merge" not in text and "--auto" not in text
