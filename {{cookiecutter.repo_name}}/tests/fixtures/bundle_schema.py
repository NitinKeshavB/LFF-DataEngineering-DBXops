r"""
Validate bundle configuration against the Databricks CLI's own JSON schema, offline.

`databricks bundle schema` prints the schema without authenticating or touching a workspace. Its
variable-reference patterns use Unicode classes (`\p{L}`, `\p{N}`) that Python's `re` cannot compile, so
they are translated to ASCII equivalents (the schema keeps its meaning; non-ASCII identifiers are not a
concern for our resource names).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft7Validator

from tests.consts import PROJECT_DIR

_ENV_WITHOUT_CREDENTIALS = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")}


def _translate(node: Any) -> None:
    if isinstance(node, dict):
        pattern = node.get("pattern")
        if isinstance(pattern, str):
            node["pattern"] = pattern.replace("[\\p{L}\\p{N}]", "[A-Za-z0-9]").replace("\\p{L}", "[A-Za-z]")
        for value in node.values():
            _translate(value)
    elif isinstance(node, list):
        for value in node:
            _translate(value)


@pytest.fixture(scope="session")
def cli_bundle_validator() -> Draft7Validator:
    """Return a validator built from `databricks bundle schema`; skip if the CLI is not installed."""
    if shutil.which("databricks") is None:
        pytest.skip("databricks CLI not installed")
    result = subprocess.run(
        ["databricks", "bundle", "schema"], capture_output=True, text=True, check=True, env=_ENV_WITHOUT_CREDENTIALS
    )
    schema = json.loads(result.stdout)
    _translate(schema)
    return Draft7Validator(schema)


def load_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Return a bundle's `databricks.yml` with every included resource file merged under `resources`."""
    root = yaml.safe_load((bundle_dir / "databricks.yml").read_text(encoding="utf-8"))
    for pattern in root.pop("include", []):
        for path in sorted(glob.glob(str(bundle_dir / pattern))):
            resources = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["resources"]
            for kind, items in resources.items():
                root.setdefault("resources", {}).setdefault(kind, {}).update(items)
    return root


def bundle_dirs() -> list[Path]:
    """Return every bundle directory (one with a databricks.yml) under `bundles/`."""
    return sorted(p.parent for p in (PROJECT_DIR / "bundles").glob("*/databricks.yml"))
