"""
QBO purchase → Expense pull latency (expense-entity review, 2026-10-03).

Four contributors to "data from QuickBooks lags" were found in code and are
pinned here, pure-logic (no DB, no QBO):

  A1  the per-run Attachable snapshot is bounded by the pull's own watermark
      (`QboAttachableService(attachables_since=...)`), and late-attached
      receipts on UNCHANGED purchases are linked from that snapshot;
  A2  the tick's MS fan-out (Excel + SharePoint) is an `expense_pull_fanout`
      outbox row, never inline Graph HTTP, and the MS drain is a bounded loop
      rather than one row per 30s tick;
  A3  a `lock_busy` tick skip is a WARNING with a consecutive-skip counter;
  A4  a purchase whose vendor is unknown is resolved on demand, and if that
      fails the tick HOLDS (non-ValueError) instead of permanently skipping.
"""
import asyncio
import json
import logging
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.purchase.business.model import QboPurchase
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import KIND_EXPENSE_PULL_FANOUT
from integrations.ms.outbox.business.worker import MsOutboxWorker
from shared.api import admin as admin_module

REALM_ID = "realm-test"
ATTACHABLE_SERVICE_MODULE = "integrations.intuit.qbo.attachable.business.service"
CONNECTOR_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"
LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)


# --------------------------------------------------------------------------- #
# A1 — attachable client + service
# --------------------------------------------------------------------------- #

def _attachable_client_capturing_queries(seen: list) -> QboAttachableClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("query"))
        return httpx.Response(200, json={"QueryResponse": {}})

    auth = MagicMock()
    auth.ensure_valid_token_classified.return_value = (
        MagicMock(access_token="tok"),
        MagicMock(value="none"),
    )
    http_client = QboHttpClient(
        realm_id=REALM_ID,
        auth_service=auth,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_budget=MagicMock(),
    )
    return QboAttachableClient(realm_id=REALM_ID, http_client=http_client)


def test_attachable_query_is_unbounded_without_watermark():
    seen: list = []
    client = _attachable_client_capturing_queries(seen)
    client.query_all_attachables()
    assert seen == ["SELECT * FROM Attachable STARTPOSITION 1 MAXRESULTS 1000"]


def test_attachable_query_is_bounded_by_last_updated_time():
    seen: list = []
    client = _attachable_client_capturing_queries(seen)
    client.query_all_attachables(last_updated_time="2026-10-03T12:00:00+00:00")
    assert len(seen) == 1
    assert seen[0].startswith("SELECT * FROM Attachable WHERE Metadata.LastUpdatedTime > '2026-10-03T12:00:00")
    assert seen[0].endswith("STARTPOSITION 1 MAXRESULTS 1000")


def _ref(entity_type, value):
    return SimpleNamespace(entity_ref_type=entity_type, entity_ref_value=value)


def _attachable(att_id, *refs):
    return SimpleNamespace(
        id=att_id, attachable_ref=list(refs), sync_token="0", file_name=f"{att_id}.pdf",
        note=None, category=None, content_type="application/pdf", size=1,
        file_access_uri=None, temp_download_uri=None,
    )


def _service_with_fake_client(attachables, *, attachables_since=None):
    auth = MagicMock()
    auth.ensure_valid_token.return_value = MagicMock(access_token="tok")
    service = QboAttachableService(auth_service=auth, attachables_since=attachables_since)
    fake_client = MagicMock()
    fake_client.query_all_attachables.return_value = attachables
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    return service, fake_client


def test_service_snapshot_passes_watermark_to_client_once():
    service, fake_client = _service_with_fake_client(
        [_attachable("a1", _ref("Purchase", "77"))],
        attachables_since="2026-10-03T12:00:00+00:00",
    )
    with patch(f"{ATTACHABLE_SERVICE_MODULE}.QboAttachableClient", return_value=fake_client):
        service._sync_to_attachments = MagicMock(side_effect=lambda rows, realm: rows)
        first = service.sync_attachables_for_purchase(REALM_ID, "77")
        second = service.sync_attachables_for_purchase(REALM_ID, "77")

    fake_client.query_all_attachables.assert_called_once_with(
        last_updated_time="2026-10-03T12:00:00+00:00"
    )
    assert [a.qbo_id for a in first] == ["a1"]
    assert [a.qbo_id for a in second] == ["a1"]


