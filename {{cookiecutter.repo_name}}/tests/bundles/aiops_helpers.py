"""Helpers shared by the AIOps bundle tests: import a job or agent script by path, and read bundle files."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import yaml

from tests.consts import PROJECT_DIR

BUNDLE = PROJECT_DIR / "bundles/aiops"
SRC = BUNDLE / "src"


def load_script(name: str, path: Path) -> ModuleType:
    """Import a script that is not part of the package (a job or agent file) as a module."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_yaml(path: Path) -> dict:
    """Return a YAML file as a dict."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))
