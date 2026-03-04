# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports
import base64

# Local Imports


@dataclass
class InvoiceInvoice:
    """
    Mapping table between Invoice module and QboInvoice integration.
    Maintains 1:1 relationship between Invoice and QboInvoice.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    invoice_id: Optional[int]
    qbo_invoice_id: Optional[int]

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
        Convert the InvoiceInvoice dataclass to a dictionary.
        """
        return asdict(self)
