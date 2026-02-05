from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from core.ai.agents.vendor_graph.graph.state import MessagesState
from core.ai.agents.vendor_graph.graph.nodes import llm_call, tool_node, should_continue

MAX_LLM_CALLS = 10


def build_agent():
    builder = StateGraph(MessagesState)
    builder.add_node("llm_call", llm_call)
    builder.add_node("tool_node", tool_node)
    
    builder.add_edge(START, "llm_call")
    builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
    builder.add_edge("tool_node", "llm_call")

    return builder.compile()

agent = build_agent()


if __name__ == "__main__":
    # Optional: visualize
    try:
        print(agent.get_graph().draw_ascii())
    except Exception:
        print(
            """
    [START]
        |
        v
   [llm_call] --(tool_calls?)--> [tool_node]
        |                              |
        |(no tool_calls)               |
        v                              v
     [END]  <-------------------------+
    """
        )

    # Demo invoke
    from langchain_core.messages import HumanMessage

    messages = [HumanMessage(content="Divide 10 by 2.")]
    out = agent.invoke({"messages": messages})

    for m in out["messages"]:
        m.pretty_print()

