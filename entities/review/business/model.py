# Python Standard Library Imports
import base64
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


class ParentType:
    """
    String constants identifying which parent entity a Review belongs to.

    Plain strings (not enum.Enum) keep API marshalling trivial — the API
    accepts/returns the lowercase form directly.
    """

    BILL        = "bill"
    EXPENSE     = "expense"
    BILL_CREDIT = "bill_credit"
    INVOICE     = "invoice"

    ALL = (BILL, EXPENSE, BILL_CREDIT, INVOICE)


@dataclass
class Review:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    review_status_id: Optional[int]
    user_id: Optional[int]
    comments: Optional[str]
    bill_id: Optional[int]
    expense_id: Optional[int]
    bill_credit_id: Optional[int]
    invoice_id: Optional[int]
    # Denormalized JOINs (vw_Review)
    status_name: Optional[str]
    status_sort_order: Optional[int]
    status_is_final: Optional[bool]
    status_is_declined: Optional[bool]
    status_color: Optional[str]
    user_firstname: Optional[str]
    user_lastname: Optional[str]

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
        Convert the review dataclass to a dictionary.
        """
        return asdict(self)
