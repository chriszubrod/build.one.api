"""U-424: a reviewer approval must leave ContractLabor's parent aggregates
equal to the sum of its line items.

`PUT /api/v1/contract-labor/{id}/bill` has always called
`repo.update_aggregates` after writing line items (router.py). The reviewer-
approval path mutates line items too — it stamps SubCostCodeId (and
optionally Description) on every line matching the reviewed project — but
never recomputed, so a ContractLabor whose lines were only ever touched by a
reviewer kept whatever parent totals it was created with. A read-only preview
against prod on 2026-09-08 found 416 ContractLabor rows inconsistent with their
children (410 of them already billed).

The fixture below is a CONSTRUCTED drift case, not a transcript of a specific
prod row — the two CLs this unit was opened against turned out to be consistent
under the billable-only semantic the sproc implements, so they are deliberately
not modeled here. See the evidence correction in SESSION_NOTES.md.

The fakes here model the real recompute rather than asserting
`update_aggregates.called`, so the tests fail on a wrong parent VALUE, not just
a missing call. For WHY the parent is the sum of already-rounded child prices,
see the ROUNDING SEMANTIC block on dbo.UpdateContractLaborAggregates.
"""

from __future__ import annotations

import re
import types
from unittest.mock import MagicMock

import pytest

from entities.contract_labor.business.service import ContractLaborService
from tests.test_sproc_single_source import (
    CONTRACT_LABOR_BASE,
    TIME_ENTRY_BASE,
    _sproc_body,
)

PROJECT_ID = 100

# $62.50/h × 1.35 markup, all lines billable. The children are an 8.0h day split
# 3.0 + 3.0 + 2.0; the two 3.0h lines each round 253.125 → 253.13, so they sum to
# $675.01, a cent above the "exact" 8.0h figure of $675.00 — the ratified
# semantic, not a bug (see dbo.contract_labor.sql). The stale parent is a 7.0h
# day's worth (590.625 → $590.63): a parent left behind when the day grew to 8.0h
# across more lines, which is the shape the reviewer path used to leave.
PROD_SHAPE_LINES = [
    (3.0, "253.13"),
    (3.0, "253.13"),
    (2.0, "168.75"),
]
EXPECTED_TOTAL = 675.01
EXPECTED_HOURS = 8.0
STALE_PARENT_TOTAL = "590.63"


def _line(li_id, *, hours, price, project_id=PROJECT_ID, sub_cost_code_id=None,
          is_billable=True, is_overhead=False):
    return types.SimpleNamespace(
        id=li_id,
        project_id=project_id,
        sub_cost_code_id=sub_cost_code_id,
        row_version_bytes=b"\x01",
        line_date="2026-08-24",
        description="d",
        hours=float(hours),
        rate=62.50,
        markup=0.35,
        price=float(price),
        is_billable=is_billable,
        is_overhead=is_overhead,
        bill_line_item_id=None,
    )


class _FakeLineItemRepo:
    """Line-item store whose update_by_id actually mutates the rows, so the
    recompute under test reads what the approval wrote."""

    def __init__(self, lines, *, fail_ids=frozenset()):
        self.lines = list(lines)
        self.fail_ids = set(fail_ids)

    def read_by_contract_labor_id(self, *, contract_labor_id):
        return list(self.lines)

    def update_by_id(self, *, id, **fields):
        if id in self.fail_ids:
            raise RuntimeError("row-version conflict")
        target = next(li for li in self.lines if li.id == id)
        for key, value in fields.items():
            if key == "row_version":
                continue
            setattr(target, key, value)
        return target


