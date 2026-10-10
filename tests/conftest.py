"""Fixtures: render the template into a temporary directory."""

from pathlib import Path
from typing import Callable

import pytest
from cookiecutter.main import cookiecutter

from tests.consts import TEMPLATE_DIR


@pytest.fixture
def render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """
    Return a function that renders the template with the given answers and returns the new project's path.

    The Lakeflow Framework submodule is added over the network, which the fast tests neither need nor want, so the
    repository URL points at nothing: the hook then reports the failed step and carries on, as it does offline.
    """
    monkeypatch.setenv("DBXOPS_FRAMEWORK_URL", str(tmp_path / "no-such-repository"))

    def _render(**answers: str) -> Path:
        out = tmp_path / "out"
        cookiecutter(str(TEMPLATE_DIR), no_input=True, extra_context=answers, output_dir=str(out))
        (project,) = list(out.iterdir())
        return project

    return _render
