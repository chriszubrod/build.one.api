"""U-743: completion repoint carries customer divergence, HasBeenBilled is never overwritten, adopt gets
an operator legacy mode, an admin re-drive of one completed expense's repoint (dry-run by default), and
record-only coding drift for a completed expense that has no suggestion.

Repo, lock, connector, resolver and staging reads are faked or monkeypatched; nothing opens a
connection, calls QBO, or touches Azure."""
import json
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from entities.coding_suggestion.business.model import CodingSuggestion
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import PurchaseChangedInQboError
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from shared.api.admin import RECORD_REQUIRED_SINCE_UTC
from shared.lifecycle.completion_gate import HoldingCodeRefused

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
ADMIN = {"sub": "a", "is_system_admin": True}
AFTER = datetime(2026, 11, 8, 12, 0, 0)
BEFORE = datetime(2026, 11, 1, 12, 0, 0)
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
CONNECTOR = "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
SUB_COST = "entities.sub_cost_code.business.service.SubCostCodeService"
PURCHASE_CLIENT = "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient"
CS_REPO = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository"
CS_LOCK = "entities.coding_suggestion.business.service.coding_suggestion_lock"
CS_SERVICE = "entities.coding_suggestion.business.service.CodingSuggestionService"
LINES = "entities.expense_line_item.business.service.ExpenseLineItemService"
PROJECT = "entities.project.business.service.ProjectService"
RECON_REPO = "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository"
RECON_RECORD = "integrations.intuit.qbo.base.reconciliation_recorder.record_reviewed_coding_not_in_qbo_deduped"
DRIFT_FOR = "shared.completion.qbo_coding_drift.record_coding_drift_for"
URL_BASE = "/api/v1/admin"


@contextmanager
def _lock_held(*_a, **_k):
    yield True


# ============================================================================ divergence (expense service)

def _div_full():
    return SimpleNamespace(
        written_lines=[{"qbo_line_id": "10", "item_ref_value": "ITEM-A"}],
        written_sync_token="TOK-9",
        qbo_purchase_qbo_id="P1",
        realm_id="R1",
    )


def _div_local(project_id=7):
    return {"10": SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=project_id,
                                  description="d", amount=Decimal("5.00"))}


def _run_divergence(*, item="ITEM-A", staged_customer="CUST-OLD", new_customer="CUST-NEW", project_id=7):
    svc = ExpenseService()
    connector = MagicMock()
    connector._get_qbo_customer_ref.return_value = (
        SimpleNamespace(value=new_customer, name="n") if new_customer else None
    )
    staged = [SimpleNamespace(qbo_line_id="10", item_ref_value="ITEM-A", customer_ref_value=staged_customer)]
    with patch.object(ExpenseService, "_read_staged_purchase_lines", return_value=staged), \
            patch(CONNECTOR, return_value=connector):
        return svc._compute_repoint_divergence(
            "S1", _div_full(), _div_local(project_id), "R1",
            lambda scc, realm_id=None, sub_cost_code_service=None: SimpleNamespace(value=item),
        )


def test_divergence_same_item_different_staged_customer_is_customer_changed():
    out = _run_divergence(staged_customer="CUST-OLD", new_customer="CUST-NEW")
    assert out is not None
    assert out["targets"][0]["customer_changed"] is True
    assert out["targets"][0]["repoint_from_item_ref_value"] == "ITEM-A"


def test_divergence_same_item_same_customer_is_no_divergence():
    assert _run_divergence(staged_customer="CUST-NEW", new_customer="CUST-NEW") is None


def test_divergence_local_project_without_qbo_customer_is_not_a_customer_divergence():
    assert _run_divergence(staged_customer="CUST-OLD", new_customer=None) is None


def test_divergence_item_diverged_with_matching_customer_reports_customer_changed_false():
    svc = ExpenseService()
    connector = MagicMock()
    connector._get_qbo_customer_ref.return_value = SimpleNamespace(value="CUST-NEW", name="n")
    staged = [SimpleNamespace(qbo_line_id="10", item_ref_value="ITEM-A", customer_ref_value="CUST-NEW")]
    with patch.object(ExpenseService, "_read_staged_purchase_lines", return_value=staged), \
            patch(CONNECTOR, return_value=connector):
        out = svc._compute_repoint_divergence(
            "S1", _div_full(), _div_local(), "R1",
            lambda scc, realm_id=None, sub_cost_code_service=None: SimpleNamespace(value="ITEM-NEW"),
        )
    assert out is not None
    assert out["targets"][0].get("customer_changed", False) is False


def test_divergence_item_diverged_and_customer_diverged_reports_customer_changed_true():
    svc = ExpenseService()
    connector = MagicMock()
    connector._get_qbo_customer_ref.return_value = SimpleNamespace(value="CUST-NEW", name="n")
    staged = [SimpleNamespace(qbo_line_id="10", item_ref_value="ITEM-A", customer_ref_value="CUST-OLD")]
    with patch.object(ExpenseService, "_read_staged_purchase_lines", return_value=staged), \
            patch(CONNECTOR, return_value=connector):
        out = svc._compute_repoint_divergence(
            "S1", _div_full(), _div_local(), "R1",
            lambda scc, realm_id=None, sub_cost_code_service=None: SimpleNamespace(value="ITEM-NEW"),
        )
    assert out["targets"][0]["customer_changed"] is True


# ============================================================================ model

def _suggestion_with_targets(targets):
    return SimpleNamespace(repoint_targets_json=json.dumps(targets))


def _target(**extra):
    base = {"qbo_line_id": "10", "repoint_from_item_ref_value": "ITEM-A", "sub_cost_code_id": 101,
            "project_id": 7, "description": "d"}
    base.update(extra)
    return base


