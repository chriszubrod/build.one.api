# Python Standard Library Imports
import base64
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class Contract:
    """
    A Project's Builder's-Fee rate — the owner-directed home for the DECIMAL(9,6)
    fraction the U6 cover page reads (0.100000 = 10%).

    MINIMAL BY DESIGN: BuildersFeeRate is the only business field. The full
    contract model (contract value, change orders, retainage, dates, and the
    relationship to the existing Budget entity) is deferred to a formal design
    conversation.
    """

    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    created_by_user_id: Optional[int] = None
    project_id: Optional[int] = None
    builders_fee_rate: Optional[Decimal] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        # DECIMAL transport as a string — never float.
        if d.get("builders_fee_rate") is not None:
            d["builders_fee_rate"] = str(d["builders_fee_rate"])
        return d
