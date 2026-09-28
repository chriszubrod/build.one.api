# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64


@dataclass
class RampTransactionFollowUp:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    ramp_transaction_id: Optional[str]
    card_holder_ramp_user_id: Optional[str]
    card_holder_name: Optional[str]
    merchant_name: Optional[str]
    amount: Optional[Decimal]
    transaction_date: Optional[str]
    needs_memo: Optional[bool]
    needs_receipt: Optional[bool]
    first_seen_at: Optional[str]
    last_drafted_at: Optional[str]
    draft_message_id: Optional[str]
    last_notified_at: Optional[str]
    notify_count: Optional[int]
    escalated_at: Optional[str]
    resolved_at: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data.get("amount") is not None:
            data["amount"] = str(data["amount"])
        return data
