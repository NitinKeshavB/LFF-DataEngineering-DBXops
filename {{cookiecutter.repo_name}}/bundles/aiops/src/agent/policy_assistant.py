"""
The policy assistant as an MLflow ResponsesAgent over a LangGraph graph, logged "from code" by `register_agent.py`.

This file is the thin framework-bound part: it builds the chat model, the tools (the vector search retriever and the
`get_claim_status` UC function) and the graph, and turns the result into response output items. The logic lives in
`policy_assistant_core.py`, which has no framework imports and is unit-tested.

Tracing follows the instrumenting-with-mlflow-tracing skill: `mlflow.langchain.autolog()` is called before the
langchain libraries are imported and then traces the LangGraph nodes, model calls and tools (the vector search tool
records a RETRIEVER span), so no node is decorated by hand. The langchain libraries are therefore imported inside
`build_agent`, through `importlib`, after the autolog call.

The logged model's configuration (`AgentSettings.to_dict()`) is read through `mlflow.models.ModelConfig`; set
`POLICY_ASSISTANT_CONFIG` to a JSON or YAML file to run the agent outside a logged model.

Loading this file needs mlflow, langgraph, langchain-core and databricks-langchain, which the job environments
install; they are not dependencies of the dbxops package.
"""

import importlib
import operator
import os
from typing import (
    Annotated,
    Any,
    Callable,
    Generator,
    Sequence,
    TypedDict,
)

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    to_chat_completions_input,
)
from policy_assistant_core import (
    RETRIEVER_COLUMNS,
    AgentSettings,
    Llm,
    Message,
    Tool,
    agent_node,
    ai_message_to_dict,
    normalize_message,
    output_specs,
    route_after_agent,
    tools_node,
)


class State(TypedDict):
    """The graph state: the conversation, to which each node appends its new messages."""

    messages: Annotated[list, operator.add]


def _to_langchain(messages: Sequence[Message]) -> list:
    """Convert neutral messages to LangChain message objects for the chat model."""
    lc = importlib.import_module("langchain_core.messages")
    converted: list = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            calls = [{"id": c["id"], "name": c["name"], "args": c["arguments"]} for c in message.get("tool_calls", [])]
            converted.append(lc.AIMessage(content=message["content"], tool_calls=calls))
        elif role == "tool":
            converted.append(lc.ToolMessage(content=message["content"], tool_call_id=message["tool_call_id"]))
        elif role == "system":
            converted.append(lc.SystemMessage(content=message["content"]))
        else:
            converted.append(lc.HumanMessage(content=message["content"]))
    return converted


def build_tools(settings: AgentSettings) -> list:
    """Return the LangChain tools: the claim status UC function and the policy wording retriever."""
    databricks_langchain = importlib.import_module("databricks_langchain")
    functions = databricks_langchain.UCFunctionToolkit(function_names=[settings.claim_status_function]).tools
    retriever = databricks_langchain.VectorSearchRetrieverTool(
        index_name=settings.vector_index, num_results=settings.num_results, columns=list(RETRIEVER_COLUMNS)
    )
    return [*functions, retriever]


def build_llm(settings: AgentSettings, tools: Sequence[Any]) -> Llm:
    """Return a function that sends neutral messages to the chat endpoint with the tools bound."""
    databricks_langchain = importlib.import_module("databricks_langchain")
    model = databricks_langchain.ChatDatabricks(endpoint=settings.llm_endpoint, temperature=0.1).bind_tools(tools)

    def call(messages: Sequence[Message]) -> Message:
        reply = model.invoke(_to_langchain(messages))
        return ai_message_to_dict(reply.content, reply.tool_calls)

    return call


def build_graph(settings: AgentSettings, llm: Llm, tools: dict[str, Tool]) -> Any:
    """Compile the LangGraph graph `agent -> (tools -> agent)* -> end` from the core node functions."""
    graph_module = importlib.import_module("langgraph.graph")
    runnables = importlib.import_module("langchain_core.runnables")
    graph = graph_module.StateGraph(State)
    graph.add_node("agent", runnables.RunnableLambda(lambda state: agent_node(state, llm)))
    graph.add_node("tools", runnables.RunnableLambda(lambda state: tools_node(state, tools)))
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent", lambda state: route_after_agent(state, settings), {"tools": "tools", "end": graph_module.END}
    )
    graph.add_edge("tools", "agent")
    return graph.compile()


class PolicyAssistant(ResponsesAgent):
    """Answers policy wording questions and claim status questions; see the module docstring."""

    def __init__(self, settings: AgentSettings, graph: Any):
        super().__init__()
        self.settings = settings
        self.graph = graph

    def _item(self, spec: dict[str, Any]) -> dict[str, Any]:
        if spec["kind"] == "text":
            return self.create_text_output_item(text=spec["text"], id=spec["id"])
        if spec["kind"] == "function_call":
            return self.create_function_call_item(
                id=spec["id"], call_id=spec["call_id"], name=spec["name"], arguments=spec["arguments"]
            )
        return self.create_function_call_output_item(call_id=spec["call_id"], output=spec["output"])

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """Run the graph and yield each message it produced as a finished output item."""
        incoming = to_chat_completions_input([item.model_dump() for item in request.input])
        messages = [normalize_message(message) for message in incoming]
        final = self.graph.invoke({"messages": messages})["messages"]
        first_new = len(messages)
        for spec in output_specs(final[first_new:], final):
            yield ResponsesAgentStreamEvent(type="response.output_item.done", item=self._item(spec))

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        """Return every output item the stream produced."""
        items = [event.item for event in self.predict_stream(request) if event.type == "response.output_item.done"]
        return ResponsesAgentResponse(output=items)


def _tool_runner(tool: Any) -> Callable[[dict[str, Any]], Any]:
    return tool.invoke


def build_agent(config: dict[str, Any]) -> PolicyAssistant:
    """Build the agent from a model configuration: settings, tools, chat model and graph."""
    settings = AgentSettings.from_mapping(config)
    tools = build_tools(settings)
    graph = build_graph(settings, build_llm(settings, tools), {tool.name: _tool_runner(tool) for tool in tools})
    return PolicyAssistant(settings, graph)


mlflow.langchain.autolog()
mlflow.models.set_model(
    build_agent(mlflow.models.ModelConfig(development_config=os.environ.get("POLICY_ASSISTANT_CONFIG")).to_dict())
)
