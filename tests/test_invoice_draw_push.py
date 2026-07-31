"""U-179 — invoice draw push pure logic and orchestration (mocked)."""

from unittest.mock import Mock

from entities.invoice.business.push import (
    InvoiceDrawPushService,
    assemble_draw_matrix,
    evaluate_gates,
    writes_enabled,
)


def test_writes_enabled_both_on():
    assert writes_enabled("true", "true") is True
    assert writes_enabled("TRUE", " True ") is True


def test_writes_enabled_either_off():
    assert writes_enabled(None, "true") is False
    assert writes_enabled("true", None) is False
    assert writes_enabled("false", "true") is False
    assert writes_enabled("true", "false") is False


def test_evaluate_gates_writes_disabled_when_either_gate_off():
    assert evaluate_gates(None, "true", "clear", False) == "writes_disabled"
    assert evaluate_gates("true", None, "clear", False) == "writes_disabled"
    assert evaluate_gates("false", "true", "clear", False) == "writes_disabled"


def test_evaluate_gates_audit_halt_without_force():
    assert evaluate_gates("true", "true", "halt", False) == "audit_not_clear"


def test_evaluate_gates_audit_halt_with_force_proceeds():
    assert evaluate_gates("true", "true", "halt", True) == "proceed"


def test_evaluate_gates_clear_proceeds():
    assert evaluate_gates("true", "true", "clear", False) == "proceed"


def _counts(**overrides):
    base = {
        "qbo_line_count": 3,
        "qbo_total_amt": "100.00",
        "dbo_line_count": 3,
        "dbo_line_sum": "100.00",
        "dbo_total_amount": "100.00",
        "sourced_line_count": 2,
        "billed_source_count": 2,
    }
    base.update(overrides)
    return base


def test_assemble_draw_matrix_all_pass():
    matrix = assemble_draw_matrix(
        _counts(),
        {"skipped": 0, "page_count": 4},
    )
    assert matrix["all_pass"] is True
    assert all(r["pass"] for r in matrix["rows"])


def test_assemble_draw_matrix_line_count_mismatch():
    matrix = assemble_draw_matrix(
        _counts(qbo_line_count=2, dbo_line_count=3),
        {"skipped": 0, "page_count": 1},
    )
    assert matrix["all_pass"] is False
    qbo_row = next(r for r in matrix["rows"] if r["check"] == "QBO lines == dbo ILIs")
    assert qbo_row["pass"] is False


def test_assemble_draw_matrix_money_mismatch_exact_decimal():
    matrix = assemble_draw_matrix(
        _counts(qbo_total_amt="100.01"),
        {"skipped": 0, "page_count": 1},
    )
    assert matrix["all_pass"] is False
    money_row = next(
        r for r in matrix["rows"] if r["check"] == "QBO TotalAmt == dbo TotalAmount"
    )
    assert money_row["pass"] is False


def _push_service_with_mocks(**kwargs):
    invoice = Mock()
    invoice.id = 42
    invoice.project_id = 7
    invoice.is_draft = False
    invoice_service = Mock()
    invoice_service.read_by_public_id.return_value = invoice
    invoice_repo = Mock()
    invoice_repo.read_source_lines_missing_readable_blob.return_value = []
    invoice_repo.compute_invoice_draw_matrix.return_value = _counts()
    defaults = {
        "invoice_service": invoice_service,
        "invoice_repo": invoice_repo,
        "line_item_service": Mock(),
        "reconciliation_service": Mock(),
        "audit_service": Mock(),
    }
    defaults.update(kwargs)
    return InvoiceDrawPushService(**defaults), invoice_service, defaults


def _gates_open(monkeypatch):
    monkeypatch.setenv("ALLOW_MS_WRITES", "true")
    monkeypatch.setenv("ALLOW_BOX_WRITES", "true")


def _audit_clear(collabs):
    collabs["audit_service"].audit.return_value = {"verdict": "clear"}
    collabs["reconciliation_service"].apply_links.return_value = {
        "summary": {"applied_count": 0},
    }
    collabs["line_item_service"].read_by_invoice_id.return_value = []


def test_push_draw_invoice_is_draft_halts_without_apply_links(monkeypatch):
    _gates_open(monkeypatch)
    svc, _invoice_service, collabs = _push_service_with_mocks()
    collabs["invoice_service"].read_by_public_id.return_value.is_draft = True

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "invoice_is_draft"
    collabs["reconciliation_service"].apply_links.assert_not_called()


def test_push_draw_missing_attachment_blob_halts_before_packet(monkeypatch):
    _gates_open(monkeypatch)
    generate_packet_fn = Mock(
        return_value={"data": {"skipped": 0, "page_count": 3}},
    )
    svc, _invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=generate_packet_fn,
    )
    _audit_clear(collabs)
    collabs["invoice_repo"].read_source_lines_missing_readable_blob.return_value = [
        {"invoice_line_item_id": 99, "source_type": "BillLineItem"},
    ]

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "missing_attachment_blob"
    generate_packet_fn.assert_not_called()


def test_push_draw_details_stamp_incomplete_skips_sharepoint(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
    )
    _audit_clear(collabs)
    collabs["invoice_repo"].compute_invoice_draw_matrix.return_value = _counts(
        sourced_line_count=3,
    )
    invoice_service.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 0,
        "message": "ok",
        "errors": [],
    }

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "details_stamp_incomplete"
    invoice_service._upload_to_sharepoint.assert_not_called()


def test_push_draw_writes_disabled_halts_without_side_effects(monkeypatch):
    monkeypatch.delenv("ALLOW_MS_WRITES", raising=False)
    monkeypatch.delenv("ALLOW_BOX_WRITES", raising=False)
    svc, invoice_service, collabs = _push_service_with_mocks()

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "writes_disabled"
    collabs["audit_service"].audit.assert_not_called()
    collabs["reconciliation_service"].apply_links.assert_not_called()
    invoice_service._upload_to_sharepoint.assert_not_called()
    invoice_service.sync_to_excel_workbook.assert_not_called()


def test_push_draw_audit_not_clear_skips_apply_links(monkeypatch):
    _gates_open(monkeypatch)
    svc, _invoice_service, collabs = _push_service_with_mocks()
    collabs["audit_service"].audit.return_value = {"verdict": "halt", "gaps": []}

    result = svc.push_draw("inv-pub-id", force=False)

    assert result["status"] == "halt"
    assert result["reason"] == "audit_not_clear"
    collabs["reconciliation_service"].apply_links.assert_not_called()


def test_push_draw_packet_incomplete_skips_sharepoint_upload(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 1, "page_count": 5}},
    )
    _audit_clear(collabs)

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "packet_incomplete"
    invoice_service._upload_to_sharepoint.assert_not_called()
