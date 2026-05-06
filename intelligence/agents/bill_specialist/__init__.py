"""Bill specialist agent — Bill CRUD + line-item CRUD + complete workflow.

Reuses Vendor / SubCostCode / Project read tools for resolution. Also
delegates project-resolution tasks to the project_specialist agent for
invoice-driven creates (Ship To address → Project lookup).

Importing this package triggers tool + delegation + agent registration.
"""
import entities.bill.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

# project_specialist registers its own tools; we only need its agent
# definition to be in the registry so the delegation tool can find it.
import intelligence.agents.project_specialist  # noqa: F401

from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

# Delegation primitive: bill_specialist hands the Ship To / job-site
# address from an invoice to project_specialist, which runs
# find_project_for_invoice and returns the matching project's public_id
# (or surfaces ambiguity if multiple candidates have similar confidence).
_register_tool(make_delegation_tool(
    name="delegate_to_project_specialist",
    target_agent="project_specialist",
    description=(
        "Hand a project-resolution task off to the project_specialist "
        "agent. Use this when creating a bill from an invoice email and "
        "you have a job-site address (Ship To) but no resolved Project "
        "public_id yet.\n\n"
        "Pass a self-contained task description in markdown that "
        "carries:\n"
        "  • The address hint (e.g. `\"917 TYNE BLVD\"`) — pre-clean "
        "    multi-line DI output if needed\n"
        "  • Optional explicit project name if the email mentions one\n"
        "  • A request to return the matching Project.public_id, plus "
        "    a one-sentence note of how it matched (for the bill memo)\n\n"
        "The specialist returns its final markdown answer with the "
        "resolved Project info; quote the public_id verbatim into your "
        "create_bill `line_project_public_id` field. If the specialist "
        "reports multiple ambiguous matches, surface the ambiguity and "
        "ask the human to pick before finalizing."
    ),
))

from intelligence.agents.bill_specialist.definition import (  # noqa: F401
    bill_specialist,
)
