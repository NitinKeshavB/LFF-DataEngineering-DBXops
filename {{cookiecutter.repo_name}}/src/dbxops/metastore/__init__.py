"""
The metadata database library (schema ``platform_meta``).

A versioned read model of definitions, deployments, runs, quality and lineage, with pluggable backends (Unity
Catalog, PostgreSQL, Lakebase; SQLite for tests). ``MetadataPublisher`` is the facade; the rest are its parts.
"""

from dbxops.metastore.collectors import (
    asset_records,
    contract_records,
    dataflow_spec_records,
    domain_records,
    glossary_records,
    product_records,
    project_tag_rows,
    registration_records,
    semantic_link_rows,
    tag_rows,
)
from dbxops.metastore.composite import CompositeRepository
from dbxops.metastore.credentials import (
    CredentialProvider,
    Credentials,
    EnvCredentials,
    LakebaseOAuthCredentials,
    SecretScopeCredentials,
)
from dbxops.metastore.errors import (
    CollectError,
    ConfigError,
    MetastoreError,
    PublishError,
    SchemaError,
    WriterError,
)
from dbxops.metastore.factory import RepositoryFactory
from dbxops.metastore.guard import WriterGuard
from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
    DefinitionRecord,
    PublishContext,
    PublishCounts,
)
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from dbxops.metastore.uc_repository import (
    DatabricksSqlConnectorExecutor,
    SparkSqlExecutor,
    SqlExecutor,
    UcRepository,
)

__all__ = [
    "PLATFORM_REPO_ID",
    "CollectError",
    "CompositeRepository",
    "ConfigError",
    "CredentialProvider",
    "Credentials",
    "DatabricksSqlConnectorExecutor",
    "DefinitionRecord",
    "EnvCredentials",
    "LakebaseOAuthCredentials",
    "MetadataPublisher",
    "MetadataRepository",
    "MetastoreError",
    "PublishContext",
    "PublishCounts",
    "PublishError",
    "RepositoryFactory",
    "SchemaError",
    "SecretScopeCredentials",
    "SparkSqlExecutor",
    "SqlAlchemyRepository",
    "SqlExecutor",
    "UcRepository",
    "WriterError",
    "WriterGuard",
    "asset_records",
    "contract_records",
    "dataflow_spec_records",
    "domain_records",
    "glossary_records",
    "product_records",
    "project_tag_rows",
    "registration_records",
    "semantic_link_rows",
    "tag_rows",
]
