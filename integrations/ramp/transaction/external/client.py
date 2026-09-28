# Python Standard Library Imports
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Local Imports
from integrations.ramp.base.client import RampHttpClient


class RampTransactionExternalClient:
    """Cursor-paged GET /developer/v1/transactions."""

    def __init__(self, http_client: RampHttpClient):
        self._http = http_client

    def list_transactions_in_window(
        self,
        *,
        window_days: int,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        from_date = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        items: List[Dict[str, Any]] = []
        next_url: Optional[str] = "developer/v1/transactions"
        params: Optional[Dict[str, Any]] = {
            "from_date": from_date,
            "page_size": page_size,
        }

        while next_url:
            body = self._http.get(
                next_url,
                params=params if next_url == "developer/v1/transactions" else None,
                operation_name="ramp.transactions.list",
            )
            params = None
            data = body.get("data") or []
            if isinstance(data, list):
                items.extend(data)
            page = body.get("page") or {}
            next_link = page.get("next") if isinstance(page, dict) else None
            next_url = str(next_link) if next_link else None

        return items

    def get_transaction(self, ramp_transaction_id: str) -> Optional[Dict[str, Any]]:
        from integrations.ramp.base.errors import RampNotFoundError

        try:
            body = self._http.get(
                f"developer/v1/transactions/{ramp_transaction_id}",
                operation_name="ramp.transactions.get",
            )
        except RampNotFoundError:
            return None
        if isinstance(body, dict) and body.get("id"):
            return body
        return None
