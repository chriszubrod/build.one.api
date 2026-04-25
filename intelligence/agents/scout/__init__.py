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
import intelligence.agents.cost_code_specialist  # noqa: F401
import intelligence.agents.customer_specialist  # noqa: F401
import intelligence.agents.project_specialist  # noqa: F401
import intelligence.agents.vendor_specialist  # noqa: F401
import intelligence.agents.bill_specialist  # noqa: F401
import intelligence.agents.bill_credit_specialist  # noqa: F401

from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

# One delegation tool per specialist. As the fleet grows, add a line here
# (and update scout.tools in definition.py) per new specialist.
_register_tool(make_delegation_tool(
    name="delegate_to_sub_cost_code",
    target_agent="sub_cost_code_specialist",
    description=(
        "Hand a sub-cost-code task off to the SubCostCode specialist "
        "agent. Use for ANY sub-cost-code work — lookups, searches by "
        "name, creates, updates, deletes, and parent CostCode resolution "
        "from a specific sub-cost-code. Pass the user's request verbatim, "
        "or a clarified version that captures all needed context (the "
        "specialist starts with no memory of this conversation). The "
        "specialist returns a final answer as markdown, often including "
        "a record card; relay it to the user per the rules in the "
        "system prompt."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_cost_code",
    target_agent="cost_code_specialist",
    description=(
        "Hand a CostCode (broad parent category) task off to the "
        "CostCode specialist agent. Use for CostCode catalog questions "
        "('what CostCodes do we have?', 'how many?', 'list them'), "
        "looking up a specific CostCode by number or public_id, "
        "finding which SubCostCodes sit under a given CostCode, AND "
        "for creating, updating, or deleting CostCodes (approval-gated). "
        "Do NOT use this for SubCostCode work — route that to "
        "delegate_to_sub_cost_code instead."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_customer",
    target_agent="customer_specialist",
    description=(
        "Hand a Customer (or 'client') task off to the Customer "
        "specialist agent. Use for customer lookups, searches, "
        "creates, updates, deletes, and questions about which "
        "projects belong to a customer. Do NOT use this for "
        "Project-specific edits — route those to delegate_to_project."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_project",
    target_agent="project_specialist",
    description=(
        "Hand a Project task off to the Project specialist agent. "
        "Use for project lookups, searches, creates, updates, "
        "deletes, and parent Customer resolution from a specific "
        "project. Do NOT use this for Customer-centric questions "
        "('what customers do we have?') — route those to "
        "delegate_to_customer."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_vendor",
    target_agent="vendor_specialist",
    description=(
        "Hand a Vendor task off to the Vendor specialist agent. Use "
        "for vendor lookups, searches by name/abbreviation, creates, "
        "updates, and (soft) deletes. The vendor catalog is large "
        "(~1100 rows) so the specialist works search-first; do not "
        "ask it to list everything. Vendor delete is soft — the row "
        "is hidden from search but historical records pointing at it "
        "are preserved."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_bill",
    target_agent="bill_specialist",
    description=(
        "Hand a Bill task off to the Bill specialist agent. Use for "
        "bill lookups (by vendor, number, or filter), reads, draft "
        "creation (parent record only — no line items), updates to "
        "parent fields, deletes, and the workflow `complete` action "
        "that finalizes a draft bill and pushes it to QBO + SharePoint "
        "+ Excel. The bill catalog is large (~18K rows) so the "
        "specialist works search-first. The specialist does NOT edit "
        "line items — tell the user to use the UI for that today."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_bill_credit",
    target_agent="bill_credit_specialist",
    description=(
        "Hand a BillCredit (vendor credit memo) task off to the "
        "BillCredit specialist agent. Use for credit lookups (by "
        "vendor, number, or filter), reads, draft creation (parent "
        "record only — no line items), updates to parent fields, "
        "deletes, and the workflow `complete` action that finalizes "
        "a draft credit. Catalog is small (~400 rows) but search-"
        "first discipline still applies. NOT for vendor bills — "
        "route those to delegate_to_bill instead. No line-item edits."
    ),
))

# Register the scout agent.
from intelligence.agents.scout.definition import scout  # noqa: F401
