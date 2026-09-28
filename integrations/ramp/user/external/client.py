# Python Standard Library Imports
from typing import Any, Dict, List, Optional

# Local Imports
from integrations.ramp.base.client import RampHttpClient


class RampUserExternalClient:
    """Cursor-paged GET /developer/v1/users."""

    def __init__(self, http_client: RampHttpClient):
        self._http = http_client

    def list_all_users(self, *, page_size: int = 100) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        next_url: Optional[str] = "developer/v1/users"
        params: Optional[Dict[str, Any]] = {"page_size": page_size}

        while next_url:
            body = self._http.get(
                next_url,
                params=params if next_url == "developer/v1/users" else None,
                operation_name="ramp.users.list",
            )
            params = None
            data = body.get("data") or []
            if isinstance(data, list):
                items.extend(data)
            page = body.get("page") or {}
            next_link = page.get("next") if isinstance(page, dict) else None
            next_url = str(next_link) if next_link else None

        return items
