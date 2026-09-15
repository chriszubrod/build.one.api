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

    BILL           = "bill"
    EXPENSE        = "expense"
    BILL_CREDIT    = "bill_credit"
    INVOICE        = "invoice"
    CONTRACT_LABOR = "contract_labor"

    ALL = (BILL, EXPENSE, BILL_CREDIT, INVOICE, CONTRACT_LABOR)


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
    status_is_initial: Optional[bool]
    status_color: Optional[str]
    user_firstname: Optional[str]
    user_lastname: Optional[str]
    contract_labor_id: Optional[int] = None
    # U-455: the kind FROZEN at insert. The `status_is_*` flags above are the
    # status's CURRENT configuration; this is what was true when the row was
    # written. Read THIS for a row's kind, never re-derive from those.
    #
    # Defaulted and placed here because the dataclass's non-defaulted fields
    # must come first — not because it is optional in practice: the column is
    # NOT NULL and every row carries it.
    review_kind: Optional[str] = None
    # FK back to the EmailMessage that triggered this Review state
    # transition (vendor invoice / forward archive / PM reply). NULL
    # when the transition was triggered by a non-email path (manual UI).
    email_message_id: Optional[int] = None

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
