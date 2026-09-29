# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64


# U-573: a follow-up row whose Ramp transaction comes back falsy this many sweeps IN
# A ROW is retired from the straggler refetch set. CONSECUTIVE is load-bearing — any
# successful fetch zeroes the run, so a transient Ramp 404 cannot retire a live row.
# Retiring is not resolving: a retired row stays an open item and still reaches the
# digest.
GONE_FROM_RAMP_MISS_THRESHOLD: int = 3


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
    # U-573. Defaulted and last so the 20-field keyword constructors already in the
    # tree keep working; SQL projection order is unrelated to dataclass field order.
    gone_from_ramp_count: Optional[int] = None
    gone_from_ramp_at: Optional[str] = None

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
