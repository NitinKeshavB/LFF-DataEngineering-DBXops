"""Tests for scripts/vendor_skills.py: command rendering, syncing from a source tree, and the lock check."""

import json
import shutil
from pathlib import Path

import pytest

from scripts import vendor_skills
from tests.consts import PROJECT_DIR

UPSTREAM_COMMAND = (
    '{{description: "Claude form"\nargument-hint: "[x]"|name: other-host\ndescription: "other"}}\n'
    "---\nUse {{`$1` when given|the typed argument}}; then run `{{/databricks:setup|/databricks-setup}}`.\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Return an empty repo root holding a lock with one tarball-style source."""
    (tmp_path / ".claude/skills").mkdir(parents=True)
    lock = {
        "schema": 1,
        "sources": {
            "demo": {
                "kind": "tarball",
                "ref": "v1",
                "skills": {"demo-skill": "skills/demo-skill"},
                "commands": {"demo-command": "commands/cmd.md"},
                "notice_files": ["LICENSE"],
                "exclude": [".gitignore"],
            }
        },
    }
    vendor_skills.save_lock(lock, tmp_path)
    return tmp_path


@pytest.fixture
def source_root(tmp_path_factory) -> Path:
    """Return a fake upstream checkout."""
    root = tmp_path_factory.mktemp("upstream")
    (root / "skills/demo-skill/references").mkdir(parents=True)
    (root / "skills/demo-skill/SKILL.md").write_text("# skill\n", encoding="utf-8")
    (root / "skills/demo-skill/references/a.md").write_text("a\n", encoding="utf-8")
    (root / "skills/demo-skill/.gitignore").write_text("ignored\n", encoding="utf-8")
    (root / "commands").mkdir()
    (root / "commands/cmd.md").write_text(UPSTREAM_COMMAND, encoding="utf-8")
    (root / "LICENSE").write_text("licence text\n", encoding="utf-8")
    return root


def _sync(repo: Path, source_root: Path) -> dict:
    lock = vendor_skills.load_lock(repo)
    vendor_skills.sync_from_root(lock, "demo", source_root, repo)
    return vendor_skills.load_lock(repo)


def test__render_command_picks_claude_variants_but_standalone_command_names():
    """Frontmatter and arguments use the first variant; plugin-namespaced command refs use the second."""
    rendered = vendor_skills.render_command(UPSTREAM_COMMAND)
    assert rendered.startswith('description: "Claude form"\nargument-hint: "[x]"\n---')
    assert "Use `$1` when given" in rendered
    assert "`/databricks-setup`" in rendered
    assert "{{" not in rendered


def test__sync_copies_files_renders_commands_and_records_checksums(repo: Path, source_root: Path):
    """Sync copies skill files (minus excludes), renders the command, adds the licence, and locks every sha256."""
    lock = _sync(repo, source_root)
    files = lock["sources"]["demo"]["files"]
    assert set(files) == {
        ".claude/skills/demo-skill/SKILL.md",
        ".claude/skills/demo-skill/references/a.md",
        ".claude/commands/demo-command.md",
        "THIRD_PARTY/demo/LICENSE",
    }
    assert files[".claude/skills/demo-skill/SKILL.md"] == vendor_skills.sha256(
        repo / ".claude/skills/demo-skill/SKILL.md"
    )
    assert not (repo / ".claude/skills/demo-skill/.gitignore").exists()
    assert vendor_skills.check(repo) == []


def test__check_reports_edited_missing_and_unlocked_files(repo: Path, source_root: Path):
    """A hand edit, a deleted file and a stray file in a vendored directory are each reported."""
    _sync(repo, source_root)
    (repo / ".claude/skills/demo-skill/SKILL.md").write_text("# edited\n", encoding="utf-8")
    (repo / ".claude/skills/demo-skill/references/a.md").unlink()
    (repo / ".claude/skills/demo-skill/extra.md").write_text("x\n", encoding="utf-8")
    problems = vendor_skills.check(repo)
    assert any("SKILL.md differs from the lock" in p for p in problems)
    assert any("missing .claude/skills/demo-skill/references/a.md" in p for p in problems)
    assert any("unlocked file .claude/skills/demo-skill/extra.md" in p for p in problems)


def test__resync_removes_files_dropped_upstream(repo: Path, source_root: Path):
    """A file removed upstream disappears from the repo on the next sync."""
    _sync(repo, source_root)
    (source_root / "skills/demo-skill/references/a.md").unlink()
    lock = _sync(repo, source_root)
    assert not (repo / ".claude/skills/demo-skill/references/a.md").exists()
    assert ".claude/skills/demo-skill/references/a.md" not in lock["sources"]["demo"]["files"]


def test__local_source_must_match_the_submodule_commit(repo: Path):
    """A local (submodule) source fails the check when UPSTREAM.lock has moved past the recorded ref."""
    lock = vendor_skills.load_lock(repo)
    lock["sources"]["demo"].update({"kind": "local", "ref": "aaa", "files": {}})
    vendor_skills.save_lock(lock, repo)
    (repo / "framework").mkdir()
    (repo / "framework/UPSTREAM.lock").write_text("tag=v1\ncommit=bbb\n", encoding="utf-8")
    assert any("submodule moved to bbb" in p for p in vendor_skills.check(repo))
    (repo / "framework/UPSTREAM.lock").write_text("tag=v1\ncommit=aaa\n", encoding="utf-8")
    assert vendor_skills.check(repo) == []


def test__the_real_vendored_tree_matches_its_lock():
    """The repository's own vendored skills are unedited and the dataflow-spec skill matches the submodule pin."""
    assert vendor_skills.check(PROJECT_DIR) == []


def test__every_vendored_source_ships_its_licence():
    """Each source in the lock has its licence file recorded under THIRD_PARTY/<source>/."""
    lock = json.loads((PROJECT_DIR / vendor_skills.LOCK_PATH).read_text(encoding="utf-8"))
    for source_id, source in lock["sources"].items():
        assert any(f.startswith(f"THIRD_PARTY/{source_id}/") for f in source["files"]), source_id


def test__main_check_returns_success_on_the_real_tree(capsys):
    """The CLI check exits 0 and says the tree matches."""
    assert vendor_skills.main(["check"]) == 0
    assert "match the lock" in capsys.readouterr().out


def test__main_check_fails_on_a_broken_tree(repo: Path, source_root: Path, capsys):
    """The CLI check exits 1 and prints each problem when the tree has drifted."""
    _sync(repo, source_root)
    shutil.rmtree(repo / ".claude/skills/demo-skill/references")
    assert vendor_skills.main(["check"], root=repo) == 1
    assert "missing" in capsys.readouterr().out