def test_model_customer_changed_non_bool_fails_closed():
    rec = _suggestion_with_targets([_target(customer_changed="yes")])
    assert CodingSuggestion.repoint_targets.fget(rec) is None


def test_model_customer_changed_absent_is_accepted():
    rec = _suggestion_with_targets([_target()])
    assert CodingSuggestion.repoint_targets.fget(rec) == [_target()]


def test_model_customer_changed_true_is_accepted():
    rec = _suggestion_with_targets([_target(customer_changed=True)])
    assert CodingSuggestion.repoint_targets.fget(rec) == [_target(customer_changed=True)]


# ============================================================================ drain (worker)

def _worker():
    return QboOutboxWorker.__new__(QboOutboxWorker)


def _drain_item(targets):
    return SimpleNamespace(
        repoint_targets=targets,
        written_lines=[{"qbo_line_id": "10", "item_ref_value": "ITEM-A"}],
        confirmed_project_id=None,
    )


def _drain_resolve(item, resolved_item="ITEM-A"):
    worker = _worker()
    local = SimpleNamespace(quantity=None, rate=None, amount=Decimal("5.00"), is_billable=False,
                            billable_decided_at=datetime(2026, 11, 8))
    row = SimpleNamespace(entity_public_id="S1")
    with patch.object(worker, "_resolve_recode_line_economics", return_value=(local, None)), \
            patch(RESOLVER, return_value=SimpleNamespace(value=resolved_item)):
        return worker._resolve_repoint_targets(row, item, "R1")


def test_drain_item_equal_target_without_customer_change_is_skipped():
    item = _drain_item([_target()])
    targets, err = _drain_resolve(item)
    assert err is None
    assert targets == []


def test_drain_item_equal_target_with_customer_changed_is_not_skipped():
    item = _drain_item([_target(customer_changed=True)])
    targets, err = _drain_resolve(item)
    assert err is None
    assert len(targets) == 1
    assert targets[0]["target_qbo_line_id"] == "10"
    assert targets[0]["repoint_from_item_ref_value"] == "ITEM-A"
    assert targets[0]["customer_changed"] is True


def test_drain_item_diverged_target_still_queued_without_customer_flag():
    item = _drain_item([_target()])
    targets, err = _drain_resolve(item, resolved_item="ITEM-NEW")
    assert err is None
    assert len(targets) == 1
    assert targets[0]["customer_changed"] is False


# ============================================================================ writer (connector)

def _conn():
    return PurchaseExpenseConnector.__new__(PurchaseExpenseConnector)


def _raw(*, token="5", item="ITEM-A", customer="CUST-OLD"):
    return {
        "Id": "P1",
        "SyncToken": token,
        "Line": [{
            "Id": "10",
            "DetailType": "ItemBasedExpenseLineDetail",
            "Amount": 5,
            "ItemBasedExpenseLineDetail": {"ItemRef": {"value": item}, "CustomerRef": {"value": customer}},
        }],
    }


def _spec(**extra):
    base = {"target_qbo_line_id": "10", "sub_cost_code_id": 101, "project_id": 7, "description": "d",
            "quantity": None, "rate": None, "amount": None, "is_billable": None,
            "repoint_from_item_ref_value": "ITEM-A"}
    base.update(extra)
    return base


def _recode(raw, *, expected="5", spec=None, customer_value="CUST-NEW", item_value="ITEM-A"):
    conn = _conn()
    client = MagicMock()
    client.get_purchase_raw.return_value = raw
    client.update_purchase_raw.return_value = {"SyncToken": "6"}
    with patch(PURCHASE_CLIENT) as cls:
        cls.return_value.__enter__.return_value = client
        with patch.object(conn, "_get_qbo_item_ref", return_value=SimpleNamespace(value=item_value, name="i")), \
                patch.object(conn, "_get_qbo_customer_ref",
                             return_value=(SimpleNamespace(value=customer_value, name="c")
                                           if customer_value else None)):
            out = conn.recode_purchase_lines(
                realm_id="R1", qbo_purchase_qbo_id="P1", lines=[spec or _spec()], expected_sync_token=expected,
            )
    return out, client


def test_writer_repoint_item_equal_customer_differs_mutates_customer():
    out, client = _recode(_raw(customer="CUST-OLD"), spec=_spec(customer_changed=True))
    assert out["status"] == "written"
    client.update_purchase_raw.assert_called_once()
    sent = client.update_purchase_raw.call_args[0][0]
    assert sent["Line"][0]["ItemBasedExpenseLineDetail"]["CustomerRef"]["value"] == "CUST-NEW"


def test_writer_repoint_token_mismatch_still_fenced_when_customer_differs():
    conn = _conn()
    client = MagicMock()
    client.get_purchase_raw.return_value = _raw(token="5", customer="CUST-OLD")
    with patch(PURCHASE_CLIENT) as cls:
        cls.return_value.__enter__.return_value = client
        with patch.object(conn, "_get_qbo_item_ref", return_value=SimpleNamespace(value="ITEM-A", name="i")), \
                patch.object(conn, "_get_qbo_customer_ref", return_value=SimpleNamespace(value="CUST-NEW", name="c")):
            with pytest.raises(PurchaseChangedInQboError):
                conn.recode_purchase_lines(
                    realm_id="R1", qbo_purchase_qbo_id="P1", lines=[_spec(customer_changed=True)],
                    expected_sync_token="4",
                )
    client.update_purchase_raw.assert_not_called()


def test_writer_repoint_item_equal_customer_equal_is_verified_no_write():
    out, client = _recode(_raw(customer="CUST-NEW"), spec=_spec(customer_changed=True))
    assert out["status"] == "already_recoded"
    assert len(out["verified_lines"]) == 1
    client.update_purchase_raw.assert_not_called()


