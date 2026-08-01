"""U-179 — invoice draw push pure logic and orchestration (mocked)."""

from unittest.mock import Mock, patch

from entities.invoice.business.push import (
    InvoiceDrawPushService,
    assemble_draw_matrix,
    build_details_insert_plan,
    evaluate_gates,
    source_public_ids_missing_from_details,
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
    excel_connector = Mock()
    excel_connector.get_excel_for_project.return_value = {"drive_item_id": 99}
    defaults = {
        "invoice_service": invoice_service,
        "invoice_repo": invoice_repo,
        "line_item_service": Mock(),
        "reconciliation_service": Mock(),
        "audit_service": Mock(),
        "worksheet_reconcile_service": Mock(),
        "excel_connector": excel_connector,
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


def _reconcile_empty(collabs):
    collabs["worksheet_reconcile_service"].reconcile.return_value = {
        "db_only": [],
        "already_tagged": [],
    }


def test_build_details_insert_plan_dedupes_same_parent_bill():
    lines = [
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "aaaa-bbbb",
            "parent_entity_id": 10,
        },
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "cccc-dddd",
            "parent_entity_id": 10,
        },
    ]
    missing = {"aaaa-bbbb", "cccc-dddd"}
    assert build_details_insert_plan(lines, missing) == [("Bill", 10)]


def test_build_details_insert_plan_distinct_parents():
    lines = [
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "a",
            "parent_entity_id": 1,
        },
        {
            "source_type": "ExpenseLineItem",
            "source_line_public_id": "b",
            "parent_entity_id": 2,
        },
    ]
    missing = {"a", "b"}
    assert build_details_insert_plan(lines, missing) == [("Bill", 1), ("Expense", 2)]


def test_build_details_insert_plan_excludes_lines_not_in_missing():
    lines = [
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "present",
            "parent_entity_id": 5,
        },
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "absent",
            "parent_entity_id": 6,
        },
    ]
    assert build_details_insert_plan(lines, {"present"}) == [("Bill", 5)]


def test_source_public_ids_missing_from_details():
    from entities.invoice.business.push import source_public_ids_missing_from_details

    db_only = [
        {"source": "Bill", "ref": "INV-1", "source_public_ids": ["aaaa-bbbb"]},
        {"source": "Expense", "ref": "—", "source_public_ids": ["cccc-dddd"]},
    ]
    enriched = [
        {
            "source_line_public_id": "aaaa-bbbb",
            "source_type": "BillLineItem",
            "parent_number": "WRONG",
        },
    ]
    assert source_public_ids_missing_from_details(db_only) == {
        "aaaa-bbbb",
        "cccc-dddd",
    }


def test_source_public_ids_missing_from_details_bill_ref():
    enriched = [
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "PID-1",
            "parent_number": "INV-100",
        },
    ]
    db_only = [
        {
            "source": "Bill",
            "ref": "INV-100",
            "db_total": 50.0,
            "source_public_ids": ["PID-1"],
        }
    ]
    assert source_public_ids_missing_from_details(db_only) == {"pid-1"}


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
    _reconcile_empty(collabs)
    collabs["invoice_repo"].compute_invoice_draw_matrix.return_value = _counts(
        sourced_line_count=3,
    )
    invoice_service.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 0,
        "message": "ok",
        "errors": [],
    }

    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=[],
    ):
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
    collabs["worksheet_reconcile_service"].reconcile.assert_not_called()


def test_push_draw_db_only_enqueues_source_inserts_and_halts(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
    )
    _audit_clear(collabs)
    collabs["worksheet_reconcile_service"].reconcile.return_value = {
        "db_only": [
            {
                "source": "Bill",
                "ref": "B-1",
                "db_total": 100.0,
                "source_public_ids": ["line-pid-1"],
            }
        ],
        "already_tagged": [{"row": 9, "ref": "OLD", "ws_total": 50.0}],
    }
    enriched = [
        {
            "source_type": "BillLineItem",
            "source_line_public_id": "line-pid-1",
            "parent_number": "B-1",
            "bill_line_item_id": 501,
            "parent_entity_id": 77,
        },
    ]
    mock_bill_svc = Mock()
    mock_bill_svc.read_by_id.return_value = Mock()
    mock_bill_svc.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 2,
    }
    mock_li_svc = Mock()
    mock_li_svc.read_by_bill_id.return_value = []

    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=enriched,
    ), patch(
        "entities.invoice.business.push._attach_parent_entity_ids",
        side_effect=lambda rows: rows,
    ), patch(
        "entities.bill.business.service.BillService",
        return_value=mock_bill_svc,
    ), patch(
        "entities.bill_line_item.business.service.BillLineItemService",
        return_value=mock_li_svc,
    ), patch(
        "entities.invoice.business.push._ki16_ensure_price_on_parent_lines",
    ):
        result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "details_inserts_enqueued"
    assert result["already_tagged"] == [{"row": 9, "ref": "OLD", "ws_total": 50.0}]
    mock_bill_svc.sync_to_excel_workbook.assert_called_once()
    mock_bill_svc._enqueue_box_excel.assert_called_once()
    invoice_service.sync_to_excel_workbook.assert_not_called()
    invoice_service._upload_to_sharepoint.assert_not_called()


