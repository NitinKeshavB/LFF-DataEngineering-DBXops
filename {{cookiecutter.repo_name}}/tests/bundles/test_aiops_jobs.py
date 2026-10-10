"""Tests for the AIOps job scripts, with fakes in place of mlflow, databricks-agents, Spark and the workspace."""

import json
from types import SimpleNamespace

import pytest
import yaml

from dbxops.config import load_environment
from tests.bundles.aiops_helpers import (
    SRC,
    load_script,
)

MODEL = "cat.example_claims_ai.policy_assistant"
INDEX = "cat.example_claims_ai.policy_chunks_index"
FUNCTION = "cat.example_claims_ai.get_claim_status"


@pytest.fixture(scope="module")
def lifecycle_task():
    """Import the lifecycle wrapper script."""
    return load_script("lifecycle_task", SRC / "jobs/lifecycle_task.py")


@pytest.fixture(scope="module")
def uc_functions():
    """Import the UC function creation script."""
    return load_script("create_uc_functions", SRC / "jobs/create_uc_functions.py")


@pytest.fixture(scope="module")
def register():
    """Import the agent registration script."""
    return load_script("register_agent", SRC / "jobs/register_agent.py")


@pytest.fixture(scope="module")
def evaluate():
    """Import the agent evaluation script."""
    return load_script("evaluate_agent", SRC / "jobs/evaluate_agent.py")


@pytest.fixture(scope="module")
def deploy():
    """Import the agent deployment script."""
    return load_script("deploy_agent", SRC / "jobs/deploy_agent.py")


# --- lifecycle_task -------------------------------------------------------------------------------------------------


def reconcile_args(config, *extra):
    """Return the arguments of a reconcile task for the dev-like test target."""
    variables = ("project_id=example_claims", "target=dev", "logical_env=_ann", "cost_center=cc-1", "region=au")
    flags = [item for pair in variables for item in ("--var", pair)]
    return ["reconcile", "--config", str(config), "--target", "dev", "--catalog", "cat", *flags, *extra]


def test__tokens_are_filled_and_the_catalog_token_is_left_for_the_lifecycle_library(lifecycle_task):
    """`{catalog}` survives rendering; every other token needs a value, and a missing one is named."""
    text = "name: '{catalog}.{project_id}_ai{logical_env}'"
    assert lifecycle_task.render_config(text, {"project_id": "p", "logical_env": "_x"}) == "name: '{catalog}.p_ai_x'"
    with pytest.raises(lifecycle_task.TaskError, match="region"):
        lifecycle_task.render_config("{project_id} {region}", {"project_id": "p"})


def test__variables_must_be_name_value_pairs_and_may_be_empty(lifecycle_task):
    """`--var logical_env=` is a valid empty value; a pair without `=` or with a bad name is rejected."""
    assert lifecycle_task.parse_variables(["a=1", "logical_env="]) == {"a": "1", "logical_env": ""}
    for bad in ("novalue", "Bad=1", "=1"):
        with pytest.raises(lifecycle_task.TaskError):
            lifecycle_task.parse_variables([bad])


def test__the_environments_file_it_writes_loads_with_the_lifecycle_loader(lifecycle_task, tmp_path):
    """The minimal environments document passes `load_environment` and carries the bundle's catalog."""
    path = tmp_path / "environments.yml"
    path.write_text(yaml.safe_dump(lifecycle_task.environments_document("dev", "cat", "https://h")), encoding="utf-8")
    environment = load_environment("dev", path)
    assert environment.catalog == "cat" and environment.workspace_host == "https://h"


@pytest.mark.parametrize(
    "config", ["vector_search/policy_index.yml", "serving/policy_assistant_endpoint.yml", "agent_bricks/policy_ka.yml"]
)
def test__every_reconcile_config_applies_once_and_then_changes_nothing_but_a_requested_sync(
    lifecycle_task, fake_gateway, config
):
    """Applying a bundle config to an empty workspace creates objects; a second run only re-syncs the index."""

    def factory(_):
        return fake_gateway

    path = SRC / config
    assert lifecycle_task.main(reconcile_args(path), factory) == 0
    first = list(fake_gateway.writes)
    assert first, "the first run should create something"
    assert lifecycle_task.main(reconcile_args(path), factory) == 0
    start = len(first)
    assert {call[0] for call in fake_gateway.writes[start:]} <= {"sync_vector_search_index"}


