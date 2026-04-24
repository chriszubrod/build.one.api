"""Scout agent package — pure orchestrator that delegates to specialists.

Importing this package triggers registration of:
  1. Sub-agents (whose own __init__ files bring in their entity tools).
  2. The delegation tool(s) scout uses to dispatch to those sub-agents.
  3. Scout itself.

Scout has no direct entity tools; every entity operation flows through a
delegation. Specialists own per-entity RBAC; scout's role only needs to
log in.

Callers that want scout available should `import intelligence.agents.scout`
(or rely on run_agent() which imports it via the registry lookup path).
"""
# Sub-agents register themselves AND the entity tools they wrap.
import intelligence.agents.sub_cost_code_specialist  # noqa: F401

from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

# One delegation tool per specialist. As the fleet grows, add a line here
# (and update scout.tools in definition.py) per new specialist.
_register_tool(make_delegation_tool(
    name="delegate_to_sub_cost_code",
    target_agent="sub_cost_code_specialist",
    description=(
        "Hand a sub-cost-code task off to the SubCostCode specialist "
        "agent. Use for ANY sub-cost-code work — lookups, searches, "
        "creates, updates, deletes, and parent CostCode resolution. "
        "Pass the user's request verbatim, or a clarified version that "
        "captures all needed context (the specialist starts with no "
        "memory of this conversation). The specialist returns a final "
        "answer as markdown, often including a record card; relay it "
        "to the user per the rules in the system prompt."
    ),
))

# Register the scout agent.
from intelligence.agents.scout.definition import scout  # noqa: F401
