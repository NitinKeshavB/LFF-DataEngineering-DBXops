"""Tests for the generated per-target substitution files."""

import json
from pathlib import Path

from dbxops.metadata.__main__ import main
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import validate
from dbxops.metadata.substitutions import (
    plan_substitutions,
    render_substitutions,
    write_substitutions,
)

DEV_FILE = "bundles/dataops/src/pipeline_configs/dev_substitutions.json"


def test__tokens_address_project_layer_schemas_in_the_target_catalog():
    """Each layer token is `<catalog>.<project_id>_<layer>{logical_env}`, and the landing path is a bronze volume."""
    tokens = render_substitutions("p", "dev_cat")["tokens"]
    assert tokens["silver_schema"] == "dev_cat.p_silver{logical_env}"
    assert tokens["landing_path"] == "/Volumes/dev_cat/p_bronze{logical_env}/landing"
    assert set(tokens) == {"bronze_schema", "silver_schema", "gold_schema", "ml_schema", "ai_schema", "landing_path"}


def test__a_generated_repo_has_current_substitution_files(repo_root: Path):
    """The fixture repo has one file per target, and none needs rewriting."""
    plans = plan_substitutions(RepoContext.load(repo_root))
    assert sorted(p.path.name for p in plans) == [
        "dev_substitutions.json",
        "environments.yml",
        "metastore.yml",
        "prod_substitutions.json",
    ]
    assert all(p.current for p in plans)


def test__an_edited_or_missing_file_is_reported_and_rewritten(repo_root: Path):
    """Hand edits are findings that name the fix, and writing restores exactly the generated content."""
    dev = repo_root / DEV_FILE
    dev.write_text(json.dumps({"tokens": {"silver_schema": "main.somewhere_else"}}), encoding="utf-8")
    (repo_root / DEV_FILE.replace("dev_", "prod_")).unlink()
    findings = [f for f in validate(RepoContext.load(repo_root)) if f.rule == "substitutions"]
    assert sorted(f.path.rsplit("/", 1)[1] for f in findings) == ["dev_substitutions.json", "prod_substitutions.json"]
    assert all("make sync-substitutions" in f.message for f in findings)
    assert len(write_substitutions(RepoContext.load(repo_root))) == 2
    assert not [f for f in validate(RepoContext.load(repo_root)) if f.rule == "substitutions"]


def test__cli_substitutions_check_and_write(repo_root: Path, capsys):
    """`substitutions --check` exits 1 when a file is stale and 0 after the plain command rewrites it."""
    (repo_root / DEV_FILE).write_text("{}", encoding="utf-8")
    assert main(["--root", str(repo_root), "substitutions", "--check"]) == 1
    assert "would write bundles/dataops/src/pipeline_configs/dev_substitutions.json" in capsys.readouterr().out
    assert main(["--root", str(repo_root), "substitutions"]) == 0
    assert main(["--root", str(repo_root), "substitutions", "--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test__the_bundle_gets_verbatim_copies_of_the_conf_files_its_jobs_read(repo_root: Path):
    """conf/environments.yml and conf/metastore.yml are copied to <bundle>/src/config/ and kept in step."""
    copy = repo_root / "bundles/dataops/src/config/metastore.yml"
    assert copy.read_text() == (repo_root / "conf/metastore.yml").read_text()
    (repo_root / "conf/metastore.yml").write_text("targets: {}\n", encoding="utf-8")
    findings = [f for f in validate(RepoContext.load(repo_root)) if f.rule == "substitutions"]
    assert [f.path for f in findings] == ["bundles/dataops/src/config/metastore.yml"]
    write_substitutions(RepoContext.load(repo_root))
    assert copy.read_text() == "targets: {}\n"
