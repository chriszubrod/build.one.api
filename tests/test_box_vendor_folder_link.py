"""Mock-based tests for BoxVendorFolderService.link_folder (U-106 Stage 6).

Patches Box HTTP, VendorService, and repos — no DB or network.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.box.folder.business.vendor_service import BoxVendorFolderService

VENDOR_SERVICE = "integrations.box.folder.business.vendor_service"

VENDOR = SimpleNamespace(id=42, public_id="VPID")
BOX_FOLDER_GET = {"id": "F1", "name": "A&A Masonry", "parent": {"id": "P1"}}


def _configure_box_http(mock_box_cls):
    mock_client = MagicMock()
    mock_client.get.return_value = BOX_FOLDER_GET
    mock_box_cls.return_value.__enter__.return_value = mock_client
    mock_box_cls.return_value.__exit__.return_value = None
    return mock_client


def _service_with_mocks(folder_repo=None, vendor_folder_repo=None):
    return BoxVendorFolderService(
        folder_repo=folder_repo or Mock(),
        vendor_folder_repo=vendor_folder_repo or Mock(),
    )


@patch(f"{VENDOR_SERVICE}.current_user_id")
@patch(f"{VENDOR_SERVICE}.VendorService")
@patch(f"{VENDOR_SERVICE}.BoxHttpClient")
def test_link_folder_fresh_link(mock_box_cls, mock_vendor_svc, mock_user_id):
    mock_user_id.get.return_value = 17
    mock_vendor_svc.return_value.read_by_public_id.return_value = VENDOR

    mock_client = _configure_box_http(mock_box_cls)

    folder_repo = Mock()
    folder_repo.read_by_box_folder_id.return_value = None
    folder_repo.create.return_value = SimpleNamespace(id=7)

    final_mapping = {"vendor_id": 42, "box_folder_id": "F1"}
    vendor_folder_repo = Mock()
    vendor_folder_repo.read_by_vendor_id.side_effect = [None, final_mapping]
    vendor_folder_repo.read_by_box_folder_id.return_value = None

    svc = _service_with_mocks(folder_repo=folder_repo, vendor_folder_repo=vendor_folder_repo)
    result = svc.link_folder("VPID", "F1")

    mock_client.get.assert_called_once_with(
        "folders/F1",
        params={"fields": "id,name,parent"},
        operation_name="box.vendor_folder.get",
    )
    folder_repo.create.assert_called_once_with(
        box_folder_id="F1",
        name="A&A Masonry",
        parent_box_folder_id="P1",
    )
    vendor_folder_repo.create.assert_called_once_with(
        vendor_id=42,
        box_folder_id=7,
        created_by_user_id=17,
    )
    folder_repo.read_by_box_folder_id.assert_called_once_with("F1")
    assert result == final_mapping


@patch(f"{VENDOR_SERVICE}.VendorService")
@patch(f"{VENDOR_SERVICE}.BoxHttpClient")
def test_link_folder_idempotent_same_folder(mock_box_cls, mock_vendor_svc):
    mock_vendor_svc.return_value.read_by_public_id.return_value = VENDOR
    _configure_box_http(mock_box_cls)

    existing = {"box_folder_id": "F1", "vendor_id": 42}
    folder_repo = Mock()
    vendor_folder_repo = Mock()
    vendor_folder_repo.read_by_vendor_id.return_value = existing

    svc = _service_with_mocks(folder_repo=folder_repo, vendor_folder_repo=vendor_folder_repo)
    assert svc.link_folder("VPID", "F1") == existing

    folder_repo.create.assert_not_called()
    vendor_folder_repo.create.assert_not_called()


@patch(f"{VENDOR_SERVICE}.VendorService")
@patch(f"{VENDOR_SERVICE}.BoxHttpClient")
def test_link_folder_conflict_different_folder(mock_box_cls, mock_vendor_svc):
    mock_vendor_svc.return_value.read_by_public_id.return_value = VENDOR
    _configure_box_http(mock_box_cls)

    vendor_folder_repo = Mock()
    vendor_folder_repo.read_by_vendor_id.return_value = {"box_folder_id": "OTHER"}

    svc = _service_with_mocks(vendor_folder_repo=vendor_folder_repo)
    with pytest.raises(ValueError, match="unlink"):
        svc.link_folder("VPID", "F1")


@patch(f"{VENDOR_SERVICE}.VendorService")
@patch(f"{VENDOR_SERVICE}.BoxHttpClient")
def test_link_folder_box_folder_taken_by_another_vendor(mock_box_cls, mock_vendor_svc):
    mock_vendor_svc.return_value.read_by_public_id.return_value = VENDOR
    _configure_box_http(mock_box_cls)

    folder_repo = Mock()
    folder_repo.read_by_box_folder_id.return_value = SimpleNamespace(id=7)

    vendor_folder_repo = Mock()
    vendor_folder_repo.read_by_vendor_id.return_value = None
    vendor_folder_repo.read_by_box_folder_id.return_value = {"vendor_id": 99}

    svc = _service_with_mocks(folder_repo=folder_repo, vendor_folder_repo=vendor_folder_repo)
    with pytest.raises(ValueError, match="already linked"):
        svc.link_folder("VPID", "F1")

    vendor_folder_repo.create.assert_not_called()
    folder_repo.create.assert_not_called()