def test_service_snapshot_is_full_realm_without_watermark():
    service, fake_client = _service_with_fake_client([])
    with patch(f"{ATTACHABLE_SERVICE_MODULE}.QboAttachableClient", return_value=fake_client):
        service.sync_attachables_for_purchase(REALM_ID, "77", sync_to_modules=False)
    fake_client.query_all_attachables.assert_called_once_with(last_updated_time=None)


def test_entity_ids_in_snapshot_is_exact_type_match():
    service, fake_client = _service_with_fake_client(
        [
            _attachable("a1", _ref("Purchase", "77")),
            _attachable("a2", _ref("PurchaseOrder", "77")),   # same id, other type
            _attachable("a3", _ref("purchase", "78")),         # case-insensitive type
            _attachable("a4", _ref("Bill", "77")),
            _attachable("a5"),                                 # no refs
        ],
        attachables_since="2026-10-03T12:00:00+00:00",
    )
    with patch(f"{ATTACHABLE_SERVICE_MODULE}.QboAttachableClient", return_value=fake_client):
        ids = service.entity_ids_in_snapshot(REALM_ID, "Purchase")
    assert ids == {"77", "78"}
    fake_client.query_all_attachables.assert_called_once()


# --------------------------------------------------------------------------- #
# A1 + A2 — the purchase script
# --------------------------------------------------------------------------- #

def _make_qbo_purchase(*, purchase_id=42, qbo_id="qbo-purchase-123"):
    return QboPurchase(
        id=purchase_id,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version=None, created_datetime=None, modified_datetime=None,
        qbo_id=qbo_id, sync_token="0", realm_id=REALM_ID,
        payment_type="CreditCard", account_ref_value="1", account_ref_name="Card",
        entity_ref_value="1", entity_ref_name="Vendor", credit=False,
        txn_date="2026-08-01", doc_number="EXP-1", private_note=None,
        total_amt=Decimal("100.00"), currency_ref_value=None, currency_ref_name=None,
        exchange_rate=None, department_ref_value=None, department_ref_name=None,
        global_tax_calculation=None,
    )


def _run_script(*, last_sync_time, attachable_service, ms_outbox, line_items, late_expense=None):
    from scripts.sync_qbo_purchase import sync_qbo_to_local

    purchase = _make_qbo_purchase()
    outcome = SyncOutcome.for_service_pull()
    outcome.synced = [purchase]
    qbo_purchase_service = MagicMock()
    qbo_purchase_service.sync_from_qbo.return_value = outcome

    expense = SimpleNamespace(id=99, public_id="33333333-3333-3333-3333-333333333333")
    purchase_connector = MagicMock()
    purchase_connector.sync_from_qbo_purchase.return_value = expense

    expense_service = MagicMock()
    expense_service.read_by_qbo_identity.return_value = late_expense
    eli_service = MagicMock()
    eli_service.read_by_expense_id.return_value = line_items

    with patch("scripts.sync_qbo_purchase.QboAttachableService", return_value=attachable_service) as svc_cls, patch(
        "entities.expense.business.service.ExpenseService", return_value=expense_service
    ), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService", return_value=eli_service
    ), patch(
        "integrations.ms.outbox.business.service.MsOutboxService", return_value=ms_outbox
    ), patch(
        "scripts.sync_qbo_purchase.read_lines_riding_out_race", return_value=[MagicMock()]
    ), patch(
        "scripts.sync_qbo_purchase.with_retry", side_effect=lambda fn, *args, **kwargs: fn(*args)
    ), patch(
        "scripts.sync_qbo_purchase.sync_purchase_attachments_to_expense_line_items", return_value=1
    ) as link, patch(
        "scripts.sync_qbo_purchase.pace_batch"
    ), patch.dict("os.environ", {"ALLOW_BOX_WRITES": "false"}):
        result, _ = sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=last_sync_time,
            qbo_purchase_service=qbo_purchase_service,
            purchase_connector=purchase_connector,
        )
    return result, svc_cls, expense_service, link


