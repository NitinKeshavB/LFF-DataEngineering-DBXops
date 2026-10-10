"""
``RepositoryFactory``: builds the configured metadata sinks of a target (Factory Method).

Reads ``conf/metastore.yml`` (see the file for the sink options) and resolves ``{catalog}`` from
``conf/environments.yml``. A sink that still holds a ``PLACEHOLDER`` value is refused, so nothing is ever published
against an unconfigured sink. Clients and engines are injectable so the factory is testable without a workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Mapping,
)

import yaml
from databricks.sdk import WorkspaceClient
from sqlalchemy import (
    create_engine,
    event,
)
from sqlalchemy.engine import URL

from dbxops.metastore.composite import CompositeRepository
from dbxops.metastore.credentials import (
    CredentialProvider,
    EnvCredentials,
    LakebaseOAuthCredentials,
    SecretScopeCredentials,
    WorkspaceSecretsClient,
)
from dbxops.metastore.errors import ConfigError
from dbxops.metastore.model import DEFAULT_SCHEMA
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from dbxops.metastore.uc_repository import (
    DatabricksSqlConnectorExecutor,
    SqlExecutor,
    UcRepository,
)

DEFAULT_RETENTION_DAYS = 400
_POOL_RECYCLE_SECONDS = 3000

ExecutorFactory = Callable[[Mapping[str, Any], str], SqlExecutor]
EngineFactory = Callable[[str, str, int, str, CredentialProvider], Any]


@dataclass(frozen=True)
class MetastoreConfig:
    """The sinks and retention of one target, with ``{catalog}`` already resolved."""

    target: str
    retention_days: int
    sinks: tuple[Mapping[str, Any], ...]
    workspace_host: str = ""


@dataclass(frozen=True)
class SinkDependencies:
    """Injectable collaborators of the sinks; anything left ``None`` is created on demand."""

    workspace_client: Any = None
    executor_factory: ExecutorFactory | None = None
    engine_factory: EngineFactory | None = None

    def client(self) -> Any:
        """Return the workspace client, creating one from the default Databricks auth when none was given."""
        return self.workspace_client or WorkspaceClient()


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error


def _reject_placeholders(sink: Mapping[str, Any]) -> None:
    for key, value in sink.items():
        if isinstance(value, str) and "PLACEHOLDER" in value:
            raise ConfigError(f"sink {sink.get('backend')!r}: {key} still holds a placeholder ({value})")


def _target_section(config: Mapping[str, Any], target: str, path: Path) -> Mapping[str, Any]:
    targets = config.get("targets") or {}
    if target not in targets:
        raise ConfigError(f"target {target!r} is not in {path}; known: {', '.join(sorted(targets))}")
    return targets[target]


def _require(sink: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        if not sink.get(key):
            raise ConfigError(f"{sink.get('backend')} sink needs {key!r}")


def _resolve_catalog(sink: Mapping[str, Any], catalog: str) -> dict[str, Any]:
    return {
        key: value.replace("{catalog}", catalog) if isinstance(value, str) else value for key, value in sink.items()
    }


def make_engine(host: str, database: str, port: int, ssl_mode: str, provider: CredentialProvider) -> Any:
    """Create a PostgreSQL engine that asks ``provider`` for fresh credentials on every new connection."""
    url = URL.create("postgresql+psycopg", host=host, port=port, database=database, query={"sslmode": ssl_mode})
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=_POOL_RECYCLE_SECONDS)

    @event.listens_for(engine, "do_connect")
    def _inject_credentials(_dialect, _conn_rec, _cargs, cparams):
        credentials = provider.get_credentials()
        cparams["user"] = credentials.user
        cparams["password"] = credentials.password

    return engine


class RepositoryFactory:
    """Builds repositories from the metastore configuration."""

    @staticmethod
    def load_config(
        config_path: str | Path, target: str, environments_path: str | Path | None = None
    ) -> MetastoreConfig:
        """Read the sinks and retention of ``target``, resolving ``{catalog}`` from ``environments.yml``."""
        config_path = Path(config_path)
        environments = _load_yaml(Path(environments_path or config_path.with_name("environments.yml")))
        section = _target_section(_load_yaml(config_path), target, config_path)
        catalog = ((environments.get("targets") or {}).get(target) or {}).get("catalog", "")
        sinks = tuple(_resolve_catalog(sink, catalog) for sink in section.get("sinks") or ())
        if not sinks:
            raise ConfigError(f"target {target!r} has no sinks in {config_path}")
        host = (environments.get("workspace") or {}).get("host") or ""
        return MetastoreConfig(target, int(section.get("retention_days", DEFAULT_RETENTION_DAYS)), sinks, host)

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        target: str,
        *,
        environments_path: str | Path | None = None,
        deps: SinkDependencies | None = None,
    ) -> MetadataRepository:
        """Build the repository of ``target``: one sink as is, several as a ``CompositeRepository``."""
        config = cls.load_config(config_path, target, environments_path)
        sinks = [cls.build_sink(sink, config, deps or SinkDependencies()) for sink in config.sinks]
        return sinks[0] if len(sinks) == 1 else CompositeRepository(sinks)

    @classmethod
    def build_sink(
        cls, sink: Mapping[str, Any], config: MetastoreConfig, deps: SinkDependencies
    ) -> MetadataRepository:
        """Build one sink from its configuration mapping."""
        _reject_placeholders(sink)
        backend = sink.get("backend")
        if backend == "uc":
            return cls._uc(sink, config, deps)
        if backend == "lakebase":
            return cls._lakebase(sink, deps)
        if backend == "postgres":
            return cls._postgres(sink, deps)
        raise ConfigError(f"unknown sink backend {backend!r}; expected uc, lakebase or postgres")

    @staticmethod
    def _uc(sink: Mapping[str, Any], config: MetastoreConfig, deps: SinkDependencies) -> UcRepository:
        _require(sink, "catalog", "warehouse_id")
        _reject_placeholders({"backend": "uc", "workspace.host": config.workspace_host})
        host = config.workspace_host.removeprefix("https://").rstrip("/")
        if deps.executor_factory is not None:
            executor = deps.executor_factory(sink, host)
        else:
            options = {"credentials_provider": lambda: deps.client().config.authenticate}
            executor = DatabricksSqlConnectorExecutor(host, sink["warehouse_id"], connection_options=options)
        return UcRepository(executor, sink["catalog"], sink.get("schema", DEFAULT_SCHEMA))

    @staticmethod
    def _lakebase(sink: Mapping[str, Any], deps: SinkDependencies) -> SqlAlchemyRepository:
        _require(sink, "instance", "database")
        provider = LakebaseOAuthCredentials(deps.client(), sink["instance"], sink.get("user"))
        make = deps.engine_factory or make_engine
        engine = make(provider.host(), sink["database"], int(sink.get("port", 5432)), "require", provider)
        return SqlAlchemyRepository(engine, sink.get("schema", DEFAULT_SCHEMA))

    @staticmethod
    def _postgres(sink: Mapping[str, Any], deps: SinkDependencies) -> SqlAlchemyRepository:
        _require(sink, "host", "database")
        provider: CredentialProvider
        if sink.get("secret_scope"):
            _require(sink, "user_key", "password_key")
            secrets = WorkspaceSecretsClient(deps.client())
            provider = SecretScopeCredentials(secrets, sink["secret_scope"], sink["user_key"], sink["password_key"])
        else:
            provider = EnvCredentials()
        make = deps.engine_factory or make_engine
        engine = make(
            sink["host"], sink["database"], int(sink.get("port", 5432)), sink.get("sslmode", "require"), provider
        )
        return SqlAlchemyRepository(engine, sink.get("schema", DEFAULT_SCHEMA))
