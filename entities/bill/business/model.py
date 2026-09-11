# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class Bill:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    vendor_id: Optional[int]
    payment_term_id: Optional[int]
    bill_date: Optional[str]
    due_date: Optional[str]
    bill_number: Optional[str]
    total_amount: Optional[Decimal]
    memo: Optional[str]
    is_draft: Optional[bool]
    # U-445 (U-357 Phase 3). The canonical lifecycle state, STORED — U-443
    # derived it per request, which was correct but unfilterable (post-filtering
    # a paginated page makes `count` lie). Defaulted so every existing
    # construction site — tests, fixtures, the QBO connectors — keeps working.
    # `is_draft` stays a real column until U-446; CK_Bill_Status_IsDraft makes
    # the two incapable of disagreeing in the meantime.
    status: Optional[str] = None
    status_datetime: Optional[str] = None
    status_origin: Optional[str] = None
    status_source_ref: Optional[str] = None
    intake_source: Optional[str] = None        # "manual" | "agent" | "script" — set-once at create
    intake_source_detail: Optional[str] = None  # username / agent name / script name
    source_email_message_id: Optional[int] = None  # FK → EmailMessage; populated by CreateBill OUTPUT, None from existing Read sprocs
    qbo_id: Optional[str] = None   # dbo-native QBO identity (U-238a); only ReadBillById/ReadBillByQboIdAndRealmId/ReadBillByPublicId (U-301b) select it
    realm_id: Optional[str] = None

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
        Convert the bill dataclass to a dictionary.
        """
        return asdict(self)
