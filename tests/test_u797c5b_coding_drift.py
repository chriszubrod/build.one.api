"""U-797c5b: record-only coding-drift check when a QBO-pulled document is completed.

We never write to QBO here. These tests pin the pure matcher, the failure-isolated
recorder helper, and the Bill / BillCredit completion wiring.
"""
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.drift_types import (
    DRIFT_REVIEWED_CODING_NOT_IN_QBO,
    KNOWN_DRIFT_TYPES,
)
from shared.completion.qbo_coding_drift import (
    CodingDrift,
    find_coding_drift,
    record_coding_drift_on_completion,
)


def _local(id=1, qbo_id="10", scc=100, project=200, description="SECRET DESCRIPTION"):
    return SimpleNamespace(
        id=id, qbo_id=qbo_id, sub_cost_code_id=scc, project_id=project, description=description,
    )


def _staged(qbo_line_id="10", item="ITEM-A", customer="CUST-A"):
    return SimpleNamespace(qbo_line_id=qbo_line_id, item_ref_value=item, customer_ref_value=customer)


def _match(local_lines, staged_lines, *, item=lambda s: "ITEM-A", cust=lambda p: "CUST-A",
           holding=lambda s: False):
    return find_coding_drift(
        local_lines, staged_lines,
        item_qbo_id_of=item, customer_qbo_id_of=cust, is_holding=holding,
    )


# ------------------------------------------------------------------ find_coding_drift

def test_item_drift_reported():
    out = _match([_local()], [_staged(item="ITEM-A")], item=lambda s: "ITEM-B")
    assert out == [CodingDrift(line_id=1, qbo_line_id="10", field="item")]


def test_customer_drift_reported():
    out = _match([_local()], [_staged(customer="CUST-A")], cust=lambda p: "CUST-B")
    assert out == [CodingDrift(line_id=1, qbo_line_id="10", field="customer")]


def test_equal_coding_yields_no_drift():
    assert _match([_local()], [_staged()]) == []


def test_local_sub_cost_code_none_means_no_item_drift():
    out = _match([_local(scc=None)], [_staged(item="ITEM-A")], item=lambda s: "ZZZ")
    assert out == []


def test_local_project_none_with_staged_customer_is_not_clearing_drift():
    """The clearing trap: a local None never implies the QBO customer should be cleared."""
    out = _match([_local(project=None)], [_staged(customer="CUST-A")], cust=lambda p: None)
    assert out == []


def test_holding_local_code_produces_no_item_drift():
    out = _match([_local(scc=9)], [_staged(item="ITEM-A")],
                 item=lambda s: "OTHER", holding=lambda s: True)
    assert out == []


def test_local_scc_whose_resolved_qbo_id_is_none_is_not_drift():
    """F2: code not in QBO (resolved None) must not count as drift; we never imply a clear."""
    out = _match([_local()], [_staged(item="ITEM-A")], item=lambda s: None)
    assert out == []


def test_local_project_whose_resolved_qbo_id_is_none_is_not_drift():
    """F2: an actor-scoped project read that returns None must not count as customer drift."""
    out = _match([_local()], [_staged(customer="CUST-A")], cust=lambda p: None)
    assert out == []


def test_local_line_without_qbo_id_is_skipped():
    out = _match([_local(qbo_id=None)], [_staged(qbo_line_id="10", item="X")],
                 item=lambda s: "Y", cust=lambda p: "Z")
    assert out == []


def test_local_line_with_no_staged_match_is_skipped():
    out = _match([_local(qbo_id="99")], [_staged(qbo_line_id="10", item="X")],
                 item=lambda s: "Y")
    assert out == []


def test_str_and_int_qbo_ids_compare_equal():
    assert _match([_local(qbo_id=10)], [_staged(qbo_line_id="10")]) == []
    out = _match([_local(qbo_id=10)], [_staged(qbo_line_id="10", item="ITEM-A")],
                 item=lambda s: "ITEM-B")
    assert out == [CodingDrift(line_id=1, qbo_line_id="10", field="item")]


