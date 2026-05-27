"""Project specialist agent — Project CRUD + Customer read for parent resolution.

Importing this package triggers:
  1. Project + Customer entity tools (the specialist's own tools).
  2. The `delegate_to_project_specialist` delegation primitive that
     other agents use to hand project-resolution tasks here. Lives
     here (rather than in each calling agent) so multiple specialists
     (bill_specialist, contract_labor_specialist, …) can share the
     registration without colliding on `register()`.
  3. The agent itself.
"""
import entities.project.intelligence.tools  # noqa: F401
import entities.customer.intelligence.tools  # noqa: F401

from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

_register_tool(make_delegation_tool(
    name="delegate_to_project_specialist",
    target_agent="project_specialist",
    description=(
        "Hand a project-resolution task off to the project_specialist "
        "agent. Use whenever you have an address or project-name hint "
        "but no resolved Project public_id yet — works for invoice "
        "Ship-To addresses, worker-timesheet job-site addresses, and "
        "any other address → Project binding.\n\n"
        "Pass a self-contained task description in markdown that "
        "carries:\n"
        "  • The address hint (e.g. `\"917 TYNE BLVD\"` or "
        "`\"206 Haverford Ave\"`) — pre-clean multi-line input if "
        "needed\n"
        "  • Optional explicit project name if the email mentions one\n"
        "  • A request to return the matching Project.public_id, plus "
        "a one-sentence note of how it matched (useful audit context)\n\n"
        "The specialist returns its final markdown answer with the "
        "resolved Project info; quote the public_id verbatim into the "
        "downstream call that needs it. If the specialist reports "
        "multiple ambiguous matches, surface the ambiguity in your own "
        "final answer (do not guess)."
    ),
))

from intelligence.agents.project_specialist.definition import (  # noqa: F401
    project_specialist,
)
