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


@patch(VENDOR_SERVICE)
@patch(TAXPAYER_ATTACHMENT_SERVICE)
@patch(TAXPAYER_SERVICE)
@pytest.mark.parametrize("blank_business", [None, "", "   "])
def test_ingest_defaults_blank_business_name_to_entity_name(
    mock_taxpayer_cls, mock_attach_cls, mock_vendor_cls, blank_business
):
    """A W-9 with no line-2 business name must not 500 on the NOT NULL column —
    it persists under the legal entity name (dbo.Taxpayer.BusinessName is NOT NULL)."""
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_taxpayer_cls.return_value.read_by_taxpayer_id_number.return_value = None
    mock_taxpayer_cls.return_value.create.return_value = _new_taxpayer()

    TaxpayerW9IngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        entity_name="Engineered Comfort, Inc.",
        business_name=blank_business,
        classification="LLC",
        taxpayer_id_number="741470916",
        is_signed=True,
        signature_date="2023-07-05",
    )

    kwargs = mock_taxpayer_cls.return_value.create.call_args.kwargs
    assert kwargs["business_name"] == "Engineered Comfort, Inc."
    assert kwargs["classification"] == "LLC"


@patch(VENDOR_SERVICE)
@patch(TAXPAYER_ATTACHMENT_SERVICE)
@patch(TAXPAYER_SERVICE)
@pytest.mark.parametrize("blank_class", [None, "", "  "])
def test_ingest_defaults_blank_classification(
    mock_taxpayer_cls, mock_attach_cls, mock_vendor_cls, blank_class
):
    """An undetected federal tax classification stores as 'Unspecified' rather than
    500-ing on the NOT NULL column (extract's `unresolved` flags it for review)."""
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_taxpayer_cls.return_value.read_by_taxpayer_id_number.return_value = None
    mock_taxpayer_cls.return_value.create.return_value = _new_taxpayer()

    TaxpayerW9IngestService().ingest(
        "VPID",
        attachment_public_id="AID",
        entity_name="Acme Co",
        business_name="Acme Co",
        classification=blank_class,
        taxpayer_id_number="111223333",
        is_signed=True,
        signature_date=None,
    )

    kwargs = mock_taxpayer_cls.return_value.create.call_args.kwargs
    assert kwargs["classification"] == "Unspecified"


@patch(VENDOR_SERVICE)
@patch(TAXPAYER_ATTACHMENT_SERVICE)
@patch(TAXPAYER_SERVICE)
def test_ingest_raises_on_blank_entity_name(
    mock_taxpayer_cls, mock_attach_cls, mock_vendor_cls
):
    """A W-9 with no legal name is unusable — surface a clear 400-mapped ValueError,
    not a cryptic SQL 515 (and never create a taxpayer)."""
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()
    mock_taxpayer_cls.return_value.read_by_taxpayer_id_number.return_value = None

    with pytest.raises(ValueError, match="entity_name"):
        TaxpayerW9IngestService().ingest(
            "VPID",
            attachment_public_id="AID",
            entity_name="   ",
            business_name=None,
            classification=None,
            taxpayer_id_number="222334444",
            is_signed=True,
            signature_date=None,
        )

    mock_taxpayer_cls.return_value.create.assert_not_called()


@patch(VENDOR_SERVICE)
@patch(TAXPAYER_ATTACHMENT_SERVICE)
@patch(TAXPAYER_SERVICE)
@pytest.mark.parametrize("bad_tin", ["", "   ", "N/A", "--"])
def test_ingest_raises_on_blank_or_digitless_tin(
    mock_taxpayer_cls, mock_attach_cls, mock_vendor_cls, bad_tin
):
    """The TIN is NOT NULL + the dedup key; a blank/digit-less value must 400, not
    500 (TaxpayerService coerces a falsy TIN to NULL). Reject before the dedup lookup."""
    mock_vendor_cls.return_value.read_by_public_id.return_value = _vendor()

    with pytest.raises(ValueError, match="taxpayer_id_number"):
        TaxpayerW9IngestService().ingest(
            "VPID",
            attachment_public_id="AID",
            entity_name="Acme Co",
            business_name="Acme Co",
            classification="LLC",
            taxpayer_id_number=bad_tin,
            is_signed=True,
            signature_date=None,
        )

    # Rejected before the dedup lookup and before any create.
    mock_taxpayer_cls.return_value.read_by_taxpayer_id_number.assert_not_called()
    mock_taxpayer_cls.return_value.create.assert_not_called()
