# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class BoxProjectWorkbook:
    """
    Row in `[box].[ProjectWorkbook]` — a 1:1 mapping between a dbo.Project and
    the Box-hosted .xlsx workbook whose DETAILS tab we sync vendor cost lines
    into (the Box mirror of the MS Graph Excel sync target).

    `box_file_id` is Box's STRING file id (NVARCHAR(32)), never a BIGINT — Box
    ids share the UUID-ish-but-numeric-string shape with dbo PKs and must never
    be aliased into the BIGINT keyspace (qbo/dbo keyspace lesson).

    `worksheet_name` is the tab the drain handler edits with openpyxl
    (default 'DETAILS'); the formula summary tabs are never touched.

    Unlike the folder mapping there is no separate registry table — the Box
    file id lives directly on this row (a workbook is mapped to exactly one
    project; a project to exactly one workbook — `ProjectId` is UNIQUE).
    """

    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    project_id: Optional[int] = None
    box_file_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    created_by_user_id: Optional[int] = None
