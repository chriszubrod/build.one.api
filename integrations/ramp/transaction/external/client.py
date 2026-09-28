# Python Standard Library Imports
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Local Imports
from integrations.ramp.base.client import RampHttpClient
from integrations.ramp.base.errors import RampNotFoundError


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

        return self._http._paginate(
            "developer/v1/transactions",
            params={
                "from_date": from_date,
                "page_size": page_size,
            },
            operation_name="ramp.transactions.list",
        )

    def get_transaction(self, ramp_transaction_id: str) -> Optional[Dict[str, Any]]:
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
