"""BusinessLicenseIngestService.ingest wiring tests (mocked collaborators, no DB/network)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.business_license.business.ingest_service import BusinessLicenseIngestService

VENDOR_SERVICE = "entities.vendor.business.service.VendorService"
BUSINESS_LICENSE_SERVICE = "entities.business_license.business.service.BusinessLicenseService"
BUSINESS_LICENSE_ATTACHMENT_SERVICE = (
    "entities.business_license_attachment.business.service.BusinessLicenseAttachmentService"
)


def _vendor():
    return SimpleNamespace(id=27, public_id="VPID", row_version="rv")


def _created_license():
    return SimpleNamespace(
        public_id="BLID",
        to_dict=lambda: {"public_id": "BLID"},
    )


@patch(BUSINESS_LICENSE_ATTACHMENT_SERVICE)
@patch(BUSINESS_LICENSE_SERVICE)
@patch(VENDOR_SERVICE)
def test_ingest_creates_license_and_attachment_link(
    mock_vendor_cls, mock_bl_cls, mock_bl_attach_cls
):
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_bl_cls.return_value.create.return_value = _created_license()
    mock_bl_attach_cls.return_value.create = MagicMock()

    result = BusinessLicenseIngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        license_number="1000432371",
        issuing_authority="City of Spring Hill",
        issue_date="2023-09-19",
        expiry_date="2024-05-15",
        verification_status="Received",
    )

    mock_bl_cls.return_value.create.assert_called_once_with(
        vendor_public_id="VPID",
        license_number="1000432371",
        issuing_authority="City of Spring Hill",
        issue_date="2023-09-19",
        expiry_date="2024-05-15",
        verification_status="Received",
    )
    mock_bl_attach_cls.return_value.create.assert_called_once_with(
        business_license_public_id="BLID",
        attachment_public_id="AID",
    )
    assert result["business_license"]["public_id"] == "BLID"
    assert result["vendor_public_id"] == "VPID"
