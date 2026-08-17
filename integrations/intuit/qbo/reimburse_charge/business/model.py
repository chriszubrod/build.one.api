# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboReimburseCharge:
    """
    Represents a QBO ReimburseCharge captured into durable staging (U-186).

    QBO auto-creates a ReimburseCharge for each Billable Bill/Purchase line with
    a CustomerRef. `source_txn_*` would hold a reverse pointer to that source
    transaction/line if QBO ever exposed one. Measured 2026-08-16 (U-242): QBO
    never returns a reverse Bill/Purchase LinkedTxn; preserve on re-pull is
    defensive/forward-compatible only. See docs/rc_source_linking_signal_2026_08_16.md.
    All QBO ids here are STRING ids (disjoint from the qbo.*.Id BIGINT keyspace).
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    realm_id: Optional[str]
    customer_ref_value: Optional[str]
    customer_ref_name: Optional[str]
    txn_date: Optional[str]
    amount: Optional[Decimal]
    has_been_invoiced: Optional[bool]
    source_txn_type: Optional[str]
    source_txn_id: Optional[str]
    source_txn_line_id: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None
