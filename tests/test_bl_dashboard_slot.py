"""Mock-based tests for VendorComplianceDashboardService business license slot (U-112)."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from entities.vendor_compliance.business.dashboard_service import (
    VendorComplianceDashboardService,
)

DASHBOARD = "entities.vendor_compliance.business.dashboard_service"

TODAY = date(2026, 7, 21)
VENDOR = SimpleNamespace(id=27)


def _business_license(*, expiry_date, verification_status="Verified"):
    return SimpleNamespace(
        public_id="BLID",
        license_number="1000432371",
        issuing_authority="City of Spring Hill",
        expiry_date=expiry_date,
        verification_status=verification_status,
    )


@patch(
    f"{DASHBOARD}.resolve_business_license_attachment",
    return_value=SimpleNamespace(public_id="ATT"),
)
@patch(f"{DASHBOARD}.resolve_current_business_license")
def test_business_license_slot_valid_future_expiry(mock_resolve_bl, _mock_resolve_att):
    mock_resolve_bl.return_value = _business_license(expiry_date="2027-01-01")

    slot = VendorComplianceDashboardService()._build_business_license_slot(VENDOR, TODAY)

    assert slot["status"] == "valid"
    assert slot["document_public_id"] == "BLID"
    assert slot["document_number"] == "1000432371"
    assert slot["attachment_public_id"] == "ATT"


@patch(
    f"{DASHBOARD}.resolve_business_license_attachment",
    return_value=SimpleNamespace(public_id="ATT"),
)
@patch(f"{DASHBOARD}.resolve_current_business_license")
def test_business_license_slot_expired(mock_resolve_bl, _mock_resolve_att):
    mock_resolve_bl.return_value = _business_license(expiry_date="2024-05-15")

    slot = VendorComplianceDashboardService()._build_business_license_slot(VENDOR, TODAY)

    assert slot["status"] == "expired"


@patch(f"{DASHBOARD}.resolve_business_license_attachment")
@patch(f"{DASHBOARD}.resolve_current_business_license", return_value=None)
def test_business_license_slot_missing(_mock_resolve_bl, _mock_resolve_att):
    slot = VendorComplianceDashboardService()._build_business_license_slot(VENDOR, TODAY)

    assert slot == {"status": "missing"}
