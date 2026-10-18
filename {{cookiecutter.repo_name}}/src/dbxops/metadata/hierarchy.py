"""
Classify every platform asset and place it in one tree.

The tree says what a workflow, job task, pipeline, flow, dataset, model, endpoint, index, agent, app or MCP server
is, and where it sits. ``classify`` decides the type, subtype and parent kind of one resource. ``build_hierarchy``
reads the bundles of a repository offline and returns every asset with its parent, workload and layer, the edges
between assets and the tasks of each workflow. Nothing here talks to a workspace.

Vocabulary (also in the glossary): a *workflow* is a Lakeflow Job and never a pipeline. An asset id is
``<asset_type>/<repo_id>/<resource_key>``; a dataset keeps its logical id ``<schema>.<table>``.
"""

from __future__ import annotations

import glob
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterator,
    Mapping,
)

import yaml

from dbxops.findings import Finding
from dbxops.metadata.contracts import (
    PRODUCER,
    get_custom,
)
from dbxops.metadata.repo import (
    RepoContext,
    SpecInfo,
)
from dbxops.metadata.specs import schema_name

RULE = "hierarchy"
WORKLOADS = ("de", "ml", "ai", "platform")
RELATIONS = ("contains", "runs", "reads", "writes", "serves", "indexes", "calls", "exposes")

# bundle resource type -> asset type (the base type; properties refine it in `classify`)
_RESOURCE_TYPES = {
    "jobs": "uc_job",
    "pipelines": "uc_pipeline",
    "volumes": "uc_volume",
    "registered_models": "uc_model",
    "models": "uc_model",
    "model_serving_endpoints": "serving_endpoint",
    "vector_search_endpoints": "vector_search_endpoint",
    "vector_search_indexes": "vector_index",
    "apps": "app",
    "mcp_services": "mcp_server",
    "dashboards": "dashboard",
    "alerts": "sql_alert",
    "genie_spaces": "genie_space",
    # not bundle resources: the files and spec parts the platform reads, classified with the same function
    "job_tasks": "uc_job_task",
    "dataflow_specs": "dataflow_spec",
    "dataflow_flows": "dataflow_flow",
    "datasets": "uc_table",
    "uc_functions": "uc_function",
    "knowledge_assistants": "knowledge_assistant",
}
_DATASET_TYPES = {
    "table": "uc_table",
    "view": "uc_view",
    "materialized_view": "uc_materialized_view",
    "streaming_table": "uc_streaming_table",
}
# the kind of resource that contains each asset type; any other type sits directly in its bundle
_PARENT_KINDS = {
    "uc_job_task": "workflow",
    "dataflow_spec": "pipeline",
    "dataflow_flow": "dataflow_spec",
    "uc_table": "flow",
    "uc_view": "flow",
    "uc_materialized_view": "flow",
    "uc_streaming_table": "flow",
    "uc_model": "workflow",
    "agent": "workflow",
    "serving_endpoint": "model",
    "vector_index": "vector_search_endpoint",
    "uc_function": "agent",
    "mcp_server": "app",
}


class UnclassifiedResource(ValueError):
    """Raised for a resource type the hierarchy has no place for."""


@dataclass(frozen=True)
class Classification:
    """Where one resource sits: its asset type, a subtype, and the kind of resource that contains it."""

    asset_type: str
    subtype: str
    parent_kind: str


def _task_kind(task: Mapping[str, Any]) -> str:
    for key in task:
        if key.endswith("_task"):
            return key.removesuffix("_task")
    return ""


def _tag(resource: Mapping[str, Any], name: str) -> str:
    tags = resource.get("tags") or {}
    if isinstance(tags, list):
        tags = {item.get("key"): item.get("value") for item in tags if isinstance(item, Mapping)}
    return str(tags.get(name) or resource.get(name) or "")


def _serving_subtype(resource: Mapping[str, Any]) -> str:
    entities = ((resource.get("config") or {}).get("served_entities")) or resource.get("served_entities") or []
    if any("external_model" in entity or "foundation_model" in entity for entity in entities):
        return "foundation_model"
    return "agent" if _tag(resource, "model_kind") == "agent" else "model"


