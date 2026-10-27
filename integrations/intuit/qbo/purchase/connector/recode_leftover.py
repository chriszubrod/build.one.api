"""U-738: shared pure rules for QBO purchase line-id recode leftover handling."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional, Sequence


def _abs_amount(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return abs(Decimal(str(value)))
    except Exception:
        return None


def true_recode_skip_reason(
    stale_line: Any,
    live_line_amount: Any,
    *,
    holding_sub_cost_code_ids: frozenset[int],
    live_line_count: int,
) -> Optional[str]:
    """
    Return a flag_manual reason when the pair fails the true-recode gate, else None.
    """
    stale_amt = _abs_amount(getattr(stale_line, "amount", None))
    live_amt = _abs_amount(live_line_amount)
    if stale_amt is None or live_amt is None:
        return "amount_unknown"
    if stale_amt != live_amt:
        return "amount_mismatch"
    scc_id = getattr(stale_line, "sub_cost_code_id", None)
    if scc_id is None:
        return None
    if scc_id in holding_sub_cost_code_ids:
        return None
    if live_line_count == 1:
        return None
    return "recode_not_suspense_line"


def compute_kept_billing_fields(
    kept_line: Any,
    removed_line: Any,
    *,
    kept_has_invoice_ref: bool,
) -> dict[str, Any]:
    """Billing fields for the kept (newer) row after merge."""
    kept_billed = bool(getattr(kept_line, "is_billed", False)) or bool(
        getattr(removed_line, "is_billed", False)
    )
    if kept_has_invoice_ref:
        kept_billed = True

    removed_decided_at = getattr(removed_line, "billable_decided_at", None)
    if removed_decided_at is not None:
        is_billable = getattr(removed_line, "is_billable", None)
        billable_decided_at = removed_decided_at
        billable_decided_by = getattr(removed_line, "billable_decided_by_user_id", None)
    else:
        is_billable = getattr(kept_line, "is_billable", None)
        billable_decided_at = getattr(kept_line, "billable_decided_at", None)
        billable_decided_by = getattr(kept_line, "billable_decided_by_user_id", None)

    return {
        "is_billed": kept_billed,
        "is_billable": is_billable,
        "billable_decided_at": billable_decided_at,
        "billable_decided_by_user_id": billable_decided_by,
    }


def pull_is_billed(direct: Any, qbo_is_billed: Optional[bool], *, has_invoice_ref: bool) -> Optional[bool]:
    """Never lower IsBilled 1→0 on invoice-linked lines unless QBO omits billing (preserve)."""
    stored = getattr(direct, "is_billed", None)
    if has_invoice_ref and stored is True and qbo_is_billed is False:
        return None
    return qbo_is_billed


def public_ids_in_workbook_outbox_payload(payload_text: str, *, col_index: int = 25) -> set[str]:
    """Extract column-Z public ids from a done MS excel outbox payload JSON string."""
    import json

    out: set[str] = set()
    if not payload_text:
        return out
    try:
        payload = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError):
        return out
    if not isinstance(payload, dict):
        return out
    values = payload.get("values")
    if not values:
        return out
    rows: Sequence
    if values and isinstance(values[0], list):
        rows = values
    else:
        rows = [values]
    for row in rows:
        if not isinstance(row, list) or len(row) <= col_index:
            continue
        cell = row[col_index]
        if cell is not None and str(cell).strip():
            out.add(str(cell).strip().lower())
    return out
