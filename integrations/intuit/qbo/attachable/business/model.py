# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class QboAttachable:
    """
    Represents a QBO Attachable stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    file_name: Optional[str]
    note: Optional[str]
    category: Optional[str]
    content_type: Optional[str]
    size: Optional[int]
    file_access_uri: Optional[str]
    temp_download_uri: Optional[str]
    # Reference to linked entity
    entity_ref_type: Optional[str]  # e.g., "Bill", "Invoice"
    entity_ref_value: Optional[str]  # QBO ID of the linked entity

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
        """Convert to dictionary with JSON-serializable values."""
        return asdict(self)

    @classmethod
    def transient(
        cls,
        *,
        qbo_id,
        realm_id,
        sync_token=None,
        file_name=None,
        note=None,
        category=None,
        content_type=None,
        size=None,
        file_access_uri=None,
        temp_download_uri=None,
        entity_ref_type=None,
        entity_ref_value=None,
    ) -> "QboAttachable":
        """
        Build an in-memory (never persisted) QboAttachable — id/public_id/row_version/
        created_datetime/modified_datetime are always None since no local row backs a
        transient instance. Collapses the 3 hand-copied constructions this shape had
        (U-300b pull path, U-285 push path x2) into one factory (U-338).
        """
        return cls(
            id=None,
            public_id=None,
            row_version=None,
            created_datetime=None,
            modified_datetime=None,
            qbo_id=qbo_id,
            sync_token=sync_token,
            realm_id=realm_id,
            file_name=file_name,
            note=note,
            category=category,
            content_type=content_type,
            size=size,
            file_access_uri=file_access_uri,
            temp_download_uri=temp_download_uri,
            entity_ref_type=entity_ref_type,
            entity_ref_value=entity_ref_value,
        )