def _flow_subtype(resource: Mapping[str, Any]) -> str:
    config = resource.get("config") or resource
    if config.get("table_type") in ("mv", "materialized_view"):
        return "mv"
    if config.get("table_type") == "view":
        return "view"
    if "cdc_snapshot_settings" in config:
        return "cdc_snapshot"
    scd = (config.get("cdc_settings") or {}).get("scd_type")
    return f"cdc_scd{scd}" if scd else "append"


def _refine_pipeline(resource: Mapping[str, Any]) -> tuple[str, str]:
    if "ingestion_definition" in resource:
        return "lakeflow_connect_pipeline", "managed_ingestion"
    return "uc_pipeline", "etl"


def _refine_model(resource: Mapping[str, Any]) -> tuple[str, str]:
    return ("agent" if _tag(resource, "model_kind") == "agent" else "uc_model"), ""


def _refine_app(resource: Mapping[str, Any]) -> tuple[str, str]:
    return ("mcp_server" if _tag(resource, "app_kind") == "mcp_server" else "app"), ""


def _refine_dataset(resource: Mapping[str, Any]) -> tuple[str, str]:
    asset_type = _DATASET_TYPES.get(str(resource.get("physical_type") or "table"), "uc_table")
    return asset_type, "quarantine" if resource.get("quarantine") else ""


# resource type -> function giving the (asset type, subtype) that the resource's properties decide
_REFINERS: dict[str, Callable[[Mapping[str, Any]], tuple[str, str]]] = {
    "job_tasks": lambda resource: ("uc_job_task", _task_kind(resource)),
    "pipelines": _refine_pipeline,
    "registered_models": _refine_model,
    "models": _refine_model,
    "model_serving_endpoints": lambda resource: ("serving_endpoint", _serving_subtype(resource)),
    "apps": _refine_app,
    "dataflow_flows": lambda resource: ("dataflow_flow", _flow_subtype(resource)),
    "datasets": _refine_dataset,
}


def classify(resource_type: str, resource: Mapping[str, Any]) -> Classification:
    """
    Return the type, subtype and parent kind of one resource.

    The resource type gives the base asset type; its properties give the subtype: a pipeline with an
    ``ingestion_definition`` is Lakeflow Connect, a model or serving config tagged ``model_kind=agent`` is an agent, an
    app tagged ``app_kind=mcp_server`` is an MCP server, a task's kind comes from its ``*_task`` key. Raises
    ``UnclassifiedResource`` for a resource type with no place in the tree.
    """
    if resource_type not in _RESOURCE_TYPES:
        raise UnclassifiedResource(f"unclassified asset: resource type {resource_type!r} has no place in the tree")
    refine = _REFINERS.get(resource_type)
    asset_type, subtype = refine(resource) if refine else (_RESOURCE_TYPES[resource_type], "")
    return Classification(asset_type, subtype, _PARENT_KINDS.get(asset_type, "bundle"))


@dataclass(frozen=True)
class AssetScope:
    """What the assets of one bundle share: its name, its asset id and its workload."""

    bundle: str
    bundle_id: str
    workload: str


@dataclass(frozen=True)
class HierarchyAsset:
    """One asset with its place in the tree."""

    asset_id: str
    asset_type: str
    subtype: str
    parent_asset_id: str
    layer: str
    resource_key: str
    scope: AssetScope

    @property
    def workload(self) -> str:
        """Return the workload of the bundle the asset belongs to."""
        return self.scope.workload

    @property
    def bundle(self) -> str:
        """Return the name of the bundle the asset belongs to."""
        return self.scope.bundle


@dataclass(frozen=True)
class AssetEdge:
    """A directed relation between two assets (``contains`` is the tree)."""

    parent_asset_id: str
    child_asset_id: str
    relation: str


@dataclass(frozen=True)
class JobTask:
    """One task of a workflow: its key, kind, dependencies and what it runs."""

    workflow_asset_id: str
    task_key: str
    kind: str
    depends_on: tuple[str, ...] = ()
    target_asset_id: str = ""


