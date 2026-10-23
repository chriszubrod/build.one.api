# Python Standard Library Imports
from typing import Any, Optional


def extract_billable_status(line: Any) -> Optional[str]:
    """Read BillableStatus from a live QBO line (item- or account-based expense detail)."""
    detail_type = getattr(line, "detail_type", None)
    if detail_type == "ItemBasedExpenseLineDetail":
        detail = getattr(line, "item_based_expense_line_detail", None)
        return getattr(detail, "billable_status", None) if detail else None
    if detail_type == "AccountBasedExpenseLineDetail":
        detail = getattr(line, "account_based_expense_line_detail", None)
        return getattr(detail, "billable_status", None) if detail else None
    return None
