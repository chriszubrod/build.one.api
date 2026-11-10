"""U-652d4 item 12: a dead letter set by ReclaimStrandedQboOutbox (a SQL-side flip) records a
ReconciliationIssue through the SAME helper the worker's own dead-letter escalation uses.

Every test names the mutant it kills. The outbox repo and the reconciliation repo are mocked."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker

ISSUE_REPO = "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository"
PUBLIC = "22222222-2222-2222-2222-222222222222"
REALM = "realm-9"
ENTITY = "33333333-3333-3333-3333-333333333333"


def _reclaimed(status):
    return {"id": 7, "public_id": PUBLIC, "status": status, "attempts": 5, "kind": "recode_purchase_line",
            "entity_type": "CodingSuggestion", "entity_public_id": ENTITY, "started_at": None}


def _worker(rows):
    repo = MagicMock()
    repo.reclaim_stranded.return_value = rows
    repo.read_by_id.return_value = SimpleNamespace(realm_id=REALM)
    return QboOutboxWorker(repo=repo, api_budget=MagicMock())


def test_reclaim_dead_letter_records_a_reconciliation_issue_with_the_escalation_shape():
    # kills: a reclaim dead letter that is only logged (the escalation the worker's own path makes is absent)
    worker = _worker([_reclaimed("dead_letter")])
    with patch(ISSUE_REPO) as issue_repo:
        worker._reclaim_stranded_rows()
    issue_repo.return_value.create.assert_called_once()
    kwargs = issue_repo.return_value.create.call_args.kwargs
    assert kwargs["drift_type"] == "recode_purchase_line_dead_letter"
    assert kwargs["severity"] == "high"
    assert kwargs["action"] == "flagged"
    assert kwargs["entity_type"] == "CodingSuggestion"
    assert kwargs["entity_public_id"] == ENTITY
    assert kwargs["realm_id"] == REALM  # the row's realm, from read_by_id
    assert PUBLIC in kwargs["details"]


def test_reclaim_to_failed_is_not_escalated():
    # kills: escalating every reclaimed row, not only the dead-letter outcome (a retry is not an issue)
    worker = _worker([_reclaimed("failed")])
    with patch(ISSUE_REPO) as issue_repo:
        worker._reclaim_stranded_rows()
    issue_repo.return_value.create.assert_not_called()


def test_escalation_failure_does_not_abort_the_reclaim_loop():
    # kills: an escalation exception escaping the reclaim (the second row would never be processed)
    worker = _worker([_reclaimed("dead_letter"), dict(_reclaimed("dead_letter"), public_id="other")])
    with patch(ISSUE_REPO) as issue_repo:
        issue_repo.return_value.create.side_effect = [RuntimeError("db down"), SimpleNamespace()]
        worker._reclaim_stranded_rows()  # must not raise
    assert issue_repo.return_value.create.call_count == 2


def test_realm_read_failure_is_isolated_and_records_nothing():
    # kills: a read error escaping the reclaim loop, or an issue recorded with no realm
    worker = _worker([_reclaimed("dead_letter"), dict(_reclaimed("dead_letter"), public_id="other")])
    worker.repo.read_by_id.side_effect = [RuntimeError("db down"), SimpleNamespace(realm_id=REALM)]
    with patch(ISSUE_REPO) as issue_repo:
        worker._reclaim_stranded_rows()  # must not raise
    assert issue_repo.return_value.create.call_count == 1  # the second row still records
