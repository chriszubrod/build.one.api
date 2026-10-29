"""U-701c: run_bill_coding_decision + background tick with Foundry fallback under the Claude cap."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.attachment.business.extraction_service import REDACTION_VERSION
from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import BillCodingActor
from tests.test_u701b3_bill_coding_decision import (
    AS_OF,
    HAIKU,
    SONNET,
    FakeSimilar,
    FakeTransport,
    _claim,
    _err,
    _intake,
    _neighbour,
    _ok,
    _snapshot,
)

CAP = "claude_unavailable:daily_cap"
DEEPSEEK = "DeepSeek-V4-Flash"
NANO = "gpt-5.4-nano"
MINI = "gpt-5.4-mini"
FOUNDRY = (DEEPSEEK, NANO, MINI)


def _capped(**foundry) -> FakeTransport:
    replies = {HAIKU: _err(CAP), SONNET: _err(CAP)}
    for model in FOUNDRY:
        replies[model] = foundry.get(model, _err("http_500"))
    return FakeTransport(replies)


def _vote_neighbours():
    return [_neighbour(11, 7, 0.95), _neighbour(13, 7, 0.93), _neighbour(15, 7, 0.91)]


def _decide(stage: str, transport: FakeTransport, *, similar=None, opt_in: Optional[bool] = True):
    repo = MagicMock()
    repo.read_vendor_scc_counts.return_value = []
    settings = SimpleNamespace(bill_coding_candidate_sccs="full", bill_text_token_hash_key="k" * 40)
    kwargs = dict(
        stage=stage,
        bill_text="invoice body text",
        vendor_id=5,
        vendor_name="Vendor",
        chosen_project_id=100,
        amount=Decimal("125.00"),
        as_of=AS_OF,
        exclude_bill_id=77,
        company_id=1,
        actor_user_id=42,
        accessible_project_ids=None,
        prior_primary=None,
        transport_for=transport.transport_for,
        settings=settings,
        repository=repo,
        suggester=MagicMock(suggest=MagicMock(return_value=None)),
        catalog=_snapshot(),
        similar_service=similar or FakeSimilar(),
    )
    if opt_in is not None:
        kwargs["fallback_on_daily_cap"] = opt_in
    return asyncio.run(bts.run_bill_coding_decision(**kwargs))


def _models(transport: FakeTransport) -> list[str]:
    return [m for m, _ in transport.calls]


# ---------------------------------------------------------------- run_bill_coding_decision


def test_drop_opt_in_codes_by_foundry_and_marks_claude_unavailable():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.6)})
    d = _decide("drop", transport)
    assert DEEPSEEK in _models(transport)
    assert (d.source, d.sub_cost_code_id, d.model) == ("fallback", 2, DEEPSEEK)
    assert d.escalation == "claude_unavailable"
    assert d.stopped_reason is None


def test_drop_opt_in_foundry_below_bar_falls_to_the_vote():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.4)})
    d = _decide("drop", transport, similar=FakeSimilar(_vote_neighbours()))
    assert DEEPSEEK in _models(transport)
    assert (d.source, d.sub_cost_code_id) == ("similar_vote", 7)
    assert d.escalation == "claude_unavailable"


def test_drop_opt_in_neither_foundry_nor_vote_is_blank():
    transport = _capped()
    d = _decide("drop", transport)
    assert set(FOUNDRY) <= set(_models(transport))
    assert (d.source, d.sub_cost_code_id) == ("none", None)
    assert d.stopped_reason == "daily_cap"


def test_tick_full_opt_in_reaches_foundry():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.6)})
    d = _decide("tick_full", transport)
    assert _models(transport) == [HAIKU, SONNET, DEEPSEEK]
    assert d.source == "fallback"


def test_tick_escalate_opt_in_never_reaches_foundry():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    d = _decide("tick_escalate", transport)
    assert _models(transport) == [SONNET]
    assert d.stopped_reason == "daily_cap"


@pytest.mark.parametrize("stage", ["drop", "tick_full"])
@pytest.mark.parametrize("opt_in", [None, False])
def test_default_never_reaches_foundry_under_the_cap(stage, opt_in):
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    d = _decide(stage, transport, opt_in=opt_in)
    assert not set(FOUNDRY) & set(_models(transport))
    assert d.stopped_reason == "daily_cap" and d.source == "none"


def test_unknown_stage_raises_with_opt_in():
    with pytest.raises(ValueError):
        _decide("bogus", _capped())


# ---------------------------------------------------------------- tick


def _run_tick(
    *,
    transport: FakeTransport,
    flag: bool,
    capped: bool = True,
    intake=None,
    claims=None,
    limit: int = 1,
    calls_today: int = 0,
    similar=None,
    snapshot=None,
):
    settings = SimpleNamespace(
        bill_coding_text_enabled=True,
        bill_coding_ladder_v2_enabled=True,
        bill_coding_foundry_on_claude_cap_enabled=flag,
        bill_coding_candidate_sccs="full",
        bill_coding_text_daily_call_cap=10,
        bill_text_token_hash_key="k" * 40,
    )
    repo = MagicMock()
    repo.count_calls_today.return_value = calls_today
    repo.claim_next.side_effect = list(claims if claims is not None else [_claim(), None])
    repo.read_bill_coding_actor.return_value = BillCodingActor(42, False, 3, Decimal("-125.00"))
    repo.read_vendor_scc_counts.return_value = []
    repo.complete.return_value = SimpleNamespace()
    blob = json.dumps({"content": "invoice body", "redaction_version": REDACTION_VERSION})
    stopped = AsyncMock(return_value=capped)
    suggester = MagicMock(suggest=MagicMock(return_value=None))
    catalog_loader = MagicMock(side_effect=lambda _as_of: snapshot or _snapshot())
    with patch.object(bts.config, "Settings", return_value=settings), patch.object(
        bts, "foundry_is_configured", return_value=True
    ), patch.object(bts.claude_budget, "is_claude_day_stopped_async", new=stopped), patch.object(
        bts, "history_eligible_for_text", return_value=(True, None)
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService.read_by_attachment_id",
        return_value=intake,
    ), patch.object(bts, "read_text_blob_with_retry", return_value=blob) as read_blob, patch.object(
        bts, "_read_vendor_name", return_value="Vendor"
    ), patch.object(
        bts, "accessible_project_ids_for_user", return_value=frozenset({501})
    ), patch.object(
        bts.SimilarBillsService, "for_company", return_value=similar or FakeSimilar()
    ):
        out = asyncio.run(bts.run_bill_text_coding_tick(
            limit=limit,
            repo=repo,
            storage=MagicMock(),
            suggester=suggester,
            catalog_loader=catalog_loader,
            transport_for=transport.transport_for,
        ))
    return SimpleNamespace(
        out=out, repo=repo, suggester=suggester, catalog_loader=catalog_loader,
        read_blob=read_blob,
    )


def test_tick_flag_off_capped_returns_before_claiming():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    run = _run_tick(transport=transport, flag=False)
    assert run.out["status"] == "capped"
    run.repo.claim_next.assert_not_called()
    assert transport.calls == []


def test_tick_flag_on_capped_claims_and_codes_tick_full_via_foundry():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    run = _run_tick(transport=transport, flag=True)
    assert run.out["status"] == "done"
    assert _models(transport) == [HAIKU, SONNET, DEEPSEEK]
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"], kw["DecisionSource"]) == ("done", 2, "fallback")
    assert kw["record_call"] is True


def test_tick_flag_on_capped_foundry_below_bar_finalizes_the_vote():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.4)})
    run = _run_tick(transport=transport, flag=True, similar=FakeSimilar(_vote_neighbours()))
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"], kw["DecisionSource"]) == ("done", 7, "similar_vote")


def test_tick_flag_on_capped_nothing_answers_finalizes_none():
    transport = _capped()
    run = _run_tick(transport=transport, flag=True)
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["failure_reason"], kw["DecisionSource"]) == ("failed", "not_accepted", "none")


def test_tick_flag_on_capped_escalate_route_left_pending_without_a_call():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    run = _run_tick(
        transport=transport, flag=True, intake=_intake(coding_escalation="low_confidence"),
    )
    assert run.out["status"] == "capped"
    assert transport.calls == []
    run.repo.complete.assert_not_called()


def test_tick_flag_on_capped_escalate_does_not_reclaim_in_the_same_tick():
    transport = _capped()
    run = _run_tick(
        transport=transport, flag=True, intake=_intake(coding_escalation="low_confidence"),
        claims=[_claim(), _claim(), _claim(), None], limit=3,
    )
    assert run.out["status"] == "capped"
    assert run.repo.claim_next.call_count == 1


def test_tick_flag_on_count_cap_still_returns_cap_reached():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    run = _run_tick(transport=transport, flag=True, calls_today=10)
    assert run.out["status"] == "cap_reached"
    run.repo.claim_next.assert_not_called()


def test_tick_flag_on_capped_escalate_reads_nothing_before_returning():
    transport = _capped()
    run = _run_tick(
        transport=transport, flag=True, intake=_intake(coding_escalation="low_confidence"),
    )
    assert run.out["status"] == "capped"
    run.suggester.suggest.assert_not_called()
    run.read_blob.assert_not_called()
    run.repo.read_bill_coding_actor.assert_not_called()
    run.repo.read_vendor_scc_counts.assert_not_called()
    run.catalog_loader.assert_not_called()


def _drop_foundry_intake(source: str):
    return _intake(
        coding_escalation="claude_unavailable", coding_source=source,
        pred_sub_cost_code_id=2, coding_model=DEEPSEEK if source == "fallback" else None,
        coding_confidence=Decimal("0.6000"), primary_model=HAIKU,
        primary_sub_cost_code_id=None, primary_confidence=None,
        primary_error="daily_cap",
    )


@pytest.mark.parametrize("source", ["fallback", "similar_vote"])
def test_tick_flag_on_capped_reuses_a_drop_coded_by_foundry_or_the_vote(source):
    transport = _capped(**{DEEPSEEK: _ok("43.10", 0.9)})
    run = _run_tick(transport=transport, flag=True, intake=_drop_foundry_intake(source))
    assert run.out["status"] == "done"
    assert transport.calls == []  # Foundry is never paid twice for the same answer
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"], kw["DecisionSource"]) == (
        "done", 2, "reused_intake",
    )
    assert kw["record_call"] is False


@pytest.mark.parametrize("source", ["fallback", "similar_vote"])
def test_tick_flag_on_not_capped_reruns_the_full_ladder_for_that_drop(source):
    transport = FakeTransport({HAIKU: _ok("43.10", 0.9)})
    run = _run_tick(
        transport=transport, flag=True, capped=False, intake=_drop_foundry_intake(source),
    )
    assert _models(transport) == [HAIKU]
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"], kw["DecisionSource"]) == ("done", 1, "primary")


def test_tick_flag_on_capped_drop_that_coded_nothing_runs_foundry():
    transport = _capped(**{DEEPSEEK: _ok("44.20", 0.9)})
    intake = _drop_foundry_intake("none")
    run = _run_tick(transport=transport, flag=True, intake=intake)
    assert DEEPSEEK in _models(transport)
    assert run.repo.complete.call_args.kwargs["DecisionSource"] == "fallback"


def test_tick_flag_on_capped_foundry_skipped_after_valid_haiku_stays_pending():
    """Haiku answered validly (paid) but its code does not resolve, Sonnet is capped:
    the Foundry rungs are SKIPPED, nothing coded it -> pending, not finalized."""
    from dataclasses import replace

    snap = _snapshot()
    snap = replace(
        snap, scc_number_to_id={k: v for k, v in snap.scc_number_to_id.items() if k != "50.00"},
    )
    transport = FakeTransport({HAIKU: _ok("50.00", 0.5), SONNET: _err(CAP)})
    run = _run_tick(transport=transport, flag=True, snapshot=snap)
    assert _models(transport) == [HAIKU, SONNET]
    assert run.out["status"] == "capped"
    run.repo.complete.assert_not_called()
