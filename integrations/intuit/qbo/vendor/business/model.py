# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboVendor:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    display_name: Optional[str]
    title: Optional[str]
    given_name: Optional[str]
    middle_name: Optional[str]
    family_name: Optional[str]
    suffix: Optional[str]
    company_name: Optional[str]
    print_on_check_name: Optional[str]
    tax_identifier: Optional[str]
    vendor_1099: Optional[bool]
    active: Optional[bool]
    primary_email_addr: Optional[str]
    primary_phone: Optional[str]
    mobile: Optional[str]
    fax: Optional[str]
    bill_addr_id: Optional[int]
    balance: Optional[Decimal]
    acct_num: Optional[str]
    web_addr: Optional[str]

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
        Convert the QboVendor dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        if data.get('balance') is not None:
            data['balance'] = float(data['balance'])
        return data
