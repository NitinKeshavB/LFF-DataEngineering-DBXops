"""Consistency tests for the AIOps bundle: its files agree with each other, with the job wiring and with the specs."""

import ast
import json
import re

import pytest
import yaml

from dbxops.metadata.repo import RepoContext
from dbxops.promotion import PromotionGate
from tests.bundles.aiops_helpers import (
    BUNDLE,
    SRC,
    load_script,
    read_yaml,
)
from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import load_bundle

VARIABLES = {"project_id": "example_claims", "target": "dev", "logical_env": "_ann", "cost_center": "c", "region": "r"}


@pytest.fixture(scope="module")
def bundle():
    """Return the merged bundle document (databricks.yml plus its resource files)."""
    return load_bundle(BUNDLE)


@pytest.fixture(scope="module")
def job(bundle):
    """Return the aiops_run job."""
    return bundle["resources"]["jobs"]["aiops_run"]


@pytest.fixture(scope="module")
def tasks(job):
    """Return the job's tasks by key."""
    return {task["task_key"]: task for task in job["tasks"]}


@pytest.fixture(scope="module")
def environments(job):
    """Return the job's environments by key."""
    return {env["environment_key"]: env["spec"] for env in job["environments"]}


@pytest.fixture(scope="module")
def lifecycle_task():
    """Import the lifecycle wrapper script."""
    return load_script("lifecycle_task", SRC / "jobs/lifecycle_task.py")


def resolve(text: str, catalog: str = "cat") -> str:
    """Replace the bundle references the job parameters use with the values of the test target."""
    values = {
        "${var.project_id}": "example_claims",
        "${bundle.target}": "dev",
        "${var.logical_env}": "_ann",
        "${var.catalog}": catalog,
        "${workspace.file_path}": "/ws/files",
    }
    for reference, value in values.items():
        text = text.replace(reference, value)
    return text


def rendered(lifecycle_task, relative: str) -> dict:
    """Return a reconcile config with the wrapper's tokens filled, `{catalog}` as the lifecycle library fills it."""
    text = lifecycle_task.render_config((SRC / relative).read_text(encoding="utf-8"), VARIABLES)
    return yaml.safe_load(text.replace("{catalog}", "cat"))


def parameters(task: dict) -> list[str]:
    """Return a task's parameters with the bundle references resolved."""
    return [resolve(str(item)) for item in task["spark_python_task"]["parameters"]]


def after(params: list[str], flag: str) -> str:
    """Return the value that follows a flag in a parameter list."""
    return params[params.index(flag) + 1]


def test__the_bundle_declares_the_requested_variables(bundle):
    """The wheel path (empty by default, set by CI) and the Agent Bricks switch (off by default) exist."""
    variables = bundle["variables"]
    assert (
        variables["dbxops_wheel_path"]["default"] == ""
        and "UC volume" in variables["dbxops_wheel_path"]["description"]
    )
    assert (
        variables["enable_agent_bricks"]["default"] is False
        and "Beta" in variables["enable_agent_bricks"]["description"]
    )


def test__every_variable_a_resource_uses_is_declared(bundle):
    """A `${var.x}` in the resource files without a variable would fail at deploy time, so catch it here."""
    used = set(re.findall(r"\$\{var\.(\w+)\}", (BUNDLE / "resources/aiops.jobs.yml").read_text(encoding="utf-8")))
    used |= set(
        re.findall(r"\$\{var\.(\w+)\}", (BUNDLE / "resources/aiops.pipelines.yml").read_text(encoding="utf-8"))
    )
    assert used <= set(bundle["variables"])


def test__the_pipelines_use_the_ai_flow_groups_and_the_ai_schema(bundle):
    """The two pipelines are one-to-one with the ai_bronze and ai_silver groups and write the ai schema."""
    pipelines = bundle["resources"]["pipelines"]
    assert {p["configuration"]["pipeline.dataFlowGroupFilter"] for p in pipelines.values()} == {
        "ai_bronze",
        "ai_silver",
    }
    assert all(p["schema"] == "${var.project_id}_ai${var.logical_env}" for p in pipelines.values())


