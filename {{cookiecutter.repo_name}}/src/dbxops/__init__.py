"""
dbxops: metadata-driven DataOps, MLOps and AIOps on Databricks.

Public API (a contract for installed users; changes follow semantic versioning):

* configuration: `load_environment`, `load_repo_config`
* metadata and registry validation: `validate_metadata`, `validate_registry`, `Finding`
* metadata database: `MetadataRepository`, `RepositoryFactory`, `MetadataPublisher`, `PublishContext`
* workspace and lifecycle: `WorkspaceGateway`, `SdkWorkspaceGateway`, `Reconciler` and its five subclasses,
  `PromotionGate`, `ModelPromoter` and the gate policies
"""

from dbxops.config import (
    ConfigError,
    Environment,
    RepoConfig,
    load_environment,
    load_repo_config,
)
from dbxops.findings import Finding
from dbxops.metadata import validate_metadata
from dbxops.metastore import (
    MetadataPublisher,
    MetadataRepository,
    MetastoreError,
    PublishContext,
    RepositoryFactory,
)
from dbxops.promotion import (
    EvalPassRate,
    GateConfigError,
    GateDecision,
    GateReport,
    MetricThreshold,
    ModelPromoter,
    PromotionGate,
    PromotionRefused,
)
from dbxops.reconcile import (
    Change,
    KnowledgeAssistantReconciler,
    MetadataSyncReconciler,
    MonitorReconciler,
    Reconciler,
    ReconcileResult,
    ServingEndpointReconciler,
    VectorSearchIndexReconciler,
)
from dbxops.registry.registration import validate_registry
from dbxops.workspace import (
    GatewayError,
    SdkWorkspaceGateway,
    WorkspaceGateway,
)

__all__ = [
    "Change",
    "ConfigError",
    "Environment",
    "EvalPassRate",
    "Finding",
    "GatewayError",
    "GateConfigError",
    "GateDecision",
    "GateReport",
    "KnowledgeAssistantReconciler",
    "MetadataPublisher",
    "MetadataRepository",
    "MetadataSyncReconciler",
    "MetastoreError",
    "MetricThreshold",
    "ModelPromoter",
    "MonitorReconciler",
    "PromotionGate",
    "PromotionRefused",
    "PublishContext",
    "ReconcileResult",
    "Reconciler",
    "RepoConfig",
    "RepositoryFactory",
    "SdkWorkspaceGateway",
    "ServingEndpointReconciler",
    "VectorSearchIndexReconciler",
    "WorkspaceGateway",
    "load_environment",
    "load_repo_config",
    "validate_metadata",
    "validate_registry",
]
