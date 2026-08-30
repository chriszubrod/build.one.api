"""Pure-logic tests for the U-335 billable_status_drift reconciler.

Detects dbo.BillLineItem / dbo.ExpenseLineItem rows that are locally
IsBilled=1 but whose mapped QBO line still carries BillableStatus='Billable'
(we deliberately never push BillableStatus back to QBO on invoice completion,
so QBO's Suggested Transactions tray keeps re-suggesting already-billed
lines). FLAG-ONLY: never auto-fixes, never mutates BillableStatus.
"""
import itertools
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

from integrations.intuit.qbo.reconciliation.business.service import (
    DRIFT_BILLABLE_STATUS_DRIFT,
    ReconciliationService,
)


class _FakeIssueRepo:
    """Mirrors tests/test_qbo_reconcile_void_query_diff.py's _FakeIssueRepo."""

    def __init__(self, *, seeded_issues=None, create_raises=False):
        self.issues = []
        # Each seed: (realm_id, entity_type, qbo_id, status)
        self.seeded_issues = list(seeded_issues or [])
        self.create_raises = create_raises
        self.create_calls = 0
        self.key_fetch_calls = 0

    def create(self, **kwargs):
        self.create_calls += 1
        if self.create_raises:
            raise RuntimeError("simulated INSERT failure")
        self.issues.append(kwargs)

    def read_unresolved_issue_keys_by_drift_type(self, drift_type):
        self.key_fetch_calls += 1
        if drift_type != DRIFT_BILLABLE_STATUS_DRIFT:
            return []
        keys = []
        for realm_id, entity_type, qbo_id, status in self.seeded_issues:
            if status == "resolved":
                continue
            if qbo_id is None:
                continue
            keys.append((realm_id, entity_type, qbo_id))
        return keys


def _fake_service(*, seeded_issues=None, create_raises=False):
    repo = _FakeIssueRepo(seeded_issues=seeded_issues, create_raises=create_raises)
    return ReconciliationService(repo=repo), repo


class _FakeCursor:
    """Returns `rows` for a bill-branch query, `[]` for anything else — the
    marker text ("dbo.BillLineItem bli" / "dbo.ExpenseLineItem eli") is
    present verbatim in each detector's SQL FROM clause."""

    def __init__(self, rows, marker):
        self._rows = rows
        self._marker = marker
        self.executed_sql = None
        self.executed_params = None

    def execute(self, sql, *params):
        self.executed_sql = sql
        self.executed_params = params

    def fetchall(self):
        if self._marker in (self.executed_sql or ""):
            return self._rows
        return []


def _patch_connection(monkeypatch, rows, marker):
    cursor = _FakeCursor(rows, marker)

    @contextmanager
    def _fake_conn():
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr("shared.database.get_connection", _fake_conn)
    return cursor


_line_id_seq = itertools.count(1)


def _bill_row(*, bill_id=1, public_id="pub-1", qbo_id="QBO-BILL-1",
              bill_number="B-1", line_id=None, amount="100.00", invoice_number="INV-1"):
    return SimpleNamespace(
        BillId=bill_id,
        BillPublicId=public_id,
        QboBillId=qbo_id,
        BillNumber=bill_number,
        BillLineItemId=line_id if line_id is not None else next(_line_id_seq),
        LineAmount=Decimal(amount) if amount is not None else None,
        InvoiceNumber=invoice_number,
    )


def _expense_row(*, expense_id=1, public_id="pub-1", qbo_id="QBO-PUR-1",
                  reference_number="R-1", line_id=None, amount="50.00", invoice_number="INV-1"):
    return SimpleNamespace(
        ExpenseId=expense_id,
        ExpensePublicId=public_id,
        QboPurchaseId=qbo_id,
        ReferenceNumber=reference_number,
        ExpenseLineItemId=line_id if line_id is not None else next(_line_id_seq),
        LineAmount=Decimal(amount) if amount is not None else None,
        InvoiceNumber=invoice_number,
    )


# ------------------------------------------------------------------ #
# Bill branch
# ------------------------------------------------------------------ #


