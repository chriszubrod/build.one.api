"""BillCredit specialist agent — vendor credit memos.

Reuses the Vendor read tools for parent resolution. No write tools for
line items in v1 — same scope as bill_specialist.

Importing this package triggers tool + agent registration.
"""
import entities.bill_credit.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401

from intelligence.agents.bill_credit_specialist.definition import (  # noqa: F401
    bill_credit_specialist,
)
