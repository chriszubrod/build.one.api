"""CertificateOfInsuranceIngestService.ingest wiring tests (mocked collaborators, no DB/network)."""
from types import SimpleNamespace
from unittest.mock import patch

from entities.certificate_of_insurance.business.ingest_service import (
    CertificateOfInsuranceIngestService,
)

VENDOR_SERVICE = "entities.vendor.business.service.VendorService"
ATTACHMENT_SERVICE = "entities.attachment.business.service.AttachmentService"
COI_SERVICE = "entities.certificate_of_insurance.business.service.CertificateOfInsuranceService"
VIP_SERVICE = "entities.vendor_insurance_policy.business.service.VendorInsurancePolicyService"


def _vendor():
    return SimpleNamespace(id=27, public_id="VPID")


def _attachment():
    return SimpleNamespace(id=555)


def _created_cert():
    return SimpleNamespace(
        public_id="COIID",
        to_dict=lambda: {"public_id": "COIID"},
    )


@patch(VIP_SERVICE)
@patch(COI_SERVICE)
@patch(ATTACHMENT_SERVICE)
@patch(VENDOR_SERVICE)
def test_ingest_creates_cert_and_policies(
    mock_vendor_cls,
    mock_attachment_cls,
    mock_coi_cls,
    mock_vip_cls,
):
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_attachment_cls.return_value.read_by_public_id.return_value = _attachment()
    mock_coi_cls.return_value.create.return_value = _created_cert()
    mock_vip_cls.return_value.create.return_value = SimpleNamespace(public_id="POLID")

    result = CertificateOfInsuranceIngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        issuing_authority="Acme",
        issue_date="2026-07-01",
        policies=[
            {"coverage_type": "GL", "expiry_date": "2027-01-01"},
            {"coverage_type": "WC", "expiry_date": "2027-02-24"},
        ],
    )

    mock_coi_cls.return_value.create.assert_called_once_with(
        vendor_public_id="VPID",
        issuing_authority="Acme",
        issue_date="2026-07-01",
        attachment_id=555,
        verification_status="Received",
    )
    assert mock_vip_cls.return_value.create.call_count == 2
    vip_calls = mock_vip_cls.return_value.create.call_args_list
    assert vip_calls[0].kwargs["certificate_of_insurance_public_id"] == "COIID"
    assert vip_calls[0].kwargs["coverage_type"] == "GL"
    assert vip_calls[1].kwargs["certificate_of_insurance_public_id"] == "COIID"
    assert vip_calls[1].kwargs["coverage_type"] == "WC"
    assert result["policy_count"] == 2
    assert result["vendor_public_id"] == "VPID"


@patch(VIP_SERVICE)
@patch(COI_SERVICE)
@patch(ATTACHMENT_SERVICE)
@patch(VENDOR_SERVICE)
def test_ingest_raises_on_missing_attachment(
    mock_vendor_cls, mock_attachment_cls, mock_coi_cls, mock_vip_cls
):
    """Codex #2: an unresolvable attachment must NOT create a PDF-less cert."""
    import pytest

    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_attachment_cls.return_value.read_by_public_id.return_value = None

    with pytest.raises(ValueError, match="Attachment with public_id"):
        CertificateOfInsuranceIngestService().ingest("VPID", attachment_public_id="BAD", policies=[])
    mock_coi_cls.return_value.create.assert_not_called()


@patch(VIP_SERVICE)
@patch(COI_SERVICE)
@patch(ATTACHMENT_SERVICE)
@patch(VENDOR_SERVICE)
def test_ingest_prevalidates_policies_before_any_write(
    mock_vendor_cls, mock_attachment_cls, mock_coi_cls, mock_vip_cls
):
    """Codex #3: a malformed policy must fail BEFORE the cert or any policy is created."""
    import pytest

    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_attachment_cls.return_value.read_by_public_id.return_value = _attachment()

    with pytest.raises(ValueError, match="invalid coverage_type"):
        CertificateOfInsuranceIngestService().ingest(
            "VPID",
            attachment_public_id="AID",
            policies=[{"coverage_type": "GL"}, {"coverage_type": "BOGUS"}],
        )
    mock_coi_cls.return_value.create.assert_not_called()
    mock_vip_cls.return_value.create.assert_not_called()
