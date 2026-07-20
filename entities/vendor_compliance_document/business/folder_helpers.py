# Python Standard Library Imports
import re
from collections import deque
from typing import Callable

# Third-party Imports

# Local Imports


COMPLIANCE_HINT_KEYWORDS = ("coi", "insurance", "license", "licence", "certificate", "acord")

_SHAREPOINT_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_compliance_hint(filename: str) -> bool:
    """Return True when filename contains a compliance-related keyword."""
    lowered = filename.lower()
    return any(keyword in lowered for keyword in COMPLIANCE_HINT_KEYWORDS)


def _sanitize_filename_base(name: str) -> str:
    """Strip SharePoint-illegal characters from a filename base."""
    cleaned = _SHAREPOINT_ILLEGAL_CHARS.sub("", name)
    return cleaned.strip() or "document"


def build_export_filename(original_filename: str, attachment_public_id: str) -> str:
    """
    Build a SharePoint-safe export filename embedding a stable attachment slug.

    Two distinct attachments with the same original name get distinct filenames;
    re-pushing the same attachment yields the same name (replace overwrites).
    """
    slug = attachment_public_id.replace("-", "")
    if not slug:
        slug = attachment_public_id

    original = original_filename or "document"
    if "." in original:
        base, ext = original.rsplit(".", 1)
        safe_base = _sanitize_filename_base(base)[:120]
        safe_ext = _SHAREPOINT_ILLEGAL_CHARS.sub("", ext)
        if safe_ext:
            return f"{safe_base}__{slug}.{safe_ext}"
        return f"{safe_base}__{slug}"
    safe_base = _sanitize_filename_base(original)[:120]
    return f"{safe_base}__{slug}"


def walk_folder_tree(
    list_children_fn: Callable[[str, str], dict],
    drive_id: str,
    root_item_id: str,
    *,
    max_depth: int = 10,
    max_items: int = 1000,
) -> list[dict]:
    """
    Breadth-first walk of a SharePoint folder tree returning flat file items.

    Each file dict includes all fields from the child item plus ``folder_path``
    (breadcrumb of parent folder names from root, ``/``-joined).
    """
    files: list[dict] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int, str]] = deque([(root_item_id, 0, "")])

    while queue and len(files) < max_items:
        item_id, depth, folder_path = queue.popleft()
        if item_id in visited:
            continue
        visited.add(item_id)

        result = list_children_fn(drive_id, item_id)
        for child in result.get("items", []):
            if len(files) >= max_items:
                break

            child_type = child.get("item_type")
            if child_type == "file":
                file_entry = dict(child)
                file_entry["folder_path"] = folder_path
                files.append(file_entry)
            elif child_type == "folder" and depth + 1 < max_depth:
                child_id = child.get("item_id")
                if not child_id or child_id in visited:
                    continue
                child_name = child.get("name") or ""
                child_path = f"{folder_path}/{child_name}" if folder_path else child_name
                queue.append((child_id, depth + 1, child_path))

    return files
