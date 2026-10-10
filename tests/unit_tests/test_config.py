"""Tests for the environment and repository config loaders, against the real config files."""

import dataclasses

import pytest

from dbxops.config import (
    ConfigError,
    load_environment,
    load_repo_config,
)
from tests.consts import PROJECT_DIR

ENVIRONMENTS = PROJECT_DIR / "conf/environments.yml"
REPO_YAML = PROJECT_DIR / "metadata/_platform/repo.yaml"


def test__environment_loads_a_target_with_the_shared_workspace_settings():
    """The prod target carries its own flags plus the shared host, tenant and framework version."""
    prod = load_environment("prod", ENVIRONMENTS)
    assert (prod.target, prod.mode, prod.github_environment) == ("prod", "production", "prod")
    assert prod.requires_approval is True
    assert prod.run_as_sp is True
    assert prod.workspace_host.startswith("https://")
    assert prod.framework_version == "v0.24.1"


def test__dev_target_runs_as_the_deploying_user_without_approval():
    """The dev target is the development-mode target that needs no approval and does not run as the SP."""
    dev = load_environment("dev", ENVIRONMENTS)
    assert (dev.mode, dev.requires_approval, dev.run_as_sp) == ("development", False, False)


def test__every_target_in_the_file_loads():
    """All five targets of the real file satisfy the loader's schema."""
    names = ("dev", "test", "uat", "preprod", "prod")
    assert [load_environment(name, ENVIRONMENTS).target for name in names] == list(names)


def test__environment_is_immutable():
    """The loaded environment is a frozen dataclass."""
    environment = load_environment("dev", ENVIRONMENTS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        environment.catalog = "other"  # type: ignore[misc]


def test__unknown_target_names_the_valid_targets():
    """An unknown target is rejected and the error lists every valid target."""
    with pytest.raises(ConfigError, match="unknown target 'staging'; valid targets: dev, preprod, prod, test, uat"):
        load_environment("staging", ENVIRONMENTS)


def test__missing_environments_file_is_a_config_error(tmp_path):
    """A missing file raises ConfigError rather than a bare OSError."""
    with pytest.raises(ConfigError, match="cannot read"):
        load_environment("dev", tmp_path / "absent.yml")


def test__a_target_missing_a_key_names_the_key(tmp_path):
    """A target without 'catalog' is rejected naming the target and the key."""
    path = tmp_path / "environments.yml"
    path.write_text(
        "workspace: {host: h, tenant_id: t}\nframework_version: v1\ntargets:\n  dev: {mode: development}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="targets.dev: missing required key 'catalog'"):
        load_environment("dev", path)


def test__a_flag_of_the_wrong_type_is_rejected(tmp_path):
    """requires_approval must be a boolean, not the string 'false'."""
    path = tmp_path / "environments.yml"
    path.write_text(
        "workspace: {host: h, tenant_id: t}\nframework_version: v1\ntargets:\n"
        "  dev: {catalog: c, mode: m, github_environment: g, requires_approval: 'false', run_as_sp: false}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="'requires_approval' must be bool, got str"):
        load_environment("dev", path)


def test__a_non_mapping_file_is_rejected(tmp_path):
    """A YAML list at the top level is not a valid config."""
    path = tmp_path / "environments.yml"
    path.write_text("- a\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_environment("dev", path)


def test__invalid_yaml_is_a_config_error(tmp_path):
    """Unparseable YAML raises ConfigError."""
    path = tmp_path / "environments.yml"
    path.write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_environment("dev", path)


def test__repo_config_loads_the_real_registry_file():
    """The machine-owned repo.yaml loads into the typed config."""
    repo = load_repo_config(REPO_YAML)
    assert (repo.project_id, repo.kind, repo.domain) == ("example_claims", "project", "claims")
    assert repo.owner_team == "example_claims_squad"
    assert repo.project_type == "TEAM"
    assert set(repo.tags) == {"cost_center", "region"}
    assert repo.standards["odcs"] == "v3.1.0"


def test__repo_config_missing_key_names_it(tmp_path):
    """A repo.yaml without a service principal is rejected naming the key."""
    path = tmp_path / "repo.yaml"
    path.write_text("project_id: p\nkind: k\ndomain: d\nproject: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required key 'service_principal'"):
        load_repo_config(path)
