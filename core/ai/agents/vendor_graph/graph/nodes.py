from __future__ import annotations

from typing import Literal

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import END

from core.ai.agents.vendor_graph.graph.state import MessagesState
from core.ai.agents.vendor_graph.graph.tools import TOOLS_BY_NAME, MODEL_WITH_TOOLS


SYSTEM_PROMPT = "You are a helpful assistant tasked with performing arithmetic on a set of inputs."


# Define the Model Node
def llm_call(state: dict):
    """LLM decides whether to call a tool or not"""
    result_msg = MODEL_WITH_TOOLS.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT)
        ]
        + state["messages"]
    )
    return {
        "messages": [result_msg],
        "llm_calls": state.get('llm_calls', 0) + 1
    }


# Define the Tool Node
def tool_node(state: MessagesState) -> dict:
    """Performs the tool call from the last assistant message"""
    result = []
    last = state["messages"][-1]

    for tool_call in last.tool_calls:
        tool = TOOLS_BY_NAME[tool_call["name"]]
        observation = tool.invoke(tool_call["args"])
        result.append(
            ToolMessage(
                content=str(observation),
                tool_call_id=tool_call["id"]
            )
        )

    return {"messages": result}


def should_continue(state: MessagesState) -> Literal["tool_node", END]:
    """Decide if we should continue the loop or stop based upon whether the LLM made a tool call."""
    last_msg = state["messages"][-1]
    if getattr(last_msg, "tool_calls", None):
        return "tool_node"
    return END
