# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class MsDriveItem:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    ms_drive_id: Optional[int]
    item_id: Optional[str]
    parent_item_id: Optional[str]
    name: Optional[str]
    item_type: Optional[str]
    size: Optional[int]
    mime_type: Optional[str]
    web_url: Optional[str]
    graph_created_datetime: Optional[str]
    graph_modified_datetime: Optional[str]

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
    def is_folder(self) -> bool:
        return self.item_type == "folder"

    @property
    def is_file(self) -> bool:
        return self.item_type == "file"

    def to_dict(self) -> dict:
        """
        Convert the MsDriveItem dataclass to a dictionary.
        """
        return asdict(self)
