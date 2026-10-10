"""Tests of RepositoryFactory: configuration, placeholder refusal, sink construction and the engine credential hook."""

from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import create_engine

from dbxops.metastore.composite import CompositeRepository
from dbxops.metastore.credentials import Credentials
from dbxops.metastore.errors import ConfigError
from dbxops.metastore.factory import (
    RepositoryFactory,
    SinkDependencies,
    make_engine,
)
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from dbxops.metastore.uc_repository import UcRepository
from tests.consts import PROJECT_DIR
from tests.fixtures.metastore import RecordingExecutor

ENVIRONMENTS = {
    "workspace": {"host": "https://adb-1.azuredatabricks.net"},
    "targets": {"dev": {"catalog": "dev_cat"}, "prod": {"catalog": "prod_cat"}},
}


def write_config(tmp_path, sinks, retention=30, target="dev"):
    """Write metastore.yml and environments.yml side by side and return the config path."""
    (tmp_path / "environments.yml").write_text(yaml.safe_dump(ENVIRONMENTS))
    path = tmp_path / "metastore.yml"
    path.write_text(yaml.safe_dump({"targets": {target: {"retention_days": retention, "sinks": sinks}}}))
    return path


UC_SINK = {"backend": "uc", "catalog": "{catalog}", "schema": "platform_meta", "warehouse_id": "wh1"}


def recording_engine_factory(calls):
    """Return an engine factory that records its arguments and returns an in-memory SQLite engine."""

    def factory(*args):
        calls.append(args)
        return create_engine("sqlite://")

    return factory


def test__the_catalog_placeholder_resolves_from_environments(tmp_path):
    """{catalog} is replaced by the target's catalog and retention is read."""
    config = RepositoryFactory.load_config(write_config(tmp_path, [UC_SINK]), "dev")
    assert config.sinks[0]["catalog"] == "dev_cat" and config.retention_days == 30


def test__a_uc_sink_builds_a_uc_repository_over_the_injected_executor(tmp_path):
    """The executor factory receives the sink and the workspace host without scheme."""
    seen = {}

    def executor_factory(sink, host):
        seen.update(sink=sink, host=host)
        return RecordingExecutor()

    repository = RepositoryFactory.from_config(
        write_config(tmp_path, [UC_SINK]), "dev", deps=SinkDependencies(executor_factory=executor_factory)
    )
    assert isinstance(repository, UcRepository)
    assert seen["host"] == "adb-1.azuredatabricks.net" and seen["sink"]["warehouse_id"] == "wh1"


def test__a_real_uc_executor_is_built_from_the_workspace_client_when_none_is_injected(tmp_path):
    """Without an executor factory, the connector executor targets the warehouse and authenticates with the SDK config."""
    client = SimpleNamespace(config=SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer x"}))
    repository = RepositoryFactory.from_config(
        write_config(tmp_path, [UC_SINK]), "dev", deps=SinkDependencies(workspace_client=client)
    )
    assert isinstance(repository, UcRepository)


def test__several_sinks_become_a_composite(tmp_path):
    """Two sinks give a CompositeRepository; the Lakebase sink uses OAuth credentials and the instance DNS."""
    calls = []

    class Database:
        """A database API stand-in."""

        def get_database_instance(self, **_kwargs):
            """Return an instance with a DNS name."""
            return SimpleNamespace(read_write_dns="lb.example")

    client = SimpleNamespace(
        database=Database(), current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="sp"))
    )

    sinks = [UC_SINK, {"backend": "lakebase", "instance": "inst", "database": "platform_meta"}]
    repository = RepositoryFactory.from_config(
        write_config(tmp_path, sinks),
        "dev",
        deps=SinkDependencies(client, lambda s, h: RecordingExecutor(), recording_engine_factory(calls)),
    )
    assert isinstance(repository, CompositeRepository) and len(repository.sinks) == 2
    host, database, port, ssl, provider = calls[0]
    assert (host, database, port, ssl) == ("lb.example", "platform_meta", 5432, "require")
    assert provider.__class__.__name__ == "LakebaseOAuthCredentials"