def test_bill_branch_flags_drifting_bill_and_aggregates_lines(monkeypatch):
    svc, repo = _fake_service()
    rows = [
        _bill_row(bill_id=1, qbo_id="QBO-BILL-1", amount="100.00", invoice_number="INV-1"),
        _bill_row(bill_id=1, qbo_id="QBO-BILL-1", amount="25.50", invoice_number="INV-1"),
    ]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result == {"auto_fixed": 0, "flagged": 1, "flagged_deduped": 0, "errors": 0}
    assert repo.create_calls == 1
    issue = repo.issues[0]
    assert issue["drift_type"] == DRIFT_BILLABLE_STATUS_DRIFT
    assert issue["action"] == "flagged"
    assert issue["entity_type"] == "Bill"
    assert issue["entity_public_id"] == "pub-1"
    assert issue["qbo_id"] == "QBO-BILL-1"
    assert issue["realm_id"] == "realm-1"
    assert issue["reconcile_run_id"] == "run-1"
    assert "2 line(s)" in issue["details"]
    assert "125.50" in issue["details"]
    assert "INV-1" in issue["details"]


def test_bill_branch_groups_multiple_drifting_bills_separately(monkeypatch):
    svc, repo = _fake_service()
    rows = [
        _bill_row(bill_id=1, qbo_id="QBO-BILL-1"),
        _bill_row(bill_id=2, qbo_id="QBO-BILL-2"),
    ]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["flagged"] == 2
    assert {i["qbo_id"] for i in repo.issues} == {"QBO-BILL-1", "QBO-BILL-2"}


