"""U-535b: read-only Box inventory of project invoice PDFs.

Walks ``<root> / <project folder> / <invoices folder> / **`` where the invoices folder is a
direct child of the project folder named ``14 ...`` or containing "invoice". Lists with
``GET folders/{id}/items`` (marker pagination, fields id,name,type,size) through the shared
``BoxHttpClient``. Reads only: no upload, folder create or any other Box write.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional, Protocol

from entities.attachment_backfill.business.matcher import InventoryFile

INVOICES_FOLDER = re.compile(r"^\s*14\b|invoice", re.IGNORECASE)
_PAGE_SIZE = 1000
_FIELDS = "id,name,type,size"


class _BoxReader(Protocol):
    def get(self, path: str, *, params: Optional[dict] = None, operation_name: Optional[str] = None,
            **kwargs: Any) -> dict: ...


def is_invoices_folder(name: Optional[str]) -> bool:
    return bool(INVOICES_FOLDER.search(name or ""))


def list_folder_items(client: _BoxReader, folder_id: str) -> Iterator[dict]:
    marker: Optional[str] = None
    while True:
        params: dict[str, Any] = {"fields": _FIELDS, "limit": _PAGE_SIZE, "usemarker": "true"}
        if marker:
            params["marker"] = marker
        page = client.get(
            f"folders/{folder_id}/items", params=params, operation_name="box.attachment_backfill.list"
        )
        for entry in page.get("entries") or []:
            yield entry
        marker = page.get("next_marker")
        if not marker:
            return


def _walk_pdfs(client: _BoxReader, start_folder_id: str) -> Iterator[tuple[dict, str]]:
    stack = [str(start_folder_id)]
    visited: set[str] = set()
    while stack:
        folder_id = stack.pop()
        if folder_id in visited:
            continue
        visited.add(folder_id)
        for entry in list_folder_items(client, folder_id):
            kind = entry.get("type")
            if kind == "folder":
                stack.append(str(entry["id"]))
            elif kind == "file" and str(entry.get("name") or "").lower().endswith(".pdf"):
                yield entry, folder_id


def walk_inventory(client: _BoxReader, root_folder_id: str) -> list[InventoryFile]:
    inventory: list[InventoryFile] = []
    for project_folder in list_folder_items(client, str(root_folder_id)):
        if project_folder.get("type") != "folder":
            continue
        project_name = str(project_folder.get("name") or "")
        for child in list_folder_items(client, str(project_folder["id"])):
            if child.get("type") != "folder" or not is_invoices_folder(child.get("name")):
                continue
            for entry, parent_id in _walk_pdfs(client, str(child["id"])):
                size = entry.get("size")
                inventory.append(
                    InventoryFile(
                        file_id=str(entry["id"]),
                        name=str(entry.get("name") or ""),
                        folder_id=parent_id,
                        project_folder_name=project_name,
                        size=int(size) if size is not None else None,
                    )
                )
    return inventory
