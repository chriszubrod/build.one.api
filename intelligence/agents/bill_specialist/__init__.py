"""Bill specialist agent — Bill CRUD + line-item CRUD + complete workflow.

Reuses Vendor / SubCostCode / Project read tools for resolution.

Importing this package triggers tool + agent registration.
"""
import entities.bill.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

from intelligence.agents.bill_specialist.definition import (  # noqa: F401
    bill_specialist,
)