def test_writer_outside_repoint_mode_item_equal_customer_differs_is_unchanged():
    spec = _spec()
    spec.pop("repoint_from_item_ref_value")
    out, client = _recode(_raw(customer="CUST-OLD"), spec=spec, expected="5")
    assert out["status"] == "already_recoded"
    assert "verified_lines" not in out
    client.update_purchase_raw.assert_not_called()


# ============================================================================ never overwrite HasBeenBilled

def _apply(target, *, is_billable, customer_ref=None):
    _conn()._apply_recode_mutation_to_raw_line(
        target,
        item_ref=SimpleNamespace(value="ITEM-A", name="i"),
        description=None, quantity=None, rate=None, amount=None,
        is_billable=is_billable, realm_id="R1", customer_ref=customer_ref,
    )
    return target["ItemBasedExpenseLineDetail"]["BillableStatus"]


def _billed_target(status):
    return {"Id": "10", "DetailType": "ItemBasedExpenseLineDetail",
            "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "ITEM-OLD"}, "BillableStatus": status}}


def test_has_been_billed_kept_when_is_billable_false():
    assert _apply(_billed_target("HasBeenBilled"), is_billable=False) == "HasBeenBilled"


def test_has_been_billed_kept_when_is_billable_true_with_customer():
    assert _apply(_billed_target("HasBeenBilled"), is_billable=True,
                  customer_ref=SimpleNamespace(value="C", name="c")) == "HasBeenBilled"


def test_billable_line_still_becomes_not_billable_when_is_billable_false():
    assert _apply(_billed_target("Billable"), is_billable=False) == "NotBillable"


# ============================================================================ adopt legacy mode

def _adopt_item(**over):
    base = dict(
        public_id=PUBLIC_ID, status="written", confirmation_source="review_submit", confirmation_seq=4,
        written_at=AFTER, written_sync_token=None, written_lines_json=None, expense_id=77,
        qbo_purchase_qbo_id="purchase-1", realm_id="realm-1", sync_token_at_suggest="SUGGEST-5",
        confirmed_sub_cost_code_id=101, confirmed_project_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _adopt_live_line(line_id, item="item-101"):
    return {"Id": line_id, "DetailType": "ItemBasedExpenseLineDetail",
            "ItemBasedExpenseLineDetail": {"ItemRef": {"value": item}}}


def _adopt(item, *, locals_=(), live=(), resolver=None, allow_legacy=False, record_since=RECORD_REQUIRED_SINCE_UTC, apply=None):
    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=item)
    svc.repo = MagicMock()
    svc.repo.adopt_written_record.return_value = SimpleNamespace(public_id=PUBLIC_ID)
    connector = MagicMock()
    connector.get_purchase_raw.return_value = {"Id": "purchase-1", "SyncToken": "LIVE-7", "Line": list(live)}
    connector._get_qbo_customer_ref.return_value = None
    line_svc = MagicMock()
    line_svc.return_value.read_by_expense_id.return_value = list(locals_)
    res = resolver or (lambda scc, realm: SimpleNamespace(value=f"item-{scc}"))
    with patch("integrations.intuit.qbo.base.client.recode_write_gate_reason", return_value=None), \
            patch(CONNECTOR, return_value=connector), patch(LINES, line_svc), patch(RESOLVER, side_effect=res):
        out = svc.adopt_written_record(PUBLIC_ID, record_required_since=record_since, allow_legacy=allow_legacy,
                                       **({"apply": apply} if apply is not None else {}))
    return out, svc


def _holding_resolver(scc, realm):
    if scc == 101:
        raise HoldingCodeRefused("holding")
    return SimpleNamespace(value=f"item-{scc}")


def test_adopt_legacy_row_refused_without_allow_legacy():
    out, svc = _adopt(_adopt_item(written_at=BEFORE), locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                      live=[_adopt_live_line("10")])
    assert out["status"] == "legacy"
    svc.repo.adopt_written_record.assert_not_called()


def test_adopt_legacy_row_adopted_with_allow_legacy():
    out, svc = _adopt(_adopt_item(written_at=BEFORE),
                      locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                      live=[_adopt_live_line("10")], allow_legacy=True, apply=True)
    assert out["status"] == "adopted"
    svc.repo.adopt_written_record.assert_called_once()


def test_adopt_holding_confirmed_code_refused_without_allow_legacy():
    out, svc = _adopt(_adopt_item(), locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                      live=[_adopt_live_line("10", item="item-holding")], resolver=_holding_resolver)
    assert out["status"] == "line_holding"
    svc.repo.adopt_written_record.assert_not_called()


def test_adopt_holding_confirmed_code_adopted_with_allow_legacy_records_holding_qbo_id():
    sub = MagicMock()
    sub.return_value.read_by_id.return_value = SimpleNamespace(id=101, qbo_id="item-holding", realm_id="realm-1")
    with patch(SUB_COST, sub):
        out, svc = _adopt(_adopt_item(), locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                          live=[_adopt_live_line("10", item="item-holding")], resolver=_holding_resolver,
                          allow_legacy=True, apply=True)
    assert out["status"] == "adopted"
    record = json.loads(svc.repo.adopt_written_record.call_args[0][2])
    assert record == [{"qbo_line_id": "10", "item_ref_value": "item-holding", "sub_cost_code_id": 101}]


@pytest.mark.parametrize("item_over, live, expected", [
    ({"written_lines_json": "[]"}, [_adopt_live_line("10")], "has_record"),
    ({"status": "adopted"}, [_adopt_live_line("10")], "not_written"),
    ({"confirmation_seq": None}, [_adopt_live_line("10")], "moved"),
    ({}, [_adopt_live_line("10", item="other-item")], "line_not_recoded"),
])
def test_adopt_allow_legacy_does_not_waive_other_refusals(item_over, live, expected):
    locals_ = [SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)]
    out, svc = _adopt(_adopt_item(written_at=BEFORE, **item_over), locals_=locals_, live=live, allow_legacy=True)
    assert out["status"] == expected
    svc.repo.adopt_written_record.assert_not_called()