def test_output_order_is_local_order_item_before_customer():
    locals_ = [_local(id=1, qbo_id="10"), _local(id=2, qbo_id="20")]
    staged = [_staged(qbo_line_id="10"), _staged(qbo_line_id="20")]
    out = _match(locals_, staged, item=lambda s: "NOPE", cust=lambda p: "NOPE")
    assert out == [
        CodingDrift(1, "10", "item"),
        CodingDrift(1, "10", "customer"),
        CodingDrift(2, "20", "item"),
        CodingDrift(2, "20", "customer"),
    ]


# ------------------------------------------------------------------ record_coding_drift_on_completion

RECORDER_DEDUPE = "integrations.intuit.qbo.base.reconciliation_recorder._record_deduped"


def _call(*, local_lines=None, staged=None, read=None, qbo_id="555", realm_id="realm-1",
          item=lambda s: "ITEM-A", cust=lambda p: "CUST-A", logger=None, repo=None):
    if read is None:
        read = MagicMock(return_value=staged if staged is not None else [_staged()])
    return record_coding_drift_on_completion(
        entity_type="Bill",
        entity_public_id="pub-123",
        qbo_id=qbo_id,
        realm_id=realm_id,
        local_lines=local_lines if local_lines is not None else [_local()],
        read_staged_lines=read,
        item_qbo_id_of=item,
        customer_qbo_id_of=cust,
        is_holding=lambda s: False,
        reconciliation_repo=repo or MagicMock(),
        logger=logger or logging.getLogger("test.u797c5b"),
    )


def test_no_qbo_id_returns_unchecked_and_reads_nothing():
    read = MagicMock()
    out = _call(qbo_id=None, read=read)
    assert out == {"checked": False, "drift_count": 0}
    read.assert_not_called()


def test_no_realm_id_returns_unchecked_and_reads_nothing():
    read = MagicMock()
    out = _call(realm_id=None, read=read)
    assert out == {"checked": False, "drift_count": 0}
    read.assert_not_called()


def test_no_staging_header_returns_unchecked():
    out = _call(read=MagicMock(return_value=None))
    assert out == {"checked": False, "drift_count": 0}


def test_drift_records_once_with_drift_type_and_ids_only():
    repo = MagicMock()
    with patch(RECORDER_DEDUPE) as dedupe:
        out = _call(
            local_lines=[_local(id=45, qbo_id="2", scc=100, project=200)],
            staged=[_staged(qbo_line_id="2", item="ITEM-A", customer="CUST-A")],
            item=lambda s: "ITEM-B",
            cust=lambda p: "CUST-A",
            repo=repo,
        )
    assert out == {"checked": True, "drift_count": 1}
    dedupe.assert_called_once()
    args, kwargs = dedupe.call_args
    assert args[0] is repo
    assert kwargs["drift_type"] == DRIFT_REVIEWED_CODING_NOT_IN_QBO
    assert kwargs["entity_type"] == "Bill"
    assert kwargs["qbo_id"] == "555"
    details = kwargs["details"]
    assert "line 45" in details and "qbo line 2" in details and "item" in details
    assert "SECRET DESCRIPTION" not in details
    assert "ITEM-A" not in details and "ITEM-B" not in details


def test_no_drift_records_nothing_and_reports_checked():
    with patch(RECORDER_DEDUPE) as dedupe:
        out = _call()
    assert out == {"checked": True, "drift_count": 0}
    dedupe.assert_not_called()


def test_read_failure_is_swallowed_and_logged():
    logger = MagicMock()
    read = MagicMock(side_effect=RuntimeError("db down"))
    out = _call(read=read, logger=logger)
    assert out == {"checked": False, "drift_count": 0}
    logger.exception.assert_called_once()


def test_each_id_resolved_at_most_once_per_call():
    item = MagicMock(return_value="ITEM-A")
    cust = MagicMock(return_value="CUST-A")
    locals_ = [_local(id=i, qbo_id=str(i)) for i in (1, 2, 3)]
    staged = [_staged(qbo_line_id=str(i)) for i in (1, 2, 3)]
    out = _call(local_lines=locals_, staged=staged, item=item, cust=cust)
    assert out == {"checked": True, "drift_count": 0}
    assert item.call_count == 1
    assert cust.call_count == 1


def test_drift_type_is_registered():
    assert DRIFT_REVIEWED_CODING_NOT_IN_QBO == "reviewed_coding_not_in_qbo"
    assert DRIFT_REVIEWED_CODING_NOT_IN_QBO in KNOWN_DRIFT_TYPES