def test__a_postgres_sink_reads_credentials_from_a_secret_scope_or_the_environment(tmp_path, monkeypatch):
    """With secret_scope the provider reads the scope; without it, environment variables."""
    calls = []
    engine_factory = recording_engine_factory(calls)
    secrets = {"user": "dbuser", "password": "dbpass"}
    client = SimpleNamespace(
        secrets=SimpleNamespace(
            get_secret=lambda scope, key: SimpleNamespace(
                value=__import__("base64").b64encode(secrets[key.split("-")[1]].encode()).decode()
            )
        )
    )
    scoped = {
        "backend": "postgres",
        "host": "h",
        "database": "d",
        "secret_scope": "kv",
        "user_key": "pg-user",
        "password_key": "pg-password",
    }
    RepositoryFactory.from_config(
        write_config(tmp_path, [scoped]),
        "dev",
        deps=SinkDependencies(workspace_client=client, engine_factory=engine_factory),
    )
    credentials = calls[0][4].get_credentials()
    assert (credentials.user, credentials.password) == ("dbuser", "dbpass")
    monkeypatch.setenv("DBXOPS_PG_USER", "envuser")
    monkeypatch.setenv("DBXOPS_PG_PASSWORD", "envpass")
    plain = {"backend": "postgres", "host": "h", "database": "d"}
    repository = RepositoryFactory.from_config(
        write_config(tmp_path, [plain]), "dev", deps=SinkDependencies(engine_factory=engine_factory)
    )
    assert isinstance(repository, SqlAlchemyRepository) and calls[1][4].get_credentials().user == "envuser"


@pytest.mark.parametrize(
    ("sink", "message"),
    [
        ({"backend": "uc", "catalog": "x", "warehouse_id": "<PLACEHOLDER-dev-warehouse-id>"}, "placeholder"),
        ({"backend": "uc", "catalog": "x"}, "warehouse_id"),
        ({"backend": "lakebase", "instance": "<PLACEHOLDER-lakebase-instance>", "database": "d"}, "placeholder"),
        ({"backend": "lakebase", "database": "d"}, "instance"),
        ({"backend": "postgres", "host": "h"}, "database"),
        ({"backend": "postgres", "host": "h", "database": "d", "secret_scope": "kv"}, "user_key"),
        ({"backend": "oracle"}, "unknown sink backend"),
    ],
)
def test__unusable_sinks_are_refused_with_a_clear_message(tmp_path, sink, message):
    """Placeholders, missing options and unknown backends raise ConfigError naming the problem."""
    with pytest.raises(ConfigError, match=message):
        RepositoryFactory.from_config(
            write_config(tmp_path, [sink]),
            "dev",
            deps=SinkDependencies(SimpleNamespace(), lambda s, h: None),
        )


def test__unknown_targets_missing_sinks_and_missing_files_are_refused(tmp_path):
    """The target must exist and have sinks; unreadable files are ConfigError."""
    path = write_config(tmp_path, [UC_SINK])
    with pytest.raises(ConfigError, match="not in"):
        RepositoryFactory.load_config(path, "nope")
    with pytest.raises(ConfigError, match="no sinks"):
        RepositoryFactory.load_config(write_config(tmp_path, [], target="dev"), "dev")
    with pytest.raises(ConfigError, match="cannot read"):
        RepositoryFactory.load_config(tmp_path / "missing.yml", "dev")


def test__the_repository_config_still_holds_placeholders_so_publishing_is_refused():
    """conf/metastore.yml ships placeholders; from_config must refuse them until they are filled in."""
    config = RepositoryFactory.load_config(PROJECT_DIR / "conf/metastore.yml", "prod")
    assert [sink["backend"] for sink in config.sinks] == ["uc", "lakebase"]
    assert config.retention_days == 400
    with pytest.raises(ConfigError, match="placeholder"):
        RepositoryFactory.from_config(PROJECT_DIR / "conf/metastore.yml", "dev")


def test__the_postgres_engine_takes_fresh_credentials_on_every_connect():
    """The do_connect hook injects the provider's current user and password, so rotated tokens are used."""

    class Provider:
        """A credential provider that hands out numbered tokens."""

        calls = 0

        def get_credentials(self):
            """Return credentials with a new token."""
            self.calls += 1
            return Credentials("u", f"token-{self.calls}")

    provider = Provider()
    engine = make_engine("db.example", "platform_meta", 5432, "require", provider)
    assert engine.url.drivername == "postgresql+psycopg" and engine.url.host == "db.example"
    assert engine.url.query["sslmode"] == "require" and engine.url.password is None
    params = {}
    engine.dialect.dispatch.do_connect(engine.dialect, None, (), params)
    assert params == {"user": "u", "password": "token-1"}
    engine.dialect.dispatch.do_connect(engine.dialect, None, (), params)
    assert params["password"] == "token-2"
    engine.dispose()