@dataclass
class Hierarchy:
    """Every asset, edge and workflow task of a repository, and what could not be placed."""

    assets: dict[str, HierarchyAsset] = field(default_factory=dict)
    edges: list[AssetEdge] = field(default_factory=list)
    tasks: list[JobTask] = field(default_factory=list)
    problems: list[Finding] = field(default_factory=list)

    def tasks_of(self, workflow_asset_id: str) -> list[JobTask]:
        """Return the tasks of one workflow, in file order."""
        return [task for task in self.tasks if task.workflow_asset_id == workflow_asset_id]


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resource_sections(bundle_dir: Path, config: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield config.get("resources") or {}
    for pattern in config.get("include") or ():
        for name in sorted(glob.glob(str(bundle_dir / pattern))):
            yield _read_yaml(Path(name)).get("resources") or {}


def load_bundle(bundle_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return a bundle's ``databricks.yml`` and its ``resources`` merged from the included files."""
    config = _read_yaml(bundle_dir / "databricks.yml")
    resources: dict[str, dict[str, Any]] = {}
    for section in _resource_sections(bundle_dir, config):
        for resource_type, items in section.items():
            resources.setdefault(resource_type, {}).update(items or {})
    return config, resources


def bundle_workload(config: Mapping[str, Any]) -> str:
    """Return the workload a bundle declares in its ``workload`` variable; empty when it declares none."""
    return str(((config.get("variables") or {}).get("workload") or {}).get("default") or "")


def _last_part(name: Any) -> str:
    return str(name).rsplit(".", maxsplit=1)[-1]


def _logical(table_name: str) -> str:
    """Return ``<schema>.<table>`` of a (possibly tokenised) table name, with any ``{logical_env}`` suffix dropped."""
    return ".".join(table_name.replace("{logical_env}", "").split(".")[-2:])


def _task_target(task: Mapping[str, Any], ids: Mapping[tuple[str, str], str]) -> str:
    reference = str(
        (task.get("pipeline_task") or {}).get("pipeline_id") or (task.get("run_job_task") or {}).get("job_id") or ""
    )
    for resource_type in ("pipelines", "jobs"):
        marker = f"${{resources.{resource_type}."
        if reference.startswith(marker):
            start = len(marker)
            return ids.get((resource_type, reference[start:].split(".")[0]), "")
    return ""


def _pipeline_layer(resource_type: str, resource: Mapping[str, Any]) -> str:
    if resource_type != "pipelines":
        return ""
    configuration = resource.get("configuration") or {}
    return str(configuration.get("pipeline.layer") or configuration.get("pipeline.dataFlowGroupFilter") or "")


class _Builder:
    """Walks the bundles one at a time; ``scope`` is the bundle being read."""

    def __init__(self, ctx: RepoContext):
        self.ctx = ctx
        self.repo = ctx.project_id
        self.result = Hierarchy()
        self.project = f"project/{self.repo}"
        self.scope = AssetScope("", "", "")
        self._workflows: dict[str, tuple[AssetScope, str]] = {}

    def asset_id(self, asset_type: str, key: str) -> str:
        return f"{asset_type}/{self.repo}/{key}"

    def add(self, asset_id: str, asset_type: str, key: str, parent: str, **place: str) -> str:
        """Add an asset to the bundle in scope; ``place`` may carry ``subtype`` and ``layer``."""
        asset = HierarchyAsset(
            asset_id, asset_type, place.get("subtype", ""), parent, place.get("layer", ""), key, self.scope
        )
        self.result.assets[asset_id] = asset
        if parent:
            self.result.edges.append(AssetEdge(parent, asset_id, "contains"))
        return asset_id

    def edge(self, parent: str, child: str, relation: str) -> None:
        self.result.edges.append(AssetEdge(parent, child, relation))

    def run(self) -> Hierarchy:
        for bundle_dir in sorted((self.ctx.root / "bundles").glob("*/databricks.yml")):
            self._bundle(bundle_dir.parent)
        self._job_written_tables()
        self._products()
        return self.result

    def _bundle(self, bundle_dir: Path) -> None:
        config, resources = load_bundle(bundle_dir)
        name = (config.get("bundle") or {}).get("name") or bundle_dir.name
        workload = bundle_workload(config)
        if workload not in WORKLOADS:
            message = f"the bundle must declare variables.workload.default as one of {', '.join(WORKLOADS)}"
            self.result.problems.append(Finding(RULE, f"bundles/{bundle_dir.name}/databricks.yml", message))
        bundle_id = self.asset_id("bundle", name)
        self.scope = AssetScope(name, bundle_id, workload)
        self.add(bundle_id, "bundle", name, self.project)
        ids = self._resources(bundle_dir, resources)
        jobs = [ids[("jobs", key)] for key in sorted(resources.get("jobs") or ())]
        self._workflows[bundle_dir.name] = (self.scope, jobs[0] if len(jobs) == 1 else "")
        self._tasks(resources, ids)
        self._vector_search(bundle_dir)
        self._serving(bundle_dir)
        self._functions(bundle_dir)
        self._specs(bundle_dir, resources, ids)

    def _resources(self, bundle_dir: Path, resources: Mapping[str, Any]) -> dict[tuple[str, str], str]:
        ids: dict[tuple[str, str], str] = {}
        for resource_type, items in resources.items():
            for key, resource in sorted(items.items()):
                try:
                    kind = classify(resource_type, resource or {})
                except UnclassifiedResource as error:
                    path = f"bundles/{bundle_dir.name}/resources"
                    self.result.problems.append(Finding(RULE, path, f"{key}: {error}"))
                    continue
                layer = _pipeline_layer(resource_type, resource or {})
                asset_id = self.asset_id(kind.asset_type, key)
                ids[(resource_type, key)] = self.add(
                    asset_id, kind.asset_type, key, self.scope.bundle_id, subtype=kind.subtype, layer=layer
                )
        return ids

    def _tasks(self, resources: Mapping[str, Any], ids: Mapping[tuple[str, str], str]) -> None:
        for job_key, job in sorted((resources.get("jobs") or {}).items()):
            workflow = ids[("jobs", job_key)]
            for task in job.get("tasks") or ():
                kind = classify("job_tasks", task)
                key = f"{job_key}/{task['task_key']}"
                task_id = self.add(
                    self.asset_id(kind.asset_type, key), kind.asset_type, key, workflow, subtype=kind.subtype
                )
                target = _task_target(task, ids)
                if target:
                    self.edge(task_id, target, "runs")
                depends = tuple(dep["task_key"] for dep in task.get("depends_on") or ())
                self.result.tasks.append(JobTask(workflow, task["task_key"], kind.subtype, depends, target))

    def _vector_search(self, bundle_dir: Path) -> None:
        for file in sorted((bundle_dir / "src/vector_search").glob("*.yml")):
            for entry in _read_yaml(file).get("vector_search_indexes") or ():
                name = entry["endpoint_name"]
                endpoint_id = self.asset_id("vector_search_endpoint", name)
                self.add(endpoint_id, "vector_search_endpoint", name, self.scope.bundle_id)
                index_key = _last_part(entry["name"])
                index_id = self.asset_id("vector_index", index_key)
                self.add(index_id, "vector_index", index_key, endpoint_id, subtype=entry.get("index_type", ""))
                source = str((entry.get("delta_sync_index_spec") or {}).get("source_table") or "")
                if source:
                    self.edge(index_id, _logical(source), "indexes")

    def _serving(self, bundle_dir: Path) -> None:
        for file in sorted((bundle_dir / "src/serving").glob("*.yml")):
            for entry in _read_yaml(file).get("serving_endpoints") or ():
                kind = classify("model_serving_endpoints", entry)
                key = str(entry["name"])
                endpoint = self.asset_id("serving_endpoint", key)
                self.add(endpoint, "serving_endpoint", key, self.scope.bundle_id, subtype=kind.subtype)
                for served in entry.get("served_entities") or ():
                    model = str(served.get("entity_name") or "")
                    if model:
                        self.edge(self.asset_id("uc_model", _last_part(model)), endpoint, "serves")

    def _functions(self, bundle_dir: Path) -> None:
        for file in sorted((bundle_dir / "src/functions").glob("*.sql")):
            self.add(self.asset_id("uc_function", file.stem), "uc_function", file.stem, self.scope.bundle_id)

    def _specs(self, bundle_dir: Path, resources: Mapping[str, Any], ids: Mapping[tuple[str, str], str]) -> None:
        pipelines = {
            (item.get("configuration") or {}).get("pipeline.dataFlowGroupFilter"): ids[("pipelines", key)]
            for key, item in (resources.get("pipelines") or {}).items()
            if ("pipelines", key) in ids
        }
        for info in self.ctx.specs:
            if info.bundle_dir != bundle_dir:
                continue
            parent = pipelines.get(info.spec.flow_group, self.scope.bundle_id)
            layer = info.spec.layer_dir.name
            key = info.spec.spec_id
            spec_id = self.add(self.asset_id("dataflow_spec", key), "dataflow_spec", key, parent, layer=layer)
            for target in info.spec.targets:
                self._flow(info, target, spec_id, layer)

    def _flow(self, info: SpecInfo, target: Any, spec_id: str, layer: str) -> None:
        node: dict[str, Any] = next((n for n in info.spec.raw.get("nodes") or () if n.get("name") == target.node), {})
        kind = classify("dataflow_flows", node)
        key = f"{info.spec.spec_id}/{target.table}"
        flow = self.add(
            self.asset_id("dataflow_flow", key), "dataflow_flow", key, spec_id, subtype=kind.subtype, layer=layer
        )
        self._flow_datasets(info, target, flow, layer)

    def _flow_datasets(self, info: SpecInfo, target: Any, flow: str, layer: str) -> None:
        schema = schema_name(target.database, info.tokens)
        physical = "materialized_view" if self.result.assets[flow].subtype == "mv" else "table"
        dataset = classify("datasets", {"physical_type": physical})
        dataset_id = f"{schema}.{target.table}"
        self.add(dataset_id, dataset.asset_type, target.table, flow, layer=layer)
        self.edge(flow, dataset_id, "writes")
        for name in target.sources:
            source = info.spec.sources.get(name)
            if source and source.table:
                self.edge(f"{schema_name(source.database, info.tokens)}.{source.table}", flow, "reads")
        if target.quarantine_table:
            quarantine = classify("datasets", {"quarantine": True})
            table = target.quarantine_table
            self.add(f"{schema}.{table}", quarantine.asset_type, table, flow, subtype=quarantine.subtype, layer=layer)

    def _job_written_tables(self) -> None:
        """Place each table a job writes (``dbx.producer: job``) under the workflow of its bundle."""
        for doc in self.ctx.contracts:
            bundle = next((part for part in Path(doc.path).parts if part in self._workflows), "")
            for obj in doc.data.get("schema") or ():
                physical = obj.get("physicalName") or ""
                if bundle and physical and get_custom(obj, PRODUCER) == "job":
                    self._job_table(bundle, ".".join(physical.split(".")[-2:]), obj)

    def _job_table(self, bundle: str, asset_id: str, obj: Mapping[str, Any]) -> None:
        if asset_id in self.result.assets:
            return
        self.scope, workflow = self._workflows[bundle]
        kind = classify("datasets", {"physical_type": obj.get("physicalType") or "table"})
        layer = asset_id.split(".")[0].removeprefix(f"{self.repo}_")
        parent = workflow or self.scope.bundle_id
        self.add(asset_id, kind.asset_type, obj.get("name") or asset_id, parent, layer=layer)
        if workflow:
            self.edge(workflow, asset_id, "writes")

    def _products(self) -> None:
        for doc in self.ctx.products:
            for port in doc.data.get("outputPorts") or ():
                asset = port.get("assetId") or next(
                    (p.get("value") for p in port.get("customProperties") or () if p.get("property") == "dbx.assetId"),
                    "",
                )
                if asset:
                    self.edge(str(doc.data.get("id")), str(asset), "exposes")


def build_hierarchy(ctx: RepoContext) -> Hierarchy:
    """Read every bundle of the repository and return its assets, edges, workflow tasks and problems."""
    return _Builder(ctx).run()
