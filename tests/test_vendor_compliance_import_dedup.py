"""Unit tests for VendorFolderService._find_existing_duplicate (U-104, no DB)."""
from types import SimpleNamespace
from unittest.mock import patch

from entities.vendor_compliance_document.business.folder_service import VendorFolderService

FOLDER_SERVICE = "entities.vendor_compliance_document.business.folder_service"

DOC_TYPE = "CERTIFICATE_OF_INSURANCE"
HASH_X = "HASHX"
HASH_OTHER = "OTHERHASH"
VENDOR_ID = 42


def _matching_doc():
    return SimpleNamespace(
        document_type=DOC_TYPE,
        attachment_id="5",
        to_dict=lambda: {"document_type": DOC_TYPE, "attachment_id": "5"},
    )


@patch(f"{FOLDER_SERVICE}.AttachmentService")
@patch(f"{FOLDER_SERVICE}.VendorComplianceDocumentService")
def test_same_type_and_hash_returns_doc(mock_vcd_cls, mock_att_cls):
    doc = _matching_doc()
    mock_vcd_cls.return_value.read_by_vendor_id.return_value = [doc]
    mock_att_cls.return_value.read_by_ids.return_value = [
        SimpleNamespace(id=5, file_hash=HASH_X),
    ]

    result = VendorFolderService()._find_existing_duplicate(VENDOR_ID, DOC_TYPE, HASH_X)

    assert result is doc
    mock_vcd_cls.return_value.read_by_vendor_id.assert_called_once_with(VENDOR_ID)
    mock_att_cls.return_value.read_by_ids.assert_called_once_with([5])


@patch(f"{FOLDER_SERVICE}.AttachmentService")
@patch(f"{FOLDER_SERVICE}.VendorComplianceDocumentService")
def test_same_type_different_hash_returns_none(mock_vcd_cls, mock_att_cls):
    doc = _matching_doc()
    mock_vcd_cls.return_value.read_by_vendor_id.return_value = [doc]
    mock_att_cls.return_value.read_by_ids.return_value = [
        SimpleNamespace(id=5, file_hash=HASH_OTHER),
    ]

    result = VendorFolderService()._find_existing_duplicate(VENDOR_ID, DOC_TYPE, HASH_X)

    assert result is None


@patch(f"{FOLDER_SERVICE}.AttachmentService")
@patch(f"{FOLDER_SERVICE}.VendorComplianceDocumentService")
def test_doc_without_attachment_id_skipped_safely(mock_vcd_cls, mock_att_cls):
    doc_no_att = SimpleNamespace(
        document_type=DOC_TYPE,
        attachment_id=None,
        to_dict=lambda: {},
    )
    mock_vcd_cls.return_value.read_by_vendor_id.return_value = [doc_no_att]

    result = VendorFolderService()._find_existing_duplicate(VENDOR_ID, DOC_TYPE, HASH_X)

    assert result is None
    mock_att_cls.return_value.read_by_ids.assert_not_called()


@patch(f"{FOLDER_SERVICE}.AttachmentService")
@patch(f"{FOLDER_SERVICE}.VendorComplianceDocumentService")
def test_no_existing_docs_returns_none_and_skips_attachment_read(mock_vcd_cls, mock_att_cls):
    mock_vcd_cls.return_value.read_by_vendor_id.return_value = []

    result = VendorFolderService()._find_existing_duplicate(VENDOR_ID, DOC_TYPE, HASH_X)

    assert result is None
    mock_att_cls.return_value.read_by_ids.assert_not_called()