# ============================================================================ admin re-drive (service)

def _expense(**over):
    base = dict(id=77, public_id=PUBLIC_ID, status="completed", qbo_id="P1", realm_id="R1")
    base.update(over)
    return SimpleNamespace(**base)


def _redrive_svc(expense, *, staged_billable="HasBeenBilled", candidate_status="written"):
    svc = ExpenseService()
    svc.read_by_public_id = MagicMock(return_value=expense)
    svc._repoint_on_complete = MagicMock(return_value={"success": True, "enqueued": 1, "lines": ["10"]})
    svc._read_staged_purchase_lines = MagicMock(return_value=[
        SimpleNamespace(qbo_line_id="10", item_ref_value="ITEM-A", customer_ref_value="C",
                        billable_status=staged_billable),
    ])
    svc._repoint_revision_for_candidate = MagicMock(return_value={
        "public_id": "S1", "lines": ["10"], "targets_token": "TOK", "revision_seq": 4,
        "confirmed_mixed_codes": False,
        "dominant": {"project_id": 7, "sub_cost_code_id": 101, "description": "d"},
        "targets": [{"qbo_line_id": "10", "sub_cost_code_id": 101, "project_id": 7, "description": "d",
                     "repoint_from_item_ref_value": "ITEM-A", "customer_changed": False}],
    })
    return svc


def _redrive_patches(candidate_status="written"):
    cs_repo = MagicMock()
    cs_repo.read_state_by_expense_ids.return_value = {77: [{"public_id": "S1", "status": candidate_status}]}
    cs_repo.read_by_public_id.return_value = SimpleNamespace(
        confirmed_project_id=None, confirmed_sub_cost_code_id=101, written_lines=[], status="written")
    lines = MagicMock()
    lines.return_value.read_by_expense_id.return_value = [
        SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=7, billable_decided_at=None,
                        is_billable=False, description="d")]
    cs_service = MagicMock()
    return [
        patch(GATE, return_value=None),
        patch(CS_REPO, return_value=cs_repo),
        patch(CS_LOCK, _lock_held),
        patch(CS_SERVICE, cs_service),
        patch(LINES, lines),
        patch.object(QboOutboxWorker, "_recode_is_billable", return_value=True),
    ], cs_service


def _run_redrive(svc, *, apply=False, candidate_status="written"):
    patches, cs_service = _redrive_patches(candidate_status)
    for p in patches:
        p.start()
    try:
        out = svc.redrive_completion_repoint(PUBLIC_ID, apply=apply)
    finally:
        for p in patches:
            p.stop()
    return out, cs_service


def test_redrive_not_found():
    svc = _redrive_svc(None)
    out, _ = _run_redrive(svc)
    assert out["status"] == "not_found"


def test_redrive_not_completed_refused():
    out, _ = _run_redrive(_redrive_svc(_expense(status="approved")))
    assert out["status"] == "not_completed"


def test_redrive_not_qbo_linked_refused():
    out, _ = _run_redrive(_redrive_svc(_expense(qbo_id=None)))
    assert out["status"] == "not_qbo_linked"


def test_redrive_writes_disabled_refused():
    svc = _redrive_svc(_expense())
    with patch(GATE, return_value="qbo_writes_disabled"):
        out = svc.redrive_completion_repoint(PUBLIC_ID)
    assert out["status"] == "writes_disabled"
    assert out["reason"] == "qbo_writes_disabled"


def test_redrive_dry_run_never_confirms_or_enqueues_and_reports_billable_state():
    svc = _redrive_svc(_expense())
    out, cs_service = _run_redrive(svc, apply=False)
    assert out["status"] == "dry_run"
    cand = out["candidates"][0]
    assert cand["public_id"] == "S1"
    target = cand["targets"][0]
    assert target["staged_billable_status"] == "HasBeenBilled"
    assert target["has_been_billed"] is True
    assert target["drain_is_billable"] is True
    cs_service.return_value.confirm.assert_not_called()
    svc._repoint_on_complete.assert_not_called()


def test_redrive_dry_run_reports_skip_reason_for_a_refused_candidate():
    svc = _redrive_svc(_expense())
    svc._repoint_revision_for_candidate = MagicMock(
        return_value={"public_id": "S1", "skip_reason": "repoint_line_unmapped"})
    out, _ = _run_redrive(svc, apply=False)
    assert out["candidates"][0] == {"public_id": "S1", "skip_reason": "repoint_line_unmapped"}


def test_redrive_apply_delegates_to_repoint_on_complete():
    svc = _redrive_svc(_expense())
    out, cs_service = _run_redrive(svc, apply=True)
    svc._repoint_on_complete.assert_called_once()
    kwargs = svc._repoint_on_complete.call_args.kwargs
    assert kwargs["expense_id"] == 77
    assert out["status"] == "enqueued"
    assert out["enqueued"] == 1


# ============================================================================ admin re-drive (router)

@pytest.fixture
def client():
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


def _as(user):
    app.dependency_overrides[get_current_user_api] = lambda: user


