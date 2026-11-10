"""U-551b-f1: worker repoint branch + written_lines persistence."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
REALM = "realm-test"
CONNECTOR = "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
SVC = "entities.coding_suggestion.business.service.CodingSuggestionService"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"

WRITTEN = [{"qbo_line_id": "L1", "item_ref_value": "item-OLD", "sub_cost_code_id": 5}]
TARGETS = [{"qbo_line_id": "L1", "sub_cost_code_id": 9, "project_id": 303, "description": "ld",
            "repoint_from_item_ref_value": "item-OLD"}]


def _item(source=REPOINT_CONFIRMATION_SOURCE, written_lines=WRITTEN, targets=TARGETS):
    return SimpleNamespace(
        public_id=PUBLIC_ID, status="enqueued", qbo_purchase_qbo_id="purchase-123",
        confirmed_sub_cost_code_id=101, confirmed_project_id=202, confirmed_description="d",
        sync_token_at_suggest="7", written_sync_token="6", qbo_purchase_id=999, realm_id=REALM,
        confirmation_source=source, confirmation_seq=3, written_lines=written_lines,
        repoint_targets=targets,
    )


def _row():
    return QboOutbox(id=1, public_id="o1", row_version="a", kind="recode_purchase_line",
                     entity_type="CodingSuggestion", entity_public_id=PUBLIC_ID, realm_id=REALM,
                     request_id="r", status="processing", attempts=0)


def _local(sub=5):  # the local line was pulled back to the written coding: targets must still win
    return SimpleNamespace(sub_cost_code_id=sub, project_id=999, description="pulled-back", quantity=1,
                           rate=10, amount=10, is_billable=True, billable_decided_at="x")


def _run(item, *, local="ok", resolver_value="item-NEW", connector_result=None, gate=None, untouched=True):
    svc = MagicMock()
    svc.read_by_public_id.return_value = item
    connector = MagicMock()
    connector.verify_purchase_untouched.return_value = untouched
    connector.recode_purchase_lines.return_value = connector_result or {"status": "written", "sync_token": "8"}
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(
        return_value=(_local() if local == "ok" else None, None if local == "ok" else "no_line"))
    worker._read_uncategorized_staging_line_ids = MagicMock(return_value=["S1"])
    res = None if resolver_value is None else SimpleNamespace(value=resolver_value, name="n")
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), patch(RESOLVER, return_value=res), \
            patch(GATE, return_value=gate):
        worker._handle_recode_purchase_line(_row())
    return svc, connector, worker


def test_repoint_calls_connector_with_local_coding():
    svc, connector, _ = _run(_item())
    connector.recode_purchase_lines.assert_called_once()
    kw = connector.recode_purchase_lines.call_args.kwargs
    assert kw["expected_sync_token"] == "7"
    (spec,) = kw["lines"]
    assert spec["repoint_from_item_ref_value"] == "item-OLD"
    assert spec["target_qbo_line_id"] == "L1"
    # coding from the PERSISTED targets (R3-C), not the pulled-back local line
    assert spec["sub_cost_code_id"] == 9 and spec["project_id"] == 303 and spec["description"] == "ld"
    assert spec["quantity"] == 1 and spec["amount"] == 10


def test_repoint_unchanged_item_is_idempotent_noop():
    svc, connector, _ = _run(_item(), resolver_value="item-OLD")
    connector.recode_purchase_lines.assert_not_called()
    svc.mark_written.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=3, sync_token="6")
    svc.mark_error.assert_not_called()


def test_repoint_without_targets_fails_closed():
    svc, connector, _ = _run(_item(targets=None))  # R4b-0: None = absent/malformed record
    connector.recode_purchase_lines.assert_not_called()
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_without_targets"


def test_repoint_explicit_empty_targets_is_an_idempotent_noop_leaving_the_record_untouched():
    # R4b-0: an explicit [] (the user recoded back to the written item) drains NOTHING
    svc, connector, _ = _run(_item(targets=[]))
    connector.recode_purchase_lines.assert_not_called()
    svc.mark_error.assert_not_called()
    svc.mark_changed_in_qbo.assert_not_called()
    svc.mark_written.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=3, sync_token="6")


def test_repoint_unresolvable_local_line():
    svc, connector, _ = _run(_item(), local="missing")
    connector.recode_purchase_lines.assert_not_called()
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_line_unresolved"


def test_repoint_unmapped_item():
    svc, connector, _ = _run(_item(), resolver_value=None)
    connector.recode_purchase_lines.assert_not_called()
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_line_unmapped"


def test_non_repoint_uses_staging_query():
    svc, connector, worker = _run(_item(source="review_submit", written_lines=[]))
    worker._read_uncategorized_staging_line_ids.assert_called_once()
    (spec,) = connector.recode_purchase_lines.call_args.kwargs["lines"]
    assert spec["target_qbo_line_id"] == "S1"
    assert "repoint_from_item_ref_value" not in spec


def test_written_passes_written_lines_json():
    lines = [{"qbo_line_id": "L1", "item_ref_value": "item-NEW", "sub_cost_code_id": 9}]
    svc, _, _ = _run(_item(), connector_result={"status": "written", "sync_token": "8", "written_lines": lines})
    kw = svc.mark_written.call_args.kwargs
    assert json.loads(kw["written_lines_json"]) == lines
    assert kw["sync_token"] == "8"


def test_already_recoded_does_not_overwrite_the_written_token():
    # already_recoded must NOT overwrite WrittenSyncToken (sproc CASE preserves it on NULL).
    svc, _, _ = _run(_item(), connector_result={"status": "already_recoded", "sync_token": "8"})
    kw = svc.mark_written.call_args.kwargs
    assert kw["sync_token"] is None and kw["written_lines_json"] is None


def test_repoint_local_line_on_holding_code_fails_closed():
    from shared.lifecycle.completion_gate import HoldingCodeRefused
    svc = MagicMock()
    svc.read_by_public_id.return_value = _item()
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    connector = MagicMock()
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), patch(GATE, return_value=None), \
            patch(RESOLVER, side_effect=HoldingCodeRefused("holding")):
        worker._handle_recode_purchase_line(_row())
    connector.recode_purchase_lines.assert_not_called()
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_line_holding"


# ---- R2-2: the written record is MERGED by qbo_line_id --------------------------------------

def test_merge_written_lines_new_wins_others_kept_in_order():
    from integrations.intuit.qbo.outbox.business.worker import _merge_written_lines

    prev = [{"qbo_line_id": "L1", "item_ref_value": "A"}, {"qbo_line_id": "L2", "item_ref_value": "X"}]
    new = [{"qbo_line_id": "L1", "item_ref_value": "B"}, {"qbo_line_id": "L3", "item_ref_value": "Z"}]
    assert _merge_written_lines(prev, new) == [
        {"qbo_line_id": "L1", "item_ref_value": "B"},
        {"qbo_line_id": "L2", "item_ref_value": "X"},
        {"qbo_line_id": "L3", "item_ref_value": "Z"},
    ]
    assert _merge_written_lines([], new) == new


def test_repoint_of_one_line_keeps_the_other_in_the_record():
    import json
    prev = [{"qbo_line_id": "L1", "item_ref_value": "item-OLD", "sub_cost_code_id": 5},
            {"qbo_line_id": "L2", "item_ref_value": "item-X", "sub_cost_code_id": 6}]
    new = [{"qbo_line_id": "L1", "item_ref_value": "item-NEW", "sub_cost_code_id": 9}]
    svc, _, _ = _run(_item(written_lines=prev),
                     connector_result={"status": "written", "sync_token": "8", "written_lines": new})
    rec = json.loads(svc.mark_written.call_args.kwargs["written_lines_json"])
    assert rec == [new[0], prev[1]]


# ---- R2-3: already_recoded verified_lines are merged, token left alone ----------------------

def test_repoint_already_recoded_merges_verified_lines_with_null_token():
    import json
    prev = [{"qbo_line_id": "L1", "item_ref_value": "item-OLD", "sub_cost_code_id": 5},
            {"qbo_line_id": "L2", "item_ref_value": "item-X", "sub_cost_code_id": 6}]
    ver = [{"qbo_line_id": "L1", "item_ref_value": "item-NEW", "sub_cost_code_id": 9}]
    svc, _, _ = _run(_item(written_lines=prev),
                     connector_result={"status": "already_recoded", "sync_token": "6", "verified_lines": ver})
    kw = svc.mark_written.call_args.kwargs
    assert kw["sync_token"] is None
    assert json.loads(kw["written_lines_json"]) == [ver[0], prev[1]]


# ---- R2-8g ----------------------------------------------------------------------------------

def test_repoint_economics_resolver_raising_is_line_unresolved():
    svc = MagicMock()
    svc.read_by_public_id.return_value = _item()
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(side_effect=RuntimeError("db down"))
    connector = MagicMock()
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), patch(GATE, return_value=None):
        worker._handle_recode_purchase_line(_row())
    connector.recode_purchase_lines.assert_not_called()
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_line_unresolved"


# ---- R3-A: token continuity ------------------------------------------------------------------


def test_first_write_written_replaces_the_previous_record():
    prev = [{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}]
    new = [{"qbo_line_id": "L2", "item_ref_value": "item-Y", "sub_cost_code_id": 7}]
    svc, _, _ = _run(_item(source="review_submit", written_lines=prev, targets=None),
                     connector_result={"status": "written", "sync_token": "9", "written_lines": new})
    kw = svc.mark_written.call_args.kwargs
    assert json.loads(kw["written_lines_json"]) == new and kw["sync_token"] == "9"


def test_first_write_already_recoded_preserves_record_and_token():
    # U-551b-f1-f3b: a first-write already_recoded now carries the record WHEN the connector emits verified_lines;
    # without them (this case) it still preserves both NULLs. The with-record case is in test_u551bf1f3b_prevent_empty_record.py.
    prev = [{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}]
    svc, _, _ = _run(_item(source="review_submit", written_lines=prev, targets=None),
                     connector_result={"status": "already_recoded", "sync_token": "9"})
    kw = svc.mark_written.call_args.kwargs
    assert kw["written_lines_json"] is None and kw["sync_token"] is None


def test_repoint_written_merges_written_and_verified_lines():
    prev = [{"qbo_line_id": "L1", "item_ref_value": "item-OLD", "sub_cost_code_id": 5},
            {"qbo_line_id": "L2", "item_ref_value": "item-X", "sub_cost_code_id": 6},
            {"qbo_line_id": "L3", "item_ref_value": "item-Z", "sub_cost_code_id": 8}]
    w = [{"qbo_line_id": "L1", "item_ref_value": "item-NEW", "sub_cost_code_id": 9}]
    v = [{"qbo_line_id": "L2", "item_ref_value": "item-Q", "sub_cost_code_id": 4}]
    svc, _, _ = _run(_item(written_lines=prev), connector_result={
        "status": "written", "sync_token": "8", "written_lines": w, "verified_lines": v})
    kw = svc.mark_written.call_args.kwargs
    assert json.loads(kw["written_lines_json"]) == [w[0], v[0], prev[2]] and kw["sync_token"] == "8"


def test_exact_ownership_laundering_sequence_through_real_worker_and_connector():
    """L1=A written (T6); human recodes L1=H (T7); a first write codes placeholder L2 at T8 -> record [L2] only."""
    import copy
    from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
    from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

    def line(i, item):
        return {"Id": i, "LineNum": int(i[1:]), "Description": "d", "Amount": "10.00",
                "DetailType": "ItemBasedExpenseLineDetail",
                "ItemBasedExpenseLineDetail": {"ItemRef": {"value": item, "name": "n"}}}
    placeholder = {"Id": "L2", "LineNum": 2, "Description": "d", "Amount": "10.00",
                   "DetailType": "AccountBasedExpenseLineDetail",
                   "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "58999",
                                                    "name": "Cost of construction : NEED TO CATEGORIZE"}}}
    live = {"Id": "purchase-123", "SyncToken": "8", "PaymentType": "CreditCard", "TxnDate": "2026-07-01",
            "Line": [line("L1", "item-H"), placeholder]}
    client = MagicMock()
    client.get_purchase_raw.return_value = copy.deepcopy(live)
    client.update_purchase_raw.return_value = dict(copy.deepcopy(live), SyncToken="9")
    cls = MagicMock()
    cls.return_value.__enter__.return_value = client
    cls.return_value.__exit__.return_value = None
    item = _item(source="review_submit", targets=None,
                 written_lines=[{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}])
    item.sync_token_at_suggest = "8"
    svc = MagicMock()
    svc.read_by_public_id.return_value = item
    worker = QboOutboxWorker()
    worker._read_uncategorized_staging_line_ids = MagicMock(return_value=["L2"])
    worker._resolve_recode_line_economics = MagicMock(return_value=(
        SimpleNamespace(sub_cost_code_id=7, project_id=303, description="y", quantity=1, rate=10, amount=10,
                        is_billable=True, billable_decided_at="x"), None))
    with patch(SVC, return_value=svc), patch(GATE, return_value=None), \
            patch("integrations.intuit.qbo.purchase.external.client.QboPurchaseClient", cls), \
            patch.object(PurchaseExpenseConnector, "__init__", lambda self, *a, **k: None), \
            patch.object(PurchaseExpenseConnector, "_get_qbo_item_ref",
                         lambda self, scc, realm: QboReferenceType(value="item-Y", name="Y")), \
            patch.object(PurchaseExpenseConnector, "_get_qbo_customer_ref",
                         lambda self, pid: QboReferenceType(value="c", name="c")):
        item.confirmation_source = "review_submit"
        item.confirmed_sub_cost_code_id, item.confirmed_project_id = 7, 303
        worker._handle_recode_purchase_line(_row())
    kw = svc.mark_written.call_args.kwargs
    record = json.loads(kw["written_lines_json"])
    assert [e["qbo_line_id"] for e in record] == ["L2"] and kw["sync_token"] == "9"
    # a record without L1 can never yield an L1 repoint target
    item2 = _item(written_lines=record, targets=[
        {"qbo_line_id": "L2", "sub_cost_code_id": 3, "project_id": 1, "description": None,
         "repoint_from_item_ref_value": "item-Y"}])
    with patch(RESOLVER, return_value=SimpleNamespace(value="item-NEW", name="n")):
        targets, err = worker._resolve_repoint_targets(_row(), item2, REALM)
    assert err is None and [t["target_qbo_line_id"] for t in targets] == ["L2"]
    assert "L1" not in {e["qbo_line_id"] for e in record}


# ---- R3-B / R4-A: the drain honors the recode kill switch by PARKING, never dead-lettering ----

def test_gate_off_raises_a_write_refused_error_and_makes_no_call_or_mark():
    from integrations.intuit.qbo.base.errors import QboRecodeWritesDisabledError, QboWriteRefusedError

    with pytest.raises(QboRecodeWritesDisabledError) as ei:
        _run(_item(), gate="recode_writes_disabled")
    assert isinstance(ei.value, QboWriteRefusedError) and "recode_writes_disabled" in str(ei.value)


def test_gate_off_makes_no_suggestion_mark_of_any_kind():
    # R4b-2(5): the refusal must leave the suggestion unmarked - every mark helper unused
    svc = MagicMock()
    svc.read_by_public_id.return_value = _item()
    connector = MagicMock()
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-NEW", name="n")), \
            patch(GATE, return_value="recode_writes_disabled"):
        with pytest.raises(Exception):
            worker._handle_recode_purchase_line(_row())
    connector.recode_purchase_lines.assert_not_called()
    svc.read_by_public_id.assert_not_called()  # M3: the gate precedes the read as well as every mark
    for m in (svc.mark_written, svc.mark_error, svc.mark_changed_in_qbo, svc.mark_enqueued):
        m.assert_not_called()
    svc.repo.mark_written.assert_not_called()


@pytest.mark.parametrize("attempts", [0, 4, 9])
def test_gate_off_parks_the_row_and_never_dead_letters(attempts):
    from datetime import datetime, timedelta, timezone

    from integrations.intuit.qbo.base.errors import QboRecodeWritesDisabledError
    from integrations.intuit.qbo.outbox.business import worker as worker_mod

    svc = MagicMock()
    svc.read_by_public_id.return_value = _item()
    connector = MagicMock()
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    worker.repo = MagicMock()
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-NEW", name="n")), \
            patch(GATE, return_value="qbo_writes_disabled"):
        with pytest.raises(QboRecodeWritesDisabledError) as ei:
            worker._handle_recode_purchase_line(_row())
        connector.recode_purchase_lines.assert_not_called()
        for m in (svc.mark_written, svc.mark_error, svc.mark_changed_in_qbo, svc.mark_enqueued):
            m.assert_not_called()
        row = _row()
        row.attempts = attempts
        before = datetime.now(timezone.utc)
        worker._handle_qbo_error(row, ei.value)
    worker.repo.mark_dead_letter.assert_not_called()
    worker.repo.mark_failed.assert_called_once()
    kw = worker.repo.mark_failed.call_args.kwargs
    assert kw["next_retry_at"] >= before + worker_mod.WRITE_REFUSED_PARK_INTERVAL - timedelta(seconds=1)
    assert kw["last_error"].startswith(worker_mod.WRITE_REFUSED_PARK_PREFIX)


def test_gate_on_is_unchanged():
    svc, connector, _ = _run(_item(), gate=None)
    connector.recode_purchase_lines.assert_called_once()


# ---- R4-B(3): the drain re-checks every persisted target against the record it reads NOW -----

@pytest.mark.parametrize("target", [
    dict(TARGETS[0], qbo_line_id="L9"),                              # not in written_lines
    dict(TARGETS[0], repoint_from_item_ref_value="item-OTHER"),      # prior item differs
])
def test_drain_refuses_targets_not_ours_with_no_connector_call(target):
    svc, connector, worker = _run(_item(targets=[target]))
    connector.recode_purchase_lines.assert_not_called()
    svc.mark_written.assert_not_called()
    svc.mark_error.assert_called_once()
    assert "repoint_target_not_ours" in str(svc.mark_error.call_args)


@pytest.mark.parametrize("target", [
    dict(TARGETS[0], qbo_line_id="L9"),                              # not in written_lines
    dict(TARGETS[0], repoint_from_item_ref_value="item-OTHER"),      # R4b-2(6): prior item mismatch
])
def test_resolve_repoint_targets_returns_not_ours_directly(target):
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    out, err = worker._resolve_repoint_targets(_row(), _item(targets=[target]), REALM)
    assert out is None and err == "repoint_target_not_ours"
    worker._resolve_recode_line_economics.assert_not_called()


def test_drain_passes_a_matching_set():
    _svc_, connector, _ = _run(_item())
    connector.recode_purchase_lines.assert_called_once()


# ---- U-551b-f1 round 6: M1 (suggestion lock), M3 (gate first), M4 (no-op fences on the live token) ----

LOCK_TARGET = "entities.coding_suggestion.business.service.qbo_app_lock"


def _recording_lock(events, *, grant=True, resources=None, timeouts=None):
    from contextlib import contextmanager

    @contextmanager
    def lock(resource, timeout_ms=15000):
        if resources is not None:
            resources.append(resource)
        if timeouts is not None:
            timeouts.append(timeout_ms)
        events.append("lock_enter" if grant else "lock_denied")
        try:
            yield grant
        finally:
            events.append("lock_exit")
    return lock


def test_drain_lock_denied_raises_busy_error_a_write_refused_with_no_read_or_mark():
    from integrations.intuit.qbo.base.errors import CodingSuggestionBusyError, QboWriteRefusedError

    events, resources, timeouts = [], [], []
    svc = MagicMock()
    svc.read_by_public_id.return_value = _item()
    connector = MagicMock()
    worker = QboOutboxWorker()
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), patch(GATE, return_value=None), \
            patch(LOCK_TARGET, _recording_lock(events, grant=False, resources=resources, timeouts=timeouts)):
        with pytest.raises(CodingSuggestionBusyError) as ei:
            worker._handle_recode_purchase_line(_row())
    assert isinstance(ei.value, QboWriteRefusedError)  # -> parks, never dead-letters
    assert resources == [f"qbo_coding_suggestion:{PUBLIC_ID}"] and timeouts == [5_000]
    svc.read_by_public_id.assert_not_called()
    connector.recode_purchase_lines.assert_not_called()
    for m in (svc.mark_written, svc.mark_error, svc.mark_changed_in_qbo, svc.mark_enqueued):
        m.assert_not_called()


@pytest.mark.parametrize("attempts", [0, 9])
def test_busy_error_parks_for_about_a_minute_at_warning_and_never_dead_letters(attempts, caplog):
    import logging
    from datetime import datetime, timedelta, timezone

    from integrations.intuit.qbo.base.errors import CodingSuggestionBusyError
    from integrations.intuit.qbo.outbox.business.worker import SUGGESTION_BUSY_PARK_INTERVAL

    assert SUGGESTION_BUSY_PARK_INTERVAL == timedelta(minutes=1)
    worker = QboOutboxWorker()
    worker.repo = MagicMock()
    row = _row()
    row.attempts = attempts
    before = datetime.now(timezone.utc)
    with caplog.at_level(logging.DEBUG):
        worker._handle_qbo_error(row, CodingSuggestionBusyError(PUBLIC_ID))
    worker.repo.mark_dead_letter.assert_not_called()
    worker.repo.mark_failed.assert_called_once()
    kw = worker.repo.mark_failed.call_args.kwargs
    wait = kw["next_retry_at"] - before
    assert timedelta(seconds=55) <= wait <= timedelta(seconds=75)  # ~1 minute, not the 15-minute gate park
    assert kw["last_error"].startswith("Parked: coding suggestion busy")
    busy = [r for r in caplog.records if r.getMessage() == "qbo.outbox.row.parked_suggestion_busy"]
    assert len(busy) == 1 and busy[0].levelno == logging.WARNING
    assert not any(r.getMessage() == "qbo.outbox.row.parked_write_refused" for r in caplog.records)


def test_recode_writes_disabled_still_takes_the_fifteen_minute_write_refused_park(caplog):
    import logging
    from datetime import datetime, timedelta, timezone

    from integrations.intuit.qbo.base.errors import QboRecodeWritesDisabledError

    worker = QboOutboxWorker()
    worker.repo = MagicMock()
    row = _row()
    before = datetime.now(timezone.utc)
    with caplog.at_level(logging.DEBUG):
        worker._handle_qbo_error(row, QboRecodeWritesDisabledError("recode_writes_disabled"))
    worker.repo.mark_dead_letter.assert_not_called()
    kw = worker.repo.mark_failed.call_args.kwargs
    assert timedelta(minutes=14, seconds=55) <= kw["next_retry_at"] - before <= timedelta(minutes=15, seconds=5)
    assert kw["last_error"].startswith("Parked: QBO writes disabled")
    assert any(r.getMessage() == "qbo.outbox.row.parked_write_refused" and r.levelno == logging.ERROR
               for r in caplog.records)
    assert not any(r.getMessage() == "qbo.outbox.row.parked_suggestion_busy" for r in caplog.records)


@pytest.mark.parametrize("scenario", ["empty_targets", "unresolved_targets", "terminal_item"])
def test_gate_off_marks_nothing_for_any_shape_and_the_row_parks(scenario):
    from integrations.intuit.qbo.base.errors import QboRecodeWritesDisabledError, QboWriteRefusedError

    item = {"empty_targets": _item(targets=[]), "unresolved_targets": _item(),
            "terminal_item": SimpleNamespace(**dict(vars(_item()), status="written"))}[scenario]
    svc = MagicMock()
    svc.read_by_public_id.return_value = item
    connector = MagicMock()
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(None, "no_line"))
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), \
            patch(RESOLVER, return_value=None), patch(GATE, return_value="recode_writes_disabled"):
        with pytest.raises(QboRecodeWritesDisabledError) as ei:
            worker._handle_recode_purchase_line(_row())
    assert isinstance(ei.value, QboWriteRefusedError)
    svc.read_by_public_id.assert_not_called()
    connector.verify_purchase_untouched.assert_not_called()
    connector.recode_purchase_lines.assert_not_called()
    for m in (svc.mark_written, svc.mark_error, svc.mark_changed_in_qbo, svc.mark_enqueued):
        m.assert_not_called()


def test_gate_off_never_takes_the_lock():
    events = []
    svc = MagicMock()
    with patch(SVC, return_value=svc), patch(GATE, return_value="recode_writes_disabled"), \
            patch(LOCK_TARGET, _recording_lock(events)):
        with pytest.raises(Exception):
            QboOutboxWorker()._handle_recode_purchase_line(_row())
    assert events == []


def test_wrong_entity_type_returns_before_gate_lock_and_read():
    events = []
    row = _row()
    row.entity_type = "Bill"
    svc = MagicMock()
    with patch(SVC, return_value=svc), patch(GATE, side_effect=AssertionError("gate")), \
            patch(LOCK_TARGET, _recording_lock(events)):
        QboOutboxWorker()._handle_recode_purchase_line(row)
    assert events == [] and not svc.read_by_public_id.called


def test_empty_targets_with_the_live_token_unchanged_marks_written_and_leaves_the_record_untouched():
    svc, connector, _ = _run(_item(targets=[]), untouched=True)
    connector.verify_purchase_untouched.assert_called_once_with(
        realm_id=REALM, qbo_purchase_qbo_id="purchase-123", expected_sync_token="6")
    connector.recode_purchase_lines.assert_not_called()
    svc.mark_written.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=3, sync_token="6")
    assert "written_lines_json" not in svc.mark_written.call_args.kwargs
    svc.mark_changed_in_qbo.assert_not_called()
    svc.mark_error.assert_not_called()


def test_empty_targets_with_the_live_token_moved_marks_changed_in_qbo():
    svc, connector, _ = _run(_item(targets=[]), untouched=False)
    svc.mark_changed_in_qbo.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=3)
    svc.mark_written.assert_not_called()
    connector.recode_purchase_lines.assert_not_called()


def test_empty_targets_get_failure_propagates_and_marks_nothing():
    from integrations.intuit.qbo.base.errors import QboError

    svc = MagicMock()
    svc.read_by_public_id.return_value = _item(targets=[])
    connector = MagicMock()
    connector.verify_purchase_untouched.side_effect = QboError("qbo down")
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), patch(GATE, return_value=None):
        with pytest.raises(QboError):
            QboOutboxWorker()._handle_recode_purchase_line(_row())
    for m in (svc.mark_written, svc.mark_error, svc.mark_changed_in_qbo):
        m.assert_not_called()


def test_non_repoint_no_targets_still_errors_without_a_get():
    svc = MagicMock()
    svc.read_by_public_id.return_value = _item(source="review_submit", written_lines=[], targets=None)
    connector = MagicMock()
    worker = QboOutboxWorker()
    worker._read_uncategorized_staging_line_ids = MagicMock(return_value=[])
    # U-652d3 F1: an empty strict set probes the label-only lines; none here, so it stays an error.
    worker._read_label_staging_line_ids = MagicMock(return_value=[])
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), patch(GATE, return_value=None), \
            patch(RESOLVER, return_value=SimpleNamespace(value="i", name="n")):
        worker._handle_recode_purchase_line(_row())
    connector.verify_purchase_untouched.assert_not_called()
    assert svc.mark_error.call_args.kwargs["write_error"] == "no_target_lines"  # F4(ii): still an error
    svc.mark_written.assert_not_called()


def test_the_drain_lock_spans_the_read_through_the_terminal_mark():
    events = []
    svc = MagicMock()
    svc.read_by_public_id.side_effect = lambda *a, **k: events.append("read") or _item()
    svc.mark_written.side_effect = lambda *a, **k: events.append("mark") or SimpleNamespace()
    connector = MagicMock()
    connector.recode_purchase_lines.side_effect = lambda **k: events.append("qbo_write") or {
        "status": "written", "sync_token": "8"}
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-NEW", name="n")), \
            patch(GATE, side_effect=lambda: events.append("gate")), patch(LOCK_TARGET, _recording_lock(events)):
        worker._handle_recode_purchase_line(_row())
    assert events == ["gate", "lock_enter", "read", "qbo_write", "mark", "lock_exit"]


def test_the_drain_lock_is_released_when_the_connector_raises():
    from integrations.intuit.qbo.base.errors import QboError

    events = []
    svc = MagicMock()
    svc.read_by_public_id.return_value = _item()
    connector = MagicMock()
    connector.recode_purchase_lines.side_effect = QboError("boom")
    worker = QboOutboxWorker()
    worker._resolve_recode_line_economics = MagicMock(return_value=(_local(), None))
    with patch(SVC, return_value=svc), patch(CONNECTOR, return_value=connector), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-NEW", name="n")), \
            patch(GATE, return_value=None), patch(LOCK_TARGET, _recording_lock(events)):
        with pytest.raises(QboError):
            worker._handle_recode_purchase_line(_row())
    assert events == ["lock_enter", "lock_exit"]