# ------------------------------------------------------------------ BillService wiring

def _bill_service():
    from entities.bill.business.service import BillService

    return BillService(repo=MagicMock())


def test_bill_without_qbo_id_does_nothing():
    svc = _bill_service()
    bill = SimpleNamespace(id=1, public_id="pub-1", qbo_id=None, realm_id=None)
    with patch("shared.completion.qbo_coding_drift.record_coding_drift_on_completion") as rec:
        result = svc._record_qbo_coding_drift(bill=bill, line_items=[_local()])
    assert result is None
    rec.assert_not_called()


def test_bill_helper_failure_is_isolated():
    """A failure inside the shared helper (here: a bill with no public_id) never raises."""
    svc = _bill_service()
    bill = SimpleNamespace(id=1, qbo_id="555", realm_id="realm-1")  # no public_id -> AttributeError inside
    with patch("shared.completion.qbo_coding_drift.record_coding_drift_on_completion") as rec:
        assert svc._record_qbo_coding_drift(bill=bill, line_items=[]) is None
    rec.assert_not_called()


def test_pulled_bill_invokes_helper_with_bill_identity_and_lines():
    svc = _bill_service()
    lines = [_local()]
    bill = SimpleNamespace(id=1, public_id="pub-1", qbo_id="555", realm_id="realm-1")
    with patch("entities.sub_cost_code.business.service.SubCostCodeService"), \
         patch("entities.project.business.service.ProjectService"), \
         patch("integrations.intuit.qbo.bill.persistence.repo.QboBillRepository") as bill_repo, \
         patch("integrations.intuit.qbo.bill.persistence.repo.QboBillLineRepository") as line_repo, \
         patch("shared.completion.qbo_coding_drift.record_coding_drift_on_completion",
               side_effect=lambda **kw: (kw["read_staged_lines"](), {"checked": True, "drift_count": 2})[1]) as rec:
        bill_repo.return_value.read_by_qbo_id_and_realm_id.return_value = SimpleNamespace(id=7)
        line_repo.return_value.read_by_qbo_bill_id.return_value = [_staged()]
        result = svc._record_qbo_coding_drift(bill=bill, line_items=lines)
    assert result is None
    kwargs = rec.call_args.kwargs
    assert kwargs["entity_type"] == "Bill"
    assert kwargs["entity_public_id"] == "pub-1"
    assert kwargs["qbo_id"] == "555"
    assert kwargs["realm_id"] == "realm-1"
    assert kwargs["local_lines"] is lines
    bill_repo.return_value.read_by_qbo_id_and_realm_id.assert_called_once_with("555", "realm-1")
    line_repo.return_value.read_by_qbo_bill_id.assert_called_once_with(7)


# ------------------------------------------------------------------ BillCredit wiring

def _credit(qbo_id=None, **overrides):
    from entities.bill_credit.business.model import BillCredit

    values = dict(
        id=7, public_id="bc-7", row_version="rv", created_datetime=None, modified_datetime=None,
        vendor_id=3, credit_date="2026-11-01", credit_number="VC-100", total_amount=Decimal("30"),
        memo=None, is_draft=True, qbo_id=qbo_id, realm_id="realm-1" if qbo_id else None,
    )
    values.update(overrides)
    return BillCredit(**values)


def _credit_service(current):
    from entities.bill_credit.business.complete_service import BillCreditCompleteService

    svc = object.__new__(BillCreditCompleteService)
    svc.bill_credit_service = SimpleNamespace(read_by_id=lambda id: current)
    return svc


def test_enqueue_signature_reverted_and_pulled_credit_result_unchanged():
    """The drift check moved to complete_bill_credit; _enqueue_qbo_sync keeps its original shape."""
    import inspect
    from entities.bill_credit.business.complete_service import BillCreditCompleteService

    assert list(inspect.signature(BillCreditCompleteService._enqueue_qbo_sync).parameters) == ["self", "bill_credit"]
    current = _credit(qbo_id="880")
    svc = _credit_service(current)
    with patch("shared.completion.qbo_coding_drift.record_coding_drift_for") as rec:
        result = svc._enqueue_qbo_sync(bill_credit=current)
    assert result == {
        "success": True,
        "message": "Already in QBO; no create queued",
        "qbo_sync_queued": False,
        "errors": [],
    }
    rec.assert_not_called()


