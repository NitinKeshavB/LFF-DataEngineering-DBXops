"""
The policy assistant's own logic, with no mlflow, langchain or langgraph import.

The framework-bound wrapper in `policy_assistant.py` wires these functions into a LangGraph graph and a
ResponsesAgent. Everything here works on plain dict messages, so it runs and is unit-tested without those libraries:

    {"role": "user" | "system", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}

The graph is `agent -> (tools -> agent)* -> end`. `agent_node` asks the model, `tools_node` runs the tools it
called, and `route_after_agent` decides between them, stopping after `max_tool_rounds` rounds of tool calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Mapping,
    Sequence,
)

Message = dict[str, Any]
State = Mapping[str, Any]
Llm = Callable[[Sequence[Message]], Message]
Tool = Callable[[dict[str, Any]], Any]

RETRIEVER_COLUMNS = ("chunk_id", "chunk_text", "document_name")
CLAIM_ID = re.compile(r"\bCLM\d{7}\b", re.IGNORECASE)
SOURCE_FILE = re.compile(r"\b[a-z0-9_]+_policy_wording\.pdf\b")
TOOL_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)  # a failing tool is reported to the model
FALLBACK_ANSWER = "I could not complete that lookup. Please rephrase the question or try again."
SYSTEM_PROMPT = (
    "You are a policy assistant for an insurer. Answer questions about policy wordings from the retrieved wording "
    "passages only, and quote the figure or time limit when the passage gives one. Look up a claim's status with the "
    "claim status function, never from memory. If the passages or the function do not answer the question, say that "
    "you do not know instead of guessing."
)


class SettingsError(ValueError):
    """Raised when the agent's model configuration is missing a key or has a wrong value."""


@dataclass(frozen=True)
class AgentSettings:
    """What the agent needs to run: the chat endpoint, the vector index, the UC function and loop limits."""

    llm_endpoint: str
    vector_index: str
    claim_status_function: str
    num_results: int = 4
    max_tool_rounds: int = 3

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> AgentSettings:
        """Build settings from the logged model configuration; raises `SettingsError` on a bad or missing value."""
        required = ("llm_endpoint", "vector_index", "claim_status_function")
        missing = [key for key in required if not config.get(key)]
        if missing:
            raise SettingsError(f"model configuration is missing: {', '.join(missing)}")
        numbers = {key: config[key] for key in ("num_results", "max_tool_rounds") if key in config}
        for key, value in numbers.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise SettingsError(f"{key} must be a positive integer, got {value!r}")
        return cls(
            llm_endpoint=str(config["llm_endpoint"]),
            vector_index=str(config["vector_index"]),
            claim_status_function=str(config["claim_status_function"]),
            **numbers,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the settings as the model configuration that `from_mapping` reads back."""
        return {
            "llm_endpoint": self.llm_endpoint,
            "vector_index": self.vector_index,
            "claim_status_function": self.claim_status_function,
            "num_results": self.num_results,
            "max_tool_rounds": self.max_tool_rounds,
        }


def extract_claim_ids(text: str) -> list[str]:
    """Return the claim ids (CLM plus seven digits) in the text, upper-cased and without duplicates, in order."""
    return list(dict.fromkeys(match.upper() for match in CLAIM_ID.findall(text)))


def routing_hint(question: str) -> str | None:
    """Return an instruction that names the claim ids to look up, or None when the question has none."""
    claim_ids = extract_claim_ids(question)
    if not claim_ids:
        return None
    return f"The question mentions claim {', '.join(claim_ids)}: look each one up with the claim status function."


def text_of(content: Any) -> str:
    """Return the text of a model reply, which is a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block if isinstance(block, str) else str(block.get("text", "")) for block in content]
        return "".join(parts)
    return "" if content is None else str(content)


