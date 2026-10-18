"""The public API of dbxops is a contract: this test pins it, so a removal or rename is a deliberate change."""

import dbxops
from tests.consts import PROJECT_DIR

PUBLIC_API = frozenset("""
    Change ConfigError Connector ConnectorEnv
    ConnectorError Environment EvalPassRate Finding
    GatewayError GateConfigError GateDecision GateReport
    JdbcConnector JsonFileState KnowledgeAssistantReconciler LandResult
    LandingState LandingTarget MetadataPublisher MetadataRepository
    MetadataSyncReconciler MetastoreError MetricThreshold ModelPromoter
    MonitorReconciler PromotionGate PromotionRefused PublishContext
    ReconcileResult Reconciler RepoConfig RepositoryFactory
    SdkWorkspaceGateway ServingEndpointReconciler SftpConnector VectorSearchIndexReconciler
    WorkspaceGateway XlsxConnector ZipConnector
    load_environment load_repo_config validate_metadata validate_registry
    """.split())


def test__the_exported_names_are_exactly_the_documented_public_api():
    """`__all__` equals the pinned set and every name resolves; removing one is a breaking change."""
    assert set(dbxops.__all__) == PUBLIC_API
    assert all(hasattr(dbxops, name) for name in PUBLIC_API)


def test__the_validation_entry_points_work_from_the_package_root():
    """validate_metadata and validate_registry run on this repository and its registry scaffold without errors."""
    assert not [f for f in dbxops.validate_metadata(PROJECT_DIR) if f.severity == "error"]
    assert dbxops.validate_registry(PROJECT_DIR / "templates/registry-repo") == []


def test__load_environment_reads_the_repos_conf():
    """The configuration entry point resolves a target of the real conf/environments.yml."""
    environment = dbxops.load_environment("prod", PROJECT_DIR / "conf/environments.yml")
    assert environment.framework_version == "v0.24.1"
