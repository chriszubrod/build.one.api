"""TaxpayerW9IngestService.ingest wiring tests (mocked collaborators, no DB/network).

End-to-end extract() is not covered here — it pulls DI + folder I/O; parser coverage
lives in tests/test_w9_parser.py.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.taxpayer.business.ingest_service import TaxpayerW9IngestService

TAXPAYER_SERVICE = "entities.taxpayer.business.service.TaxpayerService"
TAXPAYER_ATTACHMENT_SERVICE = (
    "entities.taxpayer_attachment.business.service.TaxpayerAttachmentService"
)
VENDOR_SERVICE = "entities.vendor.business.service.VendorService"


def _vendor():
    return SimpleNamespace(id=27, public_id="VPID", row_version="rv")


def _existing_taxpayer():
    return SimpleNamespace(
        public_id="TPID",
        to_dict=lambda: {"public_id": "TPID"},
    )


def _new_taxpayer():
    return SimpleNamespace(
        public_id="NEW",
        to_dict=lambda: {"public_id": "NEW"},
    )


@patch(VENDOR_SERVICE)
@patch(TAXPAYER_ATTACHMENT_SERVICE)
@patch(TAXPAYER_SERVICE)
def test_ingest_reuses_existing_taxpayer(mock_taxpayer_cls, mock_attach_cls, mock_vendor_cls):
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_taxpayer_cls.return_value.read_by_taxpayer_id_number.return_value = (
        _existing_taxpayer()
    )
    mock_attach_cls.return_value.create = MagicMock()
    mock_vendor_cls.return_value.update_by_public_id = MagicMock()

    result = TaxpayerW9IngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        entity_name="John A Smith",
        business_name="Smith Consulting LLC",
        classification="LLC",
        taxpayer_id_number="123456789",
        is_signed=True,
        signature_date="2026-03-15",
    )

    mock_taxpayer_cls.return_value.create.assert_not_called()
    mock_attach_cls.return_value.create.assert_called_once_with(
        taxpayer_public_id="TPID",
        attachment_public_id="AID",
    )
    mock_vendor_cls.return_value.update_by_public_id.assert_called_once_with(
        "VPID",
        row_version="rv",
        taxpayer_public_id="TPID",
    )
    assert result["reused_existing"] is True
    assert result["taxpayer"]["public_id"] == "TPID"
    assert result["vendor_public_id"] == "VPID"


@patch(VENDOR_SERVICE)
@patch(TAXPAYER_ATTACHMENT_SERVICE)
@patch(TAXPAYER_SERVICE)
def test_ingest_creates_taxpayer_when_tin_unknown(
    mock_taxpayer_cls, mock_attach_cls, mock_vendor_cls
):
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_taxpayer_cls.return_value.read_by_taxpayer_id_number.return_value = None
    mock_taxpayer_cls.return_value.create.return_value = _new_taxpayer()
    mock_attach_cls.return_value.create = MagicMock()
    mock_vendor_cls.return_value.update_by_public_id = MagicMock()

    result = TaxpayerW9IngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        entity_name="Jane Doe",
        business_name="Doe LLC",
        classification="LLC",
        taxpayer_id_number="987654321",
        is_signed=False,
        signature_date=None,
    )

    mock_taxpayer_cls.return_value.create.assert_called_once_with(
        entity_name="Jane Doe",
        business_name="Doe LLC",
        classification="LLC",
        taxpayer_id_number="987654321",
        is_signed=0,
        signature_date=None,
    )
    mock_attach_cls.return_value.create.assert_called_once_with(
        taxpayer_public_id="NEW",
        attachment_public_id="AID",
    )
    mock_vendor_cls.return_value.update_by_public_id.assert_called_once_with(
        "VPID",
        row_version="rv",
        taxpayer_public_id="NEW",
    )
    assert result["reused_existing"] is False
    assert result["taxpayer"]["public_id"] == "NEW"
