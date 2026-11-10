"""U-652d4 item 12 gap: `enqueued_stranded` is REPORTED under recovery and never re-enqueued; the outbox
EntityType match covers the legacy 'ExpenseCodingItem' as well as 'CodingSuggestion'.

Every test names the mutant it kills. Nothing opens a DB connection: the repo and outbox are mocked."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.service import CodingSuggestionService
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
OUTBOX = "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
P1, P2, P3 = ("11111111-1111-1111-1111-11111111111%d" % i for i in (1, 2, 3))


def _body(name):
    return strip_sql_comments(sproc_body(SQL, name))


# ---- sproc text pins ---------------------------------------------------------------------------------

def test_recovery_proc_has_the_enqueued_stranded_category_with_the_live_outbox_guard():
    # kills: the category missing, or a live-row guard that drops the pending/failed/in_progress statuses
    body = _body("ReadCodingSuggestionRecoveryCandidates")
    assert "N'enqueued_stranded'" in body
    assert "cs.[Status] = N'enqueued'" in body
    assert "NOT EXISTS" in body
    assert "N'pending', N'failed', N'in_progress'" in body
    assert "[qbo].[Outbox]" in body


def test_recovery_proc_enqueued_cutoff_is_a_null_safe_defaulted_param():
    # kills: a cut-off param without a default (breaks every existing caller) or a non-NULL-safe guard
    body = _body("ReadCodingSuggestionRecoveryCandidates")
    assert "@EnqueuedOlderThanSeconds INT = 300" in body
    assert "cs.[ModifiedDatetime] <= DATEADD(SECOND, -@EnqueuedOlderThanSeconds, SYSUTCDATETIME())" in body


@pytest.mark.parametrize("name", ["ReadStrandedConfirmedCodingSuggestions", "ReadCodingSuggestionRecoveryCandidates"])
def test_outbox_entity_type_match_is_coding_suggestion_only(name):
    # kills: widening the outbox EntityType to the legacy 'ExpenseCodingItem' (EM decision 2026-11-10: prod
    # has no live or dead-lettered legacy rows, so widening buys nothing and breaks the U-652 rule)
    body = _body(name)
    assert "N'CodingSuggestion'" in body
    assert "N'ExpenseCodingItem'" not in body


def test_stranded_confirmed_proc_live_and_dead_letter_checks_both_use_coding_suggestion():
    # kills: a live check or a dead-letter check drifting to another EntityType value
    body = _body("ReadStrandedConfirmedCodingSuggestions")
    assert body.count("N'CodingSuggestion'") == 2  # the HasDeadLetter EXISTS and the NOT EXISTS live guard
    assert "N'ExpenseCodingItem'" not in body


# ---- service: report, never enqueue ----------------------------------------------------------------

def _cand(pid, seq=4):
    return {"public_id": pid, "realm_id": "realm-1", "confirmation_seq": seq, "has_dead_letter": False}


def _svc(recovery_rows, cands=()):
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_stranded_confirmed.return_value = list(cands)
    svc.repo.read_recovery_candidates.return_value = list(recovery_rows)
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(status="confirmed", confirmation_seq=4,
                                                                   realm_id="realm-1"))
    svc._enqueue_recode_and_mark = MagicMock(return_value=True)
    return svc


@patch(GATE, return_value=None)
def test_enqueued_stranded_is_listed_under_recovery_and_never_enqueued(_gate):
    # kills: enqueuing an enqueued_stranded row (a re-send of a write QBO may already have applied), or
    # dropping the category from recovery
    rows = [{"public_id": P3, "category": "enqueued_stranded", "realm_id": "realm-1",
             "confirmation_seq": 2, "write_error": None}]
    svc = _svc(rows)
    out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["recovery"]["enqueued_stranded"] == [P3]
    svc._enqueue_recode_and_mark.assert_not_called()
    assert out["enqueued"] == 0 and out["would_enqueue"] == []


@patch(GATE, return_value=None)
def test_enqueued_stranded_is_listed_in_dry_run_too_and_does_not_touch_would_enqueue(_gate):
    # kills: a dry run that treats the category as enqueueable
    rows = [{"public_id": P3, "category": "enqueued_stranded", "realm_id": "realm-1",
             "confirmation_seq": 2, "write_error": None}]
    out = _svc(rows).sweep_stranded_confirmed(dry_run=True)
    assert out["recovery"]["enqueued_stranded"] == [P3]
    assert out["would_enqueue"] == []


@patch(GATE, return_value=None)
def test_enqueued_cutoff_is_passed_through_to_the_repo(_gate):
    # kills: the cut-off hard-coded in the service instead of threaded to the proc
    svc = _svc([])
    svc.sweep_stranded_confirmed(dry_run=True, enqueued_older_than_seconds=900)
    kwargs = svc.repo.read_recovery_candidates.call_args.kwargs
    assert kwargs.get("enqueued_older_than_seconds") == 900


# ---- service: dead letter lookup covers the legacy entity type --------------------------------------

def test_has_recode_dead_letter_probes_coding_suggestion_only():
    # kills: a dead-letter probe on any entity type other than the one confirm enqueues with
    svc = CodingSuggestionService()
    repo = MagicMock()
    repo.read_dead_letter_by_entity.return_value = SimpleNamespace(public_id="dl")
    with patch(OUTBOX, return_value=SimpleNamespace(repo=repo)):
        assert svc._has_recode_dead_letter(P1) is True
    types = {c.kwargs["entity_type"] for c in repo.read_dead_letter_by_entity.call_args_list}
    assert types == {"CodingSuggestion"}


def test_recovery_proc_limits_each_arm_so_no_category_crowds_out_another():
    # kills: one shared TOP over the UNION, letting enqueued_stranded rows crowd out error_repoint /
    # written_without_record. Each of the three arms carries its own TOP (@Limit) and its own ORDER BY.
    body = _body("ReadCodingSuggestionRecoveryCandidates")
    assert body.count("TOP (@Limit)") == 3
    assert body.count("ORDER BY cs.[ModifiedDatetime]") == 3