def test__the_index_config_creates_a_delta_sync_index_with_the_expected_names(lifecycle_task, fake_gateway):
    """The rendered index name, endpoint, source table and embedding column are what the job wires up."""
    lifecycle_task.main(reconcile_args(SRC / "vector_search/policy_index.yml"), lambda _: fake_gateway)
    creates = {call[0]: call for call in fake_gateway.writes}
    assert creates["create_vector_search_endpoint"][1:] == ("example_claims_dev_vs", "STANDARD")
    _, name, spec = creates["create_vector_search_index"]
    assert name == "cat.example_claims_ai_ann.policy_chunks_index"
    assert spec["primary_key"] == "chunk_id" and spec["index_type"] == "DELTA_SYNC"
    assert spec["delta_sync_index_spec"]["source_table"] == "cat.example_claims_ai_ann.policy_chunks"
    assert spec["delta_sync_index_spec"]["embedding_source_columns"][0]["name"] == "chunk_text"


def test__the_serving_config_sets_rate_limits_and_tags_on_the_deployed_endpoint(lifecycle_task, fake_gateway):
    """The endpoint is named for target and developer, serves the UC model, and carries limits and tags."""
    lifecycle_task.main(reconcile_args(SRC / "serving/policy_assistant_endpoint.yml"), lambda _: fake_gateway)
    _, name, config, ai_gateway, tags = [c for c in fake_gateway.writes if c[0] == "create_serving_endpoint"][0]
    assert name == "example_claims_dev_policy_assistant_ann"
    assert config["served_entities"] == [{"entity_name": "cat.example_claims_ai_ann.policy_assistant"}]
    assert {limit["key"] for limit in ai_gateway["rate_limits"]} == {"user", "endpoint"}
    assert tags == {"data_product": "dp_example_claims_policy_assistant", "cost_center": "cc-1", "region": "au"}


def test__a_reconcile_config_with_an_unfilled_token_exits_two(lifecycle_task, fake_gateway, capsys):
    """Forgetting a --var is a usage error that names the token, not a half-rendered config."""
    args = ["reconcile", "--config", str(SRC / "serving/policy_assistant_endpoint.yml"), "--target", "dev"]
    assert lifecycle_task.main([*args, "--catalog", "cat"], lambda _: fake_gateway) == 2
    assert "project_id" in capsys.readouterr().err
    assert fake_gateway.writes == []


def test__dry_run_reports_without_writing(lifecycle_task, fake_gateway, capsys):
    """`--dry-run` is passed through to the lifecycle library."""
    path = SRC / "vector_search/policy_index.yml"
    assert lifecycle_task.main(reconcile_args(path, "--dry-run"), lambda _: fake_gateway) == 0
    assert fake_gateway.writes == []
    assert "dry run" in capsys.readouterr().out


def promote_args(gate="gates/policy_assistant.gate.yml"):
    """Return the arguments of a promote task."""
    return ["promote", "--model", MODEL, "--alias", "challenger", "--gate", str(SRC / gate)]


def test__a_challenger_that_meets_the_gate_becomes_the_champion(lifecycle_task, fake_gateway, capsys):
    """The version holding @challenger is gated on its run's pass_rate and then gets @champion."""
    fake_gateway.aliases[(MODEL, "challenger")] = 4
    fake_gateway.metrics[(MODEL, 4)] = {"pass_rate": 0.91}
    assert lifecycle_task.main(promote_args(), lambda _: fake_gateway) == 0
    assert fake_gateway.aliases[(MODEL, "champion")] == 4
    facts = json.loads(capsys.readouterr().out.split("\n" + MODEL)[0])
    assert facts[0]["eval_kind"] == "agent" and facts[0]["metric"] == "pass_rate" and facts[0]["passed"] is True
    assert facts[0]["gate_source_path"].endswith("policy_assistant.gate.yml")


def test__a_challenger_below_the_threshold_is_not_promoted(lifecycle_task, fake_gateway):
    """A pass rate under the gate's threshold exits 1 and leaves the champion alone."""
    fake_gateway.aliases[(MODEL, "challenger")] = 5
    fake_gateway.metrics[(MODEL, 5)] = {"pass_rate": 0.5}
    assert lifecycle_task.main(promote_args(), lambda _: fake_gateway) == 1
    assert (MODEL, "champion") not in fake_gateway.aliases


