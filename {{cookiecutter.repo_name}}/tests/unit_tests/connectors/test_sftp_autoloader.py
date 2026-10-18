"""Tests for the dbxops-land-sftp job task (dbxops.connectors.sftp_autoloader): Auto Loader reading sftp://."""

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from dbxops.connectors.sftp_autoloader import (
    build_stream,
    main,
)


def test__build_stream_wires_the_documented_cloudfiles_option_chain():
    """cloudFiles.format, the schema location, the UC connection and the sftp:// path are all set."""
    spark = MagicMock()
    reader = spark.readStream.format.return_value
    reader.option.return_value = reader  # a fluent builder: every .option() call returns the same reader
    config = {"connection": "my_sftp_connection", "source_path": "sftp://host:22/inbound", "format": "csv"}

    build_stream(spark, config, "/Volumes/main/bronze/landing/_checkpoints/land_sandbox_sftp")

    spark.readStream.format.assert_called_once_with("cloudFiles")
    reader.option.assert_any_call("cloudFiles.format", "csv")
    reader.option.assert_any_call(
        "cloudFiles.schemaLocation", "/Volumes/main/bronze/landing/_checkpoints/land_sandbox_sftp"
    )
    reader.option.assert_any_call("databricks.connection", "my_sftp_connection")
    reader.load.assert_called_once_with("sftp://host:22/inbound")


def test__build_stream_defaults_to_csv_when_format_is_not_given():
    """A config with no format key still reads, defaulting to csv."""
    spark = MagicMock()
    reader = spark.readStream.format.return_value
    reader.option.return_value = reader
    config = {"connection": "conn", "source_path": "sftp://host:22/inbound"}

    build_stream(spark, config, "/Volumes/main/bronze/landing/_checkpoints/land")

    reader.option.assert_any_call("cloudFiles.format", "csv")


def test__main_writes_an_available_now_stream_to_the_target_table(tmp_path: Path):
    """main() builds the stream, triggers one availableNow batch, and writes it to <catalog>.<schema>.<table>."""
    config_path = tmp_path / "land.yml"
    config_path.write_text(
        yaml.safe_dump({"connection": "my_sftp_connection", "source_path": "sftp://host:22/inbound"}),
        encoding="utf-8",
    )
    spark = MagicMock()
    reader = spark.readStream.format.return_value
    reader.option.return_value = reader
    write_stream = reader.load.return_value.writeStream
    write_stream.format.return_value = write_stream
    write_stream.option.return_value = write_stream
    write_stream.trigger.return_value = write_stream

    result = main(
        [
            "--config",
            str(config_path),
            "--catalog",
            "main",
            "--schema",
            "bronze",
            "--table",
            "partner_feed_sftp_raw",
            "--checkpoint",
            "/Volumes/main/bronze/landing/_checkpoints/land_partner_feed",
        ],
        spark_session=spark,
    )

    assert result == 0
    write_stream.format.assert_called_once_with("delta")
    write_stream.option.assert_called_once_with(
        "checkpointLocation", "/Volumes/main/bronze/landing/_checkpoints/land_partner_feed"
    )
    write_stream.trigger.assert_called_once_with(availableNow=True)
    write_stream.table.assert_called_once_with("main.bronze.partner_feed_sftp_raw")
