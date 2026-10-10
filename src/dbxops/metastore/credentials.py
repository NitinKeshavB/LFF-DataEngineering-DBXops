"""
Credential providers (Strategy) for the PostgreSQL-family sinks.

* ``SecretScopeCredentials``: user and password from a Databricks secret scope, through an injected client.
* ``LakebaseOAuthCredentials``: a short-lived Lakebase database token from the Databricks SDK, refreshed before it
  expires. Verified against ``databricks-sdk`` 0.91: ``WorkspaceClient().database.generate_database_credential(
  instance_names=[...], request_id=...)`` returns a ``DatabaseCredential`` with ``token`` and an RFC 3339
  ``expiration_time``; the host is ``database.get_database_instance(name).read_write_dns``.
* ``EnvCredentials``: user and password from environment variables.
"""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
)

from dbxops.metastore.errors import ConfigError

DEFAULT_REFRESH_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class Credentials:
    """A database user and password; ``expires_at`` is set for short-lived tokens."""

    user: str
    password: str
    expires_at: datetime | None = None


class CredentialProvider(Protocol):
    """Hands out database credentials, refreshing them when needed."""

    def get_credentials(self) -> Credentials:
        """Return credentials that are valid now."""


class SecretsClient(Protocol):
    """Reads one secret value."""

    def get_secret(self, scope: str, key: str) -> str:
        """Return the plain-text value of ``key`` in ``scope``."""


class WorkspaceSecretsClient:
    """A ``SecretsClient`` over ``WorkspaceClient.secrets``, which returns base64-encoded values."""

    def __init__(self, workspace_client: Any):
        self._secrets = workspace_client.secrets

    def get_secret(self, scope: str, key: str) -> str:
        """Return the decoded value of a secret."""
        return base64.b64decode(self._secrets.get_secret(scope=scope, key=key).value).decode("utf-8")


class SecretScopeCredentials:
    """User and password read from a Databricks secret scope."""

    def __init__(self, secrets: SecretsClient, scope: str, user_key: str, password_key: str):
        self._secrets = secrets
        self._scope = scope
        self._user_key = user_key
        self._password_key = password_key

    def get_credentials(self) -> Credentials:
        """Read the user and password from the secret scope."""
        return Credentials(
            user=self._secrets.get_secret(self._scope, self._user_key),
            password=self._secrets.get_secret(self._scope, self._password_key),
        )


class EnvCredentials:
    """User and password read from environment variables."""

    def __init__(
        self,
        user_var: str = "DBXOPS_PG_USER",
        password_var: str = "DBXOPS_PG_PASSWORD",
        environ: Mapping[str, str] | None = None,
    ):
        self._user_var = user_var
        self._password_var = password_var
        self._environ = os.environ if environ is None else environ

    def get_credentials(self) -> Credentials:
        """Read the credentials; a missing variable raises ``ConfigError`` naming it."""
        missing = [name for name in (self._user_var, self._password_var) if name not in self._environ]
        if missing:
            raise ConfigError(f"environment variable(s) {', '.join(missing)} not set")
        return Credentials(user=self._environ[self._user_var], password=self._environ[self._password_var])


class LakebaseOAuthCredentials:
    """A Lakebase OAuth database token, regenerated when it is within ``DEFAULT_REFRESH_MARGIN`` of expiring."""

    def __init__(
        self,
        workspace_client: Any,
        instance_name: str,
        user: str | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._client = workspace_client
        self._instance = instance_name
        self._user = user
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._cached: Credentials | None = None

    def host(self) -> str:
        """Return the read-write DNS name of the Lakebase instance."""
        found = self._client.database.get_database_instance(name=self._instance)
        if not found.read_write_dns:
            raise ConfigError(f"Lakebase instance {self._instance!r} has no read-write DNS name (is it running?)")
        return found.read_write_dns

    def get_credentials(self) -> Credentials:
        """Return a token valid for at least ``DEFAULT_REFRESH_MARGIN``, generating a new one when needed."""
        cached = self._cached
        if (
            cached is not None
            and cached.expires_at is not None
            and cached.expires_at - self._now() > DEFAULT_REFRESH_MARGIN
        ):
            return cached
        issued = self._client.database.generate_database_credential(
            request_id=str(uuid.uuid4()), instance_names=[self._instance]
        )
        if not issued.token or not issued.expiration_time:
            raise ConfigError(f"Lakebase returned no token for instance {self._instance!r}")
        user = self._user or self._client.current_user.me().user_name
        expires = datetime.fromisoformat(issued.expiration_time.replace("Z", "+00:00"))
        self._cached = Credentials(user=user, password=issued.token, expires_at=expires)
        return self._cached