def test_script_bounds_attachable_snapshot_to_its_own_watermark():
    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.return_value = []
    attachable_service.entity_ids_in_snapshot.return_value = set()
    _, svc_cls, _, _ = _run_script(
        last_sync_time="2026-10-03T11:59:00+00:00",
        attachable_service=attachable_service, ms_outbox=MagicMock(), line_items=[],
    )
    svc_cls.assert_called_once_with(attachables_since="2026-10-03T11:59:00+00:00")


def test_script_links_late_attached_receipts_for_unchanged_purchases():
    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.return_value = [SimpleNamespace(qbo_id="att-9")]
    # 'qbo-purchase-123' changed this tick; 'qbo-purchase-OLD' did not but has a new attachable.
    attachable_service.entity_ids_in_snapshot.return_value = {"qbo-purchase-123", "qbo-purchase-OLD"}
    late_expense = SimpleNamespace(id=500, public_id="55555555-5555-5555-5555-555555555555")

    result, _, expense_service, link = _run_script(
        last_sync_time="2026-10-03T11:59:00+00:00",
        attachable_service=attachable_service, ms_outbox=MagicMock(), line_items=[],
        late_expense=late_expense,
    )
    # The changed purchase is linked in the main loop; the unchanged one in the late pass
    # (the main loop also reads the changed purchase's identity for the was_local check).
    expense_service.read_by_qbo_identity.assert_any_call("qbo-purchase-OLD", REALM_ID)
    attachable_service.sync_attachables_for_purchase.assert_any_call(
        realm_id=REALM_ID, purchase_qbo_id="qbo-purchase-OLD", sync_to_modules=True
    )
    linked_expense_ids = sorted(c.kwargs["expense_id"] for c in link.call_args_list)
    assert linked_expense_ids == [99, 500]
    assert result["attachments_linked"] == 2


def test_late_attach_pass_runs_on_an_empty_tick():
    """P0 from Pass 1: the watermark commits on an empty tick and the next tick
    bounds its snapshot by it. If the late pass only ran when a purchase changed,
    a receipt matched during a quiet window would never be seen again."""
    from scripts.sync_qbo_purchase import sync_qbo_to_local

    outcome = SyncOutcome.for_service_pull()
    outcome.synced = []  # nothing changed this tick
    qbo_purchase_service = MagicMock()
    qbo_purchase_service.sync_from_qbo.return_value = outcome

    attachable_service = MagicMock()
    attachable_service.entity_ids_in_snapshot.return_value = {"qbo-purchase-OLD"}
    attachable_service.sync_attachables_for_purchase.return_value = [SimpleNamespace(qbo_id="att-9")]
    expense_service = MagicMock()
    expense_service.read_by_qbo_identity.return_value = SimpleNamespace(id=500, public_id="p")

    with patch("scripts.sync_qbo_purchase.QboAttachableService", return_value=attachable_service), patch(
        "entities.expense.business.service.ExpenseService", return_value=expense_service
    ), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService", return_value=MagicMock()
    ), patch(
        "scripts.sync_qbo_purchase.sync_purchase_attachments_to_expense_line_items", return_value=1
    ):
        result, _ = sync_qbo_to_local(
            realm_id=REALM_ID, last_sync_time="2026-10-03T11:59:00+00:00",
            qbo_purchase_service=qbo_purchase_service, purchase_connector=MagicMock(),
        )
    assert result["purchases_synced"] == 0
    assert result["attachments_linked"] == 1
    attachable_service.sync_attachables_for_purchase.assert_called_once_with(
        realm_id=REALM_ID, purchase_qbo_id="qbo-purchase-OLD", sync_to_modules=True
    )


