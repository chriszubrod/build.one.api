"""U-797c4b — the VendorCredit pull reads the parent BillCredit's lifecycle LATE:
inside the per-line HIT update, after the line (`direct`) is read. A review that
opens between the line read and the lifecycle read is seen. The holding branch
records the credit it already read (no second read). Fakes only (the test_u361 HIT
fakes)."""

from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.bill_credit.business.service import BillCreditService
from integrations.intuit.qbo.base.field_ownership import under_review_line_coding
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)
from tests.test_u361_bill_credit_line_item_mapping_retire import (
    _build_connector,
    _make_qbo_line,
    _stamped_row,
)

CREDIT = SimpleNamespace(id=7, qbo_id="QB-7", is_draft=True)


def _drive(state, *, direct, qbo_line=None, scc=99, project="proj-qbo", scc_service=None):
    connector, bcli_svc, _ = _build_connector()
    qbo_line = qbo_line or _make_qbo_line(qbo_line_id="1", description="QBO memo", customer_ref_value="CUST-1")
    order = []

    def _line_read(*_a, **_k):
        order.append("line")
        if state.get("move_on_line_read"):
            state["status"] = state["move_on_line_read"]
        return direct

    def _lifecycle(_id):
        order.append("lifecycle")
        return state["status"], CREDIT

    bcli_svc.read_by_qbo_identity.side_effect = _line_read
    bcli_svc.update_by_public_id.return_value = SimpleNamespace(id=direct.id, public_id=direct.public_id)
    connector._get_sub_cost_code_id = MagicMock(return_value=scc)
    connector._get_project_public_id = MagicMock(return_value=project)
    if scc_service is not None:
        connector.sub_cost_code_service = scc_service
    with patch.object(VendorCreditLineItemConnector, "_parent_lifecycle", side_effect=_lifecycle):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")
    return bcli_svc.update_by_public_id.call_args, order


def _direct(scc=10, project_id=5, description="Local memo"):
    row = _stamped_row(55, "1")
    row.sub_cost_code_id = scc
    row.project_id = project_id
    row.description = description
    return row


def test_lifecycle_is_read_after_the_line_it_protects():
    _, order = _drive({"status": "in_review"}, direct=_direct())
    assert order == ["line", "lifecycle"]


def test_a_review_transition_between_the_line_read_and_the_lifecycle_read_is_seen():
    state = {"status": "draft", "move_on_line_read": "in_review"}
    call, _ = _drive(state, direct=_direct())
    assert call.kwargs["sub_cost_code_id"] == 10
    assert call.kwargs["project_public_id"] is None
    assert call.kwargs["description"] == "Local memo"


def test_under_in_review_local_coding_is_kept_amounts_from_qbo_and_row_version_from_the_late_line():
    qbo_line = _make_qbo_line(qbo_line_id="1", description="QBO memo", qty=3, amount=42)
    direct = _direct()
    call, _ = _drive({"status": "in_review"}, direct=direct, qbo_line=qbo_line)
    assert call.kwargs["sub_cost_code_id"] == 10
    assert call.kwargs["project_public_id"] is None
    assert call.kwargs["description"] == "Local memo"
    assert call.kwargs["quantity"] == qbo_line.qty
    assert call.kwargs["amount"] == qbo_line.amount
    assert call.kwargs["row_version"] == direct.row_version


def test_under_completed_qbo_values_are_written():
    qbo_line = _make_qbo_line(
        qbo_line_id="1", description="QBO memo", qty=3, amount=42,
        item_ref_value="ITEM-1", customer_ref_value="CUST-1",
    )
    call, _ = _drive({"status": "completed"}, direct=_direct(), qbo_line=qbo_line, scc=99, project="proj-qbo")
    assert call.kwargs["sub_cost_code_id"] == 99
    assert call.kwargs["project_public_id"] == "proj-qbo"
    assert call.kwargs["description"] == "QBO memo"
    assert call.kwargs["amount"] == 42


def test_preserved_coding_is_logged_with_ids_only(caplog):
    with caplog.at_level(logging.INFO):
        _drive({"status": "in_review"}, direct=_direct())
    hits = [r for r in caplog.records if "qbo.pull.local_coding_preserved_under_review" in r.getMessage()]
    assert len(hits) == 1
    msg = hits[0].getMessage()
    assert "bill_credit=19146" in msg and "line_id=55" in msg and "status=in_review" in msg
    assert "Local memo" not in msg


def test_approved_holding_uses_the_connectors_scc_service_and_the_late_credit():
    scc_service = MagicMock()
    scc_service.is_holding_code.return_value = True
    connector_recorder = MagicMock()
    with patch.object(VendorCreditLineItemConnector, "_record_holding_on_approved", connector_recorder), \
            patch.object(BillCreditService, "read_by_id") as reread:
        _drive({"status": "approved"}, direct=_direct(), scc_service=scc_service,
               qbo_line=_make_qbo_line(qbo_line_id="1", description="QBO memo", item_ref_value="ITEM-1"))
    scc_service.is_holding_code.assert_called_once_with(99)
    reread.assert_not_called()
    assert connector_recorder.call_args.args[0] is CREDIT


