"""
Landing connectors: one way to get a source that no managed connector reaches into a Volume.

A job picks a connector by ``kind``, builds a ``LandingTarget``, a ``LandingState`` and a ``ConnectorEnv``,
and calls ``land()``. See ``dbxops.connectors.base`` for the shared contract every connector follows.
"""

from __future__ import annotations

from dbxops.connectors.base import (
    Connector,
    ConnectorEnv,
    ConnectorError,
    InMemoryState,
    JsonFileState,
    LandingState,
    LandingTarget,
    LandResult,
    NotLandable,
    SecretResolver,
)
from dbxops.connectors.jdbc import JdbcConnector
from dbxops.connectors.sftp import SftpConnector
from dbxops.connectors.xlsx_files import XlsxConnector
from dbxops.connectors.zip_files import ZipConnector

__all__ = [
    "Connector",
    "ConnectorEnv",
    "ConnectorError",
    "InMemoryState",
    "JdbcConnector",
    "JsonFileState",
    "LandResult",
    "LandingState",
    "LandingTarget",
    "NotLandable",
    "SecretResolver",
    "SftpConnector",
    "XlsxConnector",
    "ZipConnector",
]