def test_credit_without_qbo_id_does_not_run_drift_check():
    current = _credit()
    svc = _credit_service(current)
    with patch("shared.completion.qbo_coding_drift.record_coding_drift_for") as rec, \
         patch("shared.completion.qbo_enqueue.enqueue_qbo_push",
               return_value={"success": True, "qbo_sync_queued": True, "errors": []}):
        svc._enqueue_qbo_sync(bill_credit=current)
    svc._record_qbo_coding_drift(current, [])
    rec.assert_not_called()


# ------------------------------------------------------------------ F1 / F3: real BillCredit method

VC_REPO = "integrations.intuit.qbo.vendorcredit.persistence.repo.QboVendorCreditRepository"
RECON_REPO = "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository"
CS_SCC = "entities.sub_cost_code.business.service.SubCostCodeService"
CS_PROJECT = "entities.project.business.service.ProjectService"


def _credit_with_line_service(current, by_id_lines):
    svc = _credit_service(current)
    svc.bill_credit_line_item_service = SimpleNamespace(
        read_by_id=lambda id: by_id_lines.get(id),
        read_by_bill_credit_id=lambda bill_credit_id: [],
    )
    return svc


def test_f1_drift_detected_via_by_id_reread_when_list_lines_lack_qbo_id():
    """F1: list-read lines carry no QboId; by-id re-read does. Real method must detect and record."""
    current = _credit(qbo_id="880")
    list_lines = [SimpleNamespace(id=1, qbo_id=None, sub_cost_code_id=10, project_id=20, description="SECRET")]
    by_id = {1: SimpleNamespace(id=1, qbo_id="5", sub_cost_code_id=10, project_id=20, description="SECRET")}
    svc = _credit_with_line_service(current, by_id)
    header_repo = MagicMock()
    header_repo.return_value.read_by_qbo_id_and_realm_id.return_value = SimpleNamespace(id=9)
    header_repo.return_value.read_lines_by_vendor_credit_id.return_value = [
        SimpleNamespace(qbo_line_id="5", item_ref_value="ITEM-A", customer_ref_value="CUST-A"),
    ]
    scc = MagicMock()
    scc.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="ITEM-B")
    scc.return_value.is_holding_code.return_value = False
    proj = MagicMock()
    proj.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="CUST-A")
    with patch(VC_REPO, header_repo), patch(RECON_REPO), patch(CS_SCC, scc), patch(CS_PROJECT, proj), \
         patch(RECORDER_DEDUPE) as dedupe:
        svc._record_qbo_coding_drift(current, list_lines)
    dedupe.assert_called_once()
    kwargs = dedupe.call_args.kwargs
    assert kwargs["drift_type"] == DRIFT_REVIEWED_CODING_NOT_IN_QBO
    assert kwargs["entity_type"] == "BillCredit"
    assert "line 1 (qbo line 5) item" in kwargs["details"]
    assert "SECRET" not in kwargs["details"]


def test_f1_line_whose_reread_is_none_is_skipped():
    current = _credit(qbo_id="880")
    list_lines = [SimpleNamespace(id=1, qbo_id=None, sub_cost_code_id=10, project_id=20)]
    svc = _credit_with_line_service(current, {})
    header_repo = MagicMock()
    header_repo.return_value.read_by_qbo_id_and_realm_id.return_value = SimpleNamespace(id=9)
    header_repo.return_value.read_lines_by_vendor_credit_id.return_value = [
        SimpleNamespace(qbo_line_id="5", item_ref_value="ITEM-A", customer_ref_value="CUST-A"),
    ]
    with patch(VC_REPO, header_repo), patch(RECON_REPO), patch(CS_SCC), patch(CS_PROJECT), \
         patch(RECORDER_DEDUPE) as dedupe:
        svc._record_qbo_coding_drift(current, list_lines)
    dedupe.assert_not_called()