def test_router_redrive_non_admin_is_403(client):
    _as({"sub": "u", "is_system_admin": False})
    with patch.object(ExpenseService, "redrive_completion_repoint") as redrive:
        r = client.post(f"{URL_BASE}/expenses/{PUBLIC_ID}/redrive-completion-repoint")
    assert r.status_code == 403
    redrive.assert_not_called()


def test_router_redrive_non_uuid_is_422(client):
    _as(ADMIN)
    with patch.object(ExpenseService, "redrive_completion_repoint") as redrive:
        r = client.post(f"{URL_BASE}/expenses/nope/redrive-completion-repoint")
    assert r.status_code == 422
    redrive.assert_not_called()


def test_router_redrive_dry_run_is_200_and_apply_false_passed(client):
    _as(ADMIN)
    with patch.object(ExpenseService, "redrive_completion_repoint",
                      return_value={"status": "dry_run", "candidates": []}) as redrive:
        r = client.post(f"{URL_BASE}/expenses/{PUBLIC_ID}/redrive-completion-repoint?apply=false")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "dry_run"
    assert redrive.call_args.kwargs["apply"] is False


def test_router_redrive_refusal_is_409_and_not_found_is_404(client):
    _as(ADMIN)
    with patch.object(ExpenseService, "redrive_completion_repoint", return_value={"status": "not_completed"}):
        assert client.post(f"{URL_BASE}/expenses/{PUBLIC_ID}/redrive-completion-repoint").status_code == 409
    with patch.object(ExpenseService, "redrive_completion_repoint", return_value={"status": "not_found"}):
        assert client.post(f"{URL_BASE}/expenses/{PUBLIC_ID}/redrive-completion-repoint").status_code == 404


def test_router_adopt_passes_allow_legacy_through(client):
    _as(ADMIN)
    url = f"{URL_BASE}/expense-coding/suggestions/{PUBLIC_ID}/adopt-written-record?allow_legacy=true"
    with patch.object(CodingSuggestionService, "adopt_written_record",
                      return_value={"status": "adopted", "lines": 1, "written_sync_token": "T"}) as adopt:
        r = client.post(url)
    assert r.status_code == 200
    assert adopt.call_args.kwargs["allow_legacy"] is True


# ============================================================================ drift for completed expense

def _drift_expense(**over):
    base = dict(id=77, public_id=PUBLIC_ID, qbo_id="P1", realm_id="R1", status="completed")
    base.update(over)
    return SimpleNamespace(**base)


def _drift_lines():
    return [SimpleNamespace(id=1, qbo_id="10", sub_cost_code_id=101, project_id=None)]


def test_expense_drift_recorded_when_no_suggestion_and_item_diverged():
    svc = ExpenseService()
    svc._read_staged_purchase_lines = MagicMock(return_value=[
        SimpleNamespace(qbo_line_id="10", item_ref_value="ITEM-OLD", customer_ref_value=None)])
    cs_repo = MagicMock()
    cs_repo.read_state_by_expense_ids.return_value = {}
    sub = MagicMock()
    sub.return_value.read_by_id.return_value = SimpleNamespace(id=101, qbo_id="ITEM-NEW")
    sub.return_value.is_holding_code.return_value = False
    with patch(CS_REPO, return_value=cs_repo), patch(SUB_COST, sub), patch(PROJECT, MagicMock()), \
            patch(RECON_REPO), patch(RECON_RECORD) as record:
        svc._record_qbo_coding_drift(expense=_drift_expense(), line_items=_drift_lines())
    record.assert_called_once()
    assert "expense" in record.call_args.kwargs["details"]


def test_expense_drift_not_recorded_when_suggestion_exists():
    svc = ExpenseService()
    svc._read_staged_purchase_lines = MagicMock(return_value=[])
    cs_repo = MagicMock()
    cs_repo.read_state_by_expense_ids.return_value = {77: [{"public_id": "S1", "status": "written"}]}
    with patch(CS_REPO, return_value=cs_repo), patch(DRIFT_FOR) as drift:
        svc._record_qbo_coding_drift(expense=_drift_expense(), line_items=_drift_lines())
    drift.assert_not_called()


def test_expense_drift_not_checked_when_not_qbo_linked():
    svc = ExpenseService()
    cs_repo = MagicMock()
    cs_repo.read_state_by_expense_ids.return_value = {}
    with patch(CS_REPO, return_value=cs_repo), patch(DRIFT_FOR) as drift:
        svc._record_qbo_coding_drift(expense=_drift_expense(qbo_id=None), line_items=_drift_lines())
    drift.assert_not_called()


def test_expense_drift_recorder_raising_never_propagates():
    svc = ExpenseService()
    cs_repo = MagicMock()
    cs_repo.read_state_by_expense_ids.return_value = {}
    with patch(CS_REPO, return_value=cs_repo), patch(DRIFT_FOR, side_effect=RuntimeError("boom")):
        svc._record_qbo_coding_drift(expense=_drift_expense(), line_items=_drift_lines())


# ============================================================================ fix round 1 (EM decision)
# The writer mutates an item-equal line for a customer difference ONLY when completion approved that customer
# change (spec customer_changed is True). Without the flag, case (a) is verified, no write, as on base.

def test_writer_repoint_customer_differs_without_customer_changed_flag_is_verified_no_write():
    out, client = _recode(_raw(customer="CUST-OLD"), spec=_spec())
    assert out["status"] == "already_recoded"
    assert len(out["verified_lines"]) == 1
    client.update_purchase_raw.assert_not_called()


def test_writer_repoint_customer_changed_false_is_verified_no_write():
    out, client = _recode(_raw(customer="CUST-OLD"), spec=_spec(customer_changed=False))
    assert out["status"] == "already_recoded"
    assert len(out["verified_lines"]) == 1
    client.update_purchase_raw.assert_not_called()


