"""U-701b4 final fix round: drop history-for-P, coding sub-deadline, reread exclude,
single-answer rule, shared helpers (behaviour-preserving refactors pinned)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import read_attachment_service as ras
from entities.bill.business.reread_attachment_service import reread_draft_bill_attachment
from entities.bill_intake_prediction.business import service as intake_service
from entities.coding_suggestion.business import bill_coding_decision as bcd
from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.business.bill_coding_decision import RungAnswer, choose_final
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.business.similar_bills_service import SimilarBillsService
from tests import test_u701b4_drop_ladder as drop
from tests import test_u704_reread_attachment as rr

LADDER = drop.LADDER
_pipeline = drop._pipeline  # noqa: F401 - autouse drop pipeline fixtures
settings = drop.settings  # noqa: F401


def _hist(scc, *, strong=True, project_id=None, source="vendor_history"):
    return BillSuggestion(
        project_id=project_id, sub_cost_code_id=scc, sub_cost_code_public_id=None,
        sub_cost_code_number=f"0{scc}", sub_cost_code_name="h", source=source,
        reason="vendor-wide history", confidence=Decimal("0.9") if strong else Decimal("0.2"),
        raw_score=Decimal("0.9") if strong else Decimal("0.2"), top_count=9, second_count=0,
        total_count=10,
    )


# ------------------------------------------------------------ 1: history-for-P prefill


def test_mode_b_history_for_p_prefill_uses_the_decisions_code_and_project(_pipeline):
    # vendor strong on 501 ("01-100") for P=300; weak vendor-wide history says 502 ("02-200")
    decision = drop._decision(
        source="history", mode="B", sub_cost_code_id=501, project_id=300,
        confidence=Decimal("0.80"), model=None, escalation=None,
        history=_hist(502, strong=False, project_id=100),
    )
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True):
        out = drop._run(suggester=drop._suggester(), project=False)
    assert out["coding"]["sub_cost_code_public_id"] == drop.SCC_PUB[501]
    assert out["coding"]["project_public_id"] == drop.PROJ_B_UUID
    assert out["coding"]["source"] == "vendor_project_history"
    assert out["coding"]["confidence"] == "0.80"
    assert out["coding"]["decision_source"] == "history"


# ------------------------------------------------------------ 2: catalog + sub-deadline


def test_prefetched_catalog_is_passed_to_the_ladder(_pipeline):
    catalog = object()
    with patch.object(ras, "load_catalog_from_db", return_value=catalog), patch(
        LADDER, new=AsyncMock(return_value=drop._decision()), create=True
    ) as ladder:
        drop._run(suggester=drop._suggester())
    assert ladder.await_args.kwargs["catalog"] is catalog


def test_slow_coding_returns_partial_with_header_and_still_records(_pipeline):
    async def slow(**_kw):
        await asyncio.sleep(5)

    with patch.object(ras, "CODING_DEADLINE_MARGIN_SECONDS", 24.8), patch(
        LADDER, new=AsyncMock(side_effect=slow), create=True
    ):
        out = drop._run(suggester=drop._suggester())
    assert out["status"] == "partial"
    assert out["coding"] is None
    assert out["fields"]["bill_number"]["value"] == "INV-1"
    assert _pipeline.record.called
    assert "coding_decision" not in _pipeline.record.call_args.kwargs


def test_no_coding_budget_left_skips_the_ladder(_pipeline):
    with patch.object(ras, "CODING_DEADLINE_MARGIN_SECONDS", 1000.0), patch(
        LADDER, new=AsyncMock(return_value=drop._decision()), create=True
    ) as ladder:
        out = drop._run(suggester=drop._suggester())
    ladder.assert_not_awaited()
    assert out["status"] == "partial" and out["fields"]["bill_number"]["value"] == "INV-1"


# ------------------------------------------------------------ 3: reread exclude


def test_reread_passes_its_bill_id_as_exclude():
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = rr._stacked_services(
        bill=rr._bill(bill_id=100),
        lines=[rr._line(line_id=5, public_id=rr.LINE1_UUID)],
        links_by_line_public_id={rr.LINE1_UUID: SimpleNamespace(attachment_id=2)},
        attachments_by_id={2: rr._attachment(att_id=2, public_id=rr.ATT_UUID)},
        project=SimpleNamespace(public_id=rr.PROJ_UUID),
    )
    read_fn = AsyncMock(return_value=rr._read_ok())
    with patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        asyncio.run(reread_draft_bill_attachment(
            bill_public_id=rr.BILL_UUID, bill_service=bill_svc, line_item_service=bli_svc,
            blia_service=blia_svc, attachment_service=att_svc, project_service=proj_svc,
            read_fn=read_fn,
        ))
    assert read_fn.await_args.kwargs["exclude_bill_id"] == 100


def test_mode_a_history_check_uses_the_ladders_exclude_bill_id(_pipeline):
    sug = drop._suggester()
    with patch(LADDER, new=AsyncMock(return_value=drop._decision()), create=True) as ladder:
        asyncio.run(ras.run_bill_read_attachment(
            attachment_public_id=drop.ATT_UUID, project_public_id=UUID(drop.PROJ_A_UUID),
            suggester=sug, exclude_bill_id=555,
        ))
    assert sug.suggest.call_args.kwargs["exclude_bill_id"] == 555
    assert ladder.await_args.kwargs["exclude_bill_id"] == 555
    # the same history is handed to the ladder (computed with the same exclude)
    assert ladder.await_args.kwargs["history_suggestion"] is sug.suggest.return_value


def test_flag_off_history_check_keeps_no_exclude(_pipeline, settings):
    settings.bill_coding_ladder_v2_enabled = False
    sug = drop._suggester()
    with patch.object(ras, "predict_text_coding", new=AsyncMock(return_value=(None, None, None))):
        asyncio.run(ras.run_bill_read_attachment(
            attachment_public_id=drop.ATT_UUID, project_public_id=UUID(drop.PROJ_A_UUID),
            suggester=sug, exclude_bill_id=555,
        ))
    assert sug.suggest.call_args.kwargs["exclude_bill_id"] is None


# ------------------------------------------------------------ 6: presentation errors


def test_presentation_error_is_partial_without_coding_but_records_decision(_pipeline):
    decision = drop._decision()
    sug = drop._suggester()
    sug.sub_cost_code_service.read_by_id.side_effect = RuntimeError("db")
    with patch(LADDER, new=AsyncMock(return_value=decision), create=True):
        out = drop._run(suggester=sug)
    assert out["status"] == "partial" and out["coding"] is None
    assert _pipeline.record.call_args.kwargs["coding_decision"] is decision


# ------------------------------------------------------------ 8/9: single-answer rule

HAIKU, SONNET, FOUNDRY = "claude-haiku", "claude-sonnet", "foundry"


def _final_b(primary=None, escalate=None, fallback=None, history=None, history_for_p=None):
    return choose_final(
        mode="B", history=history, primary=primary, escalate=escalate, fallback=fallback,
        similar_vote_scc_id=None, similar_vote_confidence=None,
        history_for_project=history_for_p,
    )


def test_sonnet_project_without_code_never_takes_haikus_code():
    pick = _final_b(
        primary=RungAnswer(HAIKU, 1, 501, Decimal("0.6")),
        escalate=RungAnswer(SONNET, None, 502, Decimal("0.9")),
        history_for_p=lambda p: None,
    )
    assert pick[:3] == ("none", None, None)


def test_haiku_project_without_code_never_takes_foundrys_code():
    pick = _final_b(
        primary=RungAnswer(HAIKU, None, 501, Decimal("0.6")),
        fallback=RungAnswer(FOUNDRY, 7, 600, Decimal("0.95")),
        history_for_p=lambda p: None,
    )
    assert pick[:3] == ("none", None, None)


def test_code_and_project_come_from_the_same_answer():
    pick = _final_b(
        primary=RungAnswer(HAIKU, 1, 501, Decimal("0.6")),
        escalate=RungAnswer(SONNET, 2, 502, Decimal("0.9")),
        history_for_p=lambda p: None,
    )
    assert pick[:3] == ("escalate", 2, 502)


def test_below_threshold_foundry_with_a_project_is_not_used():
    pick = _final_b(fallback=RungAnswer(FOUNDRY, 7, 600, Decimal("0.10")),
                    history_for_p=lambda p: _hist(9, project_id=p))
    assert pick[:3] == ("none", None, None)


def test_mode_b_escalate_without_project_falls_back_to_strong_vendor_wide_history():
    pick = _final_b(
        primary=RungAnswer(HAIKU, 1, 501, Decimal("0.6")),
        escalate=RungAnswer(SONNET, 2, None, Decimal("0.9")),
        history=_hist(8, project_id=700),
        history_for_p=lambda p: None,
    )
    assert pick[:3] == ("history", 8, 700)


# ------------------------------------------------------------ 10: run_bill_coding_decision


def _run_decision(**overrides):
    repo = MagicMock()
    repo.read_vendor_scc_counts.return_value = []
    suggester = MagicMock()
    suggester.suggest.return_value = _hist(3, strong=False)
    kwargs = dict(
        stage="drop", bill_text="body", vendor_id=5, vendor_name="V", chosen_project_id=100,
        amount=Decimal("10"), as_of=datetime(2026, 5, 1, 12, 0, 0),
        exclude_bill_id=None, company_id=None, actor_user_id=None,
        accessible_project_ids=None, settings=SimpleNamespace(
            bill_coding_candidate_sccs="full", bill_text_token_hash_key=None),
        repository=repo, suggester=suggester,
    )
    kwargs.update(overrides)
    with patch.object(bts, "decide_bill_coding", new=AsyncMock(side_effect=lambda i, d: (i, d))):
        inputs, deps = asyncio.run(bts.run_bill_coding_decision(**kwargs))
    return inputs, deps, suggester, repo


def test_precomputed_history_and_catalog_are_not_reloaded():
    from tests.test_u701b3_bill_coding_decision import _snapshot

    given = _hist(4)
    with patch.object(bts, "load_catalog_from_db") as loader:
        _inputs, deps, suggester, _repo = _run_decision(
            catalog=_snapshot(), history_suggestion=given
        )
    loader.assert_not_called()
    suggester.suggest.assert_not_called()
    assert deps.history_suggestion is given
    assert deps.candidate_sccs == deps.catalog.as_of_catalog


def test_history_computed_when_not_given():
    from tests.test_u701b3_bill_coding_decision import _snapshot

    _inputs, deps, suggester, _repo = _run_decision(catalog=_snapshot())
    assert suggester.suggest.call_args.kwargs["project_id"] == 100
    assert deps.history_suggestion is suggester.suggest.return_value


# ------------------------------------------------------------ 12/13/14: refactors


def test_rung_columns_shape():
    answer = RungAnswer("m", 1, 2, Decimal("0.5"), "err")
    assert bcd.rung_columns("Primary", answer) == {
        "PrimaryModel": "m", "PrimarySubCostCodeId": 1, "PrimaryProjectId": 2,
        "PrimaryConfidence": Decimal("0.5"), "PrimaryError": "err",
    }
    assert bcd.rung_columns("Fallback", None, ("Model", "SubCostCodeId", "Confidence")) == {
        "FallbackModel": None, "FallbackSubCostCodeId": None, "FallbackConfidence": None,
    }


def test_tick_decision_for_intake_table():
    base = MagicMock(
        status="ok", pred_sub_cost_code_id=1, pred_vendor_id=5, extracted_datetime=None,
        coding_mode="A", coding_chosen_project_id=100, primary_sub_cost_code_id=1,
        coding_escalation=None,
    )

    def decide(version, v2, escalation=None):
        base.model_version = version
        base.coding_escalation = escalation
        return intake_service.tick_decision_for_intake(
            base, extracted_datetime=None, chosen_project_id=100, vendor_id=5,
            ladder_v2_enabled=v2,
        )

    v1, v2 = intake_service.MODEL_VERSION, intake_service.MODEL_VERSION_V2
    assert decide(v1, False) == "reuse"
    assert decide(v2, False) == "full"
    assert decide("other", False) == "full"
    assert decide(v1, True) == "full"
    assert decide(v2, True) == "reuse"
    assert decide(v2, True, "low_confidence") == "escalate"
    assert decide(v2, True, "claude_unavailable") == "full"


def test_similar_service_disabled_vs_zero_hits():
    svc = SimilarBillsService.__new__(SimilarBillsService)
    kw = dict(bill_text_hashes=frozenset({1}), amount=Decimal("1"), as_of=None)
    with patch.object(SimilarBillsService, "_scored_neighbours", return_value=None):
        assert svc.similar_bills_for(**kw) is None
        assert svc.neighbours_for(**kw) == []
    with patch.object(SimilarBillsService, "_scored_neighbours", return_value=[]):
        result = svc.similar_bills_for(**kw)
        assert result is not None and result.neighbours == ()
        assert svc.neighbours_for(**kw) == []