def test_f3_staging_read_failure_never_breaks_enqueue_result(monkeypatch):
    current = _credit(qbo_id="880")
    list_lines = [SimpleNamespace(id=1, qbo_id=None, sub_cost_code_id=10, project_id=20)]
    svc = _credit_with_line_service(current, {1: SimpleNamespace(id=1, qbo_id="5", sub_cost_code_id=10, project_id=20)})
    header_repo = MagicMock()
    header_repo.return_value.read_by_qbo_id_and_realm_id.side_effect = RuntimeError("staging down")
    with patch(VC_REPO, header_repo), patch(RECON_REPO), patch(CS_SCC), patch(CS_PROJECT):
        svc._record_qbo_coding_drift(current, list_lines)
        result = svc._enqueue_qbo_sync(bill_credit=current)
    assert result == {
        "success": True,
        "message": "Already in QBO; no create queued",
        "qbo_sync_queued": False,
        "errors": [],
    }




# ------------------------------------------------------------------ F3 real complete_bill_credit path

def _complete_path(monkeypatch, *, credit, list_lines, line_by_id, line_reread_raises=False):
    from entities.bill_credit.business.complete_service import BillCreditCompleteService

    public = _credit()  # the public-id read lacks QboId, as in production
    svc = object.__new__(BillCreditCompleteService)

    class _BCS:
        def read_by_public_id(self, public_id):
            return public

        def read_by_id(self, id):
            return credit

        def update_by_public_id(self, **kwargs):
            return public

    class _Vendors:
        def read_by_id(self, id):
            return SimpleNamespace(id=3, public_id="v-3", name="Acme")

    class _Lines:
        def read_by_bill_credit_id(self, bill_credit_id):
            return list_lines

        def read_by_id(self, id):
            if line_reread_raises:
                raise RuntimeError("line reread down")
            return line_by_id.get(id)

    svc.bill_credit_service = _BCS()
    svc.vendor_service = _Vendors()
    svc.bill_credit_line_item_service = _Lines()
    svc.project_service = SimpleNamespace(read_by_id=lambda id: None)
    monkeypatch.setattr(BillCreditCompleteService, "_assert_no_holding_code_on_complete", lambda self, _id: None)
    monkeypatch.setattr(BillCreditCompleteService, "_upload_attachments_to_module_folder",
                        lambda self, **kw: {"errors": []})
    monkeypatch.setattr(BillCreditCompleteService, "_enqueue_box_excel", lambda self, **kw: None)
    monkeypatch.setattr(BillCreditCompleteService, "_enqueue_box_uploads", lambda self, **kw: None)
    import entities.bill_credit.business.complete_service as complete_mod
    monkeypatch.setattr(complete_mod.time, "sleep", lambda s: None)
    return svc


def test_f3_real_complete_path_line_reread_raises_result_unchanged(monkeypatch):
    credit = _credit(qbo_id="880")
    svc = _complete_path(
        monkeypatch, credit=credit,
        list_lines=[SimpleNamespace(id=1, qbo_id=None, project_id=None, sub_cost_code_id=None, is_draft=False, public_id="bcli-1")],
        line_by_id={}, line_reread_raises=True,
    )
    result = svc.complete_bill_credit("bc-7")
    assert result["qbo_sync"] == {
        "success": True,
        "message": "Already in QBO; no create queued",
        "qbo_sync_queued": False,
        "errors": [],
    }
    assert result["errors"] == []
    assert result["status_code"] == 200


def test_complete_path_invokes_drift_check_only_for_pulled_credit(monkeypatch):
    rec = MagicMock()
    monkeypatch.setattr("shared.completion.qbo_coding_drift.record_coding_drift_for", rec)
    svc = _complete_path(monkeypatch, credit=_credit(qbo_id="880"), list_lines=[], line_by_id={})
    result = svc.complete_bill_credit("bc-7")
    rec.assert_called_once()
    assert rec.call_args.kwargs["entity_type"] == "BillCredit"
    assert result["status_code"] == 200


def test_complete_path_app_created_credit_runs_no_drift_check(monkeypatch):
    rec = MagicMock()
    enqueue = MagicMock(return_value={"success": True, "qbo_sync_queued": True, "errors": []})
    monkeypatch.setattr("shared.completion.qbo_coding_drift.record_coding_drift_for", rec)
    monkeypatch.setattr("shared.completion.qbo_enqueue.enqueue_qbo_push", enqueue)
    svc = _complete_path(monkeypatch, credit=_credit(), list_lines=[], line_by_id={})
    svc.complete_bill_credit("bc-7")
    enqueue.assert_called_once()
    rec.assert_not_called()


