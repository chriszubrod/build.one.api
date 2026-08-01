# Python Standard Library Imports
from dataclasses import asdict, dataclass
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
    a CustomerRef. `source_txn_*` is the reverse pointer back to that source
    transaction/line — captured while un-invoiced and preserved across the
    HasBeenInvoiced=true re-pull (QBO drops the reverse LinkedTxn on the flip,
    KI-32). All QBO ids here are STRING ids (disjoint from the qbo.*.Id BIGINT
    keyspace).
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

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """
        Convert the QboReimburseCharge dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        if data.get("amount") is not None:
            data["amount"] = float(data["amount"])
        return data
