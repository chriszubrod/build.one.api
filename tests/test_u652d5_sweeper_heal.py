"""U-652d5 — the stranded sweeper heals residual CodingSuggestion.ExpenseIds on apply runs only.

The heal is DB-only, so it runs AFTER the zero-write gate (gate on + apply = no writes at all) and is
failure-isolated: a heal error must not break the re-enqueue sweep.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.coding_suggestion.business.service import CodingSuggestionService

GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
LOCK = "entities.coding_suggestion.business.service.coding_suggestion_lock"


@contextmanager
def _granted(*_a, **_k):
    yield True


def _svc(*, heal=None, heal_error=None):
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_stranded_confirmed.return_value = [
        {"public_id": "pub-1", "confirmation_seq": 1, "realm_id": "realm-1", "has_dead_letter": False},
    ]
    svc.repo.read_recovery_candidates.return_value = []
    if heal_error is not None:
        svc.repo.heal_expense_ids.side_effect = heal_error
    else:
        svc.repo.heal_expense_ids.return_value = heal if heal is not None else 3
    item = SimpleNamespace(status="confirmed", confirmation_seq=1, realm_id="realm-1")
    svc.read_by_public_id = MagicMock(return_value=item)
    svc._enqueue_recode_and_mark = MagicMock(return_value=True)
    svc._log_sweep = MagicMock()
    return svc


def test_apply_with_gate_open_heals_and_reports_the_count():
    svc = _svc(heal=3)
    with patch(GATE, return_value=None), patch(LOCK, _granted):
        result = svc.sweep_stranded_confirmed(dry_run=False)
    svc.repo.heal_expense_ids.assert_called_once_with()
    assert result["expense_id_healed"] == 3
    assert result["enqueued"] == 1


def test_gate_closed_apply_is_zero_writes_and_never_heals():
    svc = _svc()
    with patch(GATE, return_value="qbo_writes_disabled"), patch(LOCK, _granted):
        result = svc.sweep_stranded_confirmed(dry_run=False)
    svc.repo.heal_expense_ids.assert_not_called()
    assert result["status"] == "skipped"
    assert "expense_id_healed" not in result


def test_dry_run_never_heals():
    svc = _svc()
    with patch(GATE, return_value=None), patch(LOCK, _granted):
        result = svc.sweep_stranded_confirmed(dry_run=True)
    svc.repo.heal_expense_ids.assert_not_called()
    assert result["would_enqueue"] == ["pub-1"]
    assert "expense_id_healed" not in result


def test_a_heal_failure_is_isolated_and_the_sweep_still_enqueues():
    svc = _svc(heal_error=RuntimeError("boom"))
    with patch(GATE, return_value=None), patch(LOCK, _granted):
        result = svc.sweep_stranded_confirmed(dry_run=False)
    assert result["expense_id_healed"] is None
    assert result["expense_id_heal_error"] == "RuntimeError"
    assert result["enqueued"] == 1
    assert result["errors"] == []
