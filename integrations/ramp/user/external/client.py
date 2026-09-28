# Python Standard Library Imports
from typing import Any, Dict, List

# Local Imports
from integrations.ramp.base.client import RampHttpClient


class RampUserExternalClient:
    """Cursor-paged GET /developer/v1/users."""

    def __init__(self, http_client: RampHttpClient):
        self._http = http_client

    def list_all_users(self, *, page_size: int = 100) -> List[Dict[str, Any]]:
        return self._http._paginate(
            "developer/v1/users",
            params={"page_size": page_size},
            operation_name="ramp.users.list",
        )
