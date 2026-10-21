"""U-692a — defer external artifacts while expense lines sit on holding SCCs."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_EXPENSE_MOD = "entities.expense.business.service"
_CLIENT = "entities.expense.business.service"

HOLDING_SCC_ID = 9001
REAL_SCC_ID = 9002
PROJECT_ID = 93


def _is_holding(scc_id):
    return scc_id == HOLDING_SCC_ID


def _line(public_id, sub_cost_code_id, *, project_id=PROJECT_ID):
    return SimpleNamespace(
        id=abs(hash(public_id)) % 100000,
        public_id=public_id,
        sub_cost_code_id=sub_cost_code_id,
        project_id=project_id,
        description="Line",
        price=Decimal("100.00"),
        amount=None,
    )


def _expense(public_id="exp-pub"):
    return SimpleNamespace(
        id=1,
        public_id=public_id,
        vendor_id=1,
        expense_date="2026-04-17",
        reference_number="QBO-69339",
        is_credit=False,
        total_amount=Decimal("200.00"),
    )


def _wire_excel_service(service):
    excel_mapping = {"id": 1, "worksheet_name": "DETAILS"}
    driveitem = SimpleNamespace(id=1, ms_drive_id=5, item_id="item-1")
    drive = SimpleNamespace(drive_id="drive-graph", public_id="drive-pub")
    service._project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value=excel_mapping)
    )
    service._drive_repo = MagicMock(read_by_id=MagicMock(return_value=drive))
    service.vendor_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(name="Vendor Co")
    )
    service.sub_cost_code_service.is_holding_code = MagicMock(side_effect=_is_holding)
    service.sub_cost_code_service.read_by_id = MagicMock(
        side_effect=lambda id: SimpleNamespace(
            number="00.02" if str(id) == str(HOLDING_SCC_ID) else "45.00"
        )
    )
    return driveitem, drive


def _empty_worksheet():
    return {"status_code": 200, "range": {"values": []}}


def _worksheet_with_z_keys(*public_ids):
    rows = []
    for pid in public_ids:
        row = [""] * 26
        row[25] = pid
        rows.append(row)
    return {"status_code": 200, "range": {"values": rows}}


def _make_expense_service():
    from entities.expense.business.service import ExpenseService

    return ExpenseService(repo=MagicMock())


# ------------------------------------------------------------------ Excel single


def test_sync_to_excel_workbook_mixed_holding_and_real_enqueues_one_row():
    service = _make_expense_service()
    _wire_excel_service(service)
    expense = _expense()
    lines = [
        _line("holding-li", HOLDING_SCC_ID),
        _line("real-li", REAL_SCC_ID),
    ]
    enqueue_insert = MagicMock(return_value=SimpleNamespace(public_id="ob-1"))
    enqueue_append = MagicMock(return_value=SimpleNamespace(public_id="ob-2"))
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository") as di_repo, patch(
        f"{_CLIENT}.create_workbook_session", return_value="sess"
    ) as mk_session, patch(
        f"{_CLIENT}.get_excel_used_range_values", return_value=_empty_worksheet()
    ) as get_range, patch(
        f"{_CLIENT}.close_workbook_session"
    ), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        di_repo.return_value.read_all.return_value = [
            SimpleNamespace(id=1, ms_drive_id=5, item_id="item-1")
        ]
        ms_outbox_cls.return_value.enqueue_excel_insert = enqueue_insert
        ms_outbox_cls.return_value.enqueue_excel_append = enqueue_append
        result = service.sync_to_excel_workbook(expense, lines, PROJECT_ID)

    assert result["success"] is True
    assert result["deferred_holding_count"] == 1
    assert result["synced_count"] == 1
    enqueued_values = None
    if enqueue_insert.called:
        enqueued_values = enqueue_insert.call_args.kwargs["values"]
    elif enqueue_append.called:
        enqueued_values = enqueue_append.call_args.kwargs["values"]
    assert enqueued_values is not None
    assert len(enqueued_values) == 1
    row = enqueued_values[0]
    assert row[1] == "45"
    assert row[2] == "45.00"
    assert row[25] == "real-li"
    mk_session.assert_called_once()
    get_range.assert_called_once()


def test_sync_to_excel_workbook_all_holding_no_workbook_session():
    service = _make_expense_service()
    _wire_excel_service(service)
    expense = _expense()
    lines = [
        _line("h1", HOLDING_SCC_ID),
        _line("h2", HOLDING_SCC_ID),
    ]
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository"), patch(
        f"{_CLIENT}.create_workbook_session"
    ) as mk_session, patch(
        f"{_CLIENT}.get_excel_used_range_values"
    ) as get_range, patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ):
        result = service.sync_to_excel_workbook(expense, lines, PROJECT_ID)

    assert result["success"] is True
    assert result["deferred_holding_count"] == 2
    assert result["synced_count"] == 0
    assert "parked on a holding cost code" in result["message"]
    mk_session.assert_not_called()
    get_range.assert_not_called()


def test_sync_to_excel_workbook_already_present_message():
    service = _make_expense_service()
    _wire_excel_service(service)
    expense = _expense()
    lines = [
        _line("holding-li", HOLDING_SCC_ID),
        _line("real-li", REAL_SCC_ID),
    ]
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository") as di_repo, patch(
        f"{_CLIENT}.create_workbook_session", return_value="sess"
    ), patch(
        f"{_CLIENT}.get_excel_used_range_values",
        return_value=_worksheet_with_z_keys("real-li"),
    ), patch(f"{_CLIENT}.close_workbook_session"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        di_repo.return_value.read_all.return_value = [
            SimpleNamespace(id=1, ms_drive_id=5, item_id="item-1")
        ]
        ms_outbox_cls.return_value.enqueue_excel_insert = MagicMock()
        ms_outbox_cls.return_value.enqueue_excel_append = MagicMock()
        result = service.sync_to_excel_workbook(expense, lines, PROJECT_ID)

    assert result["success"] is True
    assert result["synced_count"] == 0
    assert result["deferred_holding_count"] == 1
    assert "already present (coding not re-verified)" in result["message"]
    assert "already synced" not in result["message"].lower()


# ------------------------------------------------------------------ Excel batch


def test_sync_expenses_batch_mixed_deferred_and_real():
    service = _make_expense_service()
    _wire_excel_service(service)
    exp_h = _expense("exp-h")
    exp_r = _expense("exp-r")
    pairs = [
        (exp_h, [_line("h-li", HOLDING_SCC_ID)]),
        (exp_r, [_line("r-li", REAL_SCC_ID)]),
    ]
    enqueue_insert = MagicMock(return_value=SimpleNamespace(public_id="ob-1"))
    enqueue_append = MagicMock(return_value=SimpleNamespace(public_id="ob-2"))
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository") as di_repo, patch(
        f"{_CLIENT}.create_workbook_session", return_value="sess"
    ), patch(
        f"{_CLIENT}.get_excel_used_range_values", return_value=_empty_worksheet()
    ), patch(f"{_CLIENT}.close_workbook_session"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        di_repo.return_value.read_all.return_value = [
            SimpleNamespace(id=1, ms_drive_id=5, item_id="item-1")
        ]
        ms_outbox_cls.return_value.enqueue_excel_insert = enqueue_insert
        ms_outbox_cls.return_value.enqueue_excel_append = enqueue_append
        result = service.sync_expenses_batch_to_excel(pairs, PROJECT_ID)

    assert result["success"] is True
    assert result["deferred_holding_count"] == 1
    assert result["synced_count"] == 1
    enqueued_values = None
    if enqueue_insert.called:
        enqueued_values = enqueue_insert.call_args.kwargs["values"]
    elif enqueue_append.called:
        enqueued_values = enqueue_append.call_args.kwargs["values"]
    assert enqueued_values is not None
    assert len(enqueued_values) == 1
    assert enqueued_values[0][25] == "r-li"


def test_sync_expenses_batch_all_holding_no_workbook_session():
    service = _make_expense_service()
    _wire_excel_service(service)
    pairs = [
        (_expense("e1"), [_line("h1", HOLDING_SCC_ID)]),
        (_expense("e2"), [_line("h2", HOLDING_SCC_ID)]),
    ]
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository"), patch(
        f"{_CLIENT}.create_workbook_session"
    ) as mk_session, patch(
        f"{_CLIENT}.get_excel_used_range_values"
    ) as get_range, patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ):
        result = service.sync_expenses_batch_to_excel(pairs, PROJECT_ID)

    assert result["success"] is True
    assert result["deferred_holding_count"] == 2
    assert "parked on a holding cost code" in result["message"]
    mk_session.assert_not_called()
    get_range.assert_not_called()


def test_sync_expenses_batch_already_present_message():
    service = _make_expense_service()
    _wire_excel_service(service)
    pairs = [
        (_expense("e1"), [_line("h-li", HOLDING_SCC_ID)]),
        (_expense("e2"), [_line("r-li", REAL_SCC_ID)]),
    ]
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository") as di_repo, patch(
        f"{_CLIENT}.create_workbook_session", return_value="sess"
    ), patch(
        f"{_CLIENT}.get_excel_used_range_values",
        return_value=_worksheet_with_z_keys("r-li"),
    ), patch(f"{_CLIENT}.close_workbook_session"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        di_repo.return_value.read_all.return_value = [
            SimpleNamespace(id=1, ms_drive_id=5, item_id="item-1")
        ]
        ms_outbox_cls.return_value.enqueue_excel_insert = MagicMock()
        ms_outbox_cls.return_value.enqueue_excel_append = MagicMock()
        result = service.sync_expenses_batch_to_excel(pairs, PROJECT_ID)

    assert result["success"] is True
    assert result["synced_count"] == 0
    assert result["deferred_holding_count"] == 1
    assert result["message"] == "All rows already present (coding not re-verified)"
    assert "already synced" not in result["message"].lower()


def test_sync_expenses_batch_one_expense_mixed_holding_and_real():
    service = _make_expense_service()
    _wire_excel_service(service)
    exp_mixed = _expense("exp-mixed")
    exp_real = _expense("exp-real")
    pairs = [
        (
            exp_mixed,
            [_line("h-li", HOLDING_SCC_ID), _line("real-mixed", REAL_SCC_ID)],
        ),
        (exp_real, [_line("real-only", REAL_SCC_ID)]),
    ]
    enqueue_insert = MagicMock(return_value=SimpleNamespace(public_id="ob-1"))
    enqueue_append = MagicMock(return_value=SimpleNamespace(public_id="ob-2"))
    with patch(f"{_EXPENSE_MOD}.MsDriveItemRepository") as di_repo, patch(
        f"{_CLIENT}.create_workbook_session", return_value="sess"
    ), patch(
        f"{_CLIENT}.get_excel_used_range_values", return_value=_empty_worksheet()
    ), patch(f"{_CLIENT}.close_workbook_session"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        di_repo.return_value.read_all.return_value = [
            SimpleNamespace(id=1, ms_drive_id=5, item_id="item-1")
        ]
        ms_outbox_cls.return_value.enqueue_excel_insert = enqueue_insert
        ms_outbox_cls.return_value.enqueue_excel_append = enqueue_append
        result = service.sync_expenses_batch_to_excel(pairs, PROJECT_ID)

    assert result["success"] is True
    assert result["deferred_holding_count"] == 1
    assert result["synced_count"] == 2
    enqueued_values = None
    if enqueue_insert.called:
        enqueued_values = enqueue_insert.call_args.kwargs["values"]
    elif enqueue_append.called:
        enqueued_values = enqueue_append.call_args.kwargs["values"]
    assert enqueued_values is not None
    z_keys = sorted(row[25] for row in enqueued_values)
    assert z_keys == ["real-mixed", "real-only"]


# ------------------------------------------------------------------ SharePoint uploads


def _stub_module_folder_upload(service):
    connector = MagicMock()
    connector.get_folder_for_module = MagicMock(
        return_value={"ms_drive_id": 10, "item_id": "folder-item-1"}
    )
    service._project_module_connector = connector
    service._drive_repo = MagicMock(
        read_by_id=MagicMock(
            return_value=SimpleNamespace(drive_id="drive-graph", public_id="drive-pub")
        )
    )
    service.module_service.read_by_name = MagicMock(
        return_value=SimpleNamespace(id=2, name="Expenses")
    )
    service.vendor_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Vendor", abbreviation="VND")
    )
    service.project_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Project", abbreviation="PRJ")
    )
    service.sub_cost_code_service.is_holding_code = MagicMock(side_effect=_is_holding)
    service.sub_cost_code_service.read_by_id = MagicMock(
        side_effect=lambda id: SimpleNamespace(
            number="00.02" if str(id) == str(HOLDING_SCC_ID) else "45.00"
        )
    )
    link = SimpleNamespace(attachment_id=42)
    attachment = SimpleNamespace(
        id=42,
        blob_url="https://blob.example/att.pdf",
        content_type="application/pdf",
        file_extension="pdf",
        original_filename="att.pdf",
    )
    service.expense_line_item_attachment_service.read_by_expense_line_item_id = MagicMock(
        return_value=link
    )
    service.attachment_service.read_by_id = MagicMock(return_value=attachment)


def test_upload_module_folder_defers_when_any_holding_line():
    service = _make_expense_service()
    _stub_module_folder_upload(service)
    expense = _expense()
    lines = [_line("h", HOLDING_SCC_ID), _line("r", REAL_SCC_ID)]
    with patch(f"{_EXPENSE_MOD}.AzureBlobStorage"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = MagicMock()
        result = service._upload_attachments_to_module_folder(
            expense, lines, PROJECT_ID, expense_line_items_count=2
        )
        ms_outbox_cls.return_value.enqueue_sharepoint_upload.assert_not_called()

    assert result["deferred_holding"] is True
    assert result["synced_count"] == 0
    assert "holding cost code" in result["message"]


def test_upload_module_folder_defers_when_holding_sibling_not_in_caller_subset():
    service = _make_expense_service()
    _stub_module_folder_upload(service)
    expense = _expense()
    caller_lines = [_line("real-a", REAL_SCC_ID, project_id=PROJECT_ID)]
    full_lines = [
        caller_lines[0],
        _line("holding-none", HOLDING_SCC_ID, project_id=None),
    ]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = full_lines
    with patch(f"{_EXPENSE_MOD}.AzureBlobStorage"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        enqueue = MagicMock()
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue
        result = service._upload_attachments_to_module_folder(
            expense, caller_lines, PROJECT_ID, expense_line_items_count=2
        )
        enqueue.assert_not_called()

    assert result["deferred_holding"] is True


def test_upload_module_folder_proceeds_when_full_set_has_no_holding():
    service = _make_expense_service()
    _stub_module_folder_upload(service)
    expense = _expense()
    caller_lines = [_line("real-a", REAL_SCC_ID, project_id=PROJECT_ID)]
    full_lines = [
        caller_lines[0],
        _line("real-b", REAL_SCC_ID, project_id=None),
    ]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = full_lines
    storage = MagicMock()
    storage.return_value.download_file.return_value = (b"bytes", {})
    with patch(f"{_EXPENSE_MOD}.AzureBlobStorage", storage), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        enqueue = MagicMock(return_value=SimpleNamespace(public_id="ob-1"))
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue
        result = service._upload_attachments_to_module_folder(
            expense, caller_lines, PROJECT_ID, expense_line_items_count=2
        )

    enqueue.assert_called_once()
    assert result.get("deferred_holding") is False


def test_upload_module_folder_read_failure_fail_closed():
    service = _make_expense_service()
    _stub_module_folder_upload(service)
    expense = _expense()
    lines = [_line("r1", REAL_SCC_ID)]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.side_effect = RuntimeError(
        "db read failed"
    )
    with patch(f"{_EXPENSE_MOD}.AzureBlobStorage"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        enqueue = MagicMock()
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue
        result = service._upload_attachments_to_module_folder(
            expense, lines, PROJECT_ID, expense_line_items_count=1
        )
        enqueue.assert_not_called()

    assert result["success"] is False
    assert result.get("deferred_holding") is False


def test_upload_module_folder_proceeds_without_holding_line():
    service = _make_expense_service()
    _stub_module_folder_upload(service)
    expense = _expense()
    lines = [_line("r1", REAL_SCC_ID), _line("r2", REAL_SCC_ID)]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = lines
    storage = MagicMock()
    storage.return_value.download_file.return_value = (b"bytes", {})
    outbox_row = MagicMock(public_id="ob-1")
    with patch(f"{_EXPENSE_MOD}.AzureBlobStorage", storage), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        enqueue = MagicMock(return_value=outbox_row)
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue
        result = service._upload_attachments_to_module_folder(
            expense, lines, PROJECT_ID, expense_line_items_count=2
        )

    enqueue.assert_called_once()
    assert result.get("deferred_holding") is False
    assert result["synced_count"] == 1


def test_upload_general_receipts_defers_when_any_holding_line():
    service = _make_expense_service()
    service.sub_cost_code_service.is_holding_code = MagicMock(side_effect=_is_holding)
    expense = _expense()
    lines = [_line("h", HOLDING_SCC_ID), _line("r", REAL_SCC_ID)]
    with patch.object(service, "_navigate_to_folder") as nav, patch(
        f"{_EXPENSE_MOD}.MsDriveService"
    ), patch(f"{_EXPENSE_MOD}.AzureBlobStorage"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = MagicMock()
        result = service._upload_to_general_receipts_folder(expense, lines)
        nav.assert_not_called()
        ms_outbox_cls.return_value.enqueue_sharepoint_upload.assert_not_called()

    assert result["deferred_holding"] is True
    assert "holding cost code" in result["message"]


def _stub_general_receipts_upload(service):
    service.sub_cost_code_service.is_holding_code = MagicMock(side_effect=_is_holding)
    service.sub_cost_code_service.read_by_id = MagicMock(
        side_effect=lambda id: SimpleNamespace(number="45.00")
    )
    service.vendor_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(name="Vendor", abbreviation="VND")
    )
    service.project_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(name="Project", abbreviation="PRJ")
    )
    link = SimpleNamespace(attachment_id=42)
    attachment = SimpleNamespace(
        id=42,
        blob_url="https://blob.example/att.pdf",
        content_type="application/pdf",
        file_extension="pdf",
        original_filename="att.pdf",
    )
    service.expense_line_item_attachment_service.read_by_expense_line_item_id = MagicMock(
        return_value=link
    )
    service.attachment_service.read_by_id = MagicMock(return_value=attachment)


def test_upload_general_receipts_defers_when_holding_sibling_not_in_caller_subset():
    service = _make_expense_service()
    _stub_general_receipts_upload(service)
    expense = _expense()
    caller_lines = [_line("real-a", REAL_SCC_ID, project_id=PROJECT_ID)]
    full_lines = [
        caller_lines[0],
        _line("holding-none", HOLDING_SCC_ID, project_id=None),
    ]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = full_lines
    with patch.object(service, "_navigate_to_folder") as nav, patch(
        f"{_EXPENSE_MOD}.MsDriveService"
    ), patch(f"{_EXPENSE_MOD}.AzureBlobStorage"), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        enqueue = MagicMock()
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue
        result = service._upload_to_general_receipts_folder(expense, caller_lines)
        nav.assert_not_called()
        enqueue.assert_not_called()

    assert result["deferred_holding"] is True


def test_upload_general_receipts_proceeds_without_holding_line():
    service = _make_expense_service()
    _stub_general_receipts_upload(service)
    expense = _expense()
    lines = [_line("r1", REAL_SCC_ID)]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = lines
    shared_drive = SimpleNamespace(
        drive_id="drive-1",
        public_id="drive-pub",
        web_url="https://RogersBuildLLC/sharepoint",
        name="Documents",
    )
    storage = MagicMock()
    storage.return_value.download_file.return_value = (b"bytes", {})
    with patch(f"{_EXPENSE_MOD}.MsDriveService") as drive_cls, patch.object(
        service, "_navigate_to_folder", return_value="receipts-id"
    ), patch.object(
        service, "_get_or_create_subfolder", return_value="month-id"
    ), patch(f"{_EXPENSE_MOD}.AzureBlobStorage", storage), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        drive_cls.return_value.read_all.return_value = [shared_drive]
        outbox_row = SimpleNamespace(public_id="ob-1", status="pending")
        enqueue = MagicMock(return_value=outbox_row)
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue
        result = service._upload_to_general_receipts_folder(expense, lines)

    enqueue.assert_called_once()
    assert result.get("deferred_holding") is False
    assert result["synced_count"] == 1


# ------------------------------------------------------------------ Box document enqueue


def _stub_box_document_upload(service):
    service.sub_cost_code_service.is_holding_code = MagicMock(side_effect=_is_holding)
    link = SimpleNamespace(attachment_id=42)
    attachment = SimpleNamespace(
        id=42,
        blob_url="https://blob.example/att.pdf",
        content_type="application/pdf",
        original_filename="att.pdf",
        filename=None,
    )
    service.expense_line_item_attachment_service.read_by_expense_line_item_id = MagicMock(
        return_value=link
    )
    service.attachment_service.read_by_id = MagicMock(return_value=attachment)
    return {"box_folder_id": "folder-1"}


def test_enqueue_box_uploads_defers_when_holding_sibling_not_in_caller_subset():
    service = _make_expense_service()
    mapping = _stub_box_document_upload(service)
    expense = _expense()
    caller_lines = [_line("real-a", REAL_SCC_ID, project_id=PROJECT_ID)]
    full_lines = [
        caller_lines[0],
        _line("holding-none", HOLDING_SCC_ID, project_id=None),
    ]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = full_lines
    with patch.dict("os.environ", {"ALLOW_BOX_WRITES": "true"}), patch(
        "integrations.box.folder.business.service.BoxProjectFolderService"
    ) as folder_cls, patch(
        "integrations.box.outbox.business.service.BoxOutboxService"
    ) as box_outbox_cls:
        folder_cls.return_value.read_mapping_by_project_id_and_class = MagicMock(
            return_value=mapping
        )
        enqueue = MagicMock()
        box_outbox_cls.return_value.enqueue_box_upload = enqueue
        service._enqueue_box_uploads(expense, caller_lines, "receipt")
        enqueue.assert_not_called()


def test_enqueue_box_uploads_read_failure_fail_closed():
    service = _make_expense_service()
    mapping = _stub_box_document_upload(service)
    expense = _expense()
    lines = [_line("r", REAL_SCC_ID)]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.side_effect = RuntimeError(
        "db read failed"
    )
    with patch.dict("os.environ", {"ALLOW_BOX_WRITES": "true"}), patch(
        "integrations.box.folder.business.service.BoxProjectFolderService"
    ) as folder_cls, patch(
        "integrations.box.outbox.business.service.BoxOutboxService"
    ) as box_outbox_cls:
        folder_cls.return_value.read_mapping_by_project_id_and_class = MagicMock(
            return_value=mapping
        )
        enqueue = MagicMock()
        box_outbox_cls.return_value.enqueue_box_upload = enqueue
        service._enqueue_box_uploads(expense, lines, "receipt")
        enqueue.assert_not_called()


def test_enqueue_box_uploads_enqueues_without_holding_line():
    service = _make_expense_service()
    mapping = _stub_box_document_upload(service)
    expense = _expense()
    lines = [_line("r", REAL_SCC_ID)]
    service._expense_line_item_service = MagicMock()
    service._expense_line_item_service.read_by_expense_id.return_value = lines
    with patch.dict("os.environ", {"ALLOW_BOX_WRITES": "true"}), patch(
        "integrations.box.folder.business.service.BoxProjectFolderService"
    ) as folder_cls, patch(
        "integrations.box.outbox.business.service.BoxOutboxService"
    ) as box_outbox_cls:
        folder_cls.return_value.read_mapping_by_project_id_and_class = MagicMock(
            return_value=mapping
        )
        enqueue = MagicMock()
        box_outbox_cls.return_value.enqueue_box_upload = enqueue
        service._enqueue_box_uploads(expense, lines, "receipt")
        enqueue.assert_called_once()


def test_enqueue_box_uploads_defers_with_holding_line():
    service = _make_expense_service()
    mapping = _stub_box_document_upload(service)
    expense = _expense()
    lines = [_line("h", HOLDING_SCC_ID), _line("r", REAL_SCC_ID)]
    with patch.dict("os.environ", {"ALLOW_BOX_WRITES": "true"}), patch(
        "integrations.box.folder.business.service.BoxProjectFolderService"
    ) as folder_cls, patch(
        "integrations.box.outbox.business.service.BoxOutboxService"
    ) as box_outbox_cls:
        folder_cls.return_value.read_mapping_by_project_id_and_class = MagicMock(
            return_value=mapping
        )
        enqueue = MagicMock()
        box_outbox_cls.return_value.enqueue_box_upload = enqueue
        service._enqueue_box_uploads(expense, lines, "receipt")
        enqueue.assert_not_called()


def test_enqueue_box_uploads_holding_lookup_failure_isolated():
    service = _make_expense_service()
    mapping = _stub_box_document_upload(service)
    service.sub_cost_code_service.is_holding_code = MagicMock(
        side_effect=RuntimeError("lookup failed")
    )
    expense = _expense()
    lines = [_line("r", REAL_SCC_ID)]
    with patch.dict("os.environ", {"ALLOW_BOX_WRITES": "true"}), patch(
        "integrations.box.folder.business.service.BoxProjectFolderService"
    ) as folder_cls, patch(
        "integrations.box.outbox.business.service.BoxOutboxService"
    ) as box_outbox_cls:
        folder_cls.return_value.read_mapping_by_project_id_and_class = MagicMock(
            return_value=mapping
        )
        enqueue = MagicMock()
        box_outbox_cls.return_value.enqueue_box_upload = enqueue
        service._enqueue_box_uploads(expense, lines, "receipt")
        enqueue.assert_not_called()


# ------------------------------------------------------------------ Box DETAILS rows


def _build_box_expense_rows(monkeypatch, line_items, *, is_holding_fn=_is_holding):
    import entities.expense.business.service as expense_service
    import entities.expense_line_item.business.service as eli_service
    import entities.sub_cost_code.business.service as scc_service
    import entities.vendor.business.service as vendor_service
    import integrations.box.excel.business.row_builder as rb

    monkeypatch.setattr(
        expense_service,
        "ExpenseService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: _expense()),
    )
    monkeypatch.setattr(
        eli_service,
        "ExpenseLineItemService",
        lambda: SimpleNamespace(read_by_expense_id=lambda **kw: line_items),
    )
    monkeypatch.setattr(
        vendor_service,
        "VendorService",
        lambda: SimpleNamespace(read_by_id=lambda **kw: SimpleNamespace(name="V")),
    )

    def read_scc(id):
        if str(id) == str(HOLDING_SCC_ID):
            return SimpleNamespace(number="00.02")
        return SimpleNamespace(number="45.00")

    scc_mock = SimpleNamespace(read_by_id=read_scc, is_holding_code=MagicMock(side_effect=is_holding_fn))
    monkeypatch.setattr(scc_service, "SubCostCodeService", lambda: scc_mock)
    return rb.build_details_rows("expense", "exp-pub", project_id=PROJECT_ID)


def test_build_details_rows_expense_skips_holding_line(monkeypatch):
    rows = _build_box_expense_rows(
        monkeypatch,
        [_line("h-li", HOLDING_SCC_ID), _line("r-li", REAL_SCC_ID)],
    )
    assert len(rows) == 1
    assert rows[0][1] == "45"
    assert rows[0][2] == "45.00"
    assert rows[0][25] == "r-li"


def test_repair_db_to_excel_expense_uses_synced_count_not_missing_len(capsys):
    from scripts.reconcile_project import repair_db_to_excel

    expense_id = 42
    expense = _expense()
    expense.id = expense_id
    expense.reference_number = "QBO-999"
    missing = [
        _line("real-1", REAL_SCC_ID),
        _line("real-2", REAL_SCC_ID),
    ]
    expense_service = MagicMock()
    expense_service.sync_to_excel_workbook.return_value = {
        "success": True,
        "synced_count": 1,
        "deferred_holding_count": 1,
        "errors": [],
    }
    bill_service = MagicMock()
    issues, synced_count = repair_db_to_excel(
        bills_by_id={},
        line_items_by_bill_id={},
        expenses_by_id={expense_id: expense},
        line_items_by_expense_id={expense_id: missing},
        excel_public_ids=set(),
        project_id=PROJECT_ID,
        bill_service=bill_service,
        expense_service=expense_service,
        dry_run=False,
    )
    captured = capsys.readouterr().out

    assert issues == []
    assert synced_count == 1
    assert "Deferred 1 line item(s)" in captured
    expense_service.sync_to_excel_workbook.assert_called_once()


def test_build_details_rows_bill_still_emits_holding_line(monkeypatch):
    import entities.bill.business.service as bill_service
    import entities.bill_line_item.business.service as bli_service
    import entities.sub_cost_code.business.service as scc_service
    import entities.vendor.business.service as vendor_service
    import integrations.box.excel.business.row_builder as rb

    bill = SimpleNamespace(
        id=1,
        public_id="bill-pub",
        vendor_id=1,
        bill_date="2026-04-17",
        bill_number="B-1",
    )
    holding_line = SimpleNamespace(
        id=1,
        public_id="b-holding",
        sub_cost_code_id=HOLDING_SCC_ID,
        project_id=PROJECT_ID,
        description="H",
        price=Decimal("50.00"),
        amount=None,
    )
    monkeypatch.setattr(
        bill_service,
        "BillService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: bill),
    )
    monkeypatch.setattr(
        bli_service,
        "BillLineItemService",
        lambda: SimpleNamespace(read_by_bill_id=lambda **kw: [holding_line]),
    )
    monkeypatch.setattr(
        vendor_service,
        "VendorService",
        lambda: SimpleNamespace(read_by_id=lambda **kw: SimpleNamespace(name="V")),
    )
    monkeypatch.setattr(
        scc_service,
        "SubCostCodeService",
        lambda: SimpleNamespace(
            read_by_id=lambda id: SimpleNamespace(number="00.02"),
            is_holding_code=MagicMock(return_value=True),
        ),
    )
    rows = rb.build_details_rows("bill", "bill-pub", project_id=PROJECT_ID)
    assert len(rows) == 1
    assert rows[0][25] == "b-holding"
