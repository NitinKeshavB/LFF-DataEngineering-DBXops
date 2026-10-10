"""Paths shared by the tests."""

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent
PROJECT_TEMPLATE = TEMPLATE_DIR / "{{cookiecutter.repo_name}}"
