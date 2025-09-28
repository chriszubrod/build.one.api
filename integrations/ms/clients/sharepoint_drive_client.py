"""SharePoint drive client for interacting with Microsoft Graph."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

import logging
import requests

LOGGER = logging.getLogger(__name__)


class SharePointDriveClientProtocol(Protocol):
    """Typed protocol describing the SharePoint drive client contract."""

    def get_items(
        self,
        site_id: str,
        item_id: Optional[str],
        access_token: str,
    ) -> List[Dict[str, Any]]:
        """Return the children for the provided site/item combination."""

    def get_item(self, site_id: str, item_id: str, access_token: str) -> Optional[bytes]:
        """Download the binary content of a SharePoint item."""


class SharePointDriveClient(SharePointDriveClientProtocol):
    """HTTP client that wraps Microsoft Graph drive interactions."""

    _BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, http_session: Optional[requests.Session] = None) -> None:
        self._http = http_session or requests.Session()

    def get_items(
        self,
        site_id: str,
        item_id: Optional[str],
        access_token: str,
    ) -> List[Dict[str, Any]]:
        """Recursively retrieve drive items for a site/folder."""

        resource_path = (
            f"sites/{site_id}/drive/items/{item_id}/children"
            if item_id
            else f"sites/{site_id}/drive/root/children"
        )
        url = f"{self._BASE_URL}/{resource_path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        params = {"$top": 200}

        items: List[Dict[str, Any]] = []
        next_link: Optional[str] = None

        while True:
            if next_link:
                response = self._http.get(next_link, headers=headers, timeout=30)
            else:
                response = self._http.get(url, headers=headers, params=params, timeout=30)

            if response.status_code != requests.codes.ok:
                LOGGER.error("Failed to retrieve SharePoint items: %s", response.text)
                raise RuntimeError(f"Unable to fetch SharePoint items: {response.text}")

            payload = response.json()
            for item in payload.get("value", []):
                file_info = item.get("file", {})
                parent_reference = item.get("parentReference", {})
                sharepoint_item: Dict[str, Any] = {
                    "msGraphDownloadUrl": item.get("@microsoft.graph.downloadUrl"),
                    "msCreatedDatetime": item.get("createdDateTime"),
                    "eTag": item.get("eTag"),
                    "id": item.get("id"),
                    "lastModifiedDateTime": item.get("lastModifiedDateTime"),
                    "name": item.get("name"),
                    "webUrl": item.get("webUrl"),
                    "cTag": item.get("cTag"),
                    "hashQuickHash": file_info.get("hashes", {}).get("quickXorHash"),
                    "mimeType": file_info.get("mimeType"),
                    "parentId": parent_reference.get("id"),
                    "sharedScope": item.get("shared", {}).get("scope"),
                    "size": item.get("size"),
                }
                items.append(sharepoint_item)

            next_link = payload.get("@odata.nextLink")
            if not next_link:
                break

        return items

    def get_item(self, site_id: str, item_id: str, access_token: str) -> Optional[bytes]:
        """Download the content for a specific drive item."""

        url = f"{self._BASE_URL}/sites/{site_id}/drive/items/{item_id}/content"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        }

        response = self._http.get(url, headers=headers, timeout=60)
        if response.status_code == requests.codes.ok:
            return response.content

        LOGGER.error(
            "Failed to download SharePoint item %s from site %s: %s",
            item_id,
            site_id,
            response.text,
        )
        return None


__all__ = ["SharePointDriveClient", "SharePointDriveClientProtocol"]
