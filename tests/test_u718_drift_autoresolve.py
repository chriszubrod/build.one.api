"""U-718 bite 3: auto-resolve billable_status_drift issues whose parent no longer drifts."""
import itertools
import logging
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.reconciliation.business.service import (
    DRIFT_BILLABLE_STATUS_DRIFT,
    ReconciliationService,
)


class _FakeIssueRepo:
    def __init__(
        self,
        *,
        seeded_issues=None,
        resolve_raises_for=None,
        unresolved_read_raises=False,
    ):
        self.issues = []
        self.call_order = []
        self.unresolved_read_raises = unresolved_read_raises
        # Each seed: id, realm_id, entity_type, qbo_id, status [, drift_type, action]
        self.seeded_unresolved = []
        for row in seeded_issues or []:
            if len(row) == 5:
                issue_id, realm_id, entity_type, qbo_id, status = row
                drift_type, action = DRIFT_BILLABLE_STATUS_DRIFT, "flagged"
            else:
                issue_id, realm_id, entity_type, qbo_id, status, drift_type, action = row
            self.seeded_unresolved.append(
                {
                    "id": issue_id,
                    "realm_id": realm_id,
                    "entity_type": entity_type,
                    "qbo_id": qbo_id,
                    "status": status,
                    "drift_type": drift_type,
                    "action": action,
                }
            )
        self.create_calls = 0
        self.key_fetch_calls = 0
        self.unresolved_fetch_calls = 0
        self.resolve_calls = []
        self.resolve_raises_for = set(resolve_raises_for or ())

    def create(self, **kwargs):
        self.create_calls += 1
        self.call_order.append("create")
        self.issues.append(kwargs)

    def read_unresolved_issue_keys_by_drift_type(self, drift_type):
        self.key_fetch_calls += 1
        if drift_type != DRIFT_BILLABLE_STATUS_DRIFT:
            return []
        keys = []
        for issue in self.seeded_unresolved:
            if issue["status"] == "resolved":
                continue
            if issue["qbo_id"] is None:
                continue
            keys.append((issue["realm_id"], issue["entity_type"], issue["qbo_id"]))
        return keys

    def read_unresolved_issues_by_drift_type(
        self, drift_type, *, realm_id=None, entity_type=None
    ):
        self.unresolved_fetch_calls += 1
        if self.unresolved_read_raises:
            raise RuntimeError("simulated unresolved read failure")
        out = []
        for issue in self.seeded_unresolved:
            if issue["drift_type"] != drift_type:
                continue
            if issue["action"] != "flagged":
                continue
            if issue["status"] not in ("open", "acknowledged"):
                continue
            if issue["qbo_id"] is None:
                continue
            if realm_id is not None and issue["realm_id"] != realm_id:
                continue
            if entity_type is not None and issue["entity_type"] != entity_type:
                continue
            out.append(
                {
                    "id": issue["id"],
                    "realm_id": issue["realm_id"],
                    "entity_type": issue["entity_type"],
                    "qbo_id": issue["qbo_id"],
                    "status": issue["status"],
                }
            )
        return out

    def resolve(self, issue_id):
        self.call_order.append("resolve")
        self.resolve_calls.append(issue_id)
        if issue_id in self.resolve_raises_for:
            raise RuntimeError(f"simulated resolve failure for {issue_id}")
        for issue in self.seeded_unresolved:
            if issue["id"] == issue_id:
                issue["status"] = "resolved"
                break
        return SimpleNamespace(id=issue_id, status="resolved")


def _fake_service(
    *,
    seeded_issues=None,
    resolve_raises_for=None,
    unresolved_read_raises=False,
):
    repo = _FakeIssueRepo(
        seeded_issues=seeded_issues,
        resolve_raises_for=resolve_raises_for,
        unresolved_read_raises=unresolved_read_raises,
    )
    return ReconciliationService(repo=repo), repo


class _FakeCursor:
    def __init__(self, rows, marker, *, fail_on_fetch=False, fail_on_execute=False):
        self._rows = rows
        self._marker = marker
        self.fail_on_fetch = fail_on_fetch
        self.fail_on_execute = fail_on_execute
        self.executed_sql = None

    def execute(self, sql, *params):
        if self.fail_on_execute:
            raise RuntimeError("simulated SQL failure")
        self.executed_sql = sql

    def fetchall(self):
        if self.fail_on_fetch:
            raise RuntimeError("simulated fetch failure")
        if self._marker in (self.executed_sql or ""):
            return self._rows
        return []


def _patch_connection(monkeypatch, cursor):
    @contextmanager
    def _fake_conn():
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr("shared.database.get_connection", _fake_conn)


_line_id_seq = itertools.count(1)


