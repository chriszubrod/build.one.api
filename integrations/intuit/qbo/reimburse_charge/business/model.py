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
    a CustomerRef. All QBO ids here are STRING ids (disjoint from the
    qbo.*.Id BIGINT keyspace).
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

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None
