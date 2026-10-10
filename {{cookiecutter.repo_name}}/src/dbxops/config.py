"""
Typed loaders for the deployment configuration files.

``load_environment`` reads one target of ``conf/environments.yml`` (the single source of truth for targets) and
``load_repo_config`` reads the machine-owned ``metadata/_platform/repo.yaml``. ``conf/metastore.yml`` is not
handled here.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Any,
    Mapping,
)

import yaml

DEFAULT_ENVIRONMENTS_PATH = "conf/environments.yml"
DEFAULT_REPO_CONFIG_PATH = "metadata/_platform/repo.yaml"


class ConfigError(ValueError):
    """Raised when a configuration file is missing, malformed or names an unknown target."""


@dataclass(frozen=True)
class SharedSettings:
    """Settings shared by every target of ``conf/environments.yml``."""

    workspace_host: str
    tenant_id: str
    framework_version: str


@dataclass(frozen=True)
class Environment:
    """
    One deployment target of ``conf/environments.yml``.

    ``workspace_host``, ``tenant_id`` and ``framework_version`` come from the shared settings and are exposed as
    properties.
    """

    target: str
    catalog: str
    mode: str
    github_environment: str
    requires_approval: bool
    run_as_sp: bool
    shared: SharedSettings

    @property
    def workspace_host(self) -> str:
        """Return the workspace URL shared by all targets."""
        return self.shared.workspace_host

    @property
    def tenant_id(self) -> str:
        """Return the Azure tenant id shared by all targets."""
        return self.shared.tenant_id

    @property
    def framework_version(self) -> str:
        """Return the pinned Lakeflow Framework version."""
        return self.shared.framework_version


@dataclass(frozen=True)
class ProjectInfo:
    """The ``project`` block of ``repo.yaml``."""

    title: str
    project_type: str
    owner_team: str


@dataclass(frozen=True)
class Ontology:
    """The ontology settings of ``repo.yaml``: the IRI base and the pinned standards."""

    iri_base: str
    standards: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RepoConfig:
    """
    The registry-owned identity of this repository, from ``metadata/_platform/repo.yaml``.

    ``title``, ``project_type``, ``owner_team``, ``iri_base`` and ``standards`` are exposed as properties.
    """

    project_id: str
    kind: str
    domain: str
    project: ProjectInfo
    service_principal_application_id: str
    ontology: Ontology
    tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        """Return the project title."""
        return self.project.title

    @property
    def project_type(self) -> str:
        """Return the project type."""
        return self.project.project_type

    @property
    def owner_team(self) -> str:
        """Return the owning team."""
        return self.project.owner_team

    @property
    def iri_base(self) -> str:
        """Return the base IRI for ontology identifiers."""
        return self.ontology.iri_base

    @property
    def standards(self) -> Mapping[str, str]:
        """Return the pinned standard versions."""
        return self.ontology.standards


def _read_mapping(path: str | Path) -> dict:
    try:
        with open(path, encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping")
    return data


def _require(mapping: Any, key: str, expected: type, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"{where}: missing required key {key!r}")
    value = mapping[key]
    if not isinstance(value, expected):
        raise ConfigError(f"{where}: {key!r} must be {expected.__name__}, got {type(value).__name__}")
    return value


def load_environment(target: str, path: str | Path = DEFAULT_ENVIRONMENTS_PATH) -> Environment:
    """
    Load one deployment target from ``conf/environments.yml``.

    Raises ``ConfigError`` naming the valid targets when ``target`` is unknown.
    """
    data = _read_mapping(path)
    targets = _require(data, "targets", dict, str(path))
    if target not in targets:
        raise ConfigError(f"unknown target {target!r}; valid targets: {', '.join(sorted(targets))}")
    where = f"{path}: targets.{target}"
    entry = targets[target]
    workspace = _require(data, "workspace", dict, str(path))
    return Environment(
        target=target,
        catalog=_require(entry, "catalog", str, where),
        mode=_require(entry, "mode", str, where),
        github_environment=_require(entry, "github_environment", str, where),
        requires_approval=_require(entry, "requires_approval", bool, where),
        run_as_sp=_require(entry, "run_as_sp", bool, where),
        shared=SharedSettings(
            workspace_host=_require(workspace, "host", str, f"{path}: workspace"),
            tenant_id=_require(workspace, "tenant_id", str, f"{path}: workspace"),
            framework_version=_require(data, "framework_version", str, str(path)),
        ),
    )


def load_repo_config(path: str | Path = DEFAULT_REPO_CONFIG_PATH) -> RepoConfig:
    """Load the repository identity from ``metadata/_platform/repo.yaml``."""
    data = _read_mapping(path)
    where = str(path)
    project = _require(data, "project", dict, where)
    principal = _require(data, "service_principal", dict, where)
    return RepoConfig(
        project_id=_require(data, "project_id", str, where),
        kind=_require(data, "kind", str, where),
        domain=_require(data, "domain", str, where),
        project=ProjectInfo(
            title=_require(project, "title", str, f"{where}: project"),
            project_type=_require(project, "project_type", str, f"{where}: project"),
            owner_team=_require(project, "owner_team", str, f"{where}: project"),
        ),
        service_principal_application_id=_require(principal, "application_id", str, f"{where}: service_principal"),
        ontology=Ontology(
            iri_base=_require(data, "iri_base", str, where),
            standards=dict(_require(data, "standards", dict, where)),
        ),
        tags=dict(_require(data, "tags", dict, where)),
    )