def last_user_question(messages: Sequence[Message]) -> str:
    """Return the text of the latest user message, or the empty string."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return text_of(message.get("content"))
    return ""


def build_system_prompt(messages: Sequence[Message]) -> str:
    """Return the system prompt, with the routing hint for the latest question when it names a claim."""
    hint = routing_hint(last_user_question(messages))
    return SYSTEM_PROMPT if hint is None else f"{SYSTEM_PROMPT}\n\n{hint}"


def _tool_call(raw: Mapping[str, Any]) -> dict[str, Any]:
    function = raw.get("function", raw)
    arguments = function.get("arguments", function.get("args", {}))
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    return {"id": raw.get("id") or raw.get("call_id", ""), "name": function["name"], "arguments": arguments}


def normalize_message(message: Mapping[str, Any]) -> Message:
    """Return an OpenAI-style chat message (tool call arguments as a JSON string) in the neutral message shape."""
    role = message.get("role")
    content = text_of(message.get("content"))
    if role == "assistant" and message.get("tool_calls"):
        return {"role": role, "content": content, "tool_calls": [_tool_call(call) for call in message["tool_calls"]]}
    if role == "tool":
        return {
            "role": role,
            "tool_call_id": message.get("tool_call_id", ""),
            "name": message.get("name", ""),
            "content": content,
        }
    return {"role": role, "content": content}


def ai_message_to_dict(content: Any, tool_calls: Sequence[Mapping[str, Any]] | None) -> Message:
    """Convert a model reply (text or blocks, and tool calls as `{id, name, args}`) to an assistant message."""
    message: Message = {"role": "assistant", "content": text_of(content)}
    if tool_calls:
        message["tool_calls"] = [_tool_call(call) for call in tool_calls]
    return message


def tool_rounds(messages: Sequence[Message]) -> int:
    """Return how many assistant messages asked for tools."""
    return sum(1 for message in messages if message.get("role") == "assistant" and message.get("tool_calls"))


def agent_node(state: State, llm: Llm) -> dict[str, list[Message]]:
    """Ask the model for its next message, given the system prompt and the conversation so far."""
    messages = list(state["messages"])
    system = {"role": "system", "content": build_system_prompt(messages)}
    return {"messages": [llm([system, *messages])]}


def format_tool_result(value: Any) -> str:
    """Return a tool result as text: strings as they are, anything else as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _run_tool(tools: Mapping[str, Tool], call: Mapping[str, Any]) -> str:
    tool = tools.get(call["name"])
    if tool is None:
        return f"Error: there is no tool named {call['name']!r}; available: {', '.join(sorted(tools))}"
    try:
        return format_tool_result(tool(call["arguments"]))
    except TOOL_ERRORS as error:
        return f"Error: {call['name']} failed: {error}"


def tools_node(state: State, tools: Mapping[str, Tool]) -> dict[str, list[Message]]:
    """Run every tool the last assistant message called and return one tool message per call."""
    calls = state["messages"][-1].get("tool_calls", [])
    return {
        "messages": [
            {"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": _run_tool(tools, call)}
            for call in calls
        ]
    }


def route_after_agent(state: State, settings: AgentSettings) -> str:
    """Return `tools` when the model asked for tools and the round limit allows it, else `end`."""
    messages = state["messages"]
    if messages[-1].get("tool_calls") and tool_rounds(messages) <= settings.max_tool_rounds:
        return "tools"
    return "end"


def collect_sources(messages: Sequence[Message]) -> list[str]:
    """Return the policy wording file names that appear in tool results, without duplicates, in order."""
    found = [
        name
        for message in messages
        if message.get("role") == "tool"
        for name in SOURCE_FILE.findall(message["content"])
    ]
    return list(dict.fromkeys(found))


def shape_answer(messages: Sequence[Message]) -> str:
    """Return the final answer text, with a `Sources:` line when policy wordings were retrieved."""
    final = messages[-1] if messages else {}
    answer = text_of(final.get("content")).strip() if final.get("role") == "assistant" else ""
    if not answer or final.get("tool_calls"):
        return FALLBACK_ANSWER
    sources = collect_sources(messages)
    return f"{answer}\n\nSources: {', '.join(sources)}" if sources else answer


def _settle(messages: Sequence[Message]) -> list[Message]:
    """Drop the tool calls of a final assistant message: the loop ended before they ran, so they have no result."""
    if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
        return [*messages[:-1], {"role": "assistant", "content": ""}]
    return list(messages)


def _assistant_specs(message: Message, index: int, text: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [{"kind": "text", "id": f"msg_{index}", "text": text}] if text else []
    for call in message.get("tool_calls", []):
        arguments = json.dumps(call["arguments"])
        specs.append(
            {
                "kind": "function_call",
                "id": f"fc_{call['id']}",
                "call_id": call["id"],
                "name": call["name"],
                "arguments": arguments,
            }
        )
    return specs


def output_specs(new_messages: Sequence[Message], all_messages: Sequence[Message]) -> list[dict[str, Any]]:
    """
    Describe the new messages as response output items, in order.

    `all_messages` is the whole conversation, used to attach sources to the final answer. Each spec has a `kind`:
    `text` (`id`, `text`), `function_call` (`id`, `call_id`, `name`, `arguments` as a JSON string) or
    `function_call_output` (`call_id`, `output`).
    """
    new_messages, all_messages = _settle(new_messages), _settle(all_messages)
    specs: list[dict[str, Any]] = []
    for index, message in enumerate(new_messages):
        if message["role"] == "tool":
            specs.append(
                {"kind": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]}
            )
        elif message["role"] == "assistant":
            is_final = index == len(new_messages) - 1
            text = shape_answer(all_messages) if is_final else text_of(message["content"])
            specs.extend(_assistant_specs(message, index, text))
    return specs
