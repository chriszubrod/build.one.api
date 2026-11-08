"""U-551b-f1 f4: a QBO outbox dead-letter records a qbo.ReconciliationIssue.

Mirrors the MS worker's escalation. No test here opens a DB connection: the
reconciliation repository and the outbox repository are both mocked.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import QboServerError, QboValidationError
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import MAX_ATTEMPTS, QboOutboxWorker
from shared.lifecycle.completion_gate import HoldingCodeRefused

REPO_PATH = "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository"
class _DbDown(Exception):
    """A distinct failure class: pins the BROAD `except Exception` around the escalation (a mutant
    narrowing it to RuntimeError must fail)."""

ENTITY_ID = "33333333-3333-3333-3333-333333333333"
REALM = "realm-f4"
OUTBOX_PID = "outbox-f4-1"


def _row(kind="sync_bill_to_qbo", entity_type="Bill", attempts=0):
    return QboOutbox(
        id=7, public_id=OUTBOX_PID, row_version="rv", kind=kind, entity_type=entity_type,
        entity_public_id=ENTITY_ID, realm_id=REALM, request_id="req", status="in_progress",
        attempts=attempts,
    )


def _worker():
    repo = MagicMock()
    return QboOutboxWorker(repo=repo, api_budget=MagicMock()), repo


def _fire_unknown_kind(w, row):
    row.kind = "mystery_kind"
    w._process_inner(row)


def _fire_non_retryable(w, row):
    w._handle_qbo_error(row, QboValidationError("bad payload"))


def _fire_holding_code(w, row):
    w._handle_unexpected_error(row, HoldingCodeRefused("still on 00.02"))


def _fire_unexpected_value_error(w, row):
    w._handle_unexpected_error(row, ValueError("permanent data issue"))


def _fire_retries_exhausted(w, row):
    row.attempts = MAX_ATTEMPTS - 1
    w._handle_qbo_error(row, QboServerError("boom 500"))


SITES = [
    ("unknown_kind", _fire_unknown_kind, "Unknown outbox kind"),
    ("non_retryable", _fire_non_retryable, "bad payload"),
    ("holding_code", _fire_holding_code, "still on 00.02"),
    ("unexpected_value_error", _fire_unexpected_value_error, "permanent data issue"),
    ("retries_exhausted", _fire_retries_exhausted, "boom 500"),
]


@pytest.mark.parametrize("name,fire,needle", SITES, ids=[s[0] for s in SITES])
def test_each_dead_letter_site_creates_exactly_one_flagged_issue(name, fire, needle):
    """Kills: no escalation; any per-field mapping mutant (drift_type, severity, action,
    entity_type, entity_public_id, realm_id, qbo_id, details content)."""
    w, repo = _worker()
    row = _row()
    with patch(REPO_PATH) as issue_cls:
        fire(w, row)
    repo.mark_dead_letter.assert_called_once()
    issue_cls.return_value.create.assert_called_once()
    kw = issue_cls.return_value.create.call_args.kwargs
    kind = row.kind
    assert kw["drift_type"] == f"{kind}_dead_letter"
    assert kw["severity"] == "high"
    assert kw["action"] == "flagged"
    assert kw["entity_type"] == "Bill"
    assert kw["entity_public_id"] == ENTITY_ID
    assert kw["realm_id"] == REALM
    assert kw["qbo_id"] is None
    assert kw["reconcile_run_id"] is None
    assert kind in kw["details"]
    assert OUTBOX_PID in kw["details"]
    assert f"after {(row.attempts or 0) + 1} attempts" in kw["details"]
    assert needle in kw["details"]


def test_overlong_drift_type_falls_back_to_outbox_dead_letter():
    """Kills: dropping the 32-char fallback (NVARCHAR(32) overflow) / truncating instead."""
    w, _ = _worker()
    row = _row(kind="recode_purchase_line_with_extras_x")  # +12 chars suffix > 32
    assert len(f"{row.kind}_dead_letter") > 32
    with patch(REPO_PATH) as issue_cls:
        w._dead_letter(row, "err")
    assert issue_cls.return_value.create.call_args.kwargs["drift_type"] == "outbox_dead_letter"


def test_drift_type_of_exactly_32_chars_is_kept():
    """Kills: an off-by-one `<` in the width rule."""
    w, _ = _worker()
    kind = "k" * (32 - len("_dead_letter"))
    row = _row(kind=kind)
    with patch(REPO_PATH) as issue_cls:
        w._dead_letter(row, "err")
    drift = issue_cls.return_value.create.call_args.kwargs["drift_type"]
    assert drift == f"{kind}_dead_letter" and len(drift) == 32


def test_escalation_failure_is_swallowed_and_mark_happened_first(caplog):
    """Kills: escalation before the mark; escalation failure propagating; silent swallow."""
    order = []
    w, repo = _worker()
    repo.mark_dead_letter.side_effect = lambda **kw: order.append("mark")
    with patch(REPO_PATH) as issue_cls:
        issue_cls.return_value.create.side_effect = lambda **kw: (
            order.append("escalate"), (_ for _ in ()).throw(_DbDown("db down")))[1]
        with caplog.at_level(logging.ERROR):
            w._dead_letter(_row(), "err")  # must not raise
    assert order == ["mark", "escalate"]
    assert any(r.getMessage() == "qbo.outbox.dead_letter.escalation_failed"
               and getattr(r, "outbox_public_id", None) == OUTBOX_PID for r in caplog.records)


def test_escalation_not_reached_when_mark_fails():
    """Kills: escalating a row whose dead-letter mark did not become durable."""
    w, repo = _worker()
    repo.mark_dead_letter.side_effect = RuntimeError("mark failed")
    with patch(REPO_PATH) as issue_cls:
        with pytest.raises(RuntimeError):
            w._dead_letter(_row(), "err")
    issue_cls.return_value.create.assert_not_called()


def test_non_final_retry_creates_no_issue():
    """Kills: escalating on every failure rather than only on dead-letter."""
    w, repo = _worker()
    with patch(REPO_PATH) as issue_cls:
        w._handle_qbo_error(_row(attempts=0), QboServerError("boom 500"))
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    issue_cls.return_value.create.assert_not_called()


def test_recode_purchase_line_dead_letter_names_the_coding_suggestion():
    """The f5 operator signal: a stranded recode surfaces against the CodingSuggestion.
    Kills: hardcoding the entity type / dropping the kind from the drift type."""
    w, _ = _worker()
    row = _row(kind="recode_purchase_line", entity_type="CodingSuggestion")
    with patch(REPO_PATH) as issue_cls:
        w._dead_letter(row, "QboValidationError: line gone")
    kw = issue_cls.return_value.create.call_args.kwargs
    assert kw["entity_type"] == "CodingSuggestion"
    assert kw["entity_public_id"] == ENTITY_ID
    assert kw["drift_type"] == "recode_purchase_line_dead_letter"
    assert len(kw["drift_type"]) <= 32
    assert "line gone" in kw["details"]


def test_details_truncated_to_1000_chars():
    """Kills: an unbounded NVARCHAR(MAX) dump of a QBO error body."""
    w, _ = _worker()
    with patch(REPO_PATH) as issue_cls:
        w._dead_letter(_row(), "E" * 5000)
    details = issue_cls.return_value.create.call_args.kwargs["details"]
    assert len(details) == 1000
    assert details.startswith("sync_bill_to_qbo dead-lettered after 1 attempts")


def test_flagged_log_carries_identifiers_but_no_payload(caplog):
    """Kills: dropping the operator log line / leaking the error body into it."""
    w, _ = _worker()
    with patch(REPO_PATH):
        with caplog.at_level(logging.ERROR):
            w._dead_letter(_row(), "SECRET-ERROR-BODY")
    rec = [r for r in caplog.records if r.getMessage() == "qbo.reconcile.dead_letter.flagged"]
    assert len(rec) == 1
    r = rec[0]
    assert (r.drift_type, r.severity, r.kind, r.outbox_public_id, r.entity_type, r.entity_public_id) == (
        "sync_bill_to_qbo_dead_letter", "high", "sync_bill_to_qbo", OUTBOX_PID, "Bill", ENTITY_ID)
    assert "SECRET-ERROR-BODY" not in r.getMessage()
    assert not hasattr(r, "last_error") and not hasattr(r, "details")
