"""Expense specialist agent — vendor expenses + refunds (IsCredit=true).

Reuses Vendor / SubCostCode / Project read tools for resolution.

Importing this package triggers tool + agent registration.
"""
import entities.expense.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401
import entities.sub_cost_code.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

# project_specialist self-registers the `delegate_to_project_specialist`
# tool alongside its own agent registration — importing it here brings both
# into scope so expense_specialist's tool list resolves (receipt-intake flows
# resolve the job-site project the same way bill_specialist does).
import intelligence.agents.project_specialist  # noqa: F401

from intelligence.agents.expense_specialist.definition import (  # noqa: F401
    expense_specialist,
)
