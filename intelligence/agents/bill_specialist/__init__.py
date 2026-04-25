"""Bill specialist agent — Bill read + parent-only updates + complete workflow.

Reuses the Vendor read tools for parent resolution. No write tools for
line items in v1 — variable-length-array approval cards are deferred.

Importing this package triggers tool + agent registration.
"""
import entities.bill.intelligence.tools  # noqa: F401
import entities.vendor.intelligence.tools  # noqa: F401

from intelligence.agents.bill_specialist.definition import (  # noqa: F401
    bill_specialist,
)