def test_new_local_purchase_looks_back_to_its_own_transaction_date():
    """Round-2 fix for the Pass-1 P0/P1: a purchase NEW locally (deferred, skipped,
    pulled late) may carry receipts older than the snapshot bound. A receipt cannot
    predate the purchase it is attached to, so the lookup window starts at the
    purchase's transaction date minus a margin — exact, regardless of how recently
    it was deferred (the earlier 7-day heuristic left a deferred-then-recoded
    purchase with a permanently unlinked receipt). Past the cap, or with no date,
    the full list is used."""
    from scripts.sync_qbo_purchase import (
        CREATE_WINDOW_CAP_DAYS, CREATE_WINDOW_MARGIN_DAYS, _attachable_window_for_create,
    )

    wm = "2026-10-03T11:59:00+00:00"
    assert CREATE_WINDOW_MARGIN_DAYS >= 1
    assert _attachable_window_for_create(SimpleNamespace(txn_date="2026-10-01"), wm) == (False, "2026-09-28T00:00:00+00:00")
    assert _attachable_window_for_create(SimpleNamespace(txn_date="2026-09-20"), wm) == (False, "2026-09-17T00:00:00+00:00")
    assert _attachable_window_for_create(SimpleNamespace(txn_date="2026-06-01"), wm) == (True, None)   # past the cap
    assert _attachable_window_for_create(SimpleNamespace(txn_date=None), wm) == (True, None)           # unknown age
    assert _attachable_window_for_create(SimpleNamespace(txn_date="2026-10-01"), None) == (False, None)  # full pull
    assert CREATE_WINDOW_CAP_DAYS >= 30

    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.return_value = []
    attachable_service.entity_ids_in_snapshot.return_value = set()

    def _run(*, local_exists: bool, txn_date: str):
        from scripts.sync_qbo_purchase import sync_qbo_to_local
        purchase = _make_qbo_purchase()
        purchase.txn_date = txn_date
        outcome = SyncOutcome.for_service_pull(); outcome.synced = [purchase]
        qps = MagicMock(); qps.sync_from_qbo.return_value = outcome
        expense = SimpleNamespace(id=99, public_id="33333333-3333-3333-3333-333333333333")
        connector = MagicMock(); connector.sync_from_qbo_purchase.return_value = expense
        es = MagicMock(); es.read_by_qbo_identity.return_value = expense if local_exists else None
        with patch("scripts.sync_qbo_purchase.QboAttachableService", return_value=attachable_service), patch(
            "entities.expense.business.service.ExpenseService", return_value=es
        ), patch(
            "entities.expense_line_item.business.service.ExpenseLineItemService", return_value=MagicMock()
        ), patch(
            "integrations.ms.outbox.business.service.MsOutboxService", return_value=MagicMock()
        ), patch(
            "scripts.sync_qbo_purchase.read_lines_riding_out_race", return_value=[MagicMock()]
        ), patch(
            "scripts.sync_qbo_purchase.with_retry", side_effect=lambda fn, *a, **k: fn(*a)
        ), patch("scripts.sync_qbo_purchase.pace_batch"), patch.dict("os.environ", {"ALLOW_BOX_WRITES": "false"}):
            sync_qbo_to_local(realm_id=REALM_ID, last_sync_time=wm, qbo_purchase_service=qps, purchase_connector=connector)
        kw = attachable_service.sync_attachables_for_purchase.call_args.kwargs
        return (kw["authoritative"], kw["window_since"])

    assert _run(local_exists=True, txn_date="2026-09-01") == (False, None)                       # update: bounded snapshot
    # The Pass-1 reviewer's exact scenario: deferred at T1 (txn Oct 1), receipt matched Oct 2,
    # recoded Oct 3 and first projected now — the window reaches back past the receipt.
    assert _run(local_exists=False, txn_date="2026-10-01") == (False, "2026-09-28T00:00:00+00:00")
    assert _run(local_exists=False, txn_date="2026-06-01") == (True, None)                       # very old create: full list