def test__the_flow_groups_differ_from_the_dataops_groups():
    """The aiops groups must not reuse `bronze` or `silver`, or a pipeline filter in either bundle would pick up both."""
    ctx = RepoContext.load(PROJECT_DIR)
    groups = {
        name: {i.spec.flow_group for i in ctx.specs if i.bundle_dir.name == name} for name in ("dataops", "aiops")
    }
    assert groups["aiops"] == {"ai_bronze", "ai_silver"} and not groups["aiops"] & groups["dataops"]


def test__every_table_the_specs_write_is_in_the_ai_schema_with_change_data_feed_on_the_chunks():
    """Both tables live in `<project_id>_ai`; the chunks table has change data feed and chunk_id as its CDC key."""
    ctx = RepoContext.load(PROJECT_DIR)
    specs = {i.spec.spec_id: i for i in ctx.specs if i.bundle_dir.name == "aiops"}
    assert set(specs) == {"policy_documents_parsed", "policy_chunks"}
    chunks = (BUNDLE / "src/dataflows/ai_silver/dataflowspec/policy_chunks_main.json").read_text(encoding="utf-8")
    assert '"delta.enableChangeDataFeed": "true"' in chunks and '"keys": [\n            "chunk_id"' in chunks
    bronze = (BUNDLE / "src/dataflows/ai_bronze/dataflowspec/policy_documents_parsed_main.json").read_text("utf-8")
    assert '"mode": "table"' in bronze and "ai_parse_document" in (
        BUNDLE / "src/dataflows/ai_bronze/dml/parse_policy_documents.sql"
    ).read_text(encoding="utf-8")


def test__the_job_runs_the_stages_in_dependency_order(tasks):
    """The job goes documents -> bronze -> silver -> index -> wait -> register -> evaluate -> gate -> deploy -> serving settings."""
    expected = {
        "ai_bronze": {"generate_docs"},
        "ai_silver": {"ai_bronze"},
        "reconcile_index": {"ai_silver"},
        "wait_for_index": {"reconcile_index"},
        "register_agent": {"wait_for_index", "create_functions"},
        "evaluate_agent": {"register_agent"},
        "gate_agent": {"evaluate_agent"},
        "deploy_agent": {"gate_agent"},
        "reconcile_serving": {"deploy_agent"},
        "agent_bricks_enabled": {"wait_for_index"},
        "reconcile_knowledge_assistant": {"agent_bricks_enabled"},
    }
    actual = {key: {d["task_key"] for d in task.get("depends_on", [])} for key, task in tasks.items()}
    assert {key: actual[key] for key in expected} == expected
    assert actual["generate_docs"] == set() and actual["create_functions"] == set()


def test__the_job_ends_by_recording_its_run(tasks):
    """publish_run_metadata is the last task: it runs after serving and the optional assistant, whatever their outcome."""
    publish = tasks["publish_run_metadata"]
    assert publish["run_if"] == "ALL_DONE"
    assert {d["task_key"] for d in publish["depends_on"]} == {"reconcile_serving", "reconcile_knowledge_assistant"}
    assert publish["python_wheel_task"]["entry_point"] == "dbxops-publish-run"


def test__the_knowledge_assistant_runs_only_when_agent_bricks_is_enabled(tasks):
    """A condition task compares the switch with 'true', and the KA task follows its true branch only."""
    condition = tasks["agent_bricks_enabled"]["condition_task"]
    assert condition == {"op": "EQUAL_TO", "left": "${var.enable_agent_bricks}", "right": "true"}
    assert tasks["reconcile_knowledge_assistant"]["depends_on"] == [
        {"task_key": "agent_bricks_enabled", "outcome": "true"}
    ]


def test__only_the_run_recorder_depends_on_the_knowledge_assistant(tasks):
    """The optional KA branch is a leaf apart from the final recorder (run_if ALL_DONE), so a skipped KA never blocks it."""
    dependants = {
        key
        for key, task in tasks.items()
        if "reconcile_knowledge_assistant" in {d["task_key"] for d in task.get("depends_on", [])}
    }
    assert dependants == {"publish_run_metadata"}