def test_sync_from_qbo_line_no_longer_takes_a_parent_status_argument():
    params = inspect.signature(VendorCreditLineItemConnector.sync_from_qbo_line).parameters
    assert "parent_status" not in params


def test_lifecycle_docstring_states_the_late_read_and_the_missing_fence():
    doc = VendorCreditLineItemConnector._parent_lifecycle.__doc__ or ""
    assert "after the line" in doc.lower()
    assert "fence" in doc.lower()


# ---- the pure rule (field_ownership) ----------------------------------------

LOCAL = SimpleNamespace(sub_cost_code_id=10, project_id=5, description="Local memo")


def test_approved_holding_keeps_local_scc_even_when_unset():
    scc, _, _, preserved = under_review_line_coding(
        local=SimpleNamespace(sub_cost_code_id=None, project_id=None, description=None),
        status="approved", resolved_holding=True,
        qbo_sub_cost_code_id=99, qbo_project_public_id=None, qbo_description="QBO",
    )
    assert scc is None and preserved is False


def test_holding_outside_approved_does_not_keep_the_holding_rule():
    scc, _, _, _ = under_review_line_coding(
        local=SimpleNamespace(sub_cost_code_id=None, project_id=None, description=None),
        status="in_review", resolved_holding=True,
        qbo_sub_cost_code_id=99, qbo_project_public_id=None, qbo_description=None,
    )
    assert scc == 99


def test_under_review_keeps_local_and_reports_preserved():
    scc, project, desc, preserved = under_review_line_coding(
        local=LOCAL, status="submitted",
        qbo_sub_cost_code_id=99, qbo_project_public_id="p", qbo_description="QBO",
    )
    assert (scc, project, desc, preserved) == (10, None, "Local memo", True)


def test_completed_takes_qbo_and_reports_not_preserved():
    assert under_review_line_coding(
        local=LOCAL, status="completed",
        qbo_sub_cost_code_id=99, qbo_project_public_id="p", qbo_description="QBO",
    ) == (99, "p", "QBO", False)


# ---- holding record takes the credit it was given ---------------------------

def test_holding_record_is_written_for_the_credit_with_its_qbo_id():
    obj = VendorCreditLineItemConnector.__new__(VendorCreditLineItemConnector)
    obj.reconciliation_repo = MagicMock(name="repo")
    credit = SimpleNamespace(id=7, qbo_id="QB-99")
    with patch("integrations.intuit.qbo.base.reconciliation_recorder.record_holding_code_on_approved_deduped") as rec:
        obj._record_holding_on_approved(credit, "bc-7", "realm-1", "details here")
    rec.assert_called_once_with(
        obj.reconciliation_repo, entity_type="BillCredit", entity_public_id="bc-7",
        qbo_id="QB-99", realm_id="realm-1", details="details here",
    )


def test_holding_record_does_not_reread_the_credit():
    obj = VendorCreditLineItemConnector.__new__(VendorCreditLineItemConnector)
    obj.reconciliation_repo = MagicMock(name="repo")
    with patch.object(BillCreditService, "read_by_id") as reread, \
            patch("integrations.intuit.qbo.base.reconciliation_recorder.record_holding_code_on_approved_deduped"):
        obj._record_holding_on_approved(CREDIT, "bc-7", "realm-1", "d")
    reread.assert_not_called()


# --- U-797c4b mutation pins (EM): M14 project, M09 preserved log, M11 holding gate ---

def test_under_review_the_qbo_project_is_not_written_when_qbo_names_one():
    qbo_line = _make_qbo_line(qbo_line_id="1", description="QBO memo", customer_ref_value="CUST-1")
    call, _ = _drive({"status": "in_review"}, direct=_direct(), qbo_line=qbo_line, project="proj-qbo")
    assert call.kwargs["project_public_id"] is None
    call, _ = _drive({"status": "completed"}, direct=_direct(), qbo_line=qbo_line, project="proj-qbo")
    assert call.kwargs["project_public_id"] == "proj-qbo"


def test_keeping_only_the_local_scc_still_logs_preserved(caplog):
    with caplog.at_level(logging.INFO):
        _drive({"status": "in_review"}, direct=_direct(scc=10, project_id=None, description="  "))
    assert any("local_coding_preserved_under_review" in r.getMessage() for r in caplog.records)


def test_in_review_with_a_qbo_holding_code_records_nothing():
    holding = MagicMock()
    holding.is_holding_code.return_value = True
    with patch.object(VendorCreditLineItemConnector, "_record_holding_on_approved") as rec:
        call, _ = _drive({"status": "in_review"}, direct=_direct(scc=None, project_id=None, description=None),
                         scc_service=holding,
                         qbo_line=_make_qbo_line(qbo_line_id="1", description="QBO memo", item_ref_value="ITEM-1"))
    rec.assert_not_called()
    assert call.kwargs["sub_cost_code_id"] == 99
