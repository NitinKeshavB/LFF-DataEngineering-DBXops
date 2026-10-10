"""Generate a real project, install it, and run its own validation and tests. Slow; needs network (git, pip)."""

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest
from cookiecutter.main import cookiecutter

from tests.consts import TEMPLATE_DIR

VARIANTS = {
    "example-squad": {},
    "domain-squad-no-examples": {
        "project_name": "claims_fraud",
        "squad_title": "Claims Fraud Squad",
        "kind": "domain",
        "domain": "claims__litigation",
        "include_examples": "no",
    },
}
CHECKS = (["make", "validate-metadata"], ["make", "validate-specs"], ["make", "check-skills"], ["make", "test-quick"])


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    """Run a command and fail the test with the tail of its output when it does not succeed."""
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{' '.join(command)} failed:\n{(result.stdout + result.stderr)[-3000:]}"


@pytest.mark.slow
@pytest.mark.parametrize("answers", VARIANTS.values(), ids=VARIANTS.keys())
def test__a_generated_project_installs_validates_and_passes_its_own_tests(
    answers: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    The strongest check there is: what a squad gets on day one works.

    The framework comes from the pin in framework/UPSTREAM.lock over the network, unless
    DBXOPS_TEST_FRAMEWORK_URL names a local mirror of it.
    """
    mirror = os.environ.get("DBXOPS_TEST_FRAMEWORK_URL")
    if mirror:
        monkeypatch.setenv("DBXOPS_FRAMEWORK_URL", mirror)
    else:
        monkeypatch.delenv("DBXOPS_FRAMEWORK_URL", raising=False)
    cookiecutter(str(TEMPLATE_DIR), no_input=True, extra_context=answers, output_dir=str(tmp_path / "out"))
    (project,) = list((tmp_path / "out").iterdir())
    assert (project / "framework/lakeflow_framework/src").is_dir(), "the framework submodule was not added"

    environment = tmp_path / "venv"
    venv.create(environment, with_pip=True)
    bin_dir = environment / ("Scripts" if sys.platform == "win32" else "bin")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "VIRTUAL_ENV": str(environment)}
    run(["make", "install"], project, env)
    for check in CHECKS:
        run(check, project, env)
