"""Mock-based tests for vendor compliance dashboard folder linkage signal (U-106 Stage 6).

Exercises ``VendorComplianceDashboardService._build_roster_entry`` folders block
with mocked SharePoint + Box lookups — no DB or network.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from entities.vendor_compliance_document.business.dashboard_service import (
    VendorComplianceDashboardService,
)

DASHBOARD = "entities.vendor_compliance_document.business.dashboard_service"

TODAY = date(2026, 7, 21)
VENDOR = SimpleNamespace(
    id=42,
    public_id="VPID",
    name="A&A",
    abbreviation="AA",
)


def _doc_service_empty():
    doc_service = Mock()
    doc_service.read_by_vendor_id.return_value = []
    return doc_service


def _folders_from_entry(entry):
    return entry["folders"]


@patch(f"{DASHBOARD}.resolve_latest_w9_attachment", return_value=None)
@patch(f"{DASHBOARD}.BoxVendorFolderRepository")
@patch(f"{DASHBOARD}.DriveItemVendorConnector")
def test_folders_both_linked(mock_sp_cls, mock_box_repo_cls, _mock_w9):
    mock_sp_cls.return_value.get_driveitem_for_vendor.return_value = {"id": "sp-1"}
    mock_box_repo_cls.return_value.read_by_vendor_id.return_value = {"box_folder_id": "F1"}

    svc = VendorComplianceDashboardService()
    entry = svc._build_roster_entry(VENDOR, _doc_service_empty(), TODAY)
    folders = _folders_from_entry(entry)

    assert folders["sharepoint"]["status"] == "linked"
    assert folders["box"]["status"] == "linked"
    assert folders["both_linked"] is True


@patch(f"{DASHBOARD}.resolve_latest_w9_attachment", return_value=None)
@patch(f"{DASHBOARD}.BoxVendorFolderRepository")
@patch(f"{DASHBOARD}.DriveItemVendorConnector")
def test_folders_sharepoint_only(mock_sp_cls, mock_box_repo_cls, _mock_w9):
    mock_sp_cls.return_value.get_driveitem_for_vendor.return_value = {"id": "sp-1"}
    mock_box_repo_cls.return_value.read_by_vendor_id.return_value = None

    svc = VendorComplianceDashboardService()
    folders = _folders_from_entry(
        svc._build_roster_entry(VENDOR, _doc_service_empty(), TODAY)
    )

    assert folders["sharepoint"]["status"] == "linked"
    assert folders["box"]["status"] == "missing"
    assert folders["both_linked"] is False


@patch(f"{DASHBOARD}.resolve_latest_w9_attachment", return_value=None)
@patch(f"{DASHBOARD}.BoxVendorFolderRepository")
@patch(f"{DASHBOARD}.DriveItemVendorConnector")
def test_folders_neither_linked(mock_sp_cls, mock_box_repo_cls, _mock_w9):
    mock_sp_cls.return_value.get_driveitem_for_vendor.return_value = None
    mock_box_repo_cls.return_value.read_by_vendor_id.return_value = None

    svc = VendorComplianceDashboardService()
    folders = _folders_from_entry(
        svc._build_roster_entry(VENDOR, _doc_service_empty(), TODAY)
    )

    assert folders["sharepoint"]["status"] == "missing"
    assert folders["box"]["status"] == "missing"
    assert folders["both_linked"] is False


@patch(f"{DASHBOARD}.resolve_latest_w9_attachment", return_value=None)
@patch(f"{DASHBOARD}.BoxVendorFolderRepository")
@patch(f"{DASHBOARD}.DriveItemVendorConnector")
def test_folders_lookup_exception_defaults_to_missing(mock_sp_cls, mock_box_repo_cls, _mock_w9):
    mock_sp_cls.return_value.get_driveitem_for_vendor.side_effect = RuntimeError("sp down")
    mock_box_repo_cls.return_value.read_by_vendor_id.side_effect = OSError("box down")

    svc = VendorComplianceDashboardService()
    folders = _folders_from_entry(
        svc._build_roster_entry(VENDOR, _doc_service_empty(), TODAY)
    )

    assert folders["sharepoint"]["status"] == "missing"
    assert folders["box"]["status"] == "missing"
    assert folders["both_linked"] is False
