"""Expense specialist agent — vendor expenses + refunds (IsCredit=true).

Reuses Vendor / SubCostCode / Project read tools for resolution.

Importing this package triggers tool + agent registration.
"""
import entities.expense.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

from intelligence.agents.expense_specialist.definition import (  # noqa: F401
    expense_specialist,
)
