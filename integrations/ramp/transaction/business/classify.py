# Python Standard Library Imports
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TransactionClassification:
    ramp_transaction_id: str
    is_open: bool
    is_complete: bool
    needs_memo: bool
    needs_receipt: bool
    skip_approval_only: bool


def _memo_is_blank(memo: Any) -> bool:
    if memo is None:
        return True
    return not str(memo).strip()


def _receipts_empty(receipts: Any) -> bool:
    if receipts is None:
        return True
    if isinstance(receipts, list):
        return len(receipts) == 0
    return True


def classify_transaction(raw: Dict[str, Any]) -> TransactionClassification:
    """
    Membership is decided solely by Ramp's all_requirements_met_and_approved flag.
    memo/receipts populate descriptive Needs* fields only.
    """
    ramp_id = str(raw.get("id") or "")
    flag = raw.get("all_requirements_met_and_approved")
    is_open = flag is False
    is_complete = flag is True

    needs_memo = _memo_is_blank(raw.get("memo"))
    needs_receipt = _receipts_empty(raw.get("receipts"))

    skip_approval_only = is_open and (not needs_memo) and (not needs_receipt)

    return TransactionClassification(
        ramp_transaction_id=ramp_id,
        is_open=is_open,
        is_complete=is_complete,
        needs_memo=needs_memo,
        needs_receipt=needs_receipt,
        skip_approval_only=skip_approval_only,
    )


def select_open_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return transactions selected for follow-up (after guard), preserving input order."""
    selected: List[Dict[str, Any]] = []
    for raw in transactions:
        cls = classify_transaction(raw)
        if not cls.is_open:
            continue
        if cls.skip_approval_only:
            continue
        selected.append(raw)
    return selected