def test__promoting_without_a_challenger_or_without_a_pass_rate_fails(lifecycle_task, fake_gateway, capsys):
    """No challenger alias, or a run without the pass_rate metric, both fail rather than promote."""
    assert lifecycle_task.main(promote_args(), lambda _: fake_gateway) == 1
    assert "no version with the alias @challenger" in capsys.readouterr().err
    fake_gateway.aliases[(MODEL, "challenger")] = 6
    assert lifecycle_task.main(promote_args(), lambda _: fake_gateway) == 1
    assert (MODEL, "champion") not in fake_gateway.aliases


def wait_args(**overrides):
    """Return the arguments of a wait-index task."""
    return [
        "wait-index",
        "--index",
        INDEX,
        "--timeout-minutes",
        str(overrides.get("timeout", 1)),
        "--poll-seconds",
        "5",
    ]


def test__wait_index_returns_once_the_index_is_ready(lifecycle_task, fake_gateway, capsys):
    """The task polls until the index reports ready, sleeping between polls."""
    fake_gateway.vs_indexes[INDEX] = {"status": {"ready": False}}
    polls = []

    def sleep(seconds):
        polls.append(seconds)
        if len(polls) == 2:
            fake_gateway.vs_indexes[INDEX] = {"status": {"ready": True, "indexed_row_count": 40}}

    args = lifecycle_task.build_parser().parse_args(wait_args())
    assert (
        lifecycle_task.run_wait_index(args, lambda _: fake_gateway, sleep=sleep, clock=iter(range(0, 100)).__next__)
        == 0
    )
    assert polls == [5.0, 5.0] and "40 rows indexed" in capsys.readouterr().out


def test__wait_index_fails_when_the_index_is_missing_past_the_deadline(lifecycle_task, fake_gateway, capsys):
    """A missing or never-ready index fails the task after the timeout instead of hanging."""
    args = lifecycle_task.build_parser().parse_args(wait_args())
    ticks = iter([0, 10, 70])
    assert (
        lifecycle_task.run_wait_index(args, lambda _: fake_gateway, sleep=lambda _: None, clock=lambda: next(ticks))
        == 1
    )
    assert "not ready after 1.0 minutes" in capsys.readouterr().err


# --- create_uc_functions --------------------------------------------------------------------------------------------


def test__the_function_sql_is_rendered_for_the_target_schemas(uc_functions):
    """The claim status function is created in the ai schema and reads the dataops silver fact_claim table."""
    ran = []
    code = uc_functions.main(
        [
            "--catalog",
            "cat",
            "--project-id",
            "example_claims",
            "--logical-env",
            "_ann",
            "--sql",
            str(SRC / "functions/get_claim_status.sql"),
        ],
        run_sql=ran.append,
    )
    assert code == 0 and len(ran) == 1
    assert "CREATE OR REPLACE FUNCTION cat.example_claims_ai_ann.get_claim_status(" in ran[0]
    assert "FROM cat.example_claims_silver_ann.fact_claim AS fc" in ran[0]
    assert "{" not in ran[0]


def test__an_unknown_sql_token_is_an_error(uc_functions):
    """A typo in a token name is reported, not sent to Spark."""
    with pytest.raises(ValueError, match="ai_schemma"):
        uc_functions.render_sql("SELECT 1 FROM {ai_schemma}.t", uc_functions.schema_tokens("c", "p", ""))


# --- register_agent -------------------------------------------------------------------------------------------------


class FakeClient:
    """A registry client with versions 2, 10 and 3 of the model, and version 7 behind every alias but `missing`."""

    def __init__(self, calls):
        self.calls = calls

    def search_model_versions(self, query):
        """Record the search and return three versions, 10 being the newest."""
        self.calls.append(("search_model_versions", query))
        return [SimpleNamespace(version="2"), SimpleNamespace(version="10"), SimpleNamespace(version="3")]

    def set_registered_model_alias(self, name, alias, version):
        """Record the alias that was set."""
        self.calls.append(("set_registered_model_alias", name, alias, version))

    def get_model_version_by_alias(self, name, alias):
        """Return version 7 with its source run, or raise for the alias `missing`."""
        self.calls.append(("get_model_version_by_alias", name, alias))
        if alias == "missing":
            raise KeyError(alias)
        return SimpleNamespace(version="7", run_id="run-7")

    def log_metric(self, run_id, key, value):
        """Record the metric that was logged."""
        self.calls.append(("log_metric", run_id, key, value))


class FakeRun:
    """A context manager standing in for `mlflow.start_run()`; it records that the run was opened."""

    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        """Record the run start."""
        self.calls.append(("start_run",))

    def __exit__(self, *exc):
        """Do not swallow exceptions."""
        return False


