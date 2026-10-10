"""Tests for scripts/stage_framework.sh: it stages exactly the pinned framework plus our overlay, and refuses otherwise."""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.consts import PROJECT_DIR

SUBMODULE = PROJECT_DIR / "framework/lakeflow_framework"
pytestmark = pytest.mark.skipif(not (SUBMODULE / "src").exists(), reason="framework submodule not checked out")


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Return a minimal repo copy: the script, wrapper, lock and overlay, with the real submodule symlinked in."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "framework").mkdir()
    shutil.copy(PROJECT_DIR / "scripts/stage_framework.sh", root / "scripts")
    for name in ("databricks.yml", "UPSTREAM.lock"):
        shutil.copy(PROJECT_DIR / "framework" / name, root / "framework" / name)
    shutil.copytree(PROJECT_DIR / "framework/local", root / "framework/local")
    (root / "framework/lakeflow_framework").symlink_to(SUBMODULE)
    return root


def stage(root: Path, out: Path) -> subprocess.CompletedProcess:
    """Run the stage script and capture its output."""
    return subprocess.run(
        ["bash", str(root / "scripts/stage_framework.sh"), str(out)], capture_output=True, text=True, check=False
    )


def test__staging_copies_the_framework_src_and_the_wrapper_bundle(sandbox: Path, tmp_path: Path):
    """The staged tree has the upstream src, our wrapper databricks.yml, and no Python caches."""
    out = tmp_path / "out"
    result = stage(sandbox, out)
    assert result.returncode == 0, result.stderr
    assert (out / "src/dlt_pipeline.ipynb").is_file() and (out / "src/lakeflow_framework").is_dir()
    assert (out / "databricks.yml").read_text() == (PROJECT_DIR / "framework/databricks.yml").read_text()
    assert not list(out.rglob("__pycache__"))
    pinned = (PROJECT_DIR / "framework/UPSTREAM.lock").read_text().split("commit=")[1].split()[0]
    assert (out / "STAGED_FROM_COMMIT").read_text().strip() == pinned


def test__the_overlay_lands_in_src_local_without_its_housekeeping_files(sandbox: Path, tmp_path: Path):
    """Files under framework/local are copied to src/local; .gitkeep and OVERLAY.md are not."""
    (sandbox / "framework/local/config").mkdir(exist_ok=True)
    (sandbox / "framework/local/config/org.yaml").write_text("x: 1\n")
    out = tmp_path / "out"
    assert stage(sandbox, out).returncode == 0
    assert (out / "src/local/config/org.yaml").read_text() == "x: 1\n"
    assert not (out / "src/local/OVERLAY.md").exists() and not list((out / "src/local").rglob(".gitkeep"))
    assert (out / "src/local/README.md").is_file()  # upstream's own file is kept


def test__restaging_replaces_the_previous_output(sandbox: Path, tmp_path: Path):
    """Staging into an existing directory removes stale files from the last run."""
    out = tmp_path / "out"
    assert stage(sandbox, out).returncode == 0
    (out / "stale.txt").write_text("old")
    assert stage(sandbox, out).returncode == 0
    assert not (out / "stale.txt").exists()


def test__staging_refuses_when_the_submodule_is_not_at_the_pinned_commit(sandbox: Path, tmp_path: Path):
    """A lock pinning a different commit stops the script before anything is written."""
    (sandbox / "framework/UPSTREAM.lock").write_text("tag=v0.0.0\ncommit=" + "0" * 40 + "\n")
    out = tmp_path / "out"
    result = stage(sandbox, out)
    assert result.returncode == 1
    assert "pins " + "0" * 40 in result.stderr and "make upgrade-lakeflow" in result.stderr
    assert not out.exists()


def test__staging_refuses_when_the_submodule_is_missing(sandbox: Path, tmp_path: Path):
    """Without a checked-out submodule the error tells the user how to fetch it."""
    (sandbox / "framework/lakeflow_framework").unlink()
    result = stage(sandbox, tmp_path / "out")
    assert result.returncode == 1 and "git submodule update --init" in result.stderr


def test__staging_never_modifies_the_submodule():
    """The submodule working tree is clean after staging into a temp directory."""
    before = subprocess.run(
        ["git", "-C", str(SUBMODULE), "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    assert before == ""
