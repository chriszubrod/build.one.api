# Python Standard Library Imports
import logging
import urllib.parse
from typing import Any, Dict, Optional

# Local Imports
from integrations.ms.base.client import MsGraphClient
from integrations.ms.base.errors import MsGraphError
from integrations.ms.base.locking import ms_app_lock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(e: MsGraphError, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Translate a typed MsGraphError into the legacy dict-envelope shape
    that callers expect: `{"status_code": int, "message": str, ...}`.
    Additional default-value fields (e.g., `"sites": []`) flow through
    `extra` so the response shape stays stable on the failure branch.
    """
    status = e.http_status or 500
    base: Dict[str, Any] = {"status_code": status, "message": str(e)}
    if extra:
        base.update(extra)
    return base


def _format_drive_item(item: dict) -> dict:
    """
    Format a DriveItem from MS Graph API response.
    Determines if it's a file or folder based on facets.
    """
    item_type = "folder" if "folder" in item else "file"

    size = item.get("size") if item_type == "file" else None
    mime_type = item.get("file", {}).get("mimeType") if item_type == "file" else None

    return {
        "item_id": item.get("id"),
        "name": item.get("name"),
        "item_type": item_type,
        "size": size,
        "mime_type": mime_type,
        "web_url": item.get("webUrl"),
        "parent_item_id": item.get("parentReference", {}).get("id"),
        "graph_created_datetime": item.get("createdDateTime"),
        "graph_modified_datetime": item.get("lastModifiedDateTime"),
        "child_count": item.get("folder", {}).get("childCount") if item_type == "folder" else None,
    }


# ---------------------------------------------------------------------------
# Workbook sessions
# ---------------------------------------------------------------------------


def create_workbook_session(drive_id: str, item_id: str) -> Optional[str]:
    """
    Create a persistent workbook session for batch Excel operations.
    Returns the session ID string on success, or None if session creation fails.
    Callers should fall back to sessionless behavior when None is returned.
    """
    try:
        with MsGraphClient() as client:
            result = client.post(
                f"drives/{drive_id}/items/{item_id}/workbook/createSession",
                json={"persistChanges": True},
                extra_headers={"Prefer": "respond-async"},
                timeout_tier="B",
                operation_name="workbook.create_session",
            )
        session_id = result.get("id") if isinstance(result, dict) else None
        if session_id:
            logger.info(f"Created workbook session: {session_id[:20]}...")
            return session_id
        logger.warning("createSession succeeded but returned no session ID")
        return None
    except MsGraphError as e:
        logger.warning(f"createSession failed ({e.http_status}): {e}")
        return None


def close_workbook_session(drive_id: str, item_id: str, session_id: str) -> None:
    """
    Close a workbook session. Best-effort -- failures are logged but not raised.
    """
    try:
        with MsGraphClient() as client:
            client.post(
                f"drives/{drive_id}/items/{item_id}/workbook/closeSession",
                extra_headers={"workbook-session-id": session_id},
                timeout_tier="A",
                operation_name="workbook.close_session",
            )
        logger.info("Workbook session closed successfully")
    except MsGraphError as e:
        logger.warning(f"closeSession returned error (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Site operations
# ---------------------------------------------------------------------------


def search_sites(query: str) -> dict:
    """
    Search for SharePoint sites using MS Graph API.
    Returns dict with status_code, message, and sites list.
    """
    try:
        with MsGraphClient() as client:
            data = client.get(
                "sites",
                params={"search": query},
                operation_name="sites.search",
            )
        sites = data.get("value", [])
        formatted = [
            {
                "site_id": s.get("id"),
                "display_name": s.get("displayName"),
                "web_url": s.get("webUrl"),
                "hostname": s.get("siteCollection", {}).get("hostname", ""),
            }
            for s in sites
        ]
        return {
            "message": f"Found {len(formatted)} sites",
            "status_code": 200,
            "sites": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error searching SharePoint sites: {e}")
        return _error_response(e, extra={"sites": []})


def get_my_drive() -> dict:
    """
    Get the current user's OneDrive via /me/drive.
    """
    try:
        with MsGraphClient() as client:
            drive = client.get("me/drive", operation_name="drive.get_me")
        return {
            "message": "OneDrive retrieved successfully",
            "status_code": 200,
            "drive": {
                "drive_id": drive.get("id"),
                "name": drive.get("name"),
                "web_url": drive.get("webUrl"),
                "drive_type": drive.get("driveType"),
                "description": drive.get("description", ""),
                "owner": drive.get("owner", {}),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting user's OneDrive: {e}")
        return _error_response(e, extra={"drive": None})


def get_followed_sites() -> dict:
    """
    Get SharePoint sites that the current user follows via /me/followedSites.
    """
    try:
        with MsGraphClient() as client:
            data = client.get("me/followedSites", operation_name="sites.followed")
        sites = data.get("value", [])
        formatted = [
            {
                "site_id": s.get("id"),
                "display_name": s.get("displayName"),
                "web_url": s.get("webUrl"),
                "hostname": s.get("siteCollection", {}).get("hostname", ""),
            }
            for s in sites
        ]
        return {
            "message": f"Found {len(formatted)} followed sites",
            "status_code": 200,
            "sites": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error getting followed SharePoint sites: {e}")
        return _error_response(e, extra={"sites": []})


def get_site_by_id(site_id: str) -> dict:
    """Get a SharePoint site by its MS Graph ID."""
    try:
        with MsGraphClient() as client:
            site = client.get(f"sites/{site_id}", operation_name="sites.get_by_id")
        return {
            "message": "Site retrieved successfully",
            "status_code": 200,
            "site": {
                "site_id": site.get("id"),
                "display_name": site.get("displayName"),
                "web_url": site.get("webUrl"),
                "hostname": site.get("siteCollection", {}).get("hostname", ""),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting SharePoint site by ID {site_id}: {e}")
        return _error_response(e, extra={"site": None})


def get_site_by_path(hostname: str, site_path: str) -> dict:
    """Get a SharePoint site by its hostname and path."""
    if not site_path.startswith("/"):
        site_path = f"/{site_path}"
    try:
        with MsGraphClient() as client:
            site = client.get(
                f"sites/{hostname}:{site_path}",
                operation_name="sites.get_by_path",
            )
        return {
            "message": "Site retrieved successfully",
            "status_code": 200,
            "site": {
                "site_id": site.get("id"),
                "display_name": site.get("displayName"),
                "web_url": site.get("webUrl"),
                "hostname": site.get("siteCollection", {}).get("hostname", hostname),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting SharePoint site by path {hostname}{site_path}: {e}")
        return _error_response(e, extra={"site": None})


# ---------------------------------------------------------------------------
# Drive operations
# ---------------------------------------------------------------------------


def list_site_drives(site_id: str) -> dict:
    """List all drives (document libraries) for a SharePoint site."""
    try:
        with MsGraphClient() as client:
            data = client.get(
                f"sites/{site_id}/drives",
                operation_name="drives.list_for_site",
            )
        drives = data.get("value", [])
        formatted = [
            {
                "drive_id": d.get("id"),
                "name": d.get("name"),
                "web_url": d.get("webUrl"),
                "drive_type": d.get("driveType"),
                "description": d.get("description", ""),
                "created_datetime": d.get("createdDateTime"),
            }
            for d in drives
        ]
        return {
            "message": f"Found {len(formatted)} drives",
            "status_code": 200,
            "drives": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error listing drives for site {site_id}: {e}")
        return _error_response(e, extra={"drives": []})


def get_drive_by_id(drive_id: str) -> dict:
    """Get a drive by its MS Graph ID."""
    try:
        with MsGraphClient() as client:
            drive = client.get(f"drives/{drive_id}", operation_name="drive.get_by_id")
        return {
            "message": "Drive retrieved successfully",
            "status_code": 200,
            "drive": {
                "drive_id": drive.get("id"),
                "name": drive.get("name"),
                "web_url": drive.get("webUrl"),
                "drive_type": drive.get("driveType"),
                "description": drive.get("description", ""),
                "created_datetime": drive.get("createdDateTime"),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting drive by ID {drive_id}: {e}")
        return _error_response(e, extra={"drive": None})


# ---------------------------------------------------------------------------
# Drive item operations (read)
# ---------------------------------------------------------------------------


def list_drive_root_children(drive_id: str) -> dict:
    """List items at the root of a drive."""
    try:
        with MsGraphClient() as client:
            data = client.get(
                f"drives/{drive_id}/root/children",
                operation_name="driveitem.list_root",
            )
        items = [_format_drive_item(i) for i in data.get("value", [])]
        return {
            "message": f"Found {len(items)} items",
            "status_code": 200,
            "items": items,
        }
    except MsGraphError as e:
        logger.error(f"Error listing drive root children for {drive_id}: {e}")
        return _error_response(e, extra={"items": []})


def list_drive_item_children(drive_id: str, item_id: str) -> dict:
    """List children of a specific folder in a drive."""
    try:
        with MsGraphClient() as client:
            data = client.get(
                f"drives/{drive_id}/items/{item_id}/children",
                operation_name="driveitem.list_children",
            )
        items = [_format_drive_item(i) for i in data.get("value", [])]
        return {
            "message": f"Found {len(items)} items",
            "status_code": 200,
            "items": items,
        }
    except MsGraphError as e:
        logger.error(f"Error listing drive item children for {item_id}: {e}")
        return _error_response(e, extra={"items": []})


def get_drive_item(drive_id: str, item_id: str) -> dict:
    """Get metadata for a specific item in a drive."""
    try:
        with MsGraphClient() as client:
            item = client.get(
                f"drives/{drive_id}/items/{item_id}",
                operation_name="driveitem.get",
            )
        return {
            "message": "Item retrieved successfully",
            "status_code": 200,
            "item": _format_drive_item(item),
        }
    except MsGraphError as e:
        logger.error(f"Error getting drive item {item_id}: {e}")
        return _error_response(e, extra={"item": None})


def get_drive_item_content(drive_id: str, item_id: str) -> dict:
    """
    Get the content (download) of a file in a drive.
    Returns `content` as raw bytes and `content_type` from the response headers.
    """
    try:
        with MsGraphClient() as client:
            content = client.get_bytes(
                f"drives/{drive_id}/items/{item_id}/content",
                timeout_tier="C",
                operation_name="driveitem.get_content",
            )
        # Graph returns a 302 to a pre-signed CDN URL; MsGraphClient.get_bytes
        # enables follow_redirects on the raw-bytes path so the final response
        # comes from the CDN. Content-Type from the CDN is unreliable for
        # callers — sniff from the file extension or item metadata if needed.
        return {
            "message": "Content retrieved successfully",
            "status_code": 200,
            "content": content,
            "content_type": "application/octet-stream",
        }
    except MsGraphError as e:
        logger.error(f"Error getting drive item content for {item_id}: {e}")
        return _error_response(e, extra={"content": None, "content_type": None})


# ---------------------------------------------------------------------------
# Drive item operations (write)
# ---------------------------------------------------------------------------


def upload_small_file(
    drive_id: str,
    parent_item_id: str,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    """
    Upload a small file (up to 4MB) to a folder in a drive via simple PUT.
    """
    try:
        with MsGraphClient() as client:
            item = client.upload(
                f"drives/{drive_id}/items/{parent_item_id}:/{filename}:/content",
                content=content,
                content_type=content_type,
                method="PUT",
                timeout_tier="C",
                operation_name="driveitem.upload_small",
            )
        return {
            "message": "File uploaded successfully",
            "status_code": 200,
            "item": _format_drive_item(item),
        }
    except MsGraphError as e:
        logger.error(f"Error uploading file {filename} to {parent_item_id}: {e}")
        return _error_response(e, extra={"item": None})


def upload_large_file(
    drive_id: str,
    parent_item_id: str,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    """
    Upload a file larger than 4MB via MS Graph upload session (resumable).
    Uploads in 5MB chunks.

    Note: the `upload_url` returned by createUploadSession is a pre-signed
    URL (different host, no Authorization header). The MsGraphClient's auth
    injection would be wrong for the chunk PUT; we use httpx directly for
    those with a conservative timeout. Phase 3 will introduce a dedicated
    upload-session client that adds checkpointing for resume-on-failure.
    """
    import httpx

    try:
        with MsGraphClient() as client:
            session = client.post(
                f"drives/{drive_id}/items/{parent_item_id}:/{filename}:/createUploadSession",
                json={
                    "item": {
                        "@microsoft.graph.conflictBehavior": "replace",
                        "name": filename,
                    }
                },
                timeout_tier="B",
                operation_name="driveitem.create_upload_session",
            )

        upload_url = session.get("uploadUrl")
        if not upload_url:
            return {
                "message": "Upload session did not return an uploadUrl",
                "status_code": 500,
                "item": None,
            }

        chunk_size = 5 * 1024 * 1024
        total_size = len(content)
        offset = 0
        last_json: Optional[dict] = None

        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0)) as http:
            while offset < total_size:
                chunk = content[offset : offset + chunk_size]
                chunk_len = len(chunk)
                chunk_resp = http.put(
                    upload_url,
                    headers={
                        "Content-Length": str(chunk_len),
                        "Content-Range": f"bytes {offset}-{offset + chunk_len - 1}/{total_size}",
                        "Content-Type": content_type,
                    },
                    content=chunk,
                )
                if chunk_resp.status_code not in (200, 201, 202):
                    logger.error(
                        f"Chunk upload failed at offset {offset} "
                        f"(status {chunk_resp.status_code}): {chunk_resp.text[:200]}"
                    )
                    return {
                        "message": f"Chunk upload failed: {chunk_resp.text}",
                        "status_code": chunk_resp.status_code,
                        "item": None,
                    }
                if chunk_resp.status_code in (200, 201):
                    try:
                        last_json = chunk_resp.json()
                    except Exception:
                        last_json = None
                offset += chunk_len

        if last_json:
            return {
                "message": "File uploaded successfully",
                "status_code": 201,
                "item": _format_drive_item(last_json),
            }
        return {"message": "Upload completed", "status_code": 200, "item": None}

    except MsGraphError as e:
        logger.error(f"Error creating upload session for {filename}: {e}")
        return _error_response(e, extra={"item": None})
    except httpx.HTTPError as e:
        logger.exception(f"Error uploading large file {filename}")
        return {
            "message": f"An error occurred: {e}",
            "status_code": 500,
            "item": None,
        }


def move_item(
    drive_id: str,
    item_id: str,
    new_parent_id: str,
    new_name: str = None,
) -> dict:
    """
    Move a file or folder to a different parent folder within the same drive.
    """
    body: Dict[str, Any] = {"parentReference": {"id": new_parent_id}}
    if new_name:
        body["name"] = new_name
    try:
        with MsGraphClient() as client:
            item = client.patch(
                f"drives/{drive_id}/items/{item_id}",
                json=body,
                operation_name="driveitem.move",
            )
        return {
            "message": "Item moved successfully",
            "status_code": 200,
            "item": _format_drive_item(item),
        }
    except MsGraphError as e:
        logger.error(f"Error moving item {item_id}: {e}")
        return _error_response(e, extra={"item": None})


def delete_item(drive_id: str, item_id: str) -> dict:
    """Delete a file or folder from a drive."""
    try:
        with MsGraphClient() as client:
            client.delete(
                f"drives/{drive_id}/items/{item_id}",
                operation_name="driveitem.delete",
            )
        return {"message": "Item deleted successfully", "status_code": 204}
    except MsGraphError as e:
        logger.error(f"Error deleting item {item_id}: {e}")
        return _error_response(e)


def create_folder(drive_id: str, parent_item_id: str, folder_name: str) -> dict:
    """
    Create a new folder in a drive. If a folder with the same name already
    exists (409), look it up by path and return its metadata.
    """
    payload = {
        "name": folder_name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",
    }
    try:
        with MsGraphClient() as client:
            item = client.post(
                f"drives/{drive_id}/items/{parent_item_id}/children",
                json=payload,
                operation_name="driveitem.create_folder",
            )
        return {
            "message": "Folder created successfully",
            "status_code": 201,
            "item": _format_drive_item(item),
        }
    except MsGraphError as e:
        if e.http_status == 409:
            try:
                with MsGraphClient() as client:
                    existing = client.get(
                        f"drives/{drive_id}/items/{parent_item_id}:/{folder_name}",
                        operation_name="driveitem.get_by_path",
                    )
                return {
                    "message": f"Folder '{folder_name}' already exists",
                    "status_code": 200,
                    "item": _format_drive_item(existing),
                }
            except MsGraphError as e2:
                logger.error(f"Folder exists but lookup failed for {folder_name}: {e2}")
                return {
                    "message": f"A folder with name '{folder_name}' already exists",
                    "status_code": 409,
                    "item": None,
                }
        logger.error(f"Error creating folder {folder_name}: {e}")
        return _error_response(e, extra={"item": None})


# ---------------------------------------------------------------------------
# Excel Workbook API
# ---------------------------------------------------------------------------


def get_excel_worksheets(drive_id: str, item_id: str) -> dict:
    """Get list of worksheets in an Excel workbook."""
    try:
        with MsGraphClient() as client:
            data = client.get(
                f"drives/{drive_id}/items/{item_id}/workbook/worksheets",
                timeout_tier="B",
                operation_name="excel.list_worksheets",
            )
        worksheets = data.get("value", [])
        formatted = [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "position": w.get("position"),
                "visibility": w.get("visibility"),
            }
            for w in worksheets
        ]
        return {
            "message": f"Found {len(formatted)} worksheets",
            "status_code": 200,
            "worksheets": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error getting excel worksheets for {item_id}: {e}")
        return _error_response(e, extra={"worksheets": []})


def get_excel_worksheet(drive_id: str, item_id: str, worksheet_name: str) -> dict:
    """Get a specific worksheet by name from an Excel workbook."""
    encoded_name = urllib.parse.quote(worksheet_name)
    try:
        with MsGraphClient() as client:
            ws = client.get(
                f"drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}",
                timeout_tier="B",
                operation_name="excel.get_worksheet",
            )
        return {
            "message": "Worksheet retrieved successfully",
            "status_code": 200,
            "worksheet": {
                "id": ws.get("id"),
                "name": ws.get("name"),
                "position": ws.get("position"),
                "visibility": ws.get("visibility"),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting excel worksheet {worksheet_name}: {e}")
        return _error_response(e, extra={"worksheet": None})


def _session_header(session_id: Optional[str]) -> Optional[Dict[str, str]]:
    return {"workbook-session-id": session_id} if session_id else None


def _patch_excel_range(
    *,
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    range_address: str,
    values: list,
    session_id: Optional[str],
) -> dict:
    """
    Internal: PATCH a worksheet range. Raises MsGraphError on failure.
    Callers responsible for lock ownership.
    """
    encoded_name = urllib.parse.quote(worksheet_name)
    encoded_range = urllib.parse.quote(range_address)
    with MsGraphClient() as client:
        return client.patch(
            f"drives/{drive_id}/items/{item_id}/workbook/worksheets/"
            f"{encoded_name}/range(address='{encoded_range}')",
            json={"values": values},
            extra_headers=_session_header(session_id),
            timeout_tier="B",
            operation_name="excel.update_range",
        )


def update_excel_range(
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    range_address: str,
    values: list,
    session_id: Optional[str] = None,
    max_retries: int = 3,  # retained for signature parity; retry is in MsGraphClient
) -> dict:
    """
    Update a range of cells in an Excel worksheet.

    Note: no workbook-level sp_getapplock here because writes to an explicit
    range address don't suffer from the read-then-write race. The higher-level
    `append_excel_rows` / `insert_excel_rows` paths (which compute target rows
    from usedRange or shift existing data) acquire the lock themselves.
    """
    del max_retries  # kept for backwards-compat signature; retry handled by MsGraphClient
    try:
        range_data = _patch_excel_range(
            drive_id=drive_id,
            item_id=item_id,
            worksheet_name=worksheet_name,
            range_address=range_address,
            values=values,
            session_id=session_id,
        )
        return {
            "message": "Range updated successfully",
            "status_code": 200,
            "range": {
                "address": range_data.get("address"),
                "addressLocal": range_data.get("addressLocal"),
                "cellCount": range_data.get("cellCount"),
                "columnCount": range_data.get("columnCount"),
                "rowCount": range_data.get("rowCount"),
                "values": range_data.get("values"),
            },
        }
    except MsGraphError as e:
        logger.error(
            f"Error updating excel range {range_address} in {worksheet_name}: {e}"
        )
        return _error_response(e, extra={"range": None})


def append_excel_rows(
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    values: list,
    session_id: Optional[str] = None,
) -> dict:
    """
    Append rows to an Excel worksheet. Reads usedRange then writes; the
    read + write must be atomic across processes to prevent two appenders
    from targeting the same next row, so the whole body is wrapped in
    `ms_app_lock` keyed on the workbook item_id.
    """
    encoded_name = urllib.parse.quote(worksheet_name)
    lock_resource = f"ms_excel_write:{item_id}"

    try:
        with ms_app_lock(lock_resource, timeout_ms=30000) as got_lock:
            if not got_lock:
                logger.error(
                    f"Could not acquire Excel write lock for workbook {item_id}"
                )
                return {
                    "message": f"Could not acquire Excel write lock for workbook {item_id}",
                    "status_code": 503,
                    "range": None,
                }

            with MsGraphClient() as client:
                used = client.get(
                    f"drives/{drive_id}/items/{item_id}/workbook/worksheets/"
                    f"{encoded_name}/usedRange",
                    extra_headers=_session_header(session_id),
                    timeout_tier="B",
                    operation_name="excel.used_range",
                )

            address = used.get("address", "") if isinstance(used, dict) else ""
            start_row: Optional[int] = None
            if "!" in address:
                range_part = address.split("!")[1]
                if ":" in range_part:
                    end_cell = range_part.split(":")[1]
                    import re
                    match = re.search(r"(\d+)$", end_cell)
                    if match:
                        start_row = int(match.group(1)) + 1

            if start_row is None:
                msg = (
                    f"Could not determine last row from usedRange address "
                    f"'{address}'. Aborting append to prevent data loss."
                )
                logger.error(msg)
                return {"message": msg, "status_code": 500, "range": None}

            num_rows = len(values)
            padded_values = [(row + [""] * 26)[:26] for row in values]
            range_address = f"A{start_row}:Z{start_row + num_rows - 1}"

            range_data = _patch_excel_range(
                drive_id=drive_id,
                item_id=item_id,
                worksheet_name=worksheet_name,
                range_address=range_address,
                values=padded_values,
                session_id=session_id,
            )
            return {
                "message": "Range updated successfully",
                "status_code": 200,
                "range": {
                    "address": range_data.get("address"),
                    "addressLocal": range_data.get("addressLocal"),
                    "cellCount": range_data.get("cellCount"),
                    "columnCount": range_data.get("columnCount"),
                    "rowCount": range_data.get("rowCount"),
                    "values": range_data.get("values"),
                },
            }
    except MsGraphError as e:
        logger.error(f"Error appending excel rows to {worksheet_name}: {e}")
        return _error_response(e, extra={"range": None})


def get_excel_used_range_values(
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    session_id: Optional[str] = None,
) -> dict:
    """
    Get the used range of a worksheet with all cell values. Read-only;
    no lock needed.
    """
    import re

    encoded_name = urllib.parse.quote(worksheet_name)
    try:
        with MsGraphClient() as client:
            used = client.get(
                f"drives/{drive_id}/items/{item_id}/workbook/worksheets/"
                f"{encoded_name}/usedRange",
                extra_headers=_session_header(session_id),
                timeout_tier="B",
                operation_name="excel.used_range",
            )
            address = used.get("address", "") if isinstance(used, dict) else ""
            last_row = used.get("rowCount", 1) if isinstance(used, dict) else 1
            if "!" in address:
                match = re.search(r":([A-Z]+)(\d+)$", address.split("!")[1])
                if match:
                    last_row = int(match.group(2))

            full_range = f"A1:Z{last_row}"
            encoded_range = urllib.parse.quote(full_range)
            range_data = client.get(
                f"drives/{drive_id}/items/{item_id}/workbook/worksheets/"
                f"{encoded_name}/range(address='{encoded_range}')",
                extra_headers=_session_header(session_id),
                timeout_tier="B",
                operation_name="excel.get_range",
            )
        return {
            "message": "Used range retrieved successfully",
            "status_code": 200,
            "range": {
                "address": range_data.get("address"),
                "values": range_data.get("values", []),
                "row_count": range_data.get("rowCount", 0),
                "column_count": range_data.get("columnCount", 0),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting excel used range for {worksheet_name}: {e}")
        return _error_response(e, extra={"range": None})


def insert_excel_rows(
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    row_index: int,
    values: list,
    session_id: Optional[str] = None,
    max_retries: int = 3,  # retained for signature parity; retry is in MsGraphClient
) -> dict:
    """
    Insert rows at a specific position in a worksheet, shifting existing rows
    down. Wrapped in `ms_app_lock` keyed on item_id so two inserts at the same
    row_index don't interleave (both would see pre-shift state).
    """
    del max_retries
    encoded_name = urllib.parse.quote(worksheet_name)

    num_rows = len(values)
    num_cols = len(values[0]) if values else 1

    def col_num_to_letter(n: int) -> str:
        result = ""
        while n > 0:
            n -= 1
            result = chr(65 + (n % 26)) + result
            n //= 26
        return result

    end_col = col_num_to_letter(num_cols)
    end_row = row_index + num_rows - 1
    insert_range = f"A{row_index}:{end_col}{end_row}"
    encoded_range = urllib.parse.quote(insert_range)
    lock_resource = f"ms_excel_write:{item_id}"

    try:
        with ms_app_lock(lock_resource, timeout_ms=30000) as got_lock:
            if not got_lock:
                logger.error(
                    f"Could not acquire Excel write lock for workbook {item_id}"
                )
                return {
                    "message": f"Could not acquire Excel write lock for workbook {item_id}",
                    "status_code": 503,
                    "range": None,
                }

            with MsGraphClient() as client:
                client.post(
                    f"drives/{drive_id}/items/{item_id}/workbook/worksheets/"
                    f"{encoded_name}/range(address='{encoded_range}')/insert",
                    json={"shift": "Down"},
                    extra_headers=_session_header(session_id),
                    timeout_tier="B",
                    operation_name="excel.insert_range",
                )

            range_data = _patch_excel_range(
                drive_id=drive_id,
                item_id=item_id,
                worksheet_name=worksheet_name,
                range_address=insert_range,
                values=values,
                session_id=session_id,
            )
            return {
                "message": "Range updated successfully",
                "status_code": 200,
                "range": {
                    "address": range_data.get("address"),
                    "addressLocal": range_data.get("addressLocal"),
                    "cellCount": range_data.get("cellCount"),
                    "columnCount": range_data.get("columnCount"),
                    "rowCount": range_data.get("rowCount"),
                    "values": range_data.get("values"),
                },
            }
    except MsGraphError as e:
        logger.error(f"Error inserting excel rows at {row_index}: {e}")
        return _error_response(e, extra={"range": None})


def clear_excel_range(
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    range_address: str,
    session_id: Optional[str] = None,
) -> dict:
    """Clear a range of cells in an Excel worksheet."""
    encoded_name = urllib.parse.quote(worksheet_name)
    encoded_range = urllib.parse.quote(range_address)
    try:
        with MsGraphClient() as client:
            range_data = client.post(
                f"drives/{drive_id}/items/{item_id}/workbook/worksheets/"
                f"{encoded_name}/range(address='{encoded_range}')/clear",
                json={"applyTo": "All"},
                extra_headers=_session_header(session_id),
                timeout_tier="B",
                operation_name="excel.clear_range",
            )
        return {
            "message": "Range cleared successfully",
            "status_code": 200,
            "range": {
                "address": range_data.get("address") if isinstance(range_data, dict) else None,
                "addressLocal": range_data.get("addressLocal") if isinstance(range_data, dict) else None,
            },
        }
    except MsGraphError as e:
        logger.error(f"Error clearing excel range {range_address}: {e}")
        return _error_response(e, extra={"range": None})
