# Python Standard Library Imports
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class RampApprovalState(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TransactionClassification:
    ramp_transaction_id: str
    approval_state: RampApprovalState
    needs_memo: bool
    needs_receipt: bool

    @property
    def is_open(self) -> bool:
        return self.approval_state == RampApprovalState.OPEN

    @property
    def is_complete(self) -> bool:
        return self.approval_state == RampApprovalState.COMPLETE

    @property
    def skip_approval_only(self) -> bool:
        return self.is_open and (not self.needs_memo) and (not self.needs_receipt)


def _memo_is_blank(memo: Any) -> bool:
    if memo is None:
        return True
    return not str(memo).strip()


def _receipts_empty(receipts: Any) -> bool:
    if isinstance(receipts, list):
        return not receipts
    return True


def classify_transaction(raw: Dict[str, Any]) -> TransactionClassification:
    """
    Membership is decided solely by Ramp's all_requirements_met_and_approved flag.
    memo/receipts populate descriptive Needs* fields only.
    """
    ramp_id = str(raw.get("id") or "")
    flag = raw.get("all_requirements_met_and_approved")
    if flag is False:
        approval_state = RampApprovalState.OPEN
    elif flag is True:
        approval_state = RampApprovalState.COMPLETE
    else:
        approval_state = RampApprovalState.UNKNOWN

    needs_memo = _memo_is_blank(raw.get("memo"))
    needs_receipt = _receipts_empty(raw.get("receipts"))

    return TransactionClassification(
        ramp_transaction_id=ramp_id,
        approval_state=approval_state,
        needs_memo=needs_memo,
        needs_receipt=needs_receipt,
    )
