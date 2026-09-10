"""Canonical lifecycle + review-kind resolution (U-357 / LS-00c).

Expense is the first caller (list/GET attach). Other financial documents
repoint onto the same helpers when their LS-01a units land.
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
