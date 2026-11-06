"""
U-777 — a QBO recode never costs a line its receipt, and the receipts copy
follows the coding.

Part 1 drives the real purchase-pull line decision (`PurchaseExpenseConnector.
_sync_line_items` -> `PurchaseLineExpenseLineItemConnector.sync_from_qbo_purchase_line`)
for (a) a recode that keeps the QBO line id and (b) a recode where QBO issues a
NEW line id (U-738 rebind). Only the persistence services are mocked. In both
the SAME ExpenseLineItem is updated in place, never deleted or recreated, and
no ExpenseLineItemAttachment / Attachment delete is ever reached - so the
receipt link rides along.

Part 2 shows the receipts-folder filename carries the NEW sub cost code after a
recode, that a prior DONE outbox row under the OLD filename does not suppress
the new upload, and that the old copy is never deleted (the known limit).
"""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from entities.attachment.business.service import AttachmentService
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from tests.test_u738_pull_rebind import (
    _build_line_connector,
    _make_qbo_purchase_line,
    _stamped_row,
)

_LINE_MOD = "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
_HEADER_MOD = "integrations.intuit.qbo.purchase.connector.expense.business.service"

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")

HOLDING_SCC_ID = 900


def _drive_sync(existing_row, qbo_line, *, direct_hit):
    """Run the real header->line decision for ONE live QBO line against ONE local line."""
    line_connector, line_svc = _build_line_connector()
    line_svc.read_by_expense_id.return_value = [existing_row]
    line_svc.read_invoice_linked_line_ids_for_expense.return_value = frozenset()
    line_svc.read_by_qbo_identity.return_value = existing_row if direct_hit else None
    line_svc.update_by_public_id.return_value = SimpleNamespace(
        id=existing_row.id, public_id=existing_row.public_id, realm_id="realm-1",
    )
    line_svc.read_by_id.return_value = _stamped_row(
        existing_row.id, qbo_line.qbo_line_id, sub_cost_code_id=55,
    )
    header = PurchaseExpenseConnector(expense_service=Mock())
    header._line_connector = line_connector

    scc_cls = Mock()
    scc_cls.return_value.is_holding_code.side_effect = lambda sid: sid == HOLDING_SCC_ID
    with patch(f"{_HEADER_MOD}.SubCostCodeService", scc_cls), patch(
        f"{_LINE_MOD}.resolve_dbo_sub_cost_code", return_value=SimpleNamespace(id=55),
    ), patch.object(ExpenseLineItemAttachmentService, "delete_by_public_id") as link_del, patch.object(
        AttachmentService, "delete_by_public_id",
    ) as att_del:
        header._sync_line_items(99, "exp-pub", [qbo_line], "realm-1")
    return line_svc, link_del, att_del


def _assert_updated_in_place(line_svc, link_del, att_del, *, row_id):
    line_svc.create.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()
    link_del.assert_not_called()
    att_del.assert_not_called()
    line_svc.update_by_public_id.assert_called_once()
    args = line_svc.update_by_public_id.call_args
    assert args.args[0] == f"pub-{row_id}"
    assert args.kwargs["sub_cost_code_id"] == 55
    assert not any("delete" in c[0] for c in line_svc.mock_calls)


def test_recode_that_keeps_the_qbo_line_id_updates_the_same_line():
    placeholder = _stamped_row(10, "1", description="58999 suspense", sub_cost_code_id=None)
    qbo_line = _make_qbo_purchase_line(qbo_line_id="1", description="Coded materials")
    line_svc, link_del, att_del = _drive_sync(placeholder, qbo_line, direct_hit=True)
    _assert_updated_in_place(line_svc, link_del, att_del, row_id=10)
    line_svc.repo.set_qbo_identity.assert_not_called()   # identity untouched: found BY it


@pytest.mark.parametrize("old_scc", [None, HOLDING_SCC_ID], ids=["uncoded", "holding"])
def test_recode_with_a_new_qbo_line_id_rebinds_the_same_line(old_scc):
    stale = _stamped_row(10, "1", description="58999 suspense", sub_cost_code_id=old_scc)
    qbo_line = _make_qbo_purchase_line(
        qbo_line_id="2", description="Coded materials", amount=Decimal("500"),
    )
    line_svc, link_del, att_del = _drive_sync(stale, qbo_line, direct_hit=False)
    _assert_updated_in_place(line_svc, link_del, att_del, row_id=10)
    line_svc.repo.set_qbo_identity.assert_called_once_with(id=10, qbo_id="2", realm_id="realm-1")


# --------------------------------------------------------------------------- part 2