# ------------------------------------------------------------------ EM mutation-kill tests (U-797c5b)

def test_staged_int_item_ref_equals_resolved_str_item_qbo_id():
    """M08/M09: staged ItemRefValue may be an int; it must compare equal to the resolved str id."""
    assert _match([_local()], [_staged(item=123)], item=lambda s: "123") == []
    assert _match([_local()], [_staged(customer=456)], cust=lambda p: "456") == []


def test_customer_lookup_not_called_for_a_local_line_without_project():
    """M07: a local line with no project never resolves (or compares) a customer."""
    cust = MagicMock(return_value="CUST-A")
    _match([_local(project=None)], [_staged()], cust=cust)
    cust.assert_not_called()


def test_holding_rule_is_honoured_through_the_recording_entry_point():
    """M23: record_coding_drift_on_completion must pass is_holding through to the matcher."""
    repo = MagicMock()
    with patch(RECORDER_DEDUPE) as dedupe:
        out = record_coding_drift_on_completion(
            entity_type="Bill", entity_public_id="pub-1", qbo_id="555", realm_id="realm-1",
            local_lines=[_local()], read_staged_lines=lambda: [_staged(item="ITEM-A")],
            item_qbo_id_of=lambda s: "ITEM-B", customer_qbo_id_of=lambda p: "CUST-A",
            is_holding=lambda s: True, reconciliation_repo=repo,
            logger=logging.getLogger("test.u797c5b"),
        )
    assert out == {"checked": True, "drift_count": 0}
    dedupe.assert_not_called()


def test_f1_none_reread_line_is_skipped_and_other_lines_still_checked():
    """M19: a line whose by-id re-read is None is dropped; the remaining lines are still checked."""
    current = _credit(qbo_id="880")
    list_lines = [SimpleNamespace(id=1, qbo_id=None), SimpleNamespace(id=2, qbo_id=None)]
    by_id = {2: SimpleNamespace(id=2, qbo_id="6", sub_cost_code_id=10, project_id=20)}
    svc = _credit_with_line_service(current, by_id)
    header_repo = MagicMock()
    header_repo.return_value.read_by_qbo_id_and_realm_id.return_value = SimpleNamespace(id=9)
    header_repo.return_value.read_lines_by_vendor_credit_id.return_value = [
        SimpleNamespace(qbo_line_id="6", item_ref_value="ITEM-A", customer_ref_value="CUST-A"),
    ]
    scc = MagicMock()
    scc.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="ITEM-B")
    scc.return_value.is_holding_code.return_value = False
    proj = MagicMock()
    proj.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="CUST-A")
    with patch(VC_REPO, header_repo), patch(RECON_REPO), patch(CS_SCC, scc), patch(CS_PROJECT, proj), \
         patch(RECORDER_DEDUPE) as dedupe:
        svc._record_qbo_coding_drift(current, list_lines)
    dedupe.assert_called_once()
    assert "line 2 (qbo line 6) item" in dedupe.call_args.kwargs["details"]


def test_complete_bill_runs_the_drift_check_on_the_finalized_bill_without_changing_the_result():
    """M21: complete_bill calls the drift check with the FinalizeBillById result and its lines."""
    from entities.bill.business.service import BillService

    finalized = SimpleNamespace(
        id=55, public_id="pub-55", row_version="cm9ja2V0", vendor_id=7, payment_term_id=None,
        bill_date="2026-09-01", due_date="2026-09-01", bill_number="INV-1", total_amount=None,
        memo=None, is_draft=False, qbo_id="QB-9", realm_id="realm-1",
    )
    repo = MagicMock()
    repo.finalize_by_id.return_value = finalized
    repo.read_by_bill_number_and_vendor_id.return_value = None
    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(**{**vars(finalized), "is_draft": True}))
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-pub-1")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = []
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))
    with patch.object(BillService, "_record_qbo_coding_drift") as check:
        result = svc.complete_bill(public_id="pub-55")
    check.assert_called_once()
    assert check.call_args.kwargs["bill"] is finalized
    assert check.call_args.kwargs["line_items"] == []
    assert result["bill_finalized"] is True
