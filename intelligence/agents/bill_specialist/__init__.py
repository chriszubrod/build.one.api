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

# project_specialist self-registers the `delegate_to_project_specialist`
# tool alongside its own agent registration — importing it here brings
# both into scope so bill_specialist's tool list resolves.
import intelligence.agents.project_specialist  # noqa: F401

from intelligence.agents.bill_specialist.definition import (  # noqa: F401
    bill_specialist,
)