def test_bill_branch_no_drift_writes_nothing(monkeypatch):
    """No rows returned (simulates the SQL WHERE excluding clean / already-
    HasBeenBilled lines) — nothing recorded."""
    svc, repo = _fake_service()
    _patch_connection(monkeypatch, [], "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result == {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}
    assert repo.issues == []


def test_bill_branch_multiple_invoice_numbers_are_deduped_and_sorted(monkeypatch):
    svc, repo = _fake_service()
    rows = [
        _bill_row(bill_id=1, invoice_number="INV-2"),
        _bill_row(bill_id=1, invoice_number="INV-1"),
        _bill_row(bill_id=1, invoice_number="INV-1"),
    ]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert repo.issues[0]["details"].count("INV-1") == 1
    assert "INV-1, INV-2" in repo.issues[0]["details"]


def test_bill_branch_row_with_no_invoice_link_says_unknown(monkeypatch):
    svc, repo = _fake_service()
    rows = [_bill_row(invoice_number=None)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert "unknown" in repo.issues[0]["details"]


def test_bill_branch_null_line_amount_does_not_crash_money_math(monkeypatch):
    """Money guard regression: Decimal(0) is falsy, so the accumulator must be
    skipped via `is not None`, never truthiness."""
    svc, repo = _fake_service()
    rows = [_bill_row(amount=None)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert repo.issues[0]["details"].startswith("1 line(s) totaling $0.00")


def test_bill_branch_row_missing_qbo_id_is_skipped(monkeypatch):
    svc, repo = _fake_service()
    rows = [_bill_row(qbo_id=None)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["flagged"] == 0
    assert repo.issues == []


def test_bill_branch_query_scopes_by_realm_id(monkeypatch):
    svc, repo = _fake_service()
    cursor = _patch_connection(monkeypatch, [], "dbo.BillLineItem bli")

    svc._reconcile_bill_billable_status_drift(realm_id="realm-target", run_id="run-1")

    assert cursor.executed_params == ("realm-target",)


def test_bill_branch_sql_filters_on_isbilled_and_billable_status(monkeypatch):
    """Mutation-proof pin: perturbing either literal (e.g. `IsBilled = 0`, or
    dropping the BillableStatus filter) fails this assertion immediately."""
    svc, repo = _fake_service()
    cursor = _patch_connection(monkeypatch, [], "dbo.BillLineItem bli")

    svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert "bli.IsBilled = 1" in cursor.executed_sql
    assert "ql.BillableStatus = 'Billable'" in cursor.executed_sql
    # Review finding (Codex Pass-1): the QBO staging line's own parent realm
    # must be cross-checked against the dbo-native RealmId, or a mapping row
    # orphaned by an identity-theft-clear event could misattribute a
    # different realm's drift to this Bill.
    assert "qb.RealmId = b.RealmId" in cursor.executed_sql


def test_bill_branch_invoice_fanout_does_not_double_count(monkeypatch):
    """Review finding (Codex Pass-1): dbo.InvoiceLineItem has no UNIQUE
    constraint on BillLineItemId, so the LEFT JOIN can fan out one drifting
    line into multiple rows. A single $100 line billed via two live
    InvoiceLineItem rows must still count as 1 line / $100 — not 2 / $200."""
    svc, repo = _fake_service()
    rows = [
        _bill_row(bill_id=1, line_id=99, amount="100.00", invoice_number="INV-1"),
        _bill_row(bill_id=1, line_id=99, amount="100.00", invoice_number="INV-2"),
    ]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["flagged"] == 1
    details = repo.issues[0]["details"]
    assert "1 line(s) totaling $100.00" in details
    # Both invoice numbers are still surfaced — only count/amount are deduped.
    assert "INV-1" in details and "INV-2" in details


# ------------------------------------------------------------------ #
# Purchase (Expense) branch
# ------------------------------------------------------------------ #


def test_purchase_branch_flags_drifting_expense(monkeypatch):
    svc, repo = _fake_service()
    rows = [_expense_row(expense_id=1, qbo_id="QBO-PUR-1", amount="75.00")]
    _patch_connection(monkeypatch, rows, "dbo.ExpenseLineItem eli")

    result = svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result == {"auto_fixed": 0, "flagged": 1, "flagged_deduped": 0, "errors": 0}
    issue = repo.issues[0]
    assert issue["drift_type"] == DRIFT_BILLABLE_STATUS_DRIFT
    assert issue["entity_type"] == "Expense"
    assert issue["qbo_id"] == "QBO-PUR-1"
    assert "75.00" in issue["details"]


def test_purchase_branch_no_drift_writes_nothing(monkeypatch):
    svc, repo = _fake_service()
    _patch_connection(monkeypatch, [], "dbo.ExpenseLineItem eli")

    result = svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result == {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}
    assert repo.issues == []


def test_purchase_branch_sql_filters_on_isbilled_and_billable_status(monkeypatch):
    svc, repo = _fake_service()
    cursor = _patch_connection(monkeypatch, [], "dbo.ExpenseLineItem eli")

    svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert "eli.IsBilled = 1" in cursor.executed_sql
    assert "ql.BillableStatus = 'Billable'" in cursor.executed_sql
    assert "qp.RealmId = e.RealmId" in cursor.executed_sql


def test_purchase_branch_invoice_fanout_does_not_double_count(monkeypatch):
    svc, repo = _fake_service()
    rows = [
        _expense_row(expense_id=1, line_id=77, amount="50.00", invoice_number="INV-1"),
        _expense_row(expense_id=1, line_id=77, amount="50.00", invoice_number="INV-2"),
    ]
    _patch_connection(monkeypatch, rows, "dbo.ExpenseLineItem eli")

    result = svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")

    assert result["flagged"] == 1
    details = repo.issues[0]["details"]
    assert "1 line(s) totaling $50.00" in details
    assert "INV-1" in details and "INV-2" in details


# ------------------------------------------------------------------ #
# Idempotency / dedupe (shared _unresolved_billable_status_drift_keys)
# ------------------------------------------------------------------ #


def test_dedupe_suppresses_write_but_still_counts_as_flagged(monkeypatch):
    """flagged_deduped is a SUBSET of flagged, matching RECONCILE_COUNT_KEYS'
    documented invariant for the qbo_voided detectors — do not add them."""
    realm_id = "realm-1"
    qbo_id = "QBO-BILL-1"
    svc, repo = _fake_service(seeded_issues=[(realm_id, "Bill", qbo_id, "open")])
    rows = [_bill_row(qbo_id=qbo_id)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id="run-1")

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 1
    assert repo.create_calls == 0
    assert repo.issues == []


def test_no_existing_issue_writes_normally(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "QBO-BILL-1"
    svc, repo = _fake_service()
    rows = [_bill_row(qbo_id=qbo_id)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id="run-1")

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    assert repo.create_calls == 1


def test_resolved_issue_does_not_suppress(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "QBO-BILL-1"
    svc, repo = _fake_service(seeded_issues=[(realm_id, "Bill", qbo_id, "resolved")])
    rows = [_bill_row(qbo_id=qbo_id)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id="run-1")

    assert result["flagged"] == 1
    assert result["flagged_deduped"] == 0
    assert repo.create_calls == 1


def test_acknowledged_issue_does_suppress(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "QBO-BILL-1"
    svc, repo = _fake_service(seeded_issues=[(realm_id, "Bill", qbo_id, "acknowledged")])
    rows = [_bill_row(qbo_id=qbo_id)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id="run-1")

    assert result["flagged_deduped"] == 1
    assert repo.create_calls == 0


def test_dedupe_key_isolates_entity_type_from_bill_to_expense(monkeypatch):
    """A Bill and an Expense sharing the same raw QboId string must not
    cross-suppress each other — entity_type is part of the dedupe key."""
    realm_id = "realm-1"
    shared_qbo_id = "SAME-ID"
    svc, repo = _fake_service(seeded_issues=[(realm_id, "Expense", shared_qbo_id, "open")])
    rows = [_bill_row(qbo_id=shared_qbo_id)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id="run-1")

    assert result["flagged_deduped"] == 0
    assert repo.create_calls == 1


def test_failed_write_is_not_cached_as_deduped(monkeypatch):
    realm_id = "realm-1"
    qbo_id = "QBO-BILL-1"
    svc, repo = _fake_service(create_raises=True)
    rows = [_bill_row(qbo_id=qbo_id)]
    _patch_connection(monkeypatch, rows, "dbo.BillLineItem bli")

    result = svc._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id="run-1")

    assert repo.create_calls == 1
    assert result["flagged_deduped"] == 0
    assert repo.issues == []


def test_dedupe_cache_is_shared_across_bill_and_purchase_branches(monkeypatch):
    """_unresolved_billable_status_drift_keys fetches once per
    ReconciliationService instance, mirroring _unresolved_void_keys."""
    svc, repo = _fake_service()
    bill_cursor = _patch_connection(monkeypatch, [_bill_row(qbo_id="B-1")], "dbo.BillLineItem bli")
    svc._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")
    assert repo.key_fetch_calls == 1

    _patch_connection(monkeypatch, [_expense_row(qbo_id="P-1")], "dbo.ExpenseLineItem eli")
    svc._reconcile_purchase_billable_status_drift(realm_id="realm-1", run_id="run-1")
    assert repo.key_fetch_calls == 1


# ------------------------------------------------------------------ #
# Public entry point — cross-branch aggregation + failure isolation
# ------------------------------------------------------------------ #


def test_reconcile_billable_status_drift_aggregates_both_branches(monkeypatch):
    svc, repo = _fake_service()

    def _fake_bill(self, realm_id, run_id):
        return {"auto_fixed": 0, "flagged": 2, "flagged_deduped": 1, "errors": 0}

    def _fake_purchase(self, realm_id, run_id):
        return {"auto_fixed": 0, "flagged": 3, "flagged_deduped": 0, "errors": 0}

    monkeypatch.setattr(ReconciliationService, "_reconcile_bill_billable_status_drift", _fake_bill)
    monkeypatch.setattr(ReconciliationService, "_reconcile_purchase_billable_status_drift", _fake_purchase)

    result = svc.reconcile_billable_status_drift(realm_id="realm-1")

    assert result["flagged"] == 5
    assert result["flagged_deduped"] == 1
    assert result["errors"] == 0
    assert "run_id" in result


def test_reconcile_billable_status_drift_isolates_branch_failures(monkeypatch):
    """One branch raising must not prevent the other from running (mirrors
    reconcile_bills' per-detector try/except isolation)."""
    svc, repo = _fake_service()

    def _raises(self, realm_id, run_id):
        raise RuntimeError("boom")

    def _fake_purchase(self, realm_id, run_id):
        return {"auto_fixed": 0, "flagged": 1, "flagged_deduped": 0, "errors": 0}

    monkeypatch.setattr(ReconciliationService, "_reconcile_bill_billable_status_drift", _raises)
    monkeypatch.setattr(ReconciliationService, "_reconcile_purchase_billable_status_drift", _fake_purchase)

    result = svc.reconcile_billable_status_drift(realm_id="realm-1")

    assert result["errors"] == 1
    assert result["flagged"] == 1
