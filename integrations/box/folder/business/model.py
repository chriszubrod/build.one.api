# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class BoxFolder:
    """
    Row in `[box].[Folder]` — a Box folder known to build.one.

    `box_folder_id` is Box's string folder id (NVARCHAR(32)), NOT the local
    BIGINT `id`. `[box].[ProjectFolder]` FKs the local `id`.
    """

    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    box_folder_id: Optional[str] = None
    name: Optional[str] = None
    parent_box_folder_id: Optional[str] = None


@dataclass
class BoxProjectFolder:
    """
    Row in `[box].[ProjectFolder]` — 1:1 mapping between a dbo.Project and a
    `[box].[Folder]` row. Both sides UNIQUE: a project maps to exactly one
    Box folder and vice versa.

    `box_folder_id` here is the local BIGINT FK to `[box].[Folder](Id)` —
    the external Box string id lives on the joined Folder row.
    """

    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    project_id: Optional[int] = None
    box_folder_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