def test_push_draw_empty_db_only_proceeds_to_invoice_stamp(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
    )
    _audit_clear(collabs)
    _reconcile_empty(collabs)
    invoice_service.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 2,
        "message": "ok",
        "errors": [],
    }
    invoice_service._upload_to_sharepoint.return_value = {
        "success": True,
        "errors": [],
        "synced_count": 1,
    }
    invoice_service._enqueue_box_line_pdfs.return_value = {
        "success": True,
        "enqueued": 0,
        "skipped": 0,
    }

    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=[],
    ):
        result = svc.push_draw("inv-pub-id")

    assert result["status"] == "pushed"
    invoice_service.sync_to_excel_workbook.assert_called_once()
    invoice_service._upload_to_sharepoint.assert_called_once()


def test_push_draw_local_only_skips_sharepoint_details_and_upload(monkeypatch):
    """No ms.DriveItemProjectExcel mapping -> local-only pushed payload."""
    _gates_open(monkeypatch)
    excel_connector = Mock()
    excel_connector.get_excel_for_project.return_value = None
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
        excel_connector=excel_connector,
    )
    _audit_clear(collabs)
    invoice_service._enqueue_box_line_pdfs.return_value = {
        "success": True,
        "enqueued": 0,
        "skipped": 0,
        "reason": "unmapped_project",
    }

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "pushed"
    assert result["local_only"] is True
    assert "sharepoint_details_stamp" in result["skipped_external"]
    assert "sharepoint_upload" in result["skipped_external"]
    assert "box" in result["skipped_external"]
    collabs["worksheet_reconcile_service"].reconcile.assert_not_called()
    invoice_service.sync_to_excel_workbook.assert_not_called()
    invoice_service._upload_to_sharepoint.assert_not_called()
    invoice_service._enqueue_box_line_pdfs.assert_called_once()
    collabs["invoice_repo"].compute_invoice_draw_matrix.assert_called()
    assert result["matrix"]["all_pass"] is True
    local_step = next(s for s in result["steps"] if s.get("step") == "local_only")
    assert local_step["reason"] == "no_sharepoint_excel_mapping"


def test_push_draw_mapped_excel_runs_worksheet_reconcile(monkeypatch):
    _gates_open(monkeypatch)
    excel_connector = Mock()
    excel_connector.get_excel_for_project.return_value = {"drive_item_id": 1}
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
        excel_connector=excel_connector,
    )
    _audit_clear(collabs)
    _reconcile_empty(collabs)
    invoice_service.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 1,
        "message": "ok",
        "errors": [],
    }
    invoice_service._upload_to_sharepoint.return_value = {
        "success": True,
        "errors": [],
        "synced_count": 1,
    }
    invoice_service._enqueue_box_line_pdfs.return_value = {
        "success": True,
        "enqueued": 0,
        "skipped": 0,
    }

    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=[],
    ):
        result = svc.push_draw("inv-pub-id")

    assert result["status"] == "pushed"
    assert result.get("local_only") is False
    assert result.get("skipped_external") == []
    collabs["worksheet_reconcile_service"].reconcile.assert_called_once()
    excel_connector.get_excel_for_project.assert_called_with(project_id=7)

def test_u182_billcredit_manual_insert_halt():
    from entities.invoice.business.push import (
        _missing_billcredit_source_public_ids,
        source_public_ids_missing_from_details,
    )

    bc = "00000000-0000-0000-0000-000000000088"
    db_only = [{"source_public_ids": [bc]}]
    missing = source_public_ids_missing_from_details(db_only)
    lines = [{"source_line_public_id": bc, "source_type": "BillCreditLineItem"}]
    assert _missing_billcredit_source_public_ids(lines, missing) == [bc.lower()]


def test_u182_unresolved_missing_halt():
    from entities.invoice.business.push import (
        _unresolved_missing_source_public_ids,
        source_public_ids_missing_from_details,
    )

    orphan = "00000000-0000-0000-0000-000000000099"
    db_only = [{"source_public_ids": [orphan]}]
    missing = source_public_ids_missing_from_details(db_only)
    lines = [
        {
            "source_line_public_id": "00000000-0000-0000-0000-000000000001",
            "source_type": "BillLineItem",
            "parent_entity_id": 10,
        }
    ]
    assert _unresolved_missing_source_public_ids(lines, missing, []) == [orphan.lower()]


