# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64


@dataclass
class EmployeeProjectRate:
    """Per-(Employee × Project) rate/markup override.

    NULL HourlyRate or Markup means "inherit Employee default" — the
    ReadEffectiveRateForEmployeeProject sproc handles the COALESCE.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    employee_id: Optional[int]
    project_id: Optional[int]
    hourly_rate: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    notes: Optional[str] = None
    is_deleted: Optional[bool] = False
    employee_name: Optional[str] = None
    employee_public_id: Optional[str] = None
    project_name: Optional[str] = None
    project_public_id: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.hourly_rate is not None:
            d["hourly_rate"] = str(self.hourly_rate)
        if self.markup is not None:
            d["markup"] = str(self.markup)
        return d
