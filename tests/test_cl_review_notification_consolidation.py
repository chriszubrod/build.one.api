"""Unit tests for the consolidated ContractLabor review-notification body.

Covers the U-XXX consolidation: one draft per (project, date) that COMBINES
every laborer (crew), grouped by worker, with a crew-wide ask. Pure/DB-free —
exercises the body builder + the no-work-date short-circuit. The gate,
claim-dedup, and crew-apply paths are DB-integration (manual verify_* scripts).
"""

import types

from entities.review.business.cl_notification_service import (
    ContractLaborReviewNotificationService,
)


def _row(cl_id, name, hours, billable, overhead, desc):
    """A minimal stand-in for a ReadSubmittedContractLaborLinesByWorkDate row."""
    return types.SimpleNamespace(
        ContractLaborId=cl_id,
        EmployeeName=name,
        Hours=hours,
        IsBillable=billable,
        IsOverhead=overhead,
        Description=desc,
    )


def test_build_body_combines_all_laborers():
    svc = ContractLaborReviewNotificationService()
    lines = [
        _row(1, "Brayan", 8.0, True, False, "Framing"),
        _row(2, "Elmer", 8.0, True, False, "Framing"),
        _row(3, "Wilmer", 4.0, True, False, "Cleanup"),
    ]
    body = svc._build_body(
        project_label="TB3",
        work_date="2026-07-15",
        lines=lines,
        pms=[{"firstname": "Cassidy", "lastname": "X", "email": "c@x.com"}],
    )
    # Greeting + crew-wide ask carrying project + date.
    assert "Cassidy," in body
    assert "TB3" in body and "2026-07-15" in body
    assert "applied to the full crew" in body
    # Exactly one bold header per distinct laborer — the whole crew combined.
    for name in ("Brayan", "Elmer", "Wilmer"):
        assert f"<b>{name}</b>" in body
    assert body.count("<b>") == 3


def test_build_body_groups_multiple_lines_per_worker():
    svc = ContractLaborReviewNotificationService()
    lines = [
        _row(1, "Mac", 5.0, True, False, "HA"),
        _row(1, "Mac", 3.0, True, False, "TB3 punch"),
    ]
    body = svc._build_body(
        project_label="HA", work_date="2026-06-16", lines=lines, pms=[],
    )
    # One worker header, both of the worker's lines under it.
    assert body.count("<b>Mac</b>") == 1
    assert body.count("Hours:") == 2
    # No PMs → no salutation paragraph; body opens straight at the ask.
    assert body.startswith("<p>The following Contract Labor for HA")


def test_build_body_escapes_and_applies_defaults():
    svc = ContractLaborReviewNotificationService()
    lines = [_row(1, "A & B", None, None, None, None)]
    body = svc._build_body(
        project_label="P<1>", work_date="2026-07-15", lines=lines, pms=[],
    )
    assert "A &amp; B" in body          # name HTML-escaped
    assert "P&lt;1&gt;" in body          # project label HTML-escaped
    assert "(no description)" in body    # None description default
    assert "Hours: 0.00" in body         # None hours default
    assert "Is Billable: Yes" in body    # None billable → default True
    assert "Is Overhead: No" in body     # None overhead → default False


def test_enqueue_drafts_short_circuits_without_work_date():
    """No work_date → no DB access, no raise (failure-isolated public surface)."""
    svc = ContractLaborReviewNotificationService()
    cl = types.SimpleNamespace(public_id="cl-x", work_date=None)
    svc.enqueue_drafts(contract_labor=cl)  # must return cleanly
