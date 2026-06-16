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
    Row in `[box].[ProjectFolder]` — maps a dbo.Project to a `[box].[Folder]`
    row PER document class. `doc_class` is 'invoices' (vendor AP docs →
    "14 - Invoices") or 'draw_requests' (customer invoice packets →
    "15 - Draw Requests"); a project has at most one folder per class, and each
    Box folder maps to at most one project-class row.

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
    doc_class: Optional[str] = None
    created_by_user_id: Optional[int] = None
