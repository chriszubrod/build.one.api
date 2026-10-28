"""U-701b4 (API): PDF drop runs ONE drop-stage ladder decision (flag ON); flag OFF unchanged."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import read_attachment_service as ras
from entities.coding_suggestion.business.bill_coding_decision import (
    BillCodingDecision,
    RungAnswer,
)
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from intelligence.cascade.core import CascadeResult, Rung
from shared.authz import current_company_id, current_user_id

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROJ_A_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PROJ_B_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SCC_PUB = {
    501: "11111111-1111-1111-1111-000000000501",
    502: "22222222-2222-2222-2222-000000000502",
    503: "33333333-3333-3333-3333-000000000503",
    999: "99999999-9999-9999-9999-000000000999",
}
LADDER = "entities.coding_suggestion.business.bill_text_suggestion_service.run_bill_coding_decision"


def _header_cascade():
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "vendor_name": "Acme Supply",
            "bill_number": "INV-1",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "description": "Materials",
            "confidence": 0.9,
        },
        confidence=0.9,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )


def _decision(**kw):
    base = dict(
        source="primary",
        mode="A",
        sub_cost_code_id=502,
        project_id=None,
        confidence=Decimal("0.55"),
        model="claude-haiku-5-5",
        primary=RungAnswer("claude-haiku-5-5", 502, None, Decimal("0.55")),
        escalate=None,
        fallback=None,
        similar_vote_scc_id=None,
        similar_vote_confidence=None,
        similar_neighbour_count=0,
        escalation="low_confidence",
        stopped_reason=None,
        cascades=(),
        history=None,
    )
    base.update(kw)
    return BillCodingDecision(**base)


def _history(*, strong: bool, project_id=100):
    return BillSuggestion(
        project_id=project_id,
        sub_cost_code_id=501 if strong else None,
        sub_cost_code_public_id=SCC_PUB[501] if strong else None,
        sub_cost_code_number="05.100" if strong else None,
        sub_cost_code_name="Framing" if strong else None,
        source="vendor_project_history" if strong else "none",
        reason="history" if strong else None,
        confidence=Decimal("0.85") if strong else None,
        raw_score=Decimal("0.85") if strong else Decimal("0.10"),
        top_count=5,
        second_count=1,
        total_count=6,
    )


def _suggester(history=None, *, holding=frozenset()):
    sug = MagicMock()
    sug.suggest.return_value = history or _history(strong=False)
    sug.project_service = MagicMock()
    scc_svc = MagicMock()
    scc_svc.read_by_id.side_effect = lambda i: SimpleNamespace(
        id=i, public_id=SCC_PUB[i], number=f"0{i}", name=f"SCC {i}"
    )
    scc_svc.is_holding_code.side_effect = lambda i: i in holding
    sug.sub_cost_code_service = scc_svc
    return sug


@pytest.fixture
def settings():
    with patch.object(ras.config, "Settings") as mock_settings:
        s = MagicMock()
        s.bill_drop_prefill_enabled = True
        s.bill_drop_prefill_timeout_seconds = 25
        s.bill_coding_ladder_v2_enabled = True
        mock_settings.return_value = s
        yield s


@pytest.fixture(autouse=True)
def _pipeline(settings):
    from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

    att = SimpleNamespace(id=1, public_id=ATT_UUID)
    with patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "run_bill_header_extraction", new=AsyncMock(return_value=_header_cascade())
    ), patch.object(
        ras, "resolve_vendor_match_from_name",
        return_value=VendorResolveMatch(public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10),
    ), patch.object(
        ras, "_resolve_chosen_project_id", new=AsyncMock(return_value=100)
    ), patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ), patch.object(
        ras, "resolve_accessible_project_ids", return_value=frozenset({100, 300})
    ), patch(
        "shared.access.accessible_project_ids_for_user",
        side_effect=AssertionError("drop scope must be the REQUEST user's"),
    ), patch.object(ras, "load_catalog_from_db", return_value=MagicMock()), patch.object(
        ras, "_schedule_record_read"
    ) as record, patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PS:
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "invoice body",
        }
        PS.return_value.read_by_id.side_effect = lambda pid: SimpleNamespace(
            id=pid, public_id={100: PROJ_A_UUID, 300: PROJ_B_UUID}.get(pid), name=f"P{pid}"
        )
        yield SimpleNamespace(record=record)


def _run(*, suggester, project=True):
    uid_tok = current_user_id.set(42)
    cid_tok = current_company_id.set(7)
    try:
        return asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                project_public_id=PROJ_A_UUID if project else None,
                suggester=suggester,
            )
        )
    finally:
        current_user_id.reset(uid_tok)
        current_company_id.reset(cid_tok)


def test_mode_a_strong_history_never_calls_ladder(_pipeline):
    sug = _suggester(_history(strong=True))
    with patch(LADDER, new=AsyncMock(), create=True) as ladder:
        out = _run(suggester=sug)
    ladder.assert_not_awaited()
    assert out["coding"]["source"] == "vendor_project_history"
    assert out["coding"]["sub_cost_code_public_id"] == SCC_PUB[501]
    assert "coding_decision" not in _pipeline.record.call_args.kwargs


def test_mode_a_weak_history_low_confidence_haiku_is_the_prefill(_pipeline):
    decision = _decision()
    sug = _suggester()
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True) as ladder, patch.object(
        ras, "predict_text_coding", new=AsyncMock(side_effect=AssertionError("v1 path"))
    ):
        out = _run(suggester=sug)
    ladder.assert_awaited_once()
    kw = ladder.await_args.kwargs
    assert kw["stage"] == "drop"
    assert kw["bill_text"] == "invoice body"
    assert kw["chosen_project_id"] == 100
    assert kw["vendor_id"] == 10
    assert kw["vendor_name"] == "Acme"
    assert kw["amount"] == Decimal("100.00")  # the read's total
    assert kw["exclude_bill_id"] is None
    assert out["status"] == "ok"
    assert out["coding"]["source"] == "llm"
    assert out["coding"]["sub_cost_code_public_id"] == SCC_PUB[502]
    assert out["coding"]["confidence"] == "0.55"
    assert out["coding"]["project_public_id"] == PROJ_A_UUID
    assert out["coding"]["text_status"] == "ready"
    assert out["coding"]["decision_source"] == "primary"
    rec = _pipeline.record.call_args.kwargs
    assert rec["coding_decision"] is decision
    assert rec["suggestion"].sub_cost_code_id == 502


def test_scope_is_request_users_resolved_projects_and_context(_pipeline):
    sug = _suggester()
    with patch(LADDER, new=AsyncMock(return_value=_decision()), create=True) as ladder:
        _run(suggester=sug)
    kw = ladder.await_args.kwargs
    assert kw["accessible_project_ids"] == frozenset({100, 300})
    assert kw["company_id"] == 7
    assert kw["actor_user_id"] == 42


def test_mode_b_project_comes_from_the_decision(_pipeline):
    decision = _decision(mode="B", project_id=300, confidence=Decimal("0.82"), escalation=None)
    sug = _suggester()
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True) as ladder:
        out = _run(suggester=sug, project=False)
    assert ladder.await_args.kwargs["chosen_project_id"] is None
    sug.suggest.assert_not_called()  # the ladder owns Mode B history
    assert out["coding"]["project_public_id"] == PROJ_B_UUID
    assert out["coding"]["sub_cost_code_public_id"] == SCC_PUB[502]


def test_claude_unavailable_foundry_answer_used(_pipeline):
    decision = _decision(
        source="fallback",
        sub_cost_code_id=503,
        confidence=Decimal("0.91"),
        model="foundry-x",
        primary=None,
        escalation="claude_unavailable",
        stopped_reason="daily_cap",
    )
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True):
        out = _run(suggester=_suggester())
    assert out["status"] == "ok"
    assert out["coding"]["source"] == "llm"
    assert out["coding"]["sub_cost_code_public_id"] == SCC_PUB[503]


def test_claude_unavailable_similar_vote_used_as_similar_bills(_pipeline):
    decision = _decision(
        source="similar_vote",
        sub_cost_code_id=503,
        confidence=Decimal("0.40"),
        model=None,
        primary=None,
        similar_vote_scc_id=503,
        similar_vote_confidence=Decimal("0.40"),
        escalation="claude_unavailable",
    )
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True):
        out = _run(suggester=_suggester())
    assert out["coding"]["source"] == "similar_bills"
    assert out["coding"]["sub_cost_code_public_id"] == SCC_PUB[503]
    assert out["coding"]["project_public_id"] == PROJ_A_UUID


def test_source_none_means_no_coding(_pipeline):
    decision = _decision(source="none", sub_cost_code_id=None, confidence=None, primary=None)
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True):
        out = _run(suggester=_suggester())
    assert out["coding"] is None
    assert out["status"] == "ok"
    assert _pipeline.record.call_args.kwargs["coding_decision"] is decision


def test_suspense_code_is_never_returned(_pipeline):
    decision = _decision(sub_cost_code_id=999)
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True):
        out = _run(suggester=_suggester(holding=frozenset({999})))
    assert out["coding"] is None


def test_ladder_error_is_partial_without_coding(_pipeline):
    with patch(LADDER, new=AsyncMock(side_effect=RuntimeError("boom")), create=True):
        out = _run(suggester=_suggester())
    assert out["status"] == "partial"
    assert out["coding"] is None
    assert "coding_decision" not in _pipeline.record.call_args.kwargs


def test_flag_off_runs_v1_text_path_and_never_the_ladder(_pipeline, settings):
    settings.bill_coding_ladder_v2_enabled = False
    text = BillSuggestion(
        project_id=100, sub_cost_code_id=502, sub_cost_code_public_id=SCC_PUB[502],
        sub_cost_code_number="06.200", sub_cost_code_name="x", source="llm", reason="r",
        confidence=Decimal("0.88"), raw_score=None, top_count=None, second_count=None,
        total_count=None,
    )
    with patch(LADDER, new=AsyncMock(), create=True) as ladder, patch.object(
        ras, "predict_text_coding", new=AsyncMock(return_value=(text, None, None))
    ) as predict:
        out = _run(suggester=_suggester())
    ladder.assert_not_awaited()
    predict.assert_awaited_once()
    assert out["coding"]["source"] == "llm"
    assert "decision_source" not in out["coding"]
    assert "coding_decision" not in _pipeline.record.call_args.kwargs


def test_reread_exclude_bill_id_forwarded(_pipeline):
    with patch(LADDER, new=AsyncMock(return_value=_decision()), create=True) as ladder:
        asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                project_public_id=PROJ_A_UUID,
                suggester=_suggester(),
                exclude_bill_id=555,
            )
        )
    assert ladder.await_args.kwargs["exclude_bill_id"] == 555


def test_daily_cap_early_exit_happens_before_any_ladder_call(_pipeline):
    """run_bill_coding_decision does not check the Claude day cap itself (b3 contract)."""
    with patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=True)
    ), patch(LADDER, new=AsyncMock(), create=True) as ladder:
        out = _run(suggester=_suggester())
    ladder.assert_not_awaited()
    assert out["status"] == "ai_unavailable"


def test_header_daily_cap_exits_before_ladder(_pipeline):
    capped = CascadeResult(
        accepted=False, needs_human=True, result=None, confidence=None,
        winning_rung=None, attempts=[], stopped_reason="daily_cap",
    )
    with patch.object(
        ras, "run_bill_header_extraction", new=AsyncMock(return_value=capped)
    ), patch(LADDER, new=AsyncMock(), create=True) as ladder:
        out = _run(suggester=_suggester())
    ladder.assert_not_awaited()
    assert out["status"] == "ai_unavailable"
