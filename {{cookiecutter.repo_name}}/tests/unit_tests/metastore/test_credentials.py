"""Tests of the credential providers; the Databricks SDK is replaced by fakes."""

import base64
import inspect
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace

import pytest
from databricks.sdk.service.database import (
    DatabaseAPI,
    DatabaseInstance,
)

from dbxops.metastore.credentials import (
    EnvCredentials,
    LakebaseOAuthCredentials,
    SecretScopeCredentials,
    WorkspaceSecretsClient,
)
from dbxops.metastore.errors import ConfigError

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class FakeDatabaseApi:
    """Mimics ``WorkspaceClient().database`` as verified against databricks-sdk 0.91."""

    def __init__(self, lifetime=timedelta(hours=1), dns="instance.example"):
        self.lifetime, self.dns, self.issued = lifetime, dns, []
        self.clock = lambda: T0

    def generate_database_credential(self, *, request_id=None, instance_names=None):
        """Issue a token that expires after the configured lifetime."""
        self.issued.append((request_id, instance_names))
        expires = self.clock() + self.lifetime
        return SimpleNamespace(
            token=f"token-{len(self.issued)}", expiration_time=expires.isoformat().replace("+00:00", "Z")
        )

    def get_database_instance(self, **_kwargs):
        """Return the instance with its DNS name."""
        return SimpleNamespace(read_write_dns=self.dns)


def fake_client(database=None):
    """Return a fake WorkspaceClient with a service principal identity."""
    return SimpleNamespace(
        database=database or FakeDatabaseApi(),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="sp-app-id")),
    )


class Clock:
    """A movable clock."""

    def __init__(self):
        self.now = T0

    def __call__(self):
        """Return the current fake time."""
        return self.now


def test__secret_scope_credentials_read_user_and_password_from_the_scope():
    """User and password come from two keys of the secret scope."""

    class Secrets:
        """A secrets client stand-in."""

        def get_secret(self, scope, key):
            """Return scope:key as the secret value."""
            return f"{scope}:{key}"

    credentials = SecretScopeCredentials(Secrets(), "kv", "pg-user", "pg-password").get_credentials()
    assert (credentials.user, credentials.password) == ("kv:pg-user", "kv:pg-password")


def test__the_workspace_secrets_client_decodes_base64_values():
    """The SDK returns base64; the adapter hands back plain text."""
    value = base64.b64encode(b"s3cret").decode()
    workspace = SimpleNamespace(secrets=SimpleNamespace(get_secret=lambda scope, key: SimpleNamespace(value=value)))
    assert WorkspaceSecretsClient(workspace).get_secret("kv", "k") == "s3cret"


def test__env_credentials_read_variables_and_name_the_missing_ones():
    """Present variables are returned; missing ones raise ConfigError naming them."""
    ok = EnvCredentials("U", "P", {"U": "me", "P": "pw"}).get_credentials()
    assert (ok.user, ok.password) == ("me", "pw")
    with pytest.raises(ConfigError, match="U, P"):
        EnvCredentials("U", "P", {}).get_credentials()


def test__lakebase_token_is_cached_until_it_is_close_to_expiry():
    """A second call inside the validity window reuses the token; no new credential is requested."""
    client, clock = fake_client(), Clock()
    client.database.clock = clock
    provider = LakebaseOAuthCredentials(client, "inst", now=clock)
    first = provider.get_credentials()
    clock.now = T0 + timedelta(minutes=30)
    assert provider.get_credentials() is first
    assert len(client.database.issued) == 1
    assert client.database.issued[0][1] == ["inst"] and client.database.issued[0][0]
    assert (first.user, first.password) == ("sp-app-id", "token-1")


def test__lakebase_token_is_refreshed_before_it_expires():
    """Inside the refresh margin a new token is generated, with a fresh request id."""
    client, clock = fake_client(), Clock()
    client.database.clock = clock
    provider = LakebaseOAuthCredentials(client, "inst", user="me", now=clock)
    provider.get_credentials()
    clock.now = T0 + timedelta(minutes=56)
    refreshed = provider.get_credentials()
    assert refreshed.password == "token-2" and refreshed.user == "me"
    assert client.database.issued[0][0] != client.database.issued[1][0]


def test__lakebase_host_is_the_read_write_dns_name():
    """The host comes from the instance; a stopped instance without DNS is an error."""
    assert LakebaseOAuthCredentials(fake_client(), "inst").host() == "instance.example"
    with pytest.raises(ConfigError, match="no read-write DNS"):
        LakebaseOAuthCredentials(fake_client(FakeDatabaseApi(dns=None)), "inst").host()


def test__an_empty_lakebase_response_is_an_error():
    """No token in the response raises ConfigError instead of caching nothing."""
    client = fake_client()
    client.database.generate_database_credential = lambda **kwargs: SimpleNamespace(token=None, expiration_time=None)
    with pytest.raises(ConfigError, match="no token"):
        LakebaseOAuthCredentials(client, "inst").get_credentials()


def test__the_installed_sdk_has_the_method_the_provider_calls():
    """Guards against SDK drift: the real client class exposes generate_database_credential with these keywords."""
    parameters = inspect.signature(DatabaseAPI.generate_database_credential).parameters
    assert {"instance_names", "request_id"} <= set(parameters)
    assert "read_write_dns" in DatabaseInstance.__dataclass_fields__
