# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class QboVendor:
    id: Optional[str]
    sync_token: Optional[str]
    display_name: Optional[str]
    vendor_1099: Optional[int]
    company_name: Optional[str]
    tax_identifier: Optional[str]
    print_on_check_name: Optional[str]
    bill_addr_id: Optional[str]

    def to_dict(self) -> dict:
        """
        Convert the QboVendor dataclass to a dictionary.
        """
        return asdict(self)