class FakeMlflow:
    """Records the MLflow calls the registration and evaluation scripts make."""

    def __init__(self, metrics=None):
        self.calls = []
        self.client_calls = []
        self.metrics = metrics or {}
        self.model = None

    @property
    def models(self):
        """Return `mlflow.models` with a recording `predict`."""
        return SimpleNamespace(predict=lambda **kw: self.calls.append(("predict", kw)))

    @property
    def pyfunc(self):
        """Return `mlflow.pyfunc`."""
        return SimpleNamespace(log_model=self._log_model, load_model=self._load_model)

    @property
    def tracking(self):
        """Return `mlflow.tracking`."""
        return SimpleNamespace(MlflowClient=self._client)

    @property
    def genai(self):
        """Return `mlflow.genai`."""
        return SimpleNamespace(evaluate=self._evaluate)

    @property
    def exceptions(self):
        """Return `mlflow.exceptions`, whose MlflowException is a KeyError here."""
        return SimpleNamespace(MlflowException=KeyError)

    def set_registry_uri(self, uri):
        """Record the registry URI."""
        self.calls.append(("set_registry_uri", uri))

    def set_experiment(self, *args, **kwargs):
        """Record the experiment selection."""
        self.calls.append(("set_experiment", args, kwargs))

    def start_run(self):
        """Return a context manager that records the run."""
        return FakeRun(self.calls)

    def _log_model(self, **kwargs):
        self.calls.append(("log_model", kwargs))
        return SimpleNamespace(model_uri="runs:/r1/agent")

    def _load_model(self, uri):
        self.calls.append(("load_model", uri))
        return self.model

    def _evaluate(self, **kwargs):
        self.calls.append(("evaluate", kwargs))
        return SimpleNamespace(metrics=self.metrics, run_id="eval-run")

    def _client(self, registry_uri):
        self.client_calls.append(("client", registry_uri))
        return FakeClient(self.client_calls)


class FakeResources:
    """Stands in for `mlflow.models.resources`: each class records the name it was built with."""

    def __getattr__(self, kind):
        """Return a constructor for the resource class named `kind`."""
        return lambda **kwargs: (kind, kwargs)


def register_argv(agent_file):
    """Return the registration task's arguments."""
    return [
        "--model",
        MODEL,
        "--agent-file",
        str(agent_file),
        "--experiment",
        "/Shared/x",
        "--llm-endpoint",
        "llm-1",
        "--vector-index",
        INDEX,
        "--claim-status-function",
        FUNCTION,
    ]


def test__registration_logs_the_agent_as_code_validates_it_and_sets_the_challenger(register, tmp_path):
    """The agent is logged, checked with a prediction, and then the newest version gets the challenger alias."""
    mlflow = FakeMlflow()
    agent_file = tmp_path / "policy_assistant.py"
    assert register.main(register_argv(agent_file), mlflow=mlflow, resources=FakeResources()) == 0
    kinds = [call[0] for call in mlflow.calls]
    assert kinds == ["set_registry_uri", "set_experiment", "start_run", "log_model", "predict"]
    assert mlflow.calls[4][1] == {
        "model_uri": "runs:/r1/agent",
        "input_data": register.INPUT_EXAMPLE,
        "env_manager": "uv",
    }
    assert mlflow.client_calls[-1] == ("set_registered_model_alias", MODEL, "challenger", "10")


def test__the_logged_model_ships_its_core_module_settings_and_passthrough_resources(register, tmp_path):
    """The agent file is logged with its core module, its settings and a resource for each thing it calls."""
    mlflow = FakeMlflow()
    agent_file = tmp_path / "policy_assistant.py"
    register.main(register_argv(agent_file), mlflow=mlflow, resources=FakeResources())
    logged = mlflow.calls[3][1]
    assert logged["python_model"] == str(agent_file)
    assert logged["code_paths"] == [str(tmp_path / "policy_assistant_core.py")]
    assert logged["model_config"] == {
        "llm_endpoint": "llm-1",
        "vector_index": INDEX,
        "claim_status_function": FUNCTION,
    }
    assert (logged["registered_model_name"], logged["name"]) == (MODEL, "agent")
    assert logged["pip_requirements"] == list(register.AGENT_PIP_REQUIREMENTS)
    assert logged["resources"] == [
        ("DatabricksServingEndpoint", {"endpoint_name": "llm-1"}),
        ("DatabricksVectorSearchIndex", {"index_name": INDEX}),
        ("DatabricksFunction", {"function_name": FUNCTION}),
    ]


