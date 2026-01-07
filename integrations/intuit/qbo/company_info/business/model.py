# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboCompanyInfo:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    company_name: Optional[str]
    legal_name: Optional[str]
    company_addr_id: Optional[int]
    legal_addr_id: Optional[int]
    customer_communication_addr_id: Optional[int]
    tax_payer_id: Optional[str]
    fiscal_year_start_month: Optional[int]
    country: Optional[str]
    email: Optional[str]
    web_addr: Optional[str]
    currency_ref: Optional[str]

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
        Convert the QboCompanyInfo dataclass to a dictionary.
        """
        return asdict(self)

