# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class Project:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    name: Optional[str]
    description: Optional[str]
    status: Optional[str]
    customer_id: Optional[int]
    abbreviation: Optional[str]
    # Free-text per-project notes — surfaced in the React Project edit
    # page and read by bill_specialist / project_specialist via
    # FindProjectForInvoice for project-specific guidance (address
    # aliases, special handling rules).
    notes: Optional[str] = None
    # U-099: False for overhead projects; drives the recode's BillableStatus.
    # None = the row did not carry the column (a sproc not yet re-applied):
    # UNKNOWN, never assumed True - the recode treats it as no evidence.
    is_cost_plus: Optional[bool] = None
    # Dbo-native QBO identity (U-238a). Populated only by sprocs that
    # select it (ReadProjectById, ReadProjectByQboIdAndRealmId) — None
    # elsewhere, including on entities never synced from QBO.
    qbo_id: Optional[str] = None
    realm_id: Optional[str] = None
    # Customer.Name via LEFT JOIN — populated only by the list sprocs
    # (ReadProjects, ReadProjectsByUserId); None elsewhere, including on
    # projects with no CustomerId.
    customer_name: Optional[str] = None

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
        Convert the project dataclass to a dictionary.
        """
        return asdict(self)
