"""Invoice specialist agent — customer invoices tied to projects.

Reuses the Project read tools for parent resolution. No write tools for
line items in v1 — the polymorphic InvoiceLineItem (refs Bill / Expense
/ BillCredit lines) is its own design problem.

Importing this package triggers tool + agent registration.
"""
import entities.invoice.intelligence.tools  # noqa: F401
import entities.project.intelligence.tools  # noqa: F401

from intelligence.agents.invoice_specialist.definition import (  # noqa: F401
    invoice_specialist,
)