def test__the_logged_model_configuration_is_what_the_agent_core_accepts(register):
    """The three keys the registration logs are exactly what `AgentSettings.from_mapping` requires."""
    core = load_script("policy_assistant_core", SRC / "agent/policy_assistant_core.py")
    args = SimpleNamespace(llm_endpoint="e", vector_index="i", claim_status_function="f")
    settings = core.AgentSettings.from_mapping(register.settings_from_args(args))
    assert (settings.llm_endpoint, settings.vector_index, settings.claim_status_function) == ("e", "i", "f")


# --- evaluate_agent -------------------------------------------------------------------------------------------------


def good_metrics():
    """Return evaluation metrics with the three scorers' means."""
    return {"correctness/mean": 1.0, "safety/mean": 1.0, "grounded/mean": 0.5, "latency/mean": 9.0}


class FakeScorers:
    """Stands in for `mlflow.genai.scorers`: each scorer is a tuple of its name and arguments."""

    Correctness = staticmethod(lambda: ("Correctness",))
    Safety = staticmethod(lambda: ("Safety",))
    Guidelines = staticmethod(lambda **kw: ("Guidelines", kw))


class FakeTraceLocation:
    """Stands in for `mlflow.entities.trace_location`."""

    UnityCatalog = staticmethod(lambda **kw: ("UnityCatalog", kw))


def test__a_pass_rate_is_the_mean_of_the_scorer_means_and_a_missing_scorer_is_an_error(evaluate):
    """Only the three scorers count; other `/mean` metrics such as latency do not."""
    assert evaluate.pass_rate_from_metrics(good_metrics()) == pytest.approx(2.5 / 3)
    with pytest.raises(evaluate.EvaluationError, match="safety/mean"):
        evaluate.pass_rate_from_metrics({"correctness/mean": 1.0, "grounded/mean": 1.0})


def test__the_answer_is_the_last_output_text_of_a_responses_agent_reply(evaluate):
    """Reply items other than messages (function calls and their outputs) are skipped."""
    reply = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "first"}]},
            {"type": "function_call", "name": "f", "arguments": "{}"},
            {"type": "message", "content": [{"type": "output_text", "text": "final"}]},
        ]
    }
    assert evaluate.extract_answer(reply) == "final"
    assert evaluate.extract_answer(SimpleNamespace(model_dump=lambda: reply)) == "final"
    assert evaluate.extract_answer({"output": []}) == ""


def test__the_committed_cases_load_and_have_the_shape_mlflow_expects(evaluate):
    """Every case has nested inputs and expectations, and the evaluation data keeps only those two keys."""
    cases = evaluate.load_cases(SRC / "eval/policy_assistant_cases.json")
    data = evaluate.build_eval_data(cases)
    assert len(data) == len(cases) >= 8
    assert all(set(row) == {"inputs", "expectations"} and "question" in row["inputs"] for row in data)


def test__invalid_cases_are_rejected(evaluate, tmp_path):
    """A file without cases, or a case without a question or facts, is an error that names the case."""
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(evaluate.EvaluationError, match="non-empty"):
        evaluate.load_cases(path)
    path.write_text(json.dumps({"cases": [{"id": "c1", "inputs": {"question": "q"}}]}), encoding="utf-8")
    with pytest.raises(evaluate.EvaluationError, match="c1"):
        evaluate.load_cases(path)


def eval_argv(**extra):
    """Return the evaluation task's arguments."""
    argv = ["--model", MODEL, "--cases", str(SRC / "eval/policy_assistant_cases.json"), "--experiment", "/Shared/x"]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", value]
    return argv


def answering_model(seen):
    """Return a fake registered model whose reply to every request is the text `ans`, recording the requests."""
    reply = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ans"}]}]}
    return SimpleNamespace(predict=lambda payload: seen.append(payload) or reply)


def test__evaluation_runs_the_aliased_model_and_logs_the_pass_rate_on_its_source_run(evaluate, capsys):
    """The challenger is loaded by alias, evaluated, and its pass_rate lands on the run that logged it."""
    mlflow = FakeMlflow(metrics=good_metrics())
    mlflow.model = answering_model([])
    argv = eval_argv(trace_schema="cat.example_claims_ai", warehouse_id="wh-1")
    assert evaluate.main(argv, mlflow=mlflow, scorers=FakeScorers, trace_location=FakeTraceLocation) == 0
    assert ("load_model", f"models:/{MODEL}@challenger") in mlflow.calls
    assert ("log_metric", "run-7", "pass_rate", pytest.approx(2.5 / 3)) in mlflow.client_calls
    summary = json.loads(capsys.readouterr().out)
    assert (summary["traces"], summary["version"]) == ("unity_catalog", "7") and summary["cases"] >= 8


