"""U-685 — contract-labor pdf_service + bulk error bodies must not leak exception text."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.contract_labor.business.service import ContractLaborService
from shared.access import EntityNotAccessibleError
from shared.lifecycle.completion_gate import HoldingCodeRefused

_ODBC_LEAK = "[Microsoft][ODBC Driver 18 for SQL Server] secret-xyz"
_LINE_PUBLIC_ID = "bli-pdf-test-1"


def _editable_bill():
    return SimpleNamespace(
        id=55,
        public_id="bill-55",
        vendor_id=7,
        bill_number="INV-1",
        status="draft",
        is_draft=True,
    )


def _line_item():
    return SimpleNamespace(
        id=1,
        public_id=_LINE_PUBLIC_ID,
        bill_id=55,
        project_id=None,
        sub_cost_code_id=None,
    )


def _pdf_service_with_line_items():
    from entities.contract_labor.business.pdf_service import ContractLaborPDFService

    svc = ContractLaborPDFService()
    svc.bill_service = MagicMock()
    svc.bill_service.read_by_public_id.return_value = _editable_bill()
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(id=7, name="Acme")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = [_line_item()]
    svc.bill_line_item_attachment_service = MagicMock()
    svc.bill_line_item_attachment_service.read_by_bill_line_item_id.return_value = None
    svc._get_entries_for_line_item = MagicMock(return_value=[])
    return svc


def _assert_no_leak(result: dict) -> None:
    text = json.dumps(result)
    assert "secret-xyz" not in text
    assert "ODBC" not in text


def test_pdf_storage_init_failure_sanitized():
    import entities.contract_labor.business.pdf_service as pdf_mod

    svc = _pdf_service_with_line_items()
    with patch.object(pdf_mod, "AzureBlobStorage", side_effect=RuntimeError(_ODBC_LEAK)):
        result = svc.generate_pdfs_for_bill(bill_public_id="bill-55")
    assert result["success"] is False
    assert result["message"] == "Failed to initialize storage"
    assert result["errors"] == ["Failed to initialize storage"]
    _assert_no_leak(result)


def test_pdf_per_line_item_error_sanitized():
    svc = _pdf_service_with_line_items()
    svc._get_entries_for_line_item = MagicMock(
        side_effect=RuntimeError(_ODBC_LEAK),
    )
    import entities.contract_labor.business.pdf_service as pdf_mod

    mock_storage = MagicMock()
    with patch.object(pdf_mod, "AzureBlobStorage", return_value=mock_storage):
        result = svc.generate_pdfs_for_bill(bill_public_id="bill-55")
    assert any(
        e == f"Line item {_LINE_PUBLIC_ID}: failed to generate PDF" for e in result["errors"]
    ), result["errors"]
    _assert_no_leak(result)


def test_pdf_generate_for_bill_catch_all_sanitized():
    svc = _pdf_service_with_line_items()
    svc.bill_service.read_by_public_id.side_effect = RuntimeError(_ODBC_LEAK)
    result = svc.generate_pdfs_for_bill(bill_public_id="bill-55")
    assert result["success"] is False
    assert result["message"] == "Failed to generate PDFs"
    assert result["errors"] == ["Failed to generate PDFs"]
    _assert_no_leak(result)


def test_pdf_generate_for_billed_entries_catch_all_sanitized():
    from entities.contract_labor.business.pdf_service import ContractLaborPDFService

    svc = ContractLaborPDFService()
    svc.repo = MagicMock()
    svc.repo.read_by_status.side_effect = RuntimeError(_ODBC_LEAK)
    result = svc.generate_pdfs_for_billed_entries()
    assert result["success"] is False
    assert result["message"] == "Failed to generate PDFs"
    assert result["errors"] == ["Failed to generate PDFs"]
    _assert_no_leak(result)


def test_bulk_mark_ready_entity_not_accessible_maps_to_entry_not_found():
    svc = ContractLaborService()
    svc.mark_as_ready = MagicMock(
        side_effect=EntityNotAccessibleError("ContractLabor", 987654),
    )
    result = svc.bulk_mark_as_ready(["cl-1"])
    assert result["errors"] == [{"public_id": "cl-1", "error": "Entry not found"}]
    _assert_no_leak(result)


def test_bulk_mark_ready_generic_exception_fixed_message():
    svc = ContractLaborService()
    svc.mark_as_ready = MagicMock(side_effect=RuntimeError(_ODBC_LEAK))
    result = svc.bulk_mark_as_ready(["cl-2"])
    assert result["errors"] == [{"public_id": "cl-2", "error": "Failed to mark ready"}]
    _assert_no_leak(result)


def test_bulk_mark_ready_value_error_unchanged():
    svc = ContractLaborService()
    msg = "Vendor is required before marking as ready."
    svc.mark_as_ready = MagicMock(side_effect=ValueError(msg))
    result = svc.bulk_mark_as_ready(["cl-3"])
    assert result["errors"] == [{"public_id": "cl-3", "error": msg}]


def test_bulk_mark_ready_holding_code_refused_unchanged():
    svc = ContractLaborService()
    svc.mark_as_ready = MagicMock(
        side_effect=HoldingCodeRefused("holding blocked"),
    )
    result = svc.bulk_mark_as_ready(["cl-4"])
    assert result["errors"] == [
        {
            "public_id": "cl-4",
            "error": "holding blocked",
            "reason": "holding_code",
        }
    ]


def test_bulk_delete_entity_not_accessible_maps_to_entry_not_found():
    svc = ContractLaborService()
    svc.read_by_public_id = MagicMock(
        side_effect=EntityNotAccessibleError("ContractLabor", 987654),
    )
    result = svc.bulk_delete(["cl-del-1"])
    assert result["errors"] == [{"public_id": "cl-del-1", "error": "Entry not found"}]
    _assert_no_leak(result)


def test_bulk_delete_generic_exception_fixed_message():
    svc = ContractLaborService()
    svc.read_by_public_id = MagicMock(side_effect=RuntimeError(_ODBC_LEAK))
    result = svc.bulk_delete(["cl-del-2"])
    assert result["errors"] == [{"public_id": "cl-del-2", "error": "Delete failed"}]
    _assert_no_leak(result)