class _FakeRepo:
    """ContractLabor repo whose update_aggregates mirrors
    dbo.UpdateContractLaborAggregates: parent totals are re-derived from the
    children, TotalAmount over BILLABLE lines only."""

    def __init__(self, parent, line_item_repo, *, raises=False):
        self.parent = parent
        self.line_item_repo = line_item_repo
        self.raises = raises
        self.calls = []

    def update_by_id(self, contract_labor):
        return contract_labor

    def update_aggregates(self, *, id):
        self.calls.append(id)
        if self.raises:
            raise RuntimeError("sproc unavailable")
        lines = self.line_item_repo.read_by_contract_labor_id(contract_labor_id=id)
        self.parent.total_hours = round(sum(li.hours or 0 for li in lines), 2)
        # `is not False` mirrors the sproc's billable-only TotalAmount filter,
        # so a fixture that adds a non-billable line gets the real answer.
        self.parent.total_amount = round(
            sum(li.price or 0 for li in lines if li.is_billable is not False), 2
        )
        return self.parent


def _harness(monkeypatch, *, lines, fail_ids=frozenset(), recompute_raises=False):
    """Wire a ContractLaborService whose only live collaborators are the two
    stateful fakes above; everything else (authz, ReviewStatus, Review row,
    ready-flip) is stubbed to the happy path."""
    parent = types.SimpleNamespace(
        id=10,
        public_id="cl-1",
        status="submitted",
        total_amount=float(STALE_PARENT_TOTAL),
        total_hours=7.0,
    )
    li_repo = _FakeLineItemRepo(lines, fail_ids=fail_ids)
    repo = _FakeRepo(parent, li_repo, raises=recompute_raises)
    svc = ContractLaborService(repo=repo)

    monkeypatch.setattr(svc, "read_by_public_id", lambda **kw: parent)
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: types.SimpleNamespace(
            read_by_public_id=lambda **kw: types.SimpleNamespace(
                id=PROJECT_ID, public_id="proj-a",
            ),
        ),
    )
    monkeypatch.setattr(
        "entities.contract_labor.persistence.line_item_repo."
        "ContractLaborLineItemRepository",
        lambda: li_repo,
    )
    monkeypatch.setattr(
        "entities.sub_cost_code.business.service.SubCostCodeService",
        lambda: types.SimpleNamespace(
            read_by_public_id=lambda **kw: types.SimpleNamespace(id=555, public_id="scc-uuid"),
        ),
    )

    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        types.SimpleNamespace(ProjectId=PROJECT_ID, UserId=7, Email="pm@test.com"),
    ]
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda *a: None
    monkeypatch.setattr("shared.database.get_connection", lambda: conn)
    monkeypatch.setattr("shared.database.call_procedure", lambda **kw: None)

    approved = types.SimpleNamespace(id=1, name="Approved", is_final=True, is_declined=False)
    declined = types.SimpleNamespace(id=2, name="Declined", is_final=True, is_declined=True)
    monkeypatch.setattr(
        "entities.review_status.business.service.ReviewStatusService",
        lambda: types.SimpleNamespace(
            read_all=lambda: [approved, declined],
            read_by_id=lambda id: approved if id == 1 else declined,
        ),
    )
    monkeypatch.setattr(
        "entities.review.persistence.repo.ReviewRepository",
        lambda: types.SimpleNamespace(
            create=lambda **kw: types.SimpleNamespace(
                id=99,
                review_status_id=kw["review_status_id"],
                status_is_final=True,
                status_is_declined=(kw["review_status_id"] == 2),
            ),
        ),
    )
    monkeypatch.setattr(
        svc, "mark_as_ready_via_review_approval", lambda **kw: parent,
    )
    return svc, repo, li_repo, parent


def _approve(svc, **overrides):
    kwargs = dict(
        contract_labor_public_id="cl-1",
        project_public_id="proj-a",
        decision="approved",
        reviewer_email="pm@test.com",
        sub_cost_code_public_id="scc-uuid",
    )
    kwargs.update(overrides)
    return svc._apply_decision_to_single_cl(**kwargs)


# ─── The defect ──────────────────────────────────────────────────────────


