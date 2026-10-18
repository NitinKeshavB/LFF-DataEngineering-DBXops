"""
Job task: land a source into a landing Volume with one of the file-based connectors. Installed as `dbxops-land`.

The connector's own config comes from a YAML file (kept in the bundle's `src/config/`); this task only chooses
the connector, the landing target and the state file. Running again lands only what is new. A secret in the
config must be a `{{secrets/<scope>/<key>}}` reference: it is resolved through the job's own `dbutils` at
runtime, never read from the config file in clear text.

    dbxops-land --connector xlsx --config <land.yml> --output /Volumes/<catalog>/<schema>/landing/<path> \
        --state /Volumes/<catalog>/<schema>/landing/_state/<name>.json

Bundles run it as a `python_wheel_task` (package `cookie-cutter-dbx`, entry point `dbxops-land`), with the
dbxops wheel (variable `dbxops_wheel_path`) and the connector's own extra (`paramiko` for sftp; `openpyxl` and
`pyarrow` for xlsx; nothing extra for zip) in the task's serverless environment. jdbc is not offered here: it
needs a real Spark `JdbcSource` wired by hand, not a generic CLI.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from dbxops.connectors.base import (
    ConnectorEnv,
    JsonFileState,
    LandingTarget,
    SecretResolver,
)
from dbxops.connectors.sftp import SftpConnector
from dbxops.connectors.xlsx_files import XlsxConnector
from dbxops.connectors.zip_files import ZipConnector

CONNECTORS = {"sftp": SftpConnector, "xlsx": XlsxConnector, "zip": ZipConnector}


class DatabricksSecrets:
    """Reads a secret through the job's own `dbutils`, available inside a Databricks Python task."""

    def get(self, scope: str, key: str) -> str:
        """Return the secret value."""
        from databricks.sdk.runtime import dbutils  # pylint: disable=import-outside-toplevel

        return dbutils.secrets.get(scope=scope, key=key)


def main(argv: list[str] | None = None, *, secrets: SecretResolver | None = None) -> int:
    """Land the configured source and print a one-line summary; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--connector", required=True, choices=sorted(CONNECTORS))
    parser.add_argument("--config", required=True, type=Path, help="YAML file of this connector's config")
    parser.add_argument("--output", required=True, type=Path, help="landing directory under a Volume")
    parser.add_argument("--state", required=True, type=Path, help="JSON file that remembers what has landed")
    args = parser.parse_args(argv)
    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    connector = CONNECTORS[args.connector]()
    problems = connector.validate(config)
    if problems:
        raise SystemExit("\n".join(problems))
    result = connector.land(
        config,
        LandingTarget(args.output),
        JsonFileState(args.state),
        ConnectorEnv(secrets=secrets or DatabricksSecrets()),
    )
    print(f"landed {len(result.files)} files, {result.rows} rows, skipped {result.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
