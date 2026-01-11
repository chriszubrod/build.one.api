# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboCustomer:
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
    fully_qualified_name: Optional[str]
    level: Optional[int]
    parent_ref_value: Optional[str]
    parent_ref_name: Optional[str]
    job: Optional[bool]
    active: Optional[bool]
    primary_email_addr: Optional[str]
    primary_phone: Optional[str]
    mobile: Optional[str]
    fax: Optional[str]
    bill_addr_id: Optional[int]
    ship_addr_id: Optional[int]
    balance: Optional[Decimal]
    balance_with_jobs: Optional[Decimal]
    taxable: Optional[bool]
    notes: Optional[str]
    print_on_check_name: Optional[str]

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

    @property
    def is_parent_customer(self) -> bool:
        """
        Check if this customer is a parent customer (Job=false or not set).
        """
        return not self.job or self.job == False

    @property
    def is_job(self) -> bool:
        """
        Check if this customer is a job/sub-customer (Job=true).
        """
        return self.job == True

    def to_dict(self) -> dict:
        """
        Convert the QboCustomer dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        if data.get('balance') is not None:
            data['balance'] = float(data['balance'])
        if data.get('balance_with_jobs') is not None:
            data['balance_with_jobs'] = float(data['balance_with_jobs'])
        return data