# ============================================================================ completion isolates drift

def test_complete_expense_calls_drift_recorder_and_succeeds_when_it_raises():
    from entities.expense.business.service import ExpenseService

    line = SimpleNamespace(id=1, qbo_id="10", sub_cost_code_id=101, project_id=None, description="d",
                           amount=Decimal("5.00"), quantity=None, rate=None, is_billable=False, is_draft=False, public_id="line-1")
    expense = SimpleNamespace(id=55, public_id="expense-55", status="draft", is_draft=True, vendor_id=9,
                              qbo_id="P1", realm_id="R1")
    svc = ExpenseService(repo=MagicMock())
    svc.repo.finalize_by_id.return_value = expense
    svc.read_by_public_id = MagicMock(return_value=expense)
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(id=9, public_id="vendor-9")
    eli_svc = MagicMock()
    eli_svc.read_by_expense_id.return_value = [line]
    svc._expense_line_item_service = eli_svc
    svc._enqueue_receipts_copy = MagicMock(return_value={"success": True, "queued": True, "errors": []})
    svc._enqueue_box_uploads = MagicMock()
    svc._assert_no_holding_code_on_complete = MagicMock()
    svc._enqueue_coding_recode_on_complete = MagicMock(return_value={"success": True, "errors": []})
    svc._record_qbo_coding_drift = MagicMock(side_effect=RuntimeError("boom"))

    result = svc.complete_expense("expense-55")

    svc._record_qbo_coding_drift.assert_called_once()
    assert result.get("status_code") != 500
    assert result["expense_finalized"] is True


# ============================================================================ fix round 2

def _div_billed(*, billable="HasBeenBilled", staged_customer="CUST-OLD", new_customer="CUST-NEW", item="ITEM-A",
                staged_read=None):
    svc = ExpenseService()
    connector = MagicMock()
    connector._get_qbo_customer_ref.return_value = (
        SimpleNamespace(value=new_customer, name="n") if new_customer else None
    )
    staged = [SimpleNamespace(qbo_line_id="10", item_ref_value="ITEM-A", customer_ref_value=staged_customer,
                              billable_status=billable)]
    read = MagicMock(return_value=staged) if staged_read is None else staged_read
    with patch.object(ExpenseService, "_read_staged_purchase_lines", read), patch(CONNECTOR, return_value=connector):
        return svc._compute_repoint_divergence(
            "S1", _div_full(), _div_local(), "R1",
            lambda scc, realm_id=None, sub_cost_code_service=None: SimpleNamespace(value=item),
        )


# ---- item 1: a skip envelope from _repoint_on_complete is "skipped", never "enqueued"

def test_redrive_apply_skipped_envelope_is_skipped_not_enqueued():
    envelope = {"success": True, "message": "Repoint not enqueued", "enqueued": 0, "skipped": True,
                "skip_reason": "busy", "skipped_candidates": [{"public_id": "S1", "skip_reason": "busy"}],
                "errors": []}
    svc = _redrive_svc(_expense())
    svc._repoint_on_complete = MagicMock(return_value=envelope)
    out, _ = _run_redrive(svc, apply=True)
    assert out["status"] == "skipped"
    assert out["skip_reason"] == "busy"


def test_router_redrive_apply_skipped_is_409_with_detail_object(client):
    _as(ADMIN)
    body = {"status": "skipped", "skip_reason": "busy", "skipped_candidates": [{"public_id": "S1", "skip_reason": "busy"}]}
    with patch.object(ExpenseService, "redrive_completion_repoint", return_value=body):
        r = client.post(f"{URL_BASE}/expenses/{PUBLIC_ID}/redrive-completion-repoint?apply=true")
    assert r.status_code == 409
    assert r.json()["detail"]["status"] == "skipped"
    assert r.json()["detail"]["skip_reason"] == "busy"


# ---- item 2a: no customer-only divergence on a billed line; item divergence still emits

def test_divergence_customer_only_on_billed_line_is_not_a_divergence(caplog):
    with caplog.at_level("WARNING"):
        out = _div_billed(billable="HasBeenBilled", staged_customer="CUST-OLD", new_customer="CUST-NEW", item="ITEM-A")
    assert out is None
    assert "expense.complete.repoint_customer_on_billed_line" in caplog.text


def test_divergence_item_diverged_on_billed_line_emits_without_customer_changed():
    out = _div_billed(billable="HasBeenBilled", staged_customer="CUST-OLD", new_customer="CUST-NEW", item="ITEM-NEW")
    assert out is not None
    assert "customer_changed" not in out["targets"][0]


# ---- item 2b: writer backstop: customer_changed on a HasBeenBilled live line raises, no write

def test_writer_customer_changed_on_billed_live_line_raises_repoint_line_billed():
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointLineBilledError

    raw = _raw(customer="CUST-OLD")
    raw["Line"][0]["ItemBasedExpenseLineDetail"]["BillableStatus"] = "HasBeenBilled"
    conn = _conn()
    client = MagicMock()
    client.get_purchase_raw.return_value = raw
    with patch(PURCHASE_CLIENT) as cls:
        cls.return_value.__enter__.return_value = client
        with patch.object(conn, "_get_qbo_item_ref", return_value=SimpleNamespace(value="ITEM-A", name="i")), \
                patch.object(conn, "_get_qbo_customer_ref", return_value=SimpleNamespace(value="CUST-NEW", name="c")):
            with pytest.raises(RepointLineBilledError):
                conn.recode_purchase_lines(realm_id="R1", qbo_purchase_qbo_id="P1",
                                           lines=[_spec(customer_changed=True)], expected_sync_token="5")
    client.update_purchase_raw.assert_not_called()