def test__job_environments_carry_the_job_only_libraries_and_the_wheel(environments):
    """plutoprint, the agent libraries and the dbxops wheel are job dependencies, never package dependencies."""
    register = load_script("register_agent", SRC / "jobs/register_agent.py")
    assert environments["agent"]["dependencies"] == list(register.AGENT_PIP_REQUIREMENTS)
    assert environments["lifecycle"]["dependencies"] == ["${var.dbxops_wheel_path}"]
    assert any(item.startswith("plutoprint") for item in environments["docs"]["dependencies"])
    assert {env["client"] for env in environments.values()} == {"4"}
    project = (PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = re.search(r"^dependencies = \[(.*?)\]", project, re.MULTILINE).group(1)
    assert not any(name in dependencies for name in ("mlflow", "langgraph", "plutoprint", "langchain"))


def test__tasks_use_the_environment_that_has_their_libraries(tasks):
    """Every script task names an environment, and the right one for what the script imports."""
    wanted = {
        "generate_docs": "docs",
        "create_functions": "default",
        "register_agent": "agent",
        "evaluate_agent": "agent",
        "deploy_agent": "agent",
        "reconcile_index": "lifecycle",
        "wait_for_index": "lifecycle",
        "gate_agent": "lifecycle",
        "reconcile_serving": "lifecycle",
        "reconcile_knowledge_assistant": "lifecycle",
    }
    assert {key: tasks[key]["environment_key"] for key in wanted} == wanted


def test__every_script_and_file_a_task_names_exists(tasks):
    """Python files are relative to the resources folder; workspace paths point at files that ship in src/."""
    for key, task in tasks.items():
        if "spark_python_task" not in task:
            continue
        assert (BUNDLE / "resources" / task["spark_python_task"]["python_file"]).resolve().is_file(), key
        for item in task["spark_python_task"]["parameters"]:
            if str(item).startswith("${workspace.file_path}/"):
                assert (BUNDLE / str(item).replace("${workspace.file_path}/", "")).is_file(), (key, item)


def test__the_job_and_the_reconcile_configs_agree_on_names(tasks, lifecycle_task):
    """The index, endpoint, model and function names the job passes are the ones the reconcile configs create."""
    index = rendered(lifecycle_task, "vector_search/policy_index.yml")["vector_search_indexes"][0]
    serving = rendered(lifecycle_task, "serving/policy_assistant_endpoint.yml")["serving_endpoints"][0]
    register = parameters(tasks["register_agent"])
    assert after(register, "--vector-index") == index["name"]
    assert after(parameters(tasks["wait_for_index"]), "--index") == index["name"]
    assert after(parameters(tasks["deploy_agent"]), "--endpoint-name") == serving["name"]
    assert serving["served_entities"][0]["entity_name"] == after(register, "--model")
    assert index["delta_sync_index_spec"]["source_table"] == "cat.example_claims_ai_ann.policy_chunks"
    assert after(register, "--claim-status-function") == "cat.example_claims_ai_ann.get_claim_status"
    assert after(parameters(tasks["evaluate_agent"]), "--model") == after(register, "--model")
    assert after(parameters(tasks["gate_agent"]), "--model") == after(register, "--model")
    assert after(parameters(tasks["evaluate_agent"]), "--experiment") == after(register, "--experiment")


def test__the_chunks_table_columns_the_index_and_the_ka_read_exist():
    """The index embeds `chunk_text` and is keyed on `chunk_id`; the KA reads `chunk_text` and `document_path`."""
    columns = {
        f["name"]: f
        for f in json.loads((BUNDLE / "src/dataflows/ai_silver/schemas/policy_chunks_schema.json").read_text("utf-8"))[
            "fields"
        ]
    }
    index = read_yaml(SRC / "vector_search/policy_index.yml")["vector_search_indexes"][0]
    source = read_yaml(SRC / "agent_bricks/policy_ka.yml")["knowledge_assistants"][0]["sources"][0]["index"]
    assert index["primary_key"] in columns and columns[index["primary_key"]]["nullable"] is False
    assert index["delta_sync_index_spec"]["embedding_source_columns"][0]["name"] in columns
    assert {source["text_col"], source["doc_uri_col"]} <= set(columns)
    core = load_script("policy_assistant_core", SRC / "agent/policy_assistant_core.py")
    assert set(core.RETRIEVER_COLUMNS) <= set(columns)


def test__the_function_sql_uses_only_known_tokens_and_the_dataops_claims_table():
    """Its tokens are the two schema tokens, and every column it selects exists in fact_claim."""
    sql = (SRC / "functions/get_claim_status.sql").read_text(encoding="utf-8")
    assert set(re.findall(r"\{(\w+)\}", sql)) == {"ai_schema", "silver_schema"}
    fact = json.loads(
        (PROJECT_DIR / "bundles/dataops/src/dataflows/silver/schemas/fact_claim_schema.json").read_text("utf-8")
    )
    names = {f["name"] for f in fact["fields"]}
    selected = set(re.findall(r"fc\.(\w+)", sql))
    assert selected and selected <= names


def test__the_gate_is_an_agent_pass_rate_gate_on_the_metric_the_evaluation_logs():
    """The gate file loads with the lifecycle library and reads the metric that evaluate_agent.py writes."""
    evaluate = load_script("evaluate_agent", SRC / "jobs/evaluate_agent.py")
    gate = PromotionGate.from_yaml(SRC / "gates/policy_assistant.gate.yml")
    assert gate.eval_kind == "agent" and (gate.challenger_alias, gate.champion_alias) == ("challenger", "champion")
    decisions = gate.evaluate("m", 1, {evaluate.PASS_RATE_METRIC: 1.0}).decisions
    assert [(d.metric, d.comparator, d.passed) for d in decisions] == [(evaluate.PASS_RATE_METRIC, ">=", True)]
    assert 0 < decisions[0].threshold <= 1
    assert not gate.evaluate("m", 1, {evaluate.PASS_RATE_METRIC: 0.0}).passed
    assert not gate.evaluate("m", 1, {"some_other_metric": 1.0}).passed


def test__the_document_questions_have_facts_that_the_generated_wordings_contain():
    """Each `doc_` case's expected facts appear in the synthetic policy text, so the retrieval question is answerable."""
    gen = load_script("generate_policy_docs", SRC / "jobs/generate_policy_docs.py")
    evaluate = load_script("evaluate_agent", SRC / "jobs/evaluate_agent.py")
    text = " ".join(gen.render_text(w) for w in gen.policy_wordings())
    cases = evaluate.load_cases(SRC / "eval/policy_assistant_cases.json")
    doc_cases = [c for c in cases if c["id"].startswith("doc_")]
    assert len(doc_cases) >= 8 and any(c["id"].startswith("claim_") for c in cases)
    for case in doc_cases:
        assert all(fact in text for fact in case["expectations"]["expected_facts"]), case["id"]


PRODUCT_ASSET_IDS = {
    "uc_function": "example_claims_ai.get_claim_status",
    "vector_index": "vector_index/example_claims/policy_chunks_index",
    "agent": "agent/example_claims/policy_assistant",
    "serving_endpoint": "serving_endpoint/example_claims/policy_assistant",
    "knowledge_assistant": "knowledge_assistant/example_claims/policy_ka",
}


def test__the_product_exposes_every_contract_as_an_output_port_and_reads_fact_claim():
    """Both contracts are output ports, and the one cross-bundle read is declared as an input port."""
    product = read_yaml(SRC / "products/policy_assistant.odps.yaml")
    contracts = {p["contractId"] for p in product["outputPorts"] if "contractId" in p}
    assert contracts == {"dc_example_claims_ai_policy_documents_parsed", "dc_example_claims_ai_policy_chunks"}
    assert [p["contractId"] for p in product["inputPorts"]] == ["dc_example_claims_silver_fact_claim"]


def test__the_product_exposes_every_ai_asset_as_an_output_port():
    """The vector index, agent, endpoint, KA and UC function are ports typed by asset type, with logical asset ids."""
    ports = read_yaml(SRC / "products/policy_assistant.odps.yaml")["outputPorts"]
    asset_ids = {p["type"]: p["customProperties"][0]["value"] for p in ports if "contractId" not in p}
    assert asset_ids == PRODUCT_ASSET_IDS


def test__the_asset_names_in_the_product_match_the_names_the_job_creates(tasks, lifecycle_task):
    """The logical asset ids end in the same names as the real index, model and function."""
    index = rendered(lifecycle_task, "vector_search/policy_index.yml")["vector_search_indexes"][0]["name"]
    model = after(parameters(tasks["register_agent"]), "--model")
    function = after(parameters(tasks["register_agent"]), "--claim-status-function")
    ids = {
        p["type"]: p["customProperties"][0]["value"]
        for p in read_yaml(SRC / "products/policy_assistant.odps.yaml")["outputPorts"]
    }
    assert ids["vector_index"].endswith("/" + index.rsplit(".", 1)[1])
    assert ids["agent"].endswith("/" + model.rsplit(".", 1)[1])
    assert ids["uc_function"].endswith("." + function.rsplit(".", 1)[1])
    assert ids["serving_endpoint"].endswith("/policy_assistant")


def test__both_specs_carry_the_domain_and_product_tags():
    """Both specs carry the data_domain and data_product tags of the policy assistant."""
    for path in (BUNDLE / "src/dataflows").glob("*/dataflowspec/*.json"):
        text = path.read_text(encoding="utf-8")
        assert '"data_domain": "product__wordings_pds__product_wording"' in text, path.name
        assert '"data_product": "dp_example_claims_policy_assistant"' in text, path.name


# --- the framework-bound wrapper, checked without the framework ----------------------------------------------------


@pytest.fixture(scope="module")
def wrapper_tree() -> ast.Module:
    """Parse the ResponsesAgent wrapper, which cannot be imported without mlflow and langgraph."""
    return ast.parse((SRC / "agent/policy_assistant.py").read_text(encoding="utf-8"))


def test__the_wrapper_only_imports_names_that_the_core_defines(wrapper_tree):
    """Every name taken from the core module exists there, so a rename in the core cannot silently break the wrapper."""
    core = load_script("policy_assistant_core", SRC / "agent/policy_assistant_core.py")
    imported = [
        alias.name
        for node in ast.walk(wrapper_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "policy_assistant_core"
        for alias in node.names
    ]
    assert imported and all(hasattr(core, name) for name in imported)


def test__the_wrapper_is_a_responses_agent_with_both_predict_methods(wrapper_tree):
    """The class extends ResponsesAgent and defines predict and predict_stream."""
    agents = [n for n in ast.walk(wrapper_tree) if isinstance(n, ast.ClassDef) and n.name == "PolicyAssistant"]
    assert [ast.unparse(base) for base in agents[0].bases] == ["ResponsesAgent"]
    assert {n.name for n in agents[0].body if isinstance(n, ast.FunctionDef)} >= {"predict", "predict_stream"}


def test__the_wrapper_autologs_langchain_before_it_registers_the_agent(wrapper_tree):
    """The only top-level calls are the LangChain autolog and then `set_model`, so tracing is on before the build."""
    calls = [
        ast.unparse(n.value.func)
        for n in wrapper_tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    assert calls == ["mlflow.langchain.autolog", "mlflow.models.set_model"]


def test__the_wrapper_imports_langgraph_and_langchain_only_after_the_autolog_call(wrapper_tree):
    """Those libraries are imported inside functions (through importlib), never at the top level."""
    body = wrapper_tree.body
    modules = [n.module for n in body if isinstance(n, ast.ImportFrom)]
    modules += [a.name for n in body if isinstance(n, ast.Import) for a in n.names]
    assert not {m.split(".")[0] for m in modules} & {"langgraph", "langchain_core", "databricks_langchain"}


def test__the_wrapper_builds_output_items_with_the_documented_helpers(wrapper_tree):
    """It uses the three ResponsesAgent helpers of the skill rather than raw dicts."""
    source = ast.unparse(wrapper_tree)
    for helper in ("create_text_output_item", "create_function_call_item", "create_function_call_output_item"):
        assert f"self.{helper}(" in source
    assert "response.output_item.done" in source