from tests.test_u757b_sharepoint_enqueue_characterization import (  # noqa: E402
    _R_ATTS,
    _R_LINKS,
    _R_PROJECTS,
    _R_VENDORS,
    FakeExpenseLineItemService,
    FakeLinkService,
    FakeReadById,
    FakeSccService,
    Recorder,
    _drive,
    _expense,
    _folder,
    _line,
    _make_drive_service_cls,
    _make_storage_cls,
)
from tests.test_u768_receipts_subfolder_create import TreeDriveItemService  # noqa: E402
from entities.expense.business.service import ExpenseService  # noqa: E402

_EXPENSE_MOD = "entities.expense.business.service"
_OUTBOX_MOD = "integrations.ms.outbox.business.service"
_SCCS = {"7001": SimpleNamespace(number="10.100"), "7002": SimpleNamespace(number="20.200")}


def _receipts_run(scc, completed_rows, monkeypatch):
    """Run the real `_upload_to_general_receipts_folder` + the REAL MsOutboxService
    idempotency guard; returns (enqueue payloads, repo mock)."""
    from integrations.ms.outbox.business import service as outbox_svc

    monkeypatch.setenv("ALLOW_MS_WRITES", "true")
    monkeypatch.delenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", raising=False)
    rec = Recorder()
    svc = object.__new__(ExpenseService)
    svc.vendor_service = FakeReadById(rec, "vendor", _R_VENDORS)
    svc.project_service = FakeReadById(rec, "project", _R_PROJECTS)
    svc.sub_cost_code_service = FakeSccService(rec, _SCCS)
    svc.attachment_service = FakeReadById(rec, "attachment", _R_ATTS)
    svc._expense_line_item_attachment_service = FakeLinkService(rec, _R_LINKS)
    lines = [_line(1, "li-1", scc=scc)]
    svc._expense_line_item_service = FakeExpenseLineItemService(rec, lines)
    tree = {"R": [_folder("2026", "Y26")], "Y26": [_folder("10", "M10")]}
    svc._driveitem_service = TreeDriveItemService(tree)

    repo = Mock()
    repo.read_completed_by_entity.return_value = completed_rows
    enqueued: list = []

    def _enqueue(self, **kwargs):
        enqueued.append(kwargs["payload"])
        return SimpleNamespace(public_id="ob-new", status="pending")

    with patch.object(outbox_svc, "MsOutboxRepository", return_value=repo), patch.object(
        outbox_svc.MsOutboxService, "enqueue", _enqueue,
    ), patch.object(outbox_svc, "_resolve_tenant_id", return_value="tenant-1"), patch(
        f"{_EXPENSE_MOD}.AzureBlobStorage", _make_storage_cls(rec),
    ), patch(f"{_EXPENSE_MOD}.MsDriveService", _make_drive_service_cls(rec, [_drive()])), patch(
        f"{_EXPENSE_MOD}.list_drive_item_children",
        lambda drive_id, item_id: {"status_code": 200, "items": svc._driveitem_service._children(item_id)},
    ), patch.object(svc, "_navigate_to_folder", return_value="R"):
        result = svc._upload_to_general_receipts_folder(_expense(date="2026-10-04"), lines)
    return result, enqueued, repo


def _done_row(payload):
    return SimpleNamespace(public_id="ob-old", status="done", payload=json.dumps(payload))


def test_receipts_filename_follows_the_new_coding_and_old_done_row_does_not_suppress(monkeypatch):
    # Before the recode: line coded 10.100 -> upload enqueued, then drained (DONE).
    result, first, _ = _receipts_run("7001", [], monkeypatch)
    assert result["success"] is True and len(first) == 1, result
    old_payload = first[0]
    assert "10.100" in old_payload["filename"]

    # Control: re-running with the SAME coding is suppressed by the DONE row (guard is live).
    result, again, _ = _receipts_run("7001", [_done_row(old_payload)], monkeypatch)
    assert again == [] and result["skipped_count"] == 1

    # After the recode to 20.200: new filename, NOT suppressed by the old-name DONE row.
    result, second, repo = _receipts_run("7002", [_done_row(old_payload)], monkeypatch)
    assert result["success"] is True and len(second) == 1
    new_payload = second[0]
    assert "20.200" in new_payload["filename"] and "10.100" not in new_payload["filename"]
    assert new_payload["filename"] != old_payload["filename"]
    assert new_payload["parent_item_id"] == old_payload["parent_item_id"]
    assert new_payload["blob_path"] == old_payload["blob_path"]

    # Known limit: nothing removes the OLD copy.
    assert not any("delete" in c[0].lower() for c in repo.method_calls)