def test_approval_leaves_parent_total_equal_to_sum_of_line_items(monkeypatch):
    """The headline invariant. The seed is a stale parent left behind by a
    reviewer approval; a NULL or $0.00 parent — the dominant real shape, 340 of
    the 416 drifting prod rows — heals identically, since the recompute never
    reads the pre-state."""
    lines = [_line(i, hours=h, price=p) for i, (h, p) in enumerate(PROD_SHAPE_LINES, start=1)]
    svc, repo, li_repo, parent = _harness(monkeypatch, lines=lines)

    assert parent.total_amount == float(STALE_PARENT_TOTAL)  # stale before

    _approve(svc)

    expected = round(sum(li.price for li in li_repo.lines), 2)
    assert expected == EXPECTED_TOTAL  # sum of rounded children, not 675.00
    assert parent.total_amount == expected
    assert parent.total_hours == EXPECTED_HOURS
    # The reviewed CL, not some other row — the fake's reader ignores its arg,
    # so nothing above would catch a wrong id.
    assert repo.calls == [10]


# ─── Ordering + failure isolation ────────────────────────────────────────


def test_partial_line_failure_still_recomputes_before_raising(monkeypatch):
    """When some lines applied and others didn't, the parent must match what
    actually landed — so the recompute runs BEFORE the partial-failure raise."""
    lines = [
        _line(1, hours=7.0, price=590.63),
        _line(2, hours=3.0, price=253.13),
    ]
    svc, repo, _li_repo, parent = _harness(monkeypatch, lines=lines, fail_ids={2})

    with pytest.raises(ValueError, match="partial-failure"):
        _approve(svc)

    assert repo.calls == [10]
    assert parent.total_amount == 843.76


def test_recompute_failure_does_not_undo_the_applied_decision(monkeypatch):
    """The Review row is written and the lines are coded before the recompute.
    A recompute blow-up must not surface as a retryable error — the retry would
    fail the status guard on a CL that has already advanced."""
    lines = [_line(1, hours=7.0, price=590.63)]
    svc, _repo, li_repo, _parent = _harness(monkeypatch, lines=lines, recompute_raises=True)

    result = _approve(svc)

    assert result["decision_applied"] == "approved"
    assert li_repo.lines[0].sub_cost_code_id == 555


def test_rejection_does_not_touch_parent_aggregates(monkeypatch):
    """Rejection mutates no line items, so it owes the parent no recompute."""
    lines = [_line(1, hours=7.0, price=590.63)]
    svc, repo, _li_repo, parent = _harness(monkeypatch, lines=lines)

    result = svc._apply_decision_to_single_cl(
        contract_labor_public_id="cl-1",
        project_public_id="proj-a",
        decision="rejected",
        reviewer_email="pm@test.com",
    )

    assert repo.calls == []
    assert parent.total_amount == float(STALE_PARENT_TOTAL)
    assert result["decision_applied"] == "rejected"


# ─── SQL structural pins ─────────────────────────────────────────────────

def test_update_aggregates_gates_its_row_return():
    """@ReturnRow exists and the trailing SELECT is behind it — without the
    gate, the nested EXEC below prepends a result set to the caller's cursor."""
    body = _sproc_body(CONTRACT_LABOR_BASE, "UpdateContractLaborAggregates")
    assert re.search(r"@ReturnRow\s+BIT\s*=\s*1", body), "@ReturnRow must default to 1"
    # ISNULL-defended: an explicit NULL must still return the row, or a caller
    # binding the param dynamically would silently get None back.
    assert re.search(r"IF\s+ISNULL\(\s*@ReturnRow\s*,\s*1\s*\)\s*=\s*1", body), (
        "the trailing SELECT must be gated on ISNULL(@ReturnRow, 1)"
    )


def test_aggregate_on_submit_recomputes_contract_labor_parent():
    """AggregateTimeEntryOnSubmit writes ContractLaborLineItem rows, so it owes
    the parent the same recompute — with the row return suppressed."""
    body = _sproc_body(TIME_ENTRY_BASE, "AggregateTimeEntryOnSubmit")
    match = re.search(
        r"EXEC\s+dbo\.UpdateContractLaborAggregates\s+@Id\s*=\s*@ParentRowId\s*,\s*"
        r"@ReturnRow\s*=\s*0",
        body,
    )
    assert match, (
        "AggregateTimeEntryOnSubmit must recompute the ContractLabor parent from "
        "its children via EXEC dbo.UpdateContractLaborAggregates "
        "@Id = @ParentRowId, @ReturnRow = 0"
    )
    assert body.index(match.group(0)) < body.rindex("FROM @Results"), (
        "the recompute must run before the sproc's own result SELECT"
    )