def test_service_create_window_is_a_separate_cache_bounded_by_the_oldest_request():
    """The create window is loaded once on first use and only widened (reloaded) when
    a later purchase needs an OLDER bound; the per-tick snapshot stays untouched."""
    snapshot = [_attachable("new", _ref("Purchase", "77"))]
    window = [_attachable("old", _ref("Purchase", "77")), _attachable("new", _ref("Purchase", "77"))]
    service, fake_client = _service_with_fake_client(snapshot, attachables_since="2026-10-03T12:00:00+00:00")
    fake_client.query_all_attachables.side_effect = (
        lambda last_updated_time=None: window if str(last_updated_time) < "2026-10-03" else snapshot
    )
    with patch(f"{ATTACHABLE_SERVICE_MODULE}.QboAttachableClient", return_value=fake_client):
        service._sync_to_attachments = MagicMock(side_effect=lambda rows, realm: rows)
        bounded = service.sync_attachables_for_purchase(REALM_ID, "77")
        w1 = service.sync_attachables_for_purchase(REALM_ID, "77", window_since="2026-09-28T00:00:00+00:00")
        w2 = service.sync_attachables_for_purchase(REALM_ID, "77", window_since="2026-09-30T00:00:00+00:00")  # newer: reuse
        w3 = service.sync_attachables_for_purchase(REALM_ID, "77", window_since="2026-09-20T00:00:00+00:00")  # older: widen
    assert [a.qbo_id for a in bounded] == ["new"]
    assert sorted(a.qbo_id for a in w1) == ["new", "old"]
    assert sorted(a.qbo_id for a in w2) == ["new", "old"]
    assert sorted(a.qbo_id for a in w3) == ["new", "old"]
    calls = [c.kwargs.get("last_updated_time") for c in fake_client.query_all_attachables.call_args_list]
    assert calls == ["2026-10-03T12:00:00+00:00", "2026-09-28T00:00:00+00:00", "2026-09-20T00:00:00+00:00"]


def test_service_authoritative_lookup_loads_the_full_list_once_and_keeps_the_snapshot_separate():
    bounded = [_attachable("new", _ref("Purchase", "77"))]
    full = [_attachable("old", _ref("Purchase", "77")), _attachable("new", _ref("Purchase", "77"))]
    service, fake_client = _service_with_fake_client(bounded, attachables_since="2026-10-03T12:00:00+00:00")
    fake_client.query_all_attachables.side_effect = (
        lambda last_updated_time=None: full if last_updated_time is None else bounded
    )
    with patch(f"{ATTACHABLE_SERVICE_MODULE}.QboAttachableClient", return_value=fake_client):
        service._sync_to_attachments = MagicMock(side_effect=lambda rows, realm: rows)
        bounded_hit = service.sync_attachables_for_purchase(REALM_ID, "77")
        full_hit = service.sync_attachables_for_purchase(REALM_ID, "77", authoritative=True)
        full_again = service.sync_attachables_for_purchase(REALM_ID, "77", authoritative=True)
    assert [a.qbo_id for a in bounded_hit] == ["new"]
    assert sorted(a.qbo_id for a in full_hit) == ["new", "old"]
    assert sorted(a.qbo_id for a in full_again) == ["new", "old"]
    # one bounded page set + one full page set, no re-fetch on the second authoritative call
    assert fake_client.query_all_attachables.call_count == 2


def test_script_skips_late_attach_pass_on_full_pull():
    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.return_value = []
    _run_script(last_sync_time=None, attachable_service=attachable_service,
                ms_outbox=MagicMock(), line_items=[])
    attachable_service.entity_ids_in_snapshot.assert_not_called()


def test_script_enqueues_ms_fanout_instead_of_inline_graph():
    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.return_value = []
    attachable_service.entity_ids_in_snapshot.return_value = set()
    ms_outbox = MagicMock()
    ms_outbox.enqueue_expense_pull_fanout.return_value = MagicMock()  # enqueued
    line_items = [
        SimpleNamespace(project_id=10), SimpleNamespace(project_id=10), SimpleNamespace(project_id=20),
        SimpleNamespace(project_id=None),
    ]
    result, _, expense_service, _ = _run_script(
        last_sync_time="2026-10-03T11:59:00+00:00",
        attachable_service=attachable_service, ms_outbox=ms_outbox, line_items=line_items,
    )
    # The script's ExpenseService instance is the mock itself: the inline Graph
    # methods it used to call are never invoked on it.
    expense_service.sync_expenses_batch_to_excel.assert_not_called()
    expense_service._upload_attachments_to_module_folder.assert_not_called()
    calls = sorted(
        (c.kwargs["project_id"], c.kwargs["expense_line_items_count"])
        for c in ms_outbox.enqueue_expense_pull_fanout.call_args_list
    )
    assert calls == [(10, 4), (20, 4)]
    assert result["ms_fanout_enqueued"] == 2
    assert result["ms_fanout_refused"] == 0
    assert "excel_rows_synced" not in result


