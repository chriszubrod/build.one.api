"""BillCredit specialist agent — vendor credit memos.

Reuses Vendor / SubCostCode / Project read tools for resolution.

Importing this package triggers tool + agent registration.
"""
import entities.bill_credit.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

from intelligence.agents.bill_credit_specialist.definition import (  # noqa: F401
    bill_credit_specialist,
)