def test_worker_marks_repoint_line_billed_error_with_that_reason():
    from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointLineBilledError

    worker = _worker()
    item = SimpleNamespace(status="enqueued", confirmation_seq=4, qbo_purchase_qbo_id="P1",
                           sync_token_at_suggest="5", confirmation_source=REPOINT_CONFIRMATION_SOURCE,
                           written_lines=[], written_sync_token="5")
    svc = MagicMock()
    svc.read_by_public_id.return_value = item
    svc.mark_error.return_value = SimpleNamespace(public_id="S1")
    conn = MagicMock()
    conn.recode_purchase_lines.side_effect = RepointLineBilledError(qbo_line_id="10")
    row = SimpleNamespace(entity_public_id="S1", realm_id="R1")
    with patch("entities.coding_suggestion.business.service.CodingSuggestionService", return_value=svc), \
            patch(CONNECTOR, return_value=conn), \
            patch.object(worker, "_resolve_recode_purchase_line_targets",
                         return_value=([_spec(customer_changed=True)], None)):
        worker._drain_recode_purchase_line_locked(row)
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_line_billed"


# ---- item 3: holding adopt requires the holding SubCostCode's realm to match the purchase's realm

def _holding_adopt_with_sub(sub_realm, item_realm="realm-1"):
    sub = MagicMock()
    sub.return_value.read_by_id.return_value = SimpleNamespace(id=101, qbo_id="item-holding", realm_id=sub_realm)
    with patch(SUB_COST, sub):
        return _adopt(_adopt_item(realm_id=item_realm),
                      locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                      live=[_adopt_live_line("10", item="item-holding")], resolver=_holding_resolver,
                      allow_legacy=True)


def test_adopt_holding_realm_mismatch_is_line_unmapped():
    out, svc = _holding_adopt_with_sub(sub_realm="realm-OTHER")
    assert out["status"] == "line_unmapped"
    svc.repo.adopt_written_record.assert_not_called()


def test_adopt_holding_null_realm_is_line_unmapped():
    out, svc = _holding_adopt_with_sub(sub_realm=None)
    assert out["status"] == "line_unmapped"
    svc.repo.adopt_written_record.assert_not_called()


# ---- item 4: narrow allow_legacy: holding only in the confirmed branch; legacy_multi_line; apply/dry-run

def test_adopt_holding_not_matched_in_multi_line_mode_even_with_allow_legacy():
    sub = MagicMock()
    sub.return_value.read_by_id.return_value = SimpleNamespace(id=101, qbo_id="item-holding", realm_id="realm-1")
    item = _adopt_item(confirmed_sub_cost_code_id=None, confirmation_source="review_submit")
    with patch(SUB_COST, sub):
        out, svc = _adopt(item, locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                          live=[_adopt_live_line("10", item="item-holding")], resolver=_holding_resolver,
                          allow_legacy=True)
    # round 3: every multi-line path under allow_legacy is refused before the holding check
    assert out["status"] == "legacy_multi_line"
    svc.repo.adopt_written_record.assert_not_called()


def _two_line_multi(**item_over):
    locals_ = [SimpleNamespace(qbo_id="10", sub_cost_code_id=102, project_id=None),
               SimpleNamespace(qbo_id="11", sub_cost_code_id=102, project_id=None)]
    live = [_adopt_live_line("10", item="item-102"), _adopt_live_line("11", item="item-102")]
    return _adopt(_adopt_item(**item_over), locals_=locals_, live=live, allow_legacy=True)


def test_adopt_legacy_multi_line_refused_when_confirmed_branch_has_no_match():
    out, svc = _two_line_multi()  # confirmed 101 matches no live line; a 2-line review_submit would fall through
    assert out["status"] == "legacy_multi_line"
    svc.repo.adopt_written_record.assert_not_called()


def test_adopt_multi_line_fallthrough_still_adopts_without_allow_legacy():
    locals_ = [SimpleNamespace(qbo_id="10", sub_cost_code_id=102, project_id=None),
               SimpleNamespace(qbo_id="11", sub_cost_code_id=102, project_id=None)]
    live = [_adopt_live_line("10", item="item-102"), _adopt_live_line("11", item="item-102")]
    out, svc = _adopt(_adopt_item(), locals_=locals_, live=live)  # allow_legacy False: today's behaviour
    assert out["status"] == "adopted"
    assert out["qbo_line_ids"] == ["10", "11"]


def test_adopt_legacy_dry_run_persists_nothing_and_lists_lines():
    out, svc = _adopt(_adopt_item(), locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                      live=[_adopt_live_line("10")], allow_legacy=True)  # apply defaults False
    assert out["status"] == "dry_run"
    assert out["qbo_line_ids"] == ["10"]
    assert out["written_sync_token"] == "LIVE-7"
    svc.repo.adopt_written_record.assert_not_called()


def test_adopt_legacy_apply_persists_and_carries_qbo_line_ids():
    out, svc = _adopt(_adopt_item(), locals_=[SimpleNamespace(qbo_id="10", sub_cost_code_id=101, project_id=None)],
                      live=[_adopt_live_line("10")], allow_legacy=True, apply=True)
    assert out["status"] == "adopted"
    assert out["qbo_line_ids"] == ["10"]
    svc.repo.adopt_written_record.assert_called_once()


def test_router_adopt_apply_passed_through_with_allow_legacy(client):
    _as(ADMIN)
    url = f"{URL_BASE}/expense-coding/suggestions/{PUBLIC_ID}/adopt-written-record?allow_legacy=true&apply=true"
    with patch.object(CodingSuggestionService, "adopt_written_record",
                      return_value={"status": "adopted", "lines": 1, "written_sync_token": "T", "qbo_line_ids": ["10"]}) as adopt:
        r = client.post(url)
    assert r.status_code == 200
    assert adopt.call_args.kwargs["apply"] is True


