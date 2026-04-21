# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.physical_address.external.schemas import (
    QboCompanyInfoResponse,
    QboPhysicalAddress,
)

logger = logging.getLogger(__name__)


class QboPhysicalAddressClient:
    """
    Client for retrieving PhysicalAddress data from QBO CompanyInfo.

    QBO does not expose PhysicalAddress as a first-class resource; addresses
    are nested inside CompanyInfo (and Vendor, Customer, etc.). This client
    wraps the CompanyInfo endpoint to surface the physical address.
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

    def __enter__(self) -> "QboPhysicalAddressClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_company_info(self) -> QboCompanyInfoResponse:
        """Retrieve CompanyInfo from QuickBooks (includes PhysicalAddress)."""
        data = self._http_client.get("companyinfo", operation_name="qbo.physical_address.company_info")
        return QboCompanyInfoResponse(**data)

    def get_physical_address(self, qbo_id: Optional[str] = None) -> Optional[QboPhysicalAddress]:
        """
        Retrieve PhysicalAddress from CompanyInfo.

        Args:
            qbo_id: Unused; kept for API consistency with other QBO entity
                    clients that accept an identifier.
        """
        company_info_response = self.get_company_info()
        return company_info_response.physical_address
