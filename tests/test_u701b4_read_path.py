"""U-701b4 (API): suggest read path - decision_source, supersedes, similar_bills, pending."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from entities.coding_suggestion.business.bill_suggestion_service import (
    BillSuggestion,
    apply_bill_text_tier_to_suggestion,
    compute_text_tier_status,
)
from entities.coding_suggestion.business.presenters import suggestion_to_api_payload
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillLatestTextExtractionState,
)

EXTRACTED = datetime(2026, 10, 28, 12, 0, 0)
PUB = {
    501: "11111111-1111-1111-1111-000000000501",
    502: "22222222-2222-2222-2222-000000000502",
}


def _weak_history():
    return BillSuggestion(
        project_id=None, sub_cost_code_id=None, sub_cost_code_public_id=None,
        sub_cost_code_number=None, sub_cost_code_name=None, source="none", reason=None,
        confidence=None, raw_score=None, top_count=None, second_count=None, total_count=None,
    )


def _strong_history():
    return BillSuggestion(
        project_id=100, sub_cost_code_id=501, sub_cost_code_public_id=PUB[501],
        sub_cost_code_number="05.100", sub_cost_code_name="Framing",
        source="vendor_project_history", reason="h", confidence=Decimal("0.9"),
        raw_score=Decimal("0.9"), top_count=9, second_count=0, total_count=10,
    )


def _row(**kw):
    base = dict(
        bill_id=1, attachment_id=5, extracted_datetime=EXTRACTED, status="done",
        sub_cost_code_id=502, project_id=None, confidence=Decimal("0.81"), mode="A",
        winning_model="claude-sonnet", model_version="u701b-text-v2", failure_reason=None,
        decision_source="escalate", primary_sub_cost_code_id=501, primary_confidence=Decimal("0.5"),
        escalate_sub_cost_code_id=502, escalate_confidence=Decimal("0.81"), escalate_error=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _extraction(incomplete=False):
    return BillLatestTextExtractionState(
        latest_completed_attachment_id=5,
        latest_completed_extracted_datetime=EXTRACTED,
        has_incomplete_extraction=incomplete,
    )


def _repo(row):
    repo = MagicMock()
    repo.read_by_bill_id.return_value = row
    repo.read_latest_text_extraction_state.return_value = _extraction()
    return repo


def _scc_svc():
    svc = MagicMock()
    svc.read_by_id.side_effect = lambda i: SimpleNamespace(
        id=i, public_id=PUB[i], number=f"0{i}", name=f"SCC {i}"
    )
    svc.is_holding_code.return_value = False
    return svc


def _apply(row, *, v2=True, intake=None, history=None):
    intake_svc = MagicMock()
    intake_svc.read_by_bill_id.return_value = intake
    out, status = apply_bill_text_tier_to_suggestion(
        history or _weak_history(),
        bill_id=1,
        request_project_id=100,
        text_repo=_repo(row),
        sub_cost_code_service=_scc_svc(),
        project_service=MagicMock(),
        text_enabled=True,
        ladder_v2_enabled=v2,
        intake_service=intake_svc,
    )
    return out, status, intake_svc


def _payload(s):
    return suggestion_to_api_payload(s, project_service_factory=MagicMock)


def test_escalate_differing_code_carries_supersedes_and_decision_source():
    out, status, _ = _apply(_row())
    assert status == "ready"
    p = _payload(out)
    assert p["sub_cost_code_public_id"] == PUB[502]
    assert p["supersedes_sub_cost_code_public_id"] == PUB[501]
    assert p["decision_source"] == "escalate"
    assert p["source"] == "llm"


def test_escalate_same_code_has_no_supersedes():
    out, _, _ = _apply(_row(primary_sub_cost_code_id=502))
    p = _payload(out)
    assert "supersedes_sub_cost_code_public_id" not in p
    assert p["decision_source"] == "escalate"


def test_not_escalate_has_no_supersedes():
    # the row's code (502) differs from primary_sub_cost_code_id (501), yet no supersede
    out, _, _ = _apply(_row(decision_source="primary"))
    p = _payload(out)
    assert "supersedes_sub_cost_code_public_id" not in p
    assert p["decision_source"] == "primary"


def test_flag_off_emits_no_new_fields():
    out, status, intake_svc = _apply(_row(), v2=False)
    assert status == "ready"
    p = _payload(out)
    assert "supersedes_sub_cost_code_public_id" not in p
    assert "decision_source" not in p
    intake_svc.read_by_bill_id.assert_not_called()


def test_primary_falls_back_to_the_intake_row():
    intake = SimpleNamespace(attachment_id=5, coding_escalation=None, primary_sub_cost_code_id=501)
    out, _, _ = _apply(_row(primary_sub_cost_code_id=None), intake=intake)
    assert _payload(out)["supersedes_sub_cost_code_public_id"] == PUB[501]


def test_similar_vote_presented_as_similar_bills():
    out, _, _ = _apply(_row(decision_source="similar_vote", sub_cost_code_id=501))
    p = _payload(out)
    assert p["source"] == "similar_bills"
    assert p["decision_source"] == "similar_vote"
    assert "supersedes_sub_cost_code_public_id" not in p


def test_strong_history_reports_decision_source_history_only_when_v2():
    out, _, _ = _apply(_row(), history=_strong_history())
    assert _payload(out)["decision_source"] == "history"
    out, _, _ = _apply(_row(), v2=False, history=_strong_history())
    assert "decision_source" not in _payload(out)


def test_text_status_pending_while_escalation_pending():
    intake = SimpleNamespace(attachment_id=5, coding_escalation="low_confidence", primary_sub_cost_code_id=501)
    out, status, _ = _apply(None, intake=intake)
    assert status == "pending"
    # flag OFF: same input is "none" (today's status)
    _, status_off, _ = _apply(None, v2=False, intake=intake)
    assert status_off == "none"
    # a pending tick row for the current extraction stays pending
    _, status_p, _ = _apply(_row(status="pending"), intake=intake)
    assert status_p == "pending"


def test_terminal_row_for_current_extraction_ends_pending():
    intake = SimpleNamespace(attachment_id=5, coding_escalation="low_confidence", primary_sub_cost_code_id=501)
    _, status, _ = _apply(_row(status="done", sub_cost_code_id=None), intake=intake)
    assert status == "none"


def test_escalation_for_an_older_attachment_is_not_pending():
    intake = SimpleNamespace(attachment_id=4, coding_escalation="low_confidence", primary_sub_cost_code_id=501)
    _, status, _ = _apply(None, intake=intake)
    assert status == "none"


def test_compute_text_tier_status_escalation_flag():
    assert compute_text_tier_status(
        history_blocked=False, text_row=None, extraction=_extraction(), escalation_pending=True
    ) == "pending"
    assert compute_text_tier_status(
        history_blocked=False, text_row=None, extraction=_extraction()
    ) == "none"
    assert compute_text_tier_status(
        history_blocked=True, text_row=None, extraction=None, escalation_pending=True
    ) == "none"