def test_script_counts_gate_refusals():
    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.return_value = []
    attachable_service.entity_ids_in_snapshot.return_value = set()
    ms_outbox = MagicMock()
    ms_outbox.enqueue_expense_pull_fanout.return_value = None  # ALLOW_MS_WRITES off
    result, _, _, _ = _run_script(
        last_sync_time="2026-10-03T11:59:00+00:00",
        attachable_service=attachable_service, ms_outbox=ms_outbox,
        line_items=[SimpleNamespace(project_id=10)],
    )
    assert result["ms_fanout_enqueued"] == 0
    assert result["ms_fanout_refused"] == 1


# --------------------------------------------------------------------------- #
# A2 — the outbox handler + bounded drain
# --------------------------------------------------------------------------- #

def _row(payload):
    return MsOutbox(
        id=7505, public_id="outbox-7505", row_version="rv-1",
        kind=KIND_EXPENSE_PULL_FANOUT, entity_type="Expense",
        entity_public_id="33333333-3333-3333-3333-333333333333", tenant_id="tenant-1",
        request_id="req-1", payload=json.dumps(payload), status="in_progress",
        attempts=0, ready_after=None, correlation_id=None,
    )


def _handler_env(*, expense, line_items, excel_result, sp_result):
    expense_service = MagicMock()
    expense_service.read_by_public_id.return_value = expense
    expense_service.sync_to_excel_workbook.return_value = excel_result
    expense_service._upload_attachments_to_module_folder.return_value = sp_result
    eli_service = MagicMock()
    eli_service.read_by_expense_id.return_value = line_items
    return (
        patch("entities.expense.business.service.ExpenseService", return_value=expense_service),
        patch("entities.expense_line_item.business.service.ExpenseLineItemService", return_value=eli_service),
        expense_service,
    )


def test_fanout_kind_is_dispatched_and_escalated():
    worker = MsOutboxWorker(repo=MagicMock())
    assert KIND_EXPENSE_PULL_FANOUT in worker._dispatch_table
    row = _row({"project_id": 10})
    with patch("integrations.ms.reconciliation.business.service.MsReconciliationIssueService") as recon:
        worker._dead_letter(row, "boom")
    recon.return_value.flag_dead_letter.assert_called_once()


def test_fanout_handler_runs_excel_then_sharepoint_for_the_project_lines_only():
    expense = SimpleNamespace(id=99, public_id="33333333-3333-3333-3333-333333333333")
    lines = [SimpleNamespace(project_id=10), SimpleNamespace(project_id=20)]
    p1, p2, svc = _handler_env(
        expense=expense, line_items=lines,
        excel_result={"success": True, "synced_count": 1, "errors": []},
        sp_result={"success": True, "synced_count": 1, "skipped_count": 0, "errors": []},
    )
    with p1, p2:
        MsOutboxWorker(repo=MagicMock())._handle_expense_pull_fanout(
            _row({"project_id": 10, "expense_line_items_count": 2}), {"project_id": 10, "expense_line_items_count": 2}
        )
    excel_kwargs = svc.sync_to_excel_workbook.call_args.kwargs
    assert excel_kwargs["project_id"] == 10 and excel_kwargs["line_items"] == [lines[0]]
    sp_kwargs = svc._upload_attachments_to_module_folder.call_args.kwargs
    assert sp_kwargs["project_id"] == 10
    assert sp_kwargs["line_items"] == [lines[0]]
    assert sp_kwargs["expense_line_items_count"] == 2   # filename parity with completion


def test_fanout_handler_treats_unmapped_project_as_done_not_failure():
    expense = SimpleNamespace(id=99, public_id="33333333-3333-3333-3333-333333333333")
    p1, p2, _ = _handler_env(
        expense=expense, line_items=[SimpleNamespace(project_id=10)],
        excel_result={"success": False, "message": "Excel not linked for project 10",
                      "synced_count": 0, "errors": [{"error": "Excel not linked for project 10"}]},
        sp_result={"success": False, "message": "Module folder not linked for project 10",
                   "synced_count": 0, "skipped_count": 0, "errors": [{"error": "Module folder not linked for project 10"}]},
    )
    with p1, p2:
        result = MsOutboxWorker(repo=MagicMock())._handle_expense_pull_fanout(_row({"project_id": 10}), {"project_id": 10})
    assert result is None  # returned normally: the row is marked done, not failed


