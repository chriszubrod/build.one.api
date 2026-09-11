"""Canonical lifecycle + review-kind resolution (U-357 Phase 1 / LS-01a).

**Bill** is the first caller (U-443 — list + the three single GETs). The other
six in-scope documents — expense, bill_credit, invoice, contract_labor,
employee_labor, time_entry — repoint onto these same helpers as their slices of
LS-01a land. Keep this module pure: it imports nothing from `entities/`, which
is what lets it be unit-tested without a database.
"""

from shared.lifecycle.resolver import (
    LIFECYCLE_STATUSES,
    REVIEW_STATUS_KINDS,
    attach_lifecycle,
    resolve_document_status,
    review_kind_from_flags,
)

__all__ = [
    "LIFECYCLE_STATUSES",
    "REVIEW_STATUS_KINDS",
    "attach_lifecycle",
    "resolve_document_status",
    "review_kind_from_flags",
]
