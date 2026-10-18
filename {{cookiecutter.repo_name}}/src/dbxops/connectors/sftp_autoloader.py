"""
Job task: land files from an SFTP server straight into Delta with Auto Loader. Installed as `dbxops-land-sftp`.

GA on Databricks Runtime 17.3+. No landing-Volume hop: Auto Loader streams directly from the SFTP server,
authenticating through a Unity Catalog CONNECTION (type sftp) referenced by name -- create that connection
once (see https://docs.databricks.com/aws/en/ingestion/sftp) before the first run.

    dbxops-land-sftp --config <land.yml> --catalog <catalog> --schema <schema> --table <table> \
        --checkpoint /Volumes/<catalog>/<schema>/landing/_checkpoints/<name>

`<land.yml>` holds `connection` (the UC Connection name), `source_path` (an `sftp://host:port/path` URL) and
`format` (the file format Auto Loader reads, default `csv`).

Bundles run it as a `python_wheel_task` (package `cookie-cutter-dbx`, entry point `dbxops-land-sftp`), with
only the dbxops wheel (variable `dbxops_wheel_path`) in the task's serverless environment -- no extra
dependency, since Auto Loader and the SFTP connection are native to the runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def build_stream(spark: Any, config: dict[str, Any], checkpoint: str) -> Any:
    """Return the Auto Loader streaming read for ``config``'s sftp:// source, unstarted."""
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", config.get("format", "csv"))
        .option("cloudFiles.schemaLocation", checkpoint)
        .option("databricks.connection", config["connection"])
        .load(config["source_path"])
    )


def main(argv: list[str] | None = None, *, spark_session: Any = None) -> int:
    """Land the configured SFTP source into one Delta table; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--config", required=True, type=Path, help="YAML file: connection, source_path, format")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--checkpoint", required=True, help="a Volume path used for both schema and checkpoint state")
    args = parser.parse_args(argv)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    spark = spark_session
    if spark is None:
        from pyspark.sql import SparkSession  # pylint: disable=import-outside-toplevel

        spark = SparkSession.builder.getOrCreate()
    target = f"{args.catalog}.{args.schema}.{args.table}"
    (
        build_stream(spark, config, args.checkpoint)
        .writeStream.format("delta")
        .option("checkpointLocation", args.checkpoint)
        .trigger(availableNow=True)
        .table(target)
    )
    print(f"landed into {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
