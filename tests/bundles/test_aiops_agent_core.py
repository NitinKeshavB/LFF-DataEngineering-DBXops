"""Tests for the policy assistant's framework-free logic (bundles/aiops/src/agent/policy_assistant_core.py)."""

import ast
import json

import pytest

from tests.bundles.aiops_helpers import (
    SRC,
    load_script,
)


@pytest.fixture(scope="module")
def core():
    """Import the core module; it must need neither mlflow nor langgraph."""
    return load_script("policy_assistant_core", SRC / "agent/policy_assistant_core.py")


@pytest.fixture
def settings(core):
    """Return agent settings with a small round limit so the limit is easy to reach."""
    return core.AgentSettings("llm", "c.s.index", "c.s.get_claim_status", num_results=3, max_tool_rounds=2)


def user(text):
    """Return a user message."""
    return {"role": "user", "content": text}


def call(call_id, name, **arguments):
    """Return one tool call of an assistant message."""
    return {"id": call_id, "name": name, "arguments": arguments}


def run_graph(core, settings, messages, llm, tools):
    """Drive the nodes with the same edges the LangGraph graph has: agent, then tools and back, until `end`."""
    state = {"messages": list(messages)}
    while True:
        state["messages"] += core.agent_node(state, llm)["messages"]
        if core.route_after_agent(state, settings) == "end":
            return state["messages"]
        state["messages"] += core.tools_node(state, tools)["messages"]


class ScriptedLlm:
    """A fake chat model that returns prepared replies in order and remembers what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, messages):
        """Return the next prepared reply."""
        self.seen.append(list(messages))
        return self.replies.pop(0)


def test__the_core_module_imports_no_framework(core):
    """The core imports only the standard library, so it runs where mlflow and langgraph are not installed."""
    tree = ast.parse((SRC / "agent/policy_assistant_core.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "json", "re", "dataclasses", "typing"}
    assert core.AgentSettings


def test__settings_round_trip_and_apply_defaults(core):
    """The model configuration of the three required keys builds settings with the default limits."""
    config = {"llm_endpoint": "e", "vector_index": "i", "claim_status_function": "f"}
    settings = core.AgentSettings.from_mapping(config)
    assert (settings.num_results, settings.max_tool_rounds) == (4, 3)
    assert core.AgentSettings.from_mapping(settings.to_dict()) == settings


@pytest.mark.parametrize(
    "config, message",
    [
        ({}, "llm_endpoint, vector_index, claim_status_function"),
        ({"llm_endpoint": "e", "vector_index": "i"}, "claim_status_function"),
        ({"llm_endpoint": "e", "vector_index": "i", "claim_status_function": "f", "num_results": 0}, "num_results"),
        ({"llm_endpoint": "e", "vector_index": "i", "claim_status_function": "f", "max_tool_rounds": "3"}, "max_tool"),
    ],
)
def test__settings_reject_missing_keys_and_bad_numbers(core, config, message):
    """A missing key or a non-positive number is reported by name."""
    with pytest.raises(core.SettingsError, match=message):
        core.AgentSettings.from_mapping(config)


def test__claim_ids_are_found_case_insensitively_without_duplicates(core):
    """Claim ids are CLM plus seven digits; the same id twice is returned once, upper-case."""
    text = "Compare clm0000012 with CLM0000034, and again CLM0000012; CLM12 and CLM00000345 are not claim ids."
    assert core.extract_claim_ids(text) == ["CLM0000012", "CLM0000034"]


def test__the_routing_hint_names_the_claims_and_is_absent_for_policy_questions(core):
    """A question with a claim id gets a hint to use the claim status function; one without gets none."""
    assert "CLM0000001" in core.routing_hint("What is the status of claim CLM0000001?")
    assert core.routing_hint("What is the excess on motor cover?") is None


def test__the_system_prompt_adds_the_hint_for_the_latest_question_only(core):
    """The hint follows the latest user message, not an earlier one."""
    earlier = [user("Status of CLM0000001?"), {"role": "assistant", "content": "Open."}, user("And the motor excess?")]
    assert core.build_system_prompt(earlier) == core.SYSTEM_PROMPT
    latest = [user("Motor excess?"), {"role": "assistant", "content": "$800."}, user("Status of CLM0000002?")]
    assert core.build_system_prompt(latest).endswith("look each one up with the claim status function.")


def test__text_of_flattens_content_blocks(core):
    """Model replies may be a string, a list of blocks or empty."""
    assert core.text_of("a") == "a"
    assert core.text_of([{"type": "text", "text": "a"}, "b", {"type": "tool_use", "id": "x"}]) == "ab"
    assert core.text_of(None) == ""


def test__chat_messages_are_normalised_to_the_neutral_shape(core):
    """OpenAI-style tool calls (arguments as a JSON string) become dict arguments; tool results keep their ids."""
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"claim_id": "CLM1"}'}}
        ],
    }
    assert core.normalize_message(assistant) == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "name": "f", "arguments": {"claim_id": "CLM1"}}],
    }
    tool = {"role": "tool", "tool_call_id": "c1", "content": [{"text": "ok"}]}
    assert core.normalize_message(tool) == {"role": "tool", "tool_call_id": "c1", "name": "", "content": "ok"}
    assert core.normalize_message(user("hi")) == user("hi")


def test__a_model_reply_becomes_an_assistant_message_with_its_tool_calls(core):
    """LangChain-style calls (`args`) are converted, and a reply without calls has no tool_calls key."""
    reply = core.ai_message_to_dict(
        [{"type": "text", "text": "Looking"}], [{"id": "c1", "name": "f", "args": {"a": 1}}]
    )
    assert reply == {"role": "assistant", "content": "Looking", "tool_calls": [call("c1", "f", a=1)]}
    assert core.ai_message_to_dict("done", []) == {"role": "assistant", "content": "done"}


def test__the_agent_node_sends_the_system_prompt_first_without_storing_it(core):
    """The model sees the system prompt and the conversation; the node returns only the model's new message."""
    llm = ScriptedLlm({"role": "assistant", "content": "ok"})
    out = core.agent_node({"messages": [user("Status of CLM0000007?")]}, llm)
    assert out == {"messages": [{"role": "assistant", "content": "ok"}]}
    sent = llm.seen[0]
    assert (
        sent[0]["role"] == "system" and "CLM0000007" in sent[0]["content"] and sent[1] == user("Status of CLM0000007?")
    )