def test_u182_filter_parent_lines_for_project():
    from types import SimpleNamespace
    from entities.invoice.business.push import _filter_parent_lines_for_project

    a = SimpleNamespace(project_id=128)
    b = SimpleNamespace(project_id=999)
    assert _filter_parent_lines_for_project("Bill", [a, b], 128) == [a]
    assert _filter_parent_lines_for_project("BillCredit", [a, b], 128) == [a, b]

def test_push_draw_details_unresolved_missing_halts_before_invoice_sync(monkeypatch):
    """db_only source_public_ids pid absent from invoice lines -> fail closed."""
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
    )
    _audit_clear(collabs)
    orphan = "00000000-0000-0000-0000-000000000099"
    collabs["worksheet_reconcile_service"].reconcile.return_value = {
        "db_only": [
            {
                "source": "Bill",
                "ref": "B-1",
                "source_public_ids": [orphan],
            }
        ],
        "already_tagged": [],
    }
    enriched = [
        {
            "source_line_public_id": "00000000-0000-0000-0000-000000000001",
            "source_type": "BillLineItem",
            "parent_entity_id": 10,
            "parent_number": "B-1",
        },
    ]
    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=enriched,
    ), patch(
        "entities.invoice.business.push._attach_parent_entity_ids",
        side_effect=lambda rows: rows,
    ):
        result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "details_unresolved_missing"
    assert orphan.lower() in (result.get("unresolved_source_public_ids") or [])
    invoice_service.sync_to_excel_workbook.assert_not_called()


def test_push_draw_billcredit_manual_insert_halts_before_stamp(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
    )
    _audit_clear(collabs)
    bc_pid = "00000000-0000-0000-0000-000000000088"
    collabs["worksheet_reconcile_service"].reconcile.return_value = {
        "db_only": [
            {"source": "Expense", "ref": "—", "source_public_ids": [bc_pid]}
        ],
        "already_tagged": [],
    }
    enriched = [
        {
            "source_line_public_id": bc_pid,
            "source_type": "BillCreditLineItem",
            "parent_entity_id": 20,
        }
    ]
    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=enriched,
    ), patch(
        "entities.invoice.business.push._attach_parent_entity_ids",
        side_effect=lambda rows: rows,
    ):
        result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "billcredit_manual_insert_required"
    assert bc_pid.lower() in (result.get("source_public_ids") or [])
    invoice_service.sync_to_excel_workbook.assert_not_called()


def test_push_draw_db_only_bill_enqueue_filters_sync_lines_by_project(monkeypatch):
    """Bill db_only enqueue passes only invoice project_id lines to sync_to_excel_workbook."""
    from types import SimpleNamespace

    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks(
        generate_packet_fn=lambda _pid: {"data": {"skipped": 0, "page_count": 2}},
    )
    _audit_clear(collabs)
    bill_pid = "00000000-0000-0000-0000-000000000010"
    line_on = SimpleNamespace(project_id=7, price=10.0, amount=10.0)
    line_off = SimpleNamespace(project_id=999, price=20.0, amount=20.0)
    collabs["worksheet_reconcile_service"].reconcile.return_value = {
        "db_only": [
            {
                "source": "Bill",
                "ref": "B-7",
                "db_total": 100.0,
                "source_public_ids": [bill_pid],
            }
        ],
        "already_tagged": [],
    }
    enriched = [
        {
            "source_line_public_id": bill_pid,
            "source_type": "BillLineItem",
            "parent_entity_id": 55,
            "parent_number": "B-7",
        }
    ]
    mock_bill_svc = Mock()
    mock_bill_svc.read_by_id.return_value = Mock()
    mock_bill_svc.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 1,
    }
    mock_li_svc = Mock()
    mock_li_svc.read_by_bill_id.return_value = [line_on, line_off]

    with patch(
        "entities.invoice.business.enrichment.enrich_line_items",
        return_value=enriched,
    ), patch(
        "entities.invoice.business.push._attach_parent_entity_ids",
        side_effect=lambda rows: rows,
    ), patch(
        "entities.bill.business.service.BillService",
        return_value=mock_bill_svc,
    ), patch(
        "entities.bill_line_item.business.service.BillLineItemService",
        return_value=mock_li_svc,
    ), patch(
        "entities.invoice.business.push._ki16_ensure_price_on_parent_lines",
    ):
        result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "details_inserts_enqueued"
    sync_call = mock_bill_svc.sync_to_excel_workbook.call_args
    assert sync_call.kwargs["line_items"] == [line_on]
    assert sync_call.kwargs["project_id"] == 7
    invoice_service.sync_to_excel_workbook.assert_not_called()

