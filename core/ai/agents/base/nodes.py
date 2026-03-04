"""
Base Agent Graph Nodes

Factory functions that create reusable graph nodes for LangGraph agents.
Extracted from the vendor agent pattern at core/ai/agents/vendor_agent/graph/nodes.py.
"""
from __future__ import annotations

import logging
from typing import Callable, Literal

from langchain_core.messages import SystemMessage, ToolMessage, AIMessage

logger = logging.getLogger(__name__)


def make_setup_context(context_cls, system_prompt: str):
    """
    Create a setup_context node that initializes tool context from state.

    Args:
        context_cls: A class with a .set() classmethod (e.g., AgentToolContext)
        system_prompt: The system prompt to inject if messages are empty
    """
    def setup_context(state: dict) -> dict:
        context_cls.set(
            tenant_id=state["tenant_id"],
            agent_run_id=state.get("agent_run_id"),
            user_id=state.get("user_id"),
        )
        if not state["messages"]:
            return {"messages": [SystemMessage(content=system_prompt)]}
        return {}

    return setup_context


def make_llm_call_node(get_model_fn: Callable, system_prompt: str):
    """
    Create an llm_call node that invokes the model.

    Args:
        get_model_fn: Callable that returns a LangChain chat model (with tools bound)
        system_prompt: Fallback system prompt if not already in messages
    """
    def llm_call(state: dict) -> dict:
        model = get_model_fn()
        messages = state["messages"]

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + messages

        try:
            result_msg = model.invoke(messages)
            return {
                "messages": [result_msg],
                "llm_calls": state.get("llm_calls", 0) + 1,
            }
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            error_msg = AIMessage(content=f"Error calling LLM: {str(e)}")
            return {
                "messages": [error_msg],
                "llm_calls": state.get("llm_calls", 0) + 1,
                "errors": state.get("errors", []) + [str(e)],
            }

    return llm_call


def make_tool_node(tools_by_name: dict):
    """
    Create a tool_node that executes tool calls from the last AI message.

    Args:
        tools_by_name: Dict mapping tool name -> LangChain tool object
    """
    def tool_node(state: dict) -> dict:
        last = state["messages"][-1]

        if not getattr(last, "tool_calls", None):
            return {"messages": []}

        result = []
        for tool_call in last.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            logger.info(f"Executing tool: {tool_name}")

            try:
                tool = tools_by_name.get(tool_name)
                if not tool:
                    observation = f"Error: Unknown tool '{tool_name}'"
                else:
                    observation = tool.invoke(tool_args)
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                observation = f"Error executing tool: {str(e)}"

            result.append(
                ToolMessage(
                    content=str(observation),
                    tool_call_id=tool_call["id"],
                )
            )

        return {"messages": result}

    return tool_node


def make_should_continue(max_calls: int = 100):
    """
    Create a routing function: tool_calls present -> "tool_node", else -> "check_complete".

    Args:
        max_calls: Maximum LLM calls before forcing completion
    """
    def should_continue(state: dict) -> Literal["tool_node", "check_complete"]:
        if state.get("llm_calls", 0) >= max_calls:
            logger.warning(f"Reached max LLM calls ({max_calls})")
            return "check_complete"

        last_msg = state["messages"][-1] if state["messages"] else None
        if last_msg and getattr(last_msg, "tool_calls", None):
            return "tool_node"

        return "check_complete"

    return should_continue


def make_check_complete(end_condition_fn: Callable = None):
    """
    Create a completion check routing function.

    Args:
        end_condition_fn: Optional callable(state) -> bool.
            If None, defaults to ending on interactive mode or when
            the LLM's last message has no tool calls.
    """
    def check_complete(state: dict) -> Literal["llm_call", "__end__"]:
        mode = state.get("mode", "interactive")

        # Interactive mode: end after one response (no tool calls)
        if mode == "interactive":
            return "__end__"

        # Custom end condition
        if end_condition_fn and end_condition_fn(state):
            return "__end__"

        # Batch mode: check for completion keywords
        last_msg = state["messages"][-1] if state["messages"] else None
        if last_msg and isinstance(last_msg, AIMessage):
            content = last_msg.content.lower() if last_msg.content else ""
            if any(word in content for word in ["complete", "finished", "done", "processed all"]):
                return "__end__"

        return "llm_call"

    return check_complete