def test_unmapped_prefixes_match_the_expense_service_messages():
    """The handler keys "configuration, not failure" on two ExpenseService message
    literals. A reworded message would silently turn an unmapped project into a
    dead-letter, so pin the literals structurally."""
    import inspect
    from entities.expense.business.service import ExpenseService
    from integrations.ms.outbox.business.worker import _UNMAPPED_MESSAGE_PREFIXES
    source = inspect.getsource(ExpenseService.sync_to_excel_workbook) + inspect.getsource(
        ExpenseService._upload_attachments_to_module_folder
    )
    for prefix in _UNMAPPED_MESSAGE_PREFIXES:
        assert prefix in source, f"ExpenseService no longer emits {prefix!r}"


def test_fanout_handler_raises_on_real_errors_so_the_row_retries():
    expense = SimpleNamespace(id=99, public_id="33333333-3333-3333-3333-333333333333")
    p1, p2, _ = _handler_env(
        expense=expense, line_items=[SimpleNamespace(project_id=10)],
        excel_result={"success": True, "synced_count": 1, "errors": []},
        sp_result={"success": False, "message": "Drive not found", "synced_count": 0,
                   "skipped_count": 0, "errors": [{"error": "Drive not found"}]},
    )
    from integrations.ms.base.errors import MsGraphError
    with p1, p2, pytest.raises(MsGraphError, match="Drive not found") as excinfo:
        MsOutboxWorker(repo=MagicMock())._handle_expense_pull_fanout(_row({"project_id": 10}), {"project_id": 10})
    # Retryable: `_process` routes MsGraphError to `_handle_ms_error` (backoff,
    # MAX_ATTEMPTS, then dead-letter). A plain RuntimeError dead-lettered on attempt 1.
    assert excinfo.value.is_retryable is True


def test_fanout_handler_is_done_when_expense_was_deleted():
    p1, p2, svc = _handler_env(expense=None, line_items=[], excel_result={}, sp_result={})
    with p1, p2:
        MsOutboxWorker(repo=MagicMock())._handle_expense_pull_fanout(_row({"project_id": 10}), {"project_id": 10})
    svc.sync_to_excel_workbook.assert_not_called()


def test_drain_all_honours_max_rows_and_time_budget():
    worker = MsOutboxWorker(repo=MagicMock())
    worker.drain_once = MagicMock(return_value=True)
    assert worker.drain_all(max_rows=3) == 3
    worker.drain_once.reset_mock()
    assert worker.drain_all(max_rows=50, time_budget_seconds=0.0) == 0
    worker.drain_once.assert_not_called()


def test_ms_drain_route_runs_a_bounded_loop_not_one_row():
    with patch("integrations.ms.outbox.business.worker.MsOutboxWorker") as worker_cls:
        worker_cls.return_value.drain_all.return_value = 7
        envelope = asyncio.run(admin_module.drain_ms_outbox_router())
    worker_cls.return_value.drain_all.assert_called_once_with(
        max_rows=admin_module.MS_DRAIN_MAX_ROWS,
        time_budget_seconds=admin_module.MS_DRAIN_TIME_BUDGET_SECONDS,
    )
    worker_cls.return_value.drain_once.assert_not_called()
    assert envelope["result"] == {"ms": "ok", "processed": 7}


# --------------------------------------------------------------------------- #
# A3 — lock_busy visibility
# --------------------------------------------------------------------------- #

@contextmanager
def _denied_lock(entity, timeout_ms=0):
    yield False


@contextmanager
def _granted_lock(entity, timeout_ms=0):
    yield True


def test_lock_busy_skip_warns_and_counts_consecutive_skips(monkeypatch, caplog):
    admin_module._QBO_SYNC_LOCK_BUSY_STREAK.clear()
    monkeypatch.setattr(admin_module, "_qbo_sync_fn", lambda entity: (lambda: {"result": {"success": True}, "status_code": 200}))
    monkeypatch.setattr(admin_module, "qbo_sync_lock", _denied_lock)
    with caplog.at_level(logging.WARNING, logger="shared.api.admin"):
        first = asyncio.run(admin_module.sync_qbo_router(entity="purchase", attachments=True))
        second = asyncio.run(admin_module.sync_qbo_router(entity="purchase", attachments=True))
    assert (first["reason"], first["consecutive_skips"]) == ("lock_busy", 1)
    assert second["consecutive_skips"] == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "skipped_lock_busy" in r.getMessage()]
    assert len(warnings) == 2 and "consecutive_skips=2" in warnings[-1].getMessage()

    monkeypatch.setattr(admin_module, "qbo_sync_lock", _granted_lock)
    asyncio.run(admin_module.sync_qbo_router(entity="purchase", attachments=True))
    assert "purchase" not in admin_module._QBO_SYNC_LOCK_BUSY_STREAK