def test_aggregate_on_submit_update_branch_does_not_write_parent_money():
    """The tail recompute is the SOLE writer of the parent's four money columns
    on the update path. Re-adding them to the UPDATE would be a dead store AND
    a second ROWVERSION bump per submit, invalidating a client's optimistic-
    concurrency token twice. The INSERT branch legitimately still sets them."""
    body = _sproc_body(TIME_ENTRY_BASE, "AggregateTimeEntryOnSubmit")
    update_branch = re.search(
        r"UPDATE\s+dbo\.\[ContractLabor\]\s+SET(.*?)WHERE\s+\[Id\]\s*=\s*@ParentRowId",
        body,
        re.DOTALL,
    )
    assert update_branch, "the ContractLabor parent UPDATE went missing"
    assigned = update_branch.group(1)
    for column in ("[TotalHours]", "[HourlyRate]", "[Markup]", "[TotalAmount]"):
        assert column not in assigned, (
            f"AggregateTimeEntryOnSubmit's ContractLabor UPDATE must not set "
            f"{column} — dbo.UpdateContractLaborAggregates owns it (U-424)"
        )


# ─── PUT /{public_id}: children win when they exist ──────────────────────


def _update_harness(monkeypatch, *, lines):
    """ContractLaborService wired for update_by_public_id: a parent whose own
    hours/rate/markup disagree with its children."""
    parent = types.SimpleNamespace(
        id=10,
        public_id="cl-1",
        status="submitted",
        row_version="rv",
        vendor_id=1,
        project_id=PROJECT_ID,
        employee_name="Selvin Cordova",
        work_date="2026-08-24",
        billing_period_start="2026-08-16",
        time_in=None,
        time_out=None,
        break_time=None,
        regular_hours=None,
        overtime_hours=None,
        sub_cost_code_id=None,
        description=None,
        total_hours=7.0,
        hourly_rate=62.50,
        markup=0.35,
        total_amount=float(STALE_PARENT_TOTAL),
    )
    li_repo = _FakeLineItemRepo(lines)
    repo = _FakeRepo(parent, li_repo)
    svc = ContractLaborService(repo=repo)

    monkeypatch.setattr(svc, "read_by_public_id", lambda **kw: parent)
    monkeypatch.setattr(
        "entities.contract_labor.persistence.line_item_repo."
        "ContractLaborLineItemRepository",
        lambda: li_repo,
    )
    return svc, repo, parent


def test_update_by_public_id_defers_to_line_items_when_they_exist(monkeypatch):
    """The legacy parent-field math must not clobber the children. Multi-project
    entries newly carry a parent HourlyRate (the sproc now derives one), so this
    branch fires where it previously didn't — without the recompute it would
    re-open the drift the unit closes."""
    lines = [_line(i, hours=h, price=p) for i, (h, p) in enumerate(PROD_SHAPE_LINES, start=1)]
    svc, repo, parent = _update_harness(monkeypatch, lines=lines)

    result = svc.update_by_public_id(public_id="cl-1", row_version="rv")

    assert repo.calls == [10]
    assert parent.total_amount == EXPECTED_TOTAL
    assert result is parent


def test_update_by_public_id_keeps_parent_math_when_there_are_no_line_items(monkeypatch):
    """Imported / hand-created rows legitimately carry parent-only money until
    review builds their lines — recomputing would zero them."""
    svc, repo, parent = _update_harness(monkeypatch, lines=[])

    svc.update_by_public_id(public_id="cl-1", row_version="rv", total_hours=8.0)

    assert repo.calls == []
    assert parent.total_amount == 675.00  # round(8.0*62.50,2) * 1.35
