# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.errors import QboError, QboValidationError
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo,
    QboCompanyInfoResponse,
)

logger = logging.getLogger(__name__)


def _format_datetime_for_qbo_query(datetime_input) -> Optional[str]:
    """Format a datetime for a QBO query WHERE clause (ISO 8601 with +HH:MM offset)."""
    if not datetime_input:
        return None if datetime_input is None else str(datetime_input)

    if isinstance(datetime_input, datetime):
        datetime_str = datetime_input.isoformat()
    else:
        datetime_str = str(datetime_input)

    dt_str = datetime_str.rstrip("Z")
    if dt_str.endswith("+00:00"):
        dt_str = dt_str[:-6]

    try:
        if "T" in dt_str:
            if "." in dt_str:
                dt_str = dt_str.split(".")[0]
            if dt_str.count(":") == 1:
                dt_str += ":00"
        else:
            dt_str += "T00:00:00"
        return f"{dt_str}+00:00"
    except Exception as error:
        logger.warning(
            f"Failed to format datetime '{datetime_str}' for QBO query: {error}. Using as-is."
        )
        return datetime_str


class QboCompanyInfoClient:
    """
    Client for QBO CompanyInfo endpoints. Composes `QboHttpClient` for transport.
    """

    def __init__(
        self,
        *,
        realm_id: str,
        http_client: Optional[QboHttpClient] = None,
        minor_version: int = 65,
    ):
        self.realm_id = realm_id
        self._owns_http_client = http_client is None
        self._http_client = http_client or QboHttpClient(
            realm_id=realm_id,
            minor_version=minor_version,
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> "QboCompanyInfoClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_company_info(
        self,
        company_id: Optional[str] = None,
        last_updated_time: Optional[str] = None,
    ) -> QboCompanyInfo:
        """
        Retrieve company information from QuickBooks.

        If `company_id` is provided, fetches that specific CompanyInfo directly.
        Otherwise uses the query endpoint (with optional delta filter).
        """
        if company_id:
            data = self._http_client.get(
                f"companyinfo/{company_id}",
                operation_name="qbo.company_info.get",
            )
            return QboCompanyInfoResponse(**data).company_info

        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time)
            query_string = (
                f"SELECT * FROM CompanyInfo WHERE Metadata.LastUpdatedTime > '{formatted}'"
            )
            logger.debug(
                f"Querying CompanyInfo with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        else:
            query_string = "SELECT * FROM CompanyInfo"

        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.company_info.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if query_response:
            company_info_list = query_response.get("CompanyInfo", [])
            if company_info_list:
                return QboCompanyInfo(**company_info_list[0])
            raise ValueError("No CompanyInfo found in query response")

        return QboCompanyInfoResponse(**data).company_info

    def update_company_info(
        self,
        company_info: QboCompanyInfo,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboCompanyInfo:
        """
        Update company information in QuickBooks Online.

        The `idempotency_key` is a stable QBO `?requestid=` value. Pass a
        caller-supplied key from the outbox row so retries deduplicate on
        QBO's side. When omitted, the shared client auto-generates a fresh
        UUID per call.

        Note: QBO CompanyInfo API has limited update capabilities. Only certain
        fields may be updatable; expect QboValidationError on unsupported fields.
        """
        if not company_info.id:
            raise QboValidationError("CompanyInfo ID is required for update")

        payload = {"CompanyInfo": company_info.model_dump(exclude_none=True, by_alias=True)}

        try:
            data = self._http_client.post(
                f"companyinfo/{company_info.id}",
                json=payload,
                idempotency_key=idempotency_key,
                operation_name="qbo.company_info.update",
            )
            if "CompanyInfo" in data:
                return QboCompanyInfo(**data["CompanyInfo"])
            if "QueryResponse" in data:
                query_response = data["QueryResponse"]
                company_info_list = query_response.get("CompanyInfo", [])
                if company_info_list:
                    return QboCompanyInfo(**company_info_list[0])
            return QboCompanyInfoResponse(**data).company_info
        except QboValidationError as error:
            logger.warning(
                f"CompanyInfo update may not be supported or validation failed: {error}. "
                "QBO CompanyInfo API has limited update capabilities."
            )
            raise
        except QboError:
            raise
        except Exception as error:
            logger.error(f"Error updating CompanyInfo: {error}")
            raise QboError(f"Failed to update CompanyInfo: {error}")
