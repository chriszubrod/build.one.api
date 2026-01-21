# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboAccount:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    name: Optional[str]
    acct_num: Optional[str]
    description: Optional[str]
    active: Optional[bool]
    classification: Optional[str]
    account_type: Optional[str]
    account_sub_type: Optional[str]
    fully_qualified_name: Optional[str]
    sub_account: Optional[bool]
    parent_ref_value: Optional[str]
    parent_ref_name: Optional[str]
    current_balance: Optional[Decimal]
    current_balance_with_sub_accounts: Optional[Decimal]
    currency_ref_value: Optional[str]
    currency_ref_name: Optional[str]

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
        Convert the QboAccount dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        if data.get('current_balance') is not None:
            data['current_balance'] = float(data['current_balance'])
        if data.get('current_balance_with_sub_accounts') is not None:
            data['current_balance_with_sub_accounts'] = float(data['current_balance_with_sub_accounts'])
        return data