# --------------------------------------------------------------------------- #
# A4 — vendor miss resolves on demand, else HOLDS
# --------------------------------------------------------------------------- #



def _connector(resolver):
    from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector

    with patch(LINE_CONNECTOR_PATH, return_value=MagicMock()):
        connector = PurchaseExpenseConnector(
            expense_service=MagicMock(), vendor_service=MagicMock(),
            vendor_vendor_repo=MagicMock(), qbo_vendor_repo=MagicMock(),
            vendor_on_demand_resolver=resolver,
        )
    connector._line_connector = MagicMock()
    return connector


def _purchase():
    return SimpleNamespace(
        id=901, qbo_id="77", realm_id=REALM_ID, entity_ref_value="qbo-vendor-NEW",
        doc_number="5001", txn_date="2026-07-01", private_note="card spend",
        total_amt=100, credit=False, sync_token="3",
    )


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_vendor_miss_resolves_on_demand_then_binds():
    resolver = MagicMock(return_value="vendor-pub-NEW")
    connector = _connector(resolver)
    # dbo lookup: miss first, hit after the on-demand projection.
    lookups = iter([None, "vendor-pub-NEW"])
    connector._get_vendor_public_id = MagicMock(side_effect=lambda ref, realm: next(lookups))
    stored = SimpleNamespace(id=500, public_id="exp-pub-500", reference_number="5001", row_version="rv")
    connector.expense_service.read_by_qbo_identity.return_value = stored
    connector.expense_service.update_by_public_id.return_value = stored

    with patch(f"{CONNECTOR_MODULE}.guard_lines_present"):
        connector.sync_from_qbo_purchase(_purchase(), [])

    resolver.assert_called_once_with("qbo-vendor-NEW", REALM_ID)
    assert connector.expense_service.update_by_public_id.call_args.kwargs["vendor_public_id"] == "vendor-pub-NEW"


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_vendor_lookup_404_is_a_permanent_skip_not_a_hold():
    """P2 from Pass 1: a Purchase can pay a Customer or an Employee. `vendor/{id}`
    404s for those — permanent data, so the tick must SKIP (ValueError) as it did
    before the on-demand path, never hold the watermark for 2h per reimbursement."""
    from integrations.intuit.qbo.base.errors import QboNotFoundError

    connector = _connector(MagicMock(side_effect=QboNotFoundError("404")))
    connector._get_vendor_public_id = MagicMock(return_value=None)
    with pytest.raises(ValueError, match="not a Vendor") as excinfo:
        connector.sync_from_qbo_purchase(_purchase(), [])
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_error("77", excinfo.value, label="QboPurchase->Expense", logger=logging.getLogger("t"))
    assert not outcome.should_hold
    assert outcome.skipped_ids == ["77"]
    # A second purchase paying the same non-vendor in the run does not re-issue the GET.
    with pytest.raises(ValueError, match="not a Vendor"):
        connector.sync_from_qbo_purchase(_purchase(), [])
    assert connector._vendor_on_demand_resolver.call_count == 1


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_vendor_miss_that_cannot_resolve_holds_the_watermark_instead_of_skipping():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import VendorNotResolvedError

    connector = _connector(MagicMock(side_effect=RuntimeError("QBO vendor 404")))
    connector._get_vendor_public_id = MagicMock(return_value=None)

    with pytest.raises(VendorNotResolvedError) as excinfo:
        connector.sync_from_qbo_purchase(_purchase(), [])
    assert not isinstance(excinfo.value, ValueError)

    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_error("77", excinfo.value, label="QboPurchase->Expense", logger=logging.getLogger("t"))
    assert outcome.should_hold
    assert outcome.skipped_ids == []