def test__the_tools_node_runs_each_call_and_reports_failures_to_the_model(core):
    """A tool result is text; an unknown tool and a tool that raises both come back as error text, not exceptions."""

    def boom(_):
        raise ValueError("bad id")

    tools = {"lookup": lambda args: {"status": args["claim_id"]}, "boom": boom}
    calls = [call("1", "lookup", claim_id="CLM1"), call("2", "boom"), call("3", "missing")]
    out = core.tools_node({"messages": [{"role": "assistant", "content": "", "tool_calls": calls}]}, tools)["messages"]
    assert [m["tool_call_id"] for m in out] == ["1", "2", "3"]
    assert json.loads(out[0]["content"]) == {"status": "CLM1"}
    assert "boom failed: bad id" in out[1]["content"]
    assert "no tool named 'missing'" in out[2]["content"] and "boom, lookup" in out[2]["content"]


def test__routing_continues_to_tools_until_the_round_limit(core, settings):
    """Tool calls are followed while the rounds used are within the limit, and stop one round later."""
    asked = {"role": "assistant", "content": "", "tool_calls": [call("1", "f")]}
    assert core.route_after_agent({"messages": [user("q"), asked]}, settings) == "tools"
    assert core.route_after_agent({"messages": [user("q"), {"role": "assistant", "content": "a"}]}, settings) == "end"
    too_many = [user("q"), asked, asked, asked]
    assert core.tool_rounds(too_many) == 3
    assert core.route_after_agent({"messages": too_many}, settings) == "end"


def test__sources_come_from_tool_results_without_duplicates(core):
    """Policy wording file names in tool results are collected in order; claim data adds none."""
    messages = [
        user("q"),
        {
            "role": "tool",
            "tool_call_id": "1",
            "name": "r",
            "content": "mtr_policy_wording.pdf#0001 and hom_policy_wording.pdf#0002",
        },
        {"role": "tool", "tool_call_id": "2", "name": "r", "content": "mtr_policy_wording.pdf#0003"},
        {"role": "tool", "tool_call_id": "3", "name": "f", "content": '{"status": "OPEN"}'},
    ]
    assert core.collect_sources(messages) == ["mtr_policy_wording.pdf", "hom_policy_wording.pdf"]