def test_router_adopt_dry_run_is_200(client):
    _as(ADMIN)
    url = f"{URL_BASE}/expense-coding/suggestions/{PUBLIC_ID}/adopt-written-record?allow_legacy=true"
    body = {"status": "dry_run", "qbo_line_ids": ["10"], "written_sync_token": "T"}
    with patch.object(CodingSuggestionService, "adopt_written_record", return_value=body):
        r = client.post(url)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "dry_run"


# ---- item 5: a failing staged read or customer lookup never fails the candidate

def test_divergence_staged_read_failure_still_yields_item_target(caplog):
    with caplog.at_level("WARNING"):
        out = _div_billed(item="ITEM-NEW", staged_read=MagicMock(side_effect=RuntimeError("db down")))
    assert out is not None
    assert out["targets"][0]["qbo_line_id"] == "10"
    assert out["targets"][0]["repoint_from_item_ref_value"] == "ITEM-A"
    assert "expense.complete.repoint_customer_check_unavailable" in caplog.text


# ============================================================================ fix round 3

def _billed_raw(item="ITEM-OLD", customer="CUST-OLD"):
    raw = _raw(item=item, customer=customer)
    raw["Line"][0]["ItemBasedExpenseLineDetail"]["BillableStatus"] = "HasBeenBilled"
    return raw


# ---- item 1: a billed line never gets a new customer

def test_apply_recode_keeps_old_customer_on_billed_line():
    target = {"Id": "10", "DetailType": "ItemBasedExpenseLineDetail",
              "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "ITEM-OLD"}, "CustomerRef": {"value": "CUST-OLD"},
                                             "BillableStatus": "HasBeenBilled"}}
    _conn()._apply_recode_mutation_to_raw_line(
        target, item_ref=SimpleNamespace(value="ITEM-NEW", name="i"), description=None, quantity=None,
        rate=None, amount=None, is_billable=True, realm_id="R1",
        customer_ref=SimpleNamespace(value="CUST-NEW", name="c"),
    )
    detail = target["ItemBasedExpenseLineDetail"]
    assert detail["ItemRef"]["value"] == "ITEM-NEW"
    assert detail["CustomerRef"]["value"] == "CUST-OLD"
    assert detail["BillableStatus"] == "HasBeenBilled"


def test_writer_item_repoint_on_billed_line_keeps_old_customer():
    out, client = _recode(_billed_raw(item="ITEM-OLD", customer="CUST-OLD"),
                          spec=_spec(repoint_from_item_ref_value="ITEM-OLD"),
                          customer_value="CUST-NEW", item_value="ITEM-NEW", expected="5")
    assert out["status"] == "written"
    sent = client.update_purchase_raw.call_args[0][0]["Line"][0]["ItemBasedExpenseLineDetail"]
    assert sent["ItemRef"]["value"] == "ITEM-NEW"
    assert sent["CustomerRef"]["value"] == "CUST-OLD"
    assert sent["BillableStatus"] == "HasBeenBilled"


def test_writer_customer_changed_on_billed_line_with_same_customer_is_verified_no_error():
    out, client = _recode(_billed_raw(item="ITEM-A", customer="CUST-NEW"),
                          spec=_spec(customer_changed=True), customer_value="CUST-NEW", expected="5")
    assert out["status"] == "already_recoded"
    client.update_purchase_raw.assert_not_called()


# ---- item 2: under allow_legacy, every path into multi-line mode is refused

def test_adopt_legacy_review_submit_without_confirmed_coding_refuses_multi_line_and_persists_nothing():
    locals_ = [SimpleNamespace(qbo_id="10", sub_cost_code_id=102, project_id=None),
               SimpleNamespace(qbo_id="11", sub_cost_code_id=102, project_id=None)]
    live = [_adopt_live_line("10", item="item-102"), _adopt_live_line("11", item="item-102")]
    out, svc = _adopt(_adopt_item(confirmed_sub_cost_code_id=None), locals_=locals_, live=live,
                      allow_legacy=True, apply=True)
    assert out["status"] == "legacy_multi_line"
    svc.repo.adopt_written_record.assert_not_called()


# ---- item 3: a row the billed refusal marked error is never re-driven

def test_redrive_error_repoint_refuses_repoint_line_billed_row():
    from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE as SRC
    from entities.coding_suggestion.business import service as cs_service

    targets = [{"qbo_line_id": "L1", "sub_cost_code_id": 5, "project_id": 2, "description": "d",
                "repoint_from_item_ref_value": "ITEM-A"}]
    item = SimpleNamespace(status="error", confirmation_source=SRC, confirmation_seq=6, write_error="repoint_line_billed",
                           repoint_targets=targets, repoint_targets_json=json.dumps(targets), written_sync_token="8",
                           realm_id="realm-1", confirmed_project_id=202, confirmed_sub_cost_code_id=101,
                           confirmed_description="d")
    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=item)
    svc.record_confirmation = MagicMock()
    svc._enqueue_recode_and_mark = MagicMock()
    svc._has_recode_dead_letter = MagicMock(return_value=False)
    with patch("integrations.intuit.qbo.base.client.recode_write_gate_reason", return_value=None), \
            patch.object(cs_service, "coding_suggestion_lock", _lock_held):
        out = svc.redrive_error_repoint(PUBLIC_ID)
    assert out["status"] == "billed_needs_decision"
    svc.record_confirmation.assert_not_called()
    svc._enqueue_recode_and_mark.assert_not_called()
