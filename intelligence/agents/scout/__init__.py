"""Scout agent package.

Importing this package triggers registration of scout and its tools:

  1. Entity tool modules self-register their tools when imported.
  2. scout's definition self-registers the agent.

Callers that want scout available should `import intelligence.agents.scout`
(or rely on run_agent() which imports it via the registry lookup path).
"""
# Import entity tool modules so their tools land in the tool registry.
# Add imports here as scout's scope expands to additional entities.
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.cost_code.intelligence.tools  # noqa: F401

# Import sub-agents BEFORE scout so their tools are registered when
# scout's definition resolves its delegation tool name.
import intelligence.agents.sub_cost_code_specialist  # noqa: F401

# Register the delegation tool that hands sub-cost-code work off to the
# specialist. Today scout still has the direct tools alongside; phase 1B
# removes the direct tools and tightens scout's role.
from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

_register_tool(make_delegation_tool(
    name="delegate_to_sub_cost_code",
    target_agent="sub_cost_code_specialist",
    description=(
        "Hand a sub-cost-code task off to the SubCostCode specialist "
        "agent. Use for any sub-cost-code lookup, search, create, "
        "update, or delete. Pass the user's request verbatim (or a "
        "clarified version that captures all needed context — the "
        "specialist starts with no memory of this conversation). The "
        "specialist returns a final answer string; relay it to the user."
    ),
))

# Register the scout agent.
from intelligence.agents.scout.definition import scout  # noqa: F401
