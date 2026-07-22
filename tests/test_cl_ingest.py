"""ContractorsLicenseIngestService.ingest wiring tests (mocked collaborators, no DB/network)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.contractors_license.business.ingest_service import ContractorsLicenseIngestService

VENDOR_SERVICE = "entities.vendor.business.service.VendorService"
CONTRACTORS_LICENSE_SERVICE = "entities.contractors_license.business.service.ContractorsLicenseService"
CONTRACTORS_LICENSE_ATTACHMENT_SERVICE = (
    "entities.contractors_license_attachment.business.service.ContractorsLicenseAttachmentService"
)


def _vendor():
    return SimpleNamespace(id=27, public_id="VPID", row_version="rv")


def _created_contractors_license():
    return SimpleNamespace(
        public_id="CLID",
        to_dict=lambda: {"public_id": "CLID"},
    )


@patch(CONTRACTORS_LICENSE_ATTACHMENT_SERVICE)
@patch(CONTRACTORS_LICENSE_SERVICE)
@patch(VENDOR_SERVICE)
def test_ingest_creates_license_and_attachment_link(
    mock_vendor_cls, mock_cl_cls, mock_cl_attach_cls
):
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_cl_cls.return_value.create.return_value = _created_contractors_license()
    mock_cl_attach_cls.return_value.create = MagicMock()

    result = ContractorsLicenseIngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        license_number="49103",
        issuing_authority="State of Tennessee",
        classification="CE",
        issue_date=None,
        expiry_date="2027-07-31",
        verification_status="Received",
    )

    mock_cl_cls.return_value.create.assert_called_once_with(
        vendor_public_id="VPID",
        license_number="49103",
        issuing_authority="State of Tennessee",
        classification="CE",
        issue_date=None,
        expiry_date="2027-07-31",
        verification_status="Received",
    )
    mock_cl_attach_cls.return_value.create.assert_called_once_with(
        contractors_license_public_id="CLID",
        attachment_public_id="AID",
    )
    assert result["contractors_license"]["public_id"] == "CLID"
    assert result["vendor_public_id"] == "VPID"
