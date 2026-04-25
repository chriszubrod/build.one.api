"""Expense specialist agent — vendor expenses + refunds (IsCredit=true).

Reuses the Vendor read tools for parent resolution. No write tools for
line items in v1 — same scope as bill_specialist.

Importing this package triggers tool + agent registration.
"""
import entities.expense.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401

from intelligence.agents.expense_specialist.definition import (  # noqa: F401
    expense_specialist,
)