def _bill_row(*, bill_id=1, public_id="pub-1", qbo_id="QBO-BILL-1", bill_number="B-1"):
    return SimpleNamespace(
        BillId=bill_id,
        BillPublicId=public_id,
        QboBillId=qbo_id,
        BillNumber=bill_number,
        BillLineItemId=next(_line_id_seq),
        LineAmount=Decimal("100.00"),
        InvoiceNumber="INV-1",
    )


def _expense_row(*, expense_id=1, public_id="pub-e", qbo_id="QBO-PUR-1"):
    return SimpleNamespace(
        ExpenseId=expense_id,
        ExpensePublicId=public_id,
        QboPurchaseId=qbo_id,
        ReferenceNumber="R-1",
        ExpenseLineItemId=next(_line_id_seq),
        LineAmount=Decimal("50.00"),
        InvoiceNumber="INV-1",
    )


def test_resolves_non_drifting_parents_only(monkeypatch):
    seeded = [
        (1, "realm-1", "Bill", "A", "open"),
        (2, "realm-1", "Bill", "B", "open"),
        (3, "realm-1", "Bill", "C", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded)
    rows = [_bill_row(bill_id=1, qbo_id="A")]
    cur = _FakeCursor(rows, "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 2
    assert result["auto_resolve_errors"] == 0
    assert set(repo.resolve_calls) == {2, 3}
    assert repo.seeded_unresolved[0]["status"] == "open"


def test_acknowledged_and_wrong_realm_and_entity_type_isolation(monkeypatch):
    seeded = [
        (1, "realm-1", "Bill", "B", "acknowledged"),
        (2, "realm-2", "Bill", "B", "open"),
        (3, "realm-1", "Expense", "P-1", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 1
    assert repo.resolve_calls == [1]
    assert repo.seeded_unresolved[1]["status"] == "open"
    assert repo.seeded_unresolved[2]["status"] == "open"


def test_purchase_branch_never_resolves_bill_issues(monkeypatch):
    seeded = [(1, "realm-1", "Bill", "B-1", "open")]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.ExpenseLineItem eli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 0
    assert repo.resolve_calls == []


def test_purchase_autoresolve_only_cleared_expense_not_still_drifting(monkeypatch):
    """Purchase branch must auto-resolve cleared Expense issues and skip still-drifting parents."""
    seeded = [
        (10, "realm-1", "Expense", "STILL-DRIFT", "open"),
        (11, "realm-1", "Expense", "CLEARED-1", "open"),
        (12, "realm-1", "Expense", "CLEARED-2", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded)
    rows = [_expense_row(expense_id=1, qbo_id="STILL-DRIFT")]
    cur = _FakeCursor(rows, "dbo.ExpenseLineItem eli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 2
    assert result["auto_resolve_errors"] == 0
    assert set(repo.resolve_calls) == {11, 12}
    assert repo.seeded_unresolved[0]["status"] == "open"
    assert repo.seeded_unresolved[1]["status"] == "resolved"
    assert repo.seeded_unresolved[2]["status"] == "resolved"


def test_detector_query_raises_zero_resolves(monkeypatch):
    seeded = [(1, "realm-1", "Bill", "B", "open")]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.BillLineItem bli", fail_on_fetch=True)
    _patch_connection(monkeypatch, cur)

    with pytest.raises(RuntimeError, match="simulated fetch"):
        svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert repo.resolve_calls == []


def test_zero_drift_rows_resolves_all_open_for_entity(monkeypatch):
    seeded = [
        (1, "realm-1", "Bill", "X", "open"),
        (2, "realm-1", "Bill", "Y", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 2
    assert set(repo.resolve_calls) == {1, 2}


def test_cap_exceeded_resolves_none_and_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("QBO_DRIFT_AUTORESOLVE_MAX", "2")
    seeded = [
        (1, "realm-1", "Bill", "A", "open"),
        (2, "realm-1", "Bill", "B", "open"),
        (3, "realm-1", "Bill", "C", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    with caplog.at_level(logging.ERROR):
        result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 0
    assert repo.resolve_calls == []
    assert any("QBO_DRIFT_AUTORESOLVE_MAX" in r.message for r in caplog.records)


def test_cap_at_exact_limit_resolves_all_eligible(monkeypatch):
    """Exactly QBO_DRIFT_AUTORESOLVE_MAX eligible issues must all resolve (strict > cap, not >=)."""
    monkeypatch.setenv("QBO_DRIFT_AUTORESOLVE_MAX", "2")
    seeded = [
        (1, "realm-1", "Bill", "A", "open"),
        (2, "realm-1", "Bill", "B", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 2
    assert result["auto_resolve_errors"] == 0
    assert set(repo.resolve_calls) == {1, 2}


def test_one_resolve_failure_others_still_resolve(monkeypatch):
    seeded = [
        (1, "realm-1", "Bill", "A", "open"),
        (2, "realm-1", "Bill", "B", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded, resolve_raises_for={2})
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 1
    assert result["auto_resolve_errors"] == 1
    assert set(repo.resolve_calls) == {1, 2}
    assert repo.seeded_unresolved[0]["status"] == "resolved"
    assert repo.seeded_unresolved[1]["status"] == "open"


def test_middle_resolve_failure_does_not_stop_remaining_eligible(monkeypatch):
    """Per-issue resolve errors must not break the loop — later eligible issues still resolve."""
    seeded = [
        (1, "realm-1", "Bill", "A", "open"),
        (2, "realm-1", "Bill", "B", "open"),
        (3, "realm-1", "Bill", "C", "open"),
    ]
    svc, repo = _fake_service(seeded_issues=seeded, resolve_raises_for={2})
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 2
    assert result["auto_resolve_errors"] == 1
    assert repo.resolve_calls == [1, 2, 3]
    assert repo.seeded_unresolved[0]["status"] == "resolved"
    assert repo.seeded_unresolved[1]["status"] == "open"
    assert repo.seeded_unresolved[2]["status"] == "resolved"


def test_normalized_qbo_id_still_drifting_not_resolved(monkeypatch):
    """int 123 in drift row vs str '123' on issue — same parent, still drifting."""
    seeded = [(1, "realm-1", "Bill", "123", "open")]
    svc, repo = _fake_service(seeded_issues=seeded)
    rows = [_bill_row(qbo_id=123)]
    cur = _FakeCursor(rows, "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 0
    assert repo.resolve_calls == []


def test_issue_qbo_id_whitespace_padding_still_drifting_not_resolved(monkeypatch):
    """Issue-side normalize_qbo_id must match padded issue id to drifting parent '123'."""
    seeded = [(1, "realm-1", "Bill", " 123 ", "open")]
    svc, repo = _fake_service(seeded_issues=seeded)
    rows = [_bill_row(qbo_id="123")]
    cur = _FakeCursor(rows, "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 0
    assert repo.resolve_calls == []
    assert repo.seeded_unresolved[0]["status"] == "open"


def test_issue_qbo_id_whitespace_padding_cleared_parent_resolved(monkeypatch):
    """Issue-side normalize_qbo_id must resolve padded id when parent no longer drifts."""
    seeded = [(1, "realm-1", "Bill", " 456 ", "open")]
    svc, repo = _fake_service(seeded_issues=seeded)
    cur = _FakeCursor([], "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["auto_resolved"] == 1
    assert repo.resolve_calls == [1]
    assert repo.seeded_unresolved[0]["status"] == "resolved"


def test_unresolved_read_failure_still_flags_new_drift(monkeypatch, caplog):
    """Auto-resolve read must not abort flagging for newly drifting parents."""
    svc, repo = _fake_service(unresolved_read_raises=True)
    rows = [_bill_row(bill_id=1, qbo_id="NEW-DRIFT")]
    cur = _FakeCursor(rows, "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    with caplog.at_level(logging.ERROR):
        result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["flagged"] == 1
    assert result["auto_resolved"] == 0
    assert result["auto_resolve_errors"] == 1
    assert repo.create_calls == 1
    assert repo.resolve_calls == []
    assert any(
        "qbo.reconcile.billable_status_drift.autoresolve.read_failed" in r.message
        for r in caplog.records
    )


def test_flag_create_happens_before_autoresolve_resolve(monkeypatch):
    """Flagging loop must complete before any auto-resolve resolve call."""
    seeded = [(99, "realm-1", "Bill", "CLEARED", "open")]
    svc, repo = _fake_service(seeded_issues=seeded)
    rows = [_bill_row(bill_id=1, qbo_id="STILL-DRIFT")]
    cur = _FakeCursor(rows, "dbo.BillLineItem bli")
    _patch_connection(monkeypatch, cur)

    svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert repo.create_calls == 1
    assert repo.resolve_calls == [99]
    create_idx = repo.call_order.index("create")
    resolve_idx = repo.call_order.index("resolve")
    assert create_idx < resolve_idx


def test_reconcile_billable_status_drift_aggregates_autoresolve(monkeypatch):
    svc, repo = _fake_service()

    def _fake_bill(self, realm_id, run_id):
        return {
            "auto_fixed": 0,
            "flagged": 0,
            "flagged_deduped": 0,
            "errors": 0,
            "auto_resolved": 2,
            "auto_resolve_errors": 1,
        }

    def _fake_purchase(self, realm_id, run_id):
        return {
            "auto_fixed": 0,
            "flagged": 0,
            "flagged_deduped": 0,
            "errors": 0,
            "auto_resolved": 3,
            "auto_resolve_errors": 0,
        }

    monkeypatch.setattr(ReconciliationService, "_reconcile_bill_billable_status_drift", _fake_bill)
    monkeypatch.setattr(
        ReconciliationService, "_reconcile_purchase_billable_status_drift", _fake_purchase
    )

    result = svc.reconcile_billable_status_drift(realm_id="realm-1")

    assert result["auto_resolved"] == 5
    assert result["auto_resolve_errors"] == 1
