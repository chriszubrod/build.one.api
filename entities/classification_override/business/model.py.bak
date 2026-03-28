# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ClassificationOverride:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]

    match_type: str             # 'email' | 'domain'
    match_value: str            # 'ar@acme.com' or 'acme.com'
    classification_type: str    # bill | expense | vendor_credit | inquiry | statement

    notes: Optional[str]
    is_active: bool
    created_by: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)