def test__the_final_answer_lists_sources_and_falls_back_when_empty(core):
    """Sources are appended when wordings were retrieved; an empty or unfinished answer gets the fallback."""
    tool = {"role": "tool", "tool_call_id": "1", "name": "r", "content": "trv_policy_wording.pdf#0001"}
    assert (
        core.shape_answer([user("q"), tool, {"role": "assistant", "content": " $250. "}])
        == "$250.\n\nSources: trv_policy_wording.pdf"
    )
    assert core.shape_answer([user("q"), {"role": "assistant", "content": "Hello."}]) == "Hello."
    assert core.shape_answer([user("q"), {"role": "assistant", "content": ""}]) == core.FALLBACK_ANSWER
    unfinished = {"role": "assistant", "content": "x", "tool_calls": [call("1", "f")]}
    assert core.shape_answer([user("q"), unfinished]) == core.FALLBACK_ANSWER
    assert core.shape_answer([]) == core.FALLBACK_ANSWER


def test__a_policy_question_runs_retrieval_then_answers_with_sources(core, settings):
    """End to end with fakes: the model calls the retriever, reads the passage, and answers; output items follow."""
    llm = ScriptedLlm(
        {"role": "assistant", "content": "", "tool_calls": [call("c1", "search", query="motor excess")]},
        {"role": "assistant", "content": "The standard excess is $800."},
    )
    tools = {"search": lambda args: f"mtr_policy_wording.pdf#0001 The standard excess is $800 ({args['query']})"}
    start = [user("What is the motor excess?")]
    final = run_graph(core, settings, start, llm, tools)
    assert [m["role"] for m in final] == ["user", "assistant", "tool", "assistant"]
    assert "motor excess" in final[2]["content"]
    specs = core.output_specs(final[1:], final)
    assert [s["kind"] for s in specs] == ["function_call", "function_call_output", "text"]
    assert specs[0]["arguments"] == '{"query": "motor excess"}' and specs[0]["call_id"] == "c1"
    assert specs[2]["text"] == "The standard excess is $800.\n\nSources: mtr_policy_wording.pdf"


def test__a_claim_question_gets_the_hint_and_calls_the_function(core, settings):
    """The model is told to look the claim up, and the function result is what it answers from."""
    llm = ScriptedLlm(
        {"role": "assistant", "content": "", "tool_calls": [call("c1", "get_claim_status", claim_id="CLM0000001")]},
        {"role": "assistant", "content": "Claim CLM0000001 is OPEN."},
    )
    tools = {"get_claim_status": lambda args: [{"claim_id": args["claim_id"], "status": "OPEN"}]}
    final = run_graph(core, settings, [user("What is the status of claim CLM0000001?")], llm, tools)
    assert "look each one up" in llm.seen[0][0]["content"]
    assert json.loads(final[2]["content"]) == [{"claim_id": "CLM0000001", "status": "OPEN"}]
    assert final[-1]["content"] == "Claim CLM0000001 is OPEN."


def test__a_model_that_never_stops_calling_tools_ends_with_the_fallback_and_no_dangling_call(core, settings):
    """After the round limit the loop ends; the unanswered tool call is dropped and the fallback text is returned."""
    looping = {"role": "assistant", "content": "", "tool_calls": [call("c", "search", query="x")]}
    llm = ScriptedLlm(*[dict(looping) for _ in range(5)])
    final = run_graph(core, settings, [user("q")], llm, {"search": lambda args: "nothing"})
    specs = core.output_specs(final[1:], final)
    assert specs[-1] == {"kind": "text", "id": f"msg_{len(final) - 2}", "text": core.FALLBACK_ANSWER}
    assert {s["kind"] for s in specs[:-1]} <= {"function_call", "function_call_output"}
    assert sum(s["kind"] == "function_call" for s in specs) == sum(s["kind"] == "function_call_output" for s in specs)
