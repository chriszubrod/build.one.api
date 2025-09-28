"""Client responsible for uploading files to SharePoint."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Sequence

import base64
import io
import logging
import requests

LOGGER = logging.getLogger(__name__)


class SharePointUploadClientProtocol(Protocol):
    """Typed protocol describing SharePoint upload operations."""

    def upload_file(
        self,
        access_token: str,
        site_id: str,
        folder_id: str,
        files: Sequence[Dict[str, Any]],
        conflict_behavior: str = "replace",
    ) -> Dict[str, Any]:
        """Upload the provided files to SharePoint."""


class SharePointUploadClient(SharePointUploadClientProtocol):
    """HTTP client for uploading binary content to SharePoint."""

    _BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, http_session: Optional[requests.Session] = None) -> None:
        self._http = http_session or requests.Session()

    def upload_file(
        self,
        access_token: str,
        site_id: str,
        folder_id: str,
        files: Sequence[Dict[str, Any]],
        conflict_behavior: str = "replace",
    ) -> Dict[str, Any]:
        if not files:
            return {
                "success": False,
                "status_code": 400,
                "message": "No file provided for upload",
            }

        payload = files[0]
        file_name = payload.get("name")
        file_size = payload.get("size", 0)
        file_data = payload.get("data")

        if not file_name:
            return {
                "success": False,
                "status_code": 404,
                "message": "File name is required",
            }

        binary_data = self._decode_file_data(file_data)
        if binary_data is None:
            return {
                "success": False,
                "status_code": 400,
                "message": "Invalid file data provided",
            }

        file_buffer = io.BytesIO(binary_data)
        file_buffer.seek(0)

        if file_size and file_size > 4 * 1024 * 1024:
            result = self._large_file_upload(
                access_token,
                site_id,
                folder_id,
                file_buffer,
                file_name,
                len(binary_data),
                conflict_behavior,
            )
        else:
            result = self._simple_upload(
                access_token,
                site_id,
                folder_id,
                file_buffer,
                file_name,
                conflict_behavior,
            )

        result.setdefault("success", result.get("status_code") in {200, 201})
        return result

    def _decode_file_data(self, file_content: Any) -> Optional[bytes]:
        if isinstance(file_content, bytes):
            return file_content

        if isinstance(file_content, str):
            try:
                base64_data = file_content.split("base64,")[-1]
                return base64.b64decode(base64_data)
            except (ValueError, IndexError, TypeError) as exc:
                LOGGER.error("Invalid base64 payload provided: %%s", exc)
                return None

        LOGGER.error("Unsupported file payload type: %s", type(file_content))
        return None

    def _simple_upload(
        self,
        access_token: str,
        site_id: str,
        folder_id: str,
        file_obj: io.BytesIO,
        file_name: str,
        conflict_behavior: str,
    ) -> Dict[str, Any]:
        url = f"{self._BASE_URL}/sites/{site_id}/drive/items/{folder_id}:/{file_name}:/content"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
            "Prefer": f"respond-async,conflictBehavior={conflict_behavior}",
        }

        response = self._http.put(url, headers=headers, data=file_obj, timeout=30)
        if response.status_code == 201:
            data = response.json()
            data.update({"success": True, "status_code": 201})
            return data

        return {
            "success": False,
            "status_code": response.status_code,
            "message": f"Upload failed: {response.text}",
        }

    def _large_file_upload(
        self,
        access_token: str,
        site_id: str,
        folder_id: str,
        file_obj: io.BytesIO,
        file_name: str,
        file_size: int,
        conflict_behavior: str,
    ) -> Dict[str, Any]:
        session_url = (
            f"{self._BASE_URL}/sites/{site_id}/drive/items/{folder_id}:/{file_name}:/createUploadSession"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Prefer": f"respond-async,conflictBehavior={conflict_behavior}",
        }

        session_response = self._http.post(session_url, headers=headers, timeout=30)
        if session_response.status_code != 200:
            return {
                "status_code": session_response.status_code,
                "message": f"Failed to create upload session: {session_response.text}",
            }

        upload_url = session_response.json().get("uploadUrl")
        chunk_size = 320 * 1024

        for start in range(0, file_size, chunk_size):
            chunk = file_obj.read(chunk_size)
            chunk_end = min(start + len(chunk) - 1, file_size - 1)
            chunk_headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{chunk_end}/{file_size}",
            }
            chunk_response = self._http.put(upload_url, headers=chunk_headers, data=chunk, timeout=30)

            if chunk_response.status_code not in (201, 202):
                return {
                    "status_code": chunk_response.status_code,
                    "message": f"Failed to upload chunk: {chunk_response.text}",
                }

            if chunk_response.status_code == 201:
                data = chunk_response.json()
                data.update({"success": True, "status_code": 201})
                return data

        return {
            "status_code": 202,
            "message": "Upload session completed",
            "success": True,
        }


__all__ = ["SharePointUploadClient", "SharePointUploadClientProtocol"]
