"""Mock-based tests for VendorComplianceDashboardService contractors license slot (U-114)."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from entities.vendor_compliance_document.business.dashboard_service import (
    VendorComplianceDashboardService,
)

DASHBOARD = "entities.vendor_compliance_document.business.dashboard_service"

TODAY = date(2026, 7, 21)
VENDOR = SimpleNamespace(id=27)


def _contractors_license(*, expiry_date, verification_status="Verified"):
    return SimpleNamespace(
        public_id="CLID",
        license_number="49103",
        issuing_authority="State of TN",
        classification="CE",
        expiry_date=expiry_date,
        verification_status=verification_status,
    )


@patch(
    f"{DASHBOARD}.resolve_contractors_license_attachment",
    return_value=SimpleNamespace(public_id="ATT"),
)
@patch(f"{DASHBOARD}.resolve_current_contractors_license")
def test_contractors_license_slot_valid_future_expiry(mock_resolve_cl, _mock_resolve_att):
    mock_resolve_cl.return_value = _contractors_license(expiry_date="2027-07-31")

    slot = VendorComplianceDashboardService()._build_contractors_license_slot(VENDOR, TODAY)

    assert slot["status"] == "valid"
    assert slot["document_public_id"] == "CLID"
    assert slot["document_number"] == "49103"
    assert slot["classification"] == "CE"
    assert slot["attachment_public_id"] == "ATT"


@patch(
    f"{DASHBOARD}.resolve_contractors_license_attachment",
    return_value=SimpleNamespace(public_id="ATT"),
)
@patch(f"{DASHBOARD}.resolve_current_contractors_license")
def test_contractors_license_slot_expired(mock_resolve_cl, _mock_resolve_att):
    mock_resolve_cl.return_value = _contractors_license(expiry_date="2024-05-15")

    slot = VendorComplianceDashboardService()._build_contractors_license_slot(VENDOR, TODAY)

    assert slot["status"] == "expired"


@patch(f"{DASHBOARD}.resolve_contractors_license_attachment")
@patch(f"{DASHBOARD}.resolve_current_contractors_license", return_value=None)
def test_contractors_license_slot_missing(_mock_resolve_cl, _mock_resolve_att):
    slot = VendorComplianceDashboardService()._build_contractors_license_slot(VENDOR, TODAY)

    assert slot == {"status": "missing"}