def test__the_evaluation_calls_the_model_with_each_question_and_uses_the_three_scorers(evaluate):
    """`predict_fn` sends the question as a user message; the scorers are correctness, safety and groundedness."""
    mlflow = FakeMlflow(metrics=good_metrics())
    seen = []
    mlflow.model = answering_model(seen)
    evaluate.main(eval_argv(), mlflow=mlflow, scorers=FakeScorers, trace_location=FakeTraceLocation)
    run = [call for call in mlflow.calls if call[0] == "evaluate"][0][1]
    assert run["scorers"][2] == ("Guidelines", {"name": "grounded", "guidelines": evaluate.GROUNDED_GUIDELINES})
    assert run["predict_fn"]("What is the excess?") == {"response": "ans"}
    assert seen == [{"input": [{"role": "user", "content": "What is the excess?"}]}]


def test__traces_go_to_unity_catalog_when_a_schema_and_a_warehouse_are_given(evaluate):
    """The experiment is bound to a UC trace location and the warehouse id is exported for MLflow."""
    env = {}
    mlflow = FakeMlflow()
    destination = evaluate.TraceDestination("cat.sch", "wh-1")
    assert evaluate.configure_tracking(mlflow, "/Shared/x", destination, FakeTraceLocation, env) == "unity_catalog"
    assert env == {"MLFLOW_TRACING_SQL_WAREHOUSE_ID": "wh-1"}
    assert mlflow.calls[-1] == (
        "set_experiment",
        (),
        {
            "experiment_name": "/Shared/x",
            "trace_location": ("UnityCatalog", {"catalog_name": "cat", "schema_name": "sch"}),
        },
    )


def test__traces_use_the_experiment_and_a_warning_is_printed_without_a_warehouse(evaluate, capsys):
    """With no warehouse the experiment keeps its own trace storage, and the fallback is stated, not silent."""
    mlflow = FakeMlflow()
    destination = evaluate.TraceDestination("cat.sch", "")
    assert evaluate.configure_tracking(mlflow, "/Shared/x", destination, FakeTraceLocation, {}) == "experiment"
    assert mlflow.calls[-1] == ("set_experiment", ("/Shared/x",), {})
    assert "warning" in capsys.readouterr().err


def test__an_evaluation_without_the_scorer_metrics_fails_instead_of_logging_a_pass_rate(evaluate):
    """If MLflow does not return the expected metrics there is no pass_rate for the gate to find."""
    mlflow = FakeMlflow(metrics={"correctness/mean": 1.0})
    mlflow.model = SimpleNamespace(predict=lambda payload: {"output": []})
    with pytest.raises(evaluate.EvaluationError):
        evaluate.main(eval_argv(), mlflow=mlflow, scorers=FakeScorers, trace_location=FakeTraceLocation)
    assert not [call for call in mlflow.client_calls if call[0] == "log_metric"]


# --- deploy_agent ---------------------------------------------------------------------------------------------------


def test__deploy_serves_the_champion_version_on_the_named_endpoint(deploy, capsys):
    """`agents.deploy` is called with the model, the champion's version and an explicit endpoint name."""
    calls = []

    def agents_deploy(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(endpoint_name=kwargs["endpoint_name"], query_endpoint="https://h/serving/q")

    agents = SimpleNamespace(deploy=agents_deploy)
    argv = ["--model", MODEL, "--endpoint-name", "ep-1"]
    assert deploy.main(argv, mlflow=FakeMlflow(), agents=agents) == 0
    assert calls == [((MODEL, "7"), {"endpoint_name": "ep-1"})]
    assert json.loads(capsys.readouterr().out) == {
        "model": MODEL,
        "version": "7",
        "endpoint_name": "ep-1",
        "query_endpoint": "https://h/serving/q",
    }


def test__deploy_fails_without_a_champion_and_deploys_nothing(deploy, capsys):
    """A model with no champion alias is an error, and agents.deploy is never called."""
    agents = SimpleNamespace(deploy=lambda *a, **k: pytest.fail("must not deploy"))
    argv = ["--model", MODEL, "--endpoint-name", "ep-1", "--alias", "missing"]
    assert deploy.main(argv, mlflow=FakeMlflow(), agents=agents) == 1
    assert "no version with the alias @missing" in capsys.readouterr().err
