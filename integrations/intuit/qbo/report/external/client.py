# Python Standard Library Imports
import logging
from typing import Any, Dict, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.errors import QboValidationError

logger = logging.getLogger(__name__)


# Conservative allowlist for the PoC. Intuit Reports API supports more, but we
# only expose the ones we've identified as candidates for build.one. New report
# names should be added here intentionally rather than passed straight through,
# so an upstream typo can't trigger a 404 hit on the QBO API.
ALLOWED_REPORTS: frozenset[str] = frozenset({
    "UnbilledCharges",
    "ProfitAndLoss",
    "BalanceSheet",
    "GeneralLedger",
    "TransactionList",
    "TrialBalance",
    "CashFlow",
    "APAgingDetail",
    "APAgingSummary",
    "ARAgingDetail",
    "ARAgingSummary",
    "VendorBalance",
    "VendorBalanceDetail",
    "CustomerBalance",
    "CustomerBalanceDetail",
})


class QboReportClient:
    """
    Client for QBO Reports endpoints. Composes `QboHttpClient` for transport.

    Reports are read-only and return a deeply nested JSON shape
    (`{Header, Columns, Rows}`) that varies per report. This client passes the
    response through unchanged — shaping is the caller's responsibility.
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

    def __enter__(self) -> "QboReportClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_report(
        self,
        report_name: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if report_name not in ALLOWED_REPORTS:
            raise QboValidationError(
                f"Unsupported QBO report: {report_name}. "
                f"Allowed: {sorted(ALLOWED_REPORTS)}"
            )

        return self._http_client.get(
            f"reports/{report_name}",
            params=params or None,
            operation_name=f"qbo.report.{report_name.lower()}",
        )
