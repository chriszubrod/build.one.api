"""U-701b3: ladder V2 decision core, wrapper, intake mapping and V2 tick (all mocked)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from entities.attachment.business.extraction_service import REDACTION_VERSION
from entities.bill_intake_prediction.business.model import BillIntakePrediction
from entities.bill_intake_prediction.business.service import (
    BillIntakePredictionService,
    tick_decision_for_intake,
)
from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.business.bill_coding_decision import (
    BillCodingCatalog,
    BillCodingDecision,
    BillCodingDeps,
    BillCodingInputs,
    RungAnswer,
    SimilarScope,
    choose_final,
    decide_bill_coding,
    map_cascade_role_answers,
    rung_error_code,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    HISTORY_STRONG_RAW_SCORE,
    BillSuggestion,
)
from entities.coding_suggestion.business.similar_bills import BillTokenProfile, Neighbour
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillCodingActor,
    BillTextCodingClaim,
)
from intelligence.cascade.bill_coding import ALLOWED_PAYLOAD_KEYS_V2, FULL_CONTENT_CHAR_CAP
from intelligence.cascade.core import Attempt, CascadeResult, Rung
from intelligence.transport.base import Done, TextDelta, TransportError, Usage

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
AS_OF = datetime(2026, 5, 1, 12, 0, 0)
OLD = datetime(2020, 1, 1)

# id -> (number, name); 9 is Suspense, 7 is in the catalog but not in the narrowed list.
_SCCS = {
    1: ("43.10", "Plumbing"),
    2: ("44.20", "Electrical"),
    3: ("50.00", "Framing"),
    7: ("61.00", "Paint"),
    9: ("00.02", "Suspense"),
}


def _ref(number: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(number=number, name=name, created=OLD)


SCC_BY_ID = {i: _ref(n, nm) for i, (n, nm) in _SCCS.items()}
SCC_NUM_TO_ID = {n: i for i, (n, _nm) in _SCCS.items()}
FULL_CATALOG = [
    {"number": n, "name": nm} for i, (n, nm) in sorted(_SCCS.items()) if i != 9
]
NARROWED = [row for row in FULL_CATALOG if row["number"] != "61.00"]
PROJECTS = [{"abbreviation": "P1", "name": "One"}, {"abbreviation": "P2", "name": "Two"}]
PROJ_TO_ID = {"P1": 501, "P2": 502}


def _catalog() -> BillCodingCatalog:
    return BillCodingCatalog(
        scc_by_id=SCC_BY_ID,
        scc_number_to_id=SCC_NUM_TO_ID,
        ambiguous_scc_numbers=frozenset(),
        proj_abbrev_to_id=PROJ_TO_ID,
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=PROJECTS,
        as_of_catalog=FULL_CATALOG,
    )


def _snapshot() -> bts.CatalogSnapshot:
    return bts.CatalogSnapshot(
        scc_by_id=SCC_BY_ID,
        scc_number_to_id=SCC_NUM_TO_ID,
        ambiguous_scc_numbers=frozenset(),
        proj_abbrev_to_id=PROJ_TO_ID,
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=PROJECTS,
        vendor_name="Vendor",
    )


def _hist(scc: int = 1, *, strong: bool = True, project_id: Optional[int] = 900) -> BillSuggestion:
    return BillSuggestion(
        project_id=project_id,
        sub_cost_code_id=scc,
        sub_cost_code_public_id=None,
        sub_cost_code_number=_SCCS[scc][0],
        sub_cost_code_name=_SCCS[scc][1],
        source="vendor_history",
        reason="r",
        confidence=Decimal("0.80") if strong else Decimal("0.40"),
        raw_score=HISTORY_STRONG_RAW_SCORE if strong else Decimal("0.40"),
        top_count=5,
        second_count=1,
        total_count=6,
    )


def _neighbour(bill_id: int, scc_id: int, combined: float = 0.9) -> Neighbour:
    bill = SimpleNamespace(bill_id=bill_id, label_scc_id=scc_id)
    prof = BillTokenProfile(
        enriched=SimpleNamespace(bill=bill),
        tokens=frozenset(),
        idf_weight_sum=0.0,
        log_abs_amount=0.0,
    )
    return Neighbour(combined, prof, 0.8, 1.0, True)


class FakeSimilar:
    def __init__(self, neighbours=(), error: Optional[Exception] = None):
        self.neighbours = list(neighbours)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def neighbours_for(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return list(self.neighbours)


def _ok(scc_number: str, conf: float, project: Optional[str] = None):
    body = {"scc_number": scc_number, "project_abbreviation": project, "confidence": conf}
    return [TextDelta(text=json.dumps(body)), Done(usage=Usage(input_tokens=10, output_tokens=5))]


def _err(code: str):
    return [TransportError(message="provider said: secret detail", code=code)]


class FakeTransport:
    """Scripted per-model replies; records the model and payload of every call."""

    def __init__(self, replies: dict[str, list]):
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def stream(self, messages, *, model, **_kwargs):
        self.calls.append((model, json.loads(messages[0].content[0].text)))
        for event in self.replies[model]:
            yield event

    def transport_for(self, _provider: str) -> "FakeTransport":
        return self


def _inputs(**overrides) -> BillCodingInputs:
    base = dict(
        stage="tick_full",
        vendor_id=5,
        vendor_name="Vendor",
        chosen_project_id=100,
        bill_text="invoice body",
        query_token_hashes=frozenset({1, 2, 3}),
        amount=Decimal("125.00"),
        as_of=AS_OF,
        exclude_bill_id=77,
        scope=SimilarScope(company_id=1, actor_user_id=42, accessible_project_ids=frozenset({501})),
        prior_primary=None,
    )
    base.update(overrides)
    return BillCodingInputs(**base)


def _deps(transport: Optional[FakeTransport] = None, **overrides) -> BillCodingDeps:
    base = dict(
        catalog=_catalog(),
        history_rows=[],
        history_suggestion=None,
        candidate_sccs=list(NARROWED),
        candidate_scc_narrowed=True,
        similar_service=None,
        history_for_project=None,
        transport_for=transport.transport_for if transport else None,
    )
    base.update(overrides)
    return BillCodingDeps(**base)


def _decide(inputs: BillCodingInputs, deps: BillCodingDeps) -> BillCodingDecision:
    return asyncio.run(decide_bill_coding(inputs, deps))


# ---------------------------------------------------------------- choose_final


def _ra(scc: Optional[int], conf: str = "0.80", *, project: Optional[int] = None, error=None):
    return RungAnswer("m", scc, project, Decimal(conf), error)


@pytest.mark.parametrize(
    "history,primary,escalate,fallback,vote,expected_source,expected_scc",
    [
        (_hist(1), _ra(2), None, None, 3, "history", 1),
        (None, _ra(2, "0.50"), _ra(3, "0.90"), None, None, "escalate", 3),
        (None, _ra(2, "0.40"), None, None, 3, "primary", 2),
        (None, _ra(None, error="http_500"), None, _ra(3, "0.55"), 7, "fallback", 3),
        (None, _ra(None, error="http_500"), None, _ra(3, "0.45"), 7, "similar_vote", 7),
        (None, None, None, None, None, "none", None),
        (_hist(1, strong=False), None, None, None, None, "none", None),
        (_hist(1, strong=False), None, None, None, 7, "similar_vote", 7),
    ],
)
def test_choose_final_mode_a_truth_table(
    history, primary, escalate, fallback, vote, expected_source, expected_scc
):
    src, scc, proj, _conf, _model = choose_final(
        mode="A",
        history=history,
        primary=primary,
        escalate=escalate,
        fallback=fallback,
        similar_vote_scc_id=vote,
        similar_vote_confidence=Decimal("0.6") if vote else None,
    )
    assert (src, scc, proj) == (expected_source, expected_scc, None)


def test_choose_final_mode_b_text_project_first():
    vendor_wide = _hist(1, project_id=900)  # strong, vendor-wide, project 900
    model_p1 = _ra(2, "0.90", project=501)
    for_p = {501: _hist(3, project_id=501)}

    # Model's project P1 keys history: strong for P1 -> P1's code, project P1.
    assert choose_final(
        mode="B", history=vendor_wide, primary=model_p1, escalate=None, fallback=None,
        similar_vote_scc_id=None, similar_vote_confidence=None,
        history_for_project=for_p.get,
    )[:3] == ("history", 3, 501)
    # History weak for P1 -> the model's code on P1 (never the vendor-wide 900).
    assert choose_final(
        mode="B", history=vendor_wide, primary=model_p1, escalate=None, fallback=None,
        similar_vote_scc_id=None, similar_vote_confidence=None,
        history_for_project=lambda _p: _hist(3, strong=False, project_id=501),
    )[:3] == ("primary", 2, 501)
    # No model project -> strong vendor-wide history with its own project.
    assert choose_final(
        mode="B", history=vendor_wide, primary=_ra(2, "0.90"), escalate=None, fallback=None,
        similar_vote_scc_id=None, similar_vote_confidence=None,
    )[:3] == ("history", 1, 900)
    # Model unavailable -> strong vendor-wide history.
    assert choose_final(
        mode="B", history=vendor_wide, primary=_ra(None, error="http_500"), escalate=None,
        fallback=None, similar_vote_scc_id=None, similar_vote_confidence=None,
    )[:3] == ("history", 1, 900)
    # No project, weak history -> model code, no project.
    assert choose_final(
        mode="B", history=_hist(1, strong=False), primary=_ra(2, "0.30"), escalate=None,
        fallback=None, similar_vote_scc_id=None, similar_vote_confidence=None,
    )[:3] == ("primary", 2, None)


def test_mode_b_decide_runs_model_before_strong_vendor_history():
    transport = FakeTransport({HAIKU: _ok("44.20", 0.9, "P1")})
    asked: list[int] = []

    def for_project(project_id: int):
        asked.append(project_id)
        return _hist(3, project_id=project_id)

    decision = _decide(
        _inputs(chosen_project_id=None, stage="drop"),
        _deps(transport, history_suggestion=_hist(1, project_id=900), history_for_project=for_project),
    )
    assert [model for model, _ in transport.calls] == [HAIKU]
    assert asked == [501]
    assert (decision.source, decision.sub_cost_code_id, decision.project_id) == ("history", 3, 501)
    assert decision.escalation is None


def test_mode_a_strong_history_short_circuits_without_a_call():
    transport = FakeTransport({})
    decision = _decide(_inputs(), _deps(transport, history_suggestion=_hist(1)))
    assert transport.calls == []
    assert decision.source == "history" and decision.cascades == ()


# ---------------------------------------------------------------- roles / markers


def _attempt(model: str, *, scc: Optional[str] = None, conf=None, error=None, fallback=False):
    return Attempt(
        rung=Rung("anthropic", model, fallback_only=fallback),
        accepted=False,
        confidence=conf,
        validated=scc is not None,
        result={"scc_number": scc, "project_abbreviation": None} if scc else None,
        error=error,
    )


def test_map_cascade_roles_and_escalate_only():
    cascade = CascadeResult(
        accepted=False, needs_human=True, result=None, confidence=None, winning_rung=None,
        attempts=[
            _attempt(HAIKU, scc="43.10", conf=0.5),
            _attempt(SONNET, error="claude_unavailable:overloaded:retry later"),
            _attempt("DeepSeek-V4-Flash", error="skipped:fallback_only", fallback=True),
        ],
    )
    p, e, f = map_cascade_role_answers(cascade, scc_number_to_id=SCC_NUM_TO_ID, proj_abbrev_to_id={})
    assert (p.model, p.sub_cost_code_id, e.model, e.error, f) == (HAIKU, 1, SONNET, "overloaded", None)

    sonnet_only = replace(cascade, attempts=[_attempt(SONNET, scc="44.20", conf=0.9)])
    p, e, f = map_cascade_role_answers(
        sonnet_only, scc_number_to_id=SCC_NUM_TO_ID, proj_abbrev_to_id={}, escalate_only=True
    )
    assert p is None and e.model == SONNET and e.sub_cost_code_id == 2


def test_rung_error_code_never_keeps_provider_text():
    assert rung_error_code("claude_unavailable:daily_cap:spent 6.45") == "daily_cap"
    assert rung_error_code("http_500: upstream said secret") == "http_500"
    assert rung_error_code("ValueError: secret") == "error"
    assert rung_error_code("no parseable result") == "unparseable"
    assert rung_error_code(None) is None


@pytest.mark.parametrize(
    "replies,history,expected",
    [
        ({HAIKU: _ok("43.10", 0.69)}, None, "low_confidence"),
        ({HAIKU: _ok("43.10", 0.70)}, None, None),
        (
            {HAIKU: _err("http_500"), "DeepSeek-V4-Flash": _ok("43.10", 0.8)},
            None,
            "claude_unavailable",
        ),
        ({}, _hist(1), None),
    ],
)
def test_drop_escalation_marker(replies, history, expected):
    transport = FakeTransport(replies)
    decision = _decide(_inputs(stage="drop"), _deps(transport, history_suggestion=history))
    assert decision.escalation == expected
    if expected == "low_confidence":
        # A Haiku answer at ANY confidence is the drop's prefill.
        assert (decision.source, decision.sub_cost_code_id) == ("primary", 1)
    if expected == "claude_unavailable":
        assert decision.source == "fallback" and decision.fallback.model == "DeepSeek-V4-Flash"


# ---------------------------------------------------------------- P0 tick_escalate


HAIKU_PRIOR = RungAnswer(HAIKU, 1, None, Decimal("0.6000"), None)


@pytest.mark.parametrize(
    "sonnet_reply,expected_source,expected_scc,expected_err,expected_stop",
    [
        (_ok("44.20", 0.9), "escalate", 2, None, None),
        (_err("http_500"), "primary", 1, "http_500", None),
        (_err("claude_unavailable:daily_cap"), "primary", 1, "daily_cap", "daily_cap"),
    ],
)
def test_tick_escalate_on_real_escalate_ladder(
    sonnet_reply, expected_source, expected_scc, expected_err, expected_stop
):
    transport = FakeTransport({SONNET: sonnet_reply})
    decision = _decide(
        _inputs(stage="tick_escalate", prior_primary=HAIKU_PRIOR),
        _deps(transport, history_suggestion=_hist(3)),
    )
    assert [model for model, _ in transport.calls] == [SONNET]  # Sonnet only, no Foundry
    assert decision.primary == HAIKU_PRIOR
    assert decision.escalate.model == SONNET
    assert decision.escalate.error == expected_err
    assert (decision.source, decision.sub_cost_code_id) == (expected_source, expected_scc)
    assert decision.stopped_reason == expected_stop


# ---------------------------------------------------------------- payload / neighbours


def test_v2_payload_bill_text_budget_and_keys():
    for size, expected_len in ((20_000, 20_000), (70_000, FULL_CONTENT_CHAR_CAP)):
        transport = FakeTransport({HAIKU: _ok("43.10", 0.9)})
        _decide(_inputs(bill_text="x" * size), _deps(transport))
        payload = transport.calls[0][1]
        assert set(payload) <= ALLOWED_PAYLOAD_KEYS_V2
        assert not {"memo", "description", "line_descriptions"} & set(payload)
        text = payload["bill_text"]
        if size <= FULL_CONTENT_CHAR_CAP:
            assert text == "x" * size
        else:
            assert text == "x" * FULL_CONTENT_CHAR_CAP + "\n…(truncated)"
        assert len(text.rstrip("\n…(truncated)")) == expected_len


def test_neighbours_reach_payload_union_and_filtered_vote():
    neighbours = [
        _neighbour(11, 7, 0.95),  # 61.00: not in the narrowed list -> union adds it
        _neighbour(12, 9, 0.94),  # Suspense -> dropped from block and vote
        _neighbour(13, 7, 0.93),
        _neighbour(14, 2, 0.92),
        _neighbour(15, 7, 0.91),
        _neighbour(16, 9, 0.90),
        _neighbour(17, 9, 0.89),
        _neighbour(18, 9, 0.88),
    ]
    similar = FakeSimilar(neighbours)
    transport = FakeTransport({HAIKU: _err("http_500"), "DeepSeek-V4-Flash": _err("http_500"),
                               "gpt-5.4-nano": _err("http_500"), "gpt-5.4-mini": _err("http_500")})
    decision = _decide(
        _inputs(stage="drop", amount=Decimal("-125.00")),
        _deps(transport, similar_service=similar),
    )
    payload = transport.calls[0][1]
    assert 0 < len(payload["similar_bills"]) <= 5
    assert all(row["scc_number"] != "00.02" for row in payload["similar_bills"])
    assert "61.00" in {row["number"] for row in payload["candidate_sccs"]}
    # Vote over the filtered list (Suspense majority never wins) and the code is used.
    assert decision.similar_vote_scc_id == 7
    assert (decision.source, decision.sub_cost_code_id) == ("similar_vote", 7)
    call = similar.calls[0]
    assert call["exclude_bill_id"] == 77 and call["amount"] == Decimal("125.00")
    assert call["visible"].actor_user_id == 42
    assert call["visible"].accessible_project_ids == frozenset({501})


@pytest.mark.parametrize(
    "scope,service_error",
    [
        (SimilarScope(company_id=1, actor_user_id=None, accessible_project_ids=frozenset()), None),
        (SimilarScope(company_id=None, actor_user_id=42, accessible_project_ids=None), None),
        (SimilarScope(company_id=1, actor_user_id=42, accessible_project_ids=None), RuntimeError("x")),
    ],
)
def test_similar_lookup_is_scoped_and_best_effort(scope, service_error):
    similar = FakeSimilar([_neighbour(11, 2)], error=service_error)
    transport = FakeTransport({HAIKU: _ok("43.10", 0.9)})
    decision = _decide(_inputs(scope=scope), _deps(transport, similar_service=similar))
    assert decision.source == "primary"
    assert decision.similar_neighbour_count == 0 and decision.similar_vote_scc_id is None
    if service_error is None:
        assert similar.calls == []


# ---------------------------------------------------------------- wrapper


def _wrapper(settings, similar, suggester, **overrides):
    repo = MagicMock()
    repo.read_vendor_scc_counts.return_value = []
    kwargs = dict(
        stage="tick_full",
        bill_text="invoice body text",
        vendor_id=5,
        vendor_name="Vendor",
        chosen_project_id=None,
        amount=Decimal("-40.00"),
        as_of=AS_OF,
        exclude_bill_id=77,
        company_id=1,
        actor_user_id=42,
        accessible_project_ids=None,
        settings=settings,
        repository=repo,
        suggester=suggester,
        catalog=_snapshot(),
        similar_service=similar,
    )
    kwargs.update(overrides)
    with patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.decide_bill_coding",
    ) as decide:
        async def fake(inputs, deps):
            return (inputs, deps)

        decide.side_effect = fake
        return asyncio.run(bts.run_bill_coding_decision(**kwargs))


def test_run_bill_coding_decision_builds_deps():
    settings = SimpleNamespace(bill_coding_candidate_sccs="narrowed", bill_text_token_hash_key="k" * 40)
    suggester = MagicMock()
    suggester.suggest.return_value = _hist(1)
    similar = FakeSimilar()
    with patch.object(bts, "load_narrowing_history_scc_numbers", return_value=["43.10", "44.20", "50.00"]):
        inputs, deps = _wrapper(settings, similar, suggester)
    assert inputs.query_token_hashes and inputs.amount == Decimal("40.00")
    assert inputs.scope == SimilarScope(company_id=1, actor_user_id=42, accessible_project_ids=None)
    # Narrowed payload list, FULL as-of catalog for the similar filters.
    assert [r["number"] for r in deps.candidate_sccs] == ["43.10", "44.20", "50.00"]
    assert deps.candidate_scc_narrowed is True
    assert {r["number"] for r in deps.catalog.as_of_catalog} == {r["number"] for r in FULL_CATALOG}
    assert deps.similar_service is similar
    deps.history_for_project(501)
    assert suggester.suggest.call_args.kwargs["project_id"] == 501
    assert suggester.suggest.call_args.kwargs["exclude_bill_id"] == 77


def test_run_bill_coding_decision_no_hash_key_means_no_hashes():
    settings = SimpleNamespace(bill_coding_candidate_sccs="full", bill_text_token_hash_key=None)
    inputs, _deps_ = _wrapper(settings, FakeSimilar(), MagicMock(suggest=MagicMock(return_value=None)))
    assert inputs.query_token_hashes == frozenset()


# ---------------------------------------------------------------- intake


def _intake(**overrides) -> BillIntakePrediction:
    base = dict(
        id=1, public_id="p", attachment_id=2, extracted_datetime=AS_OF, bill_id=1,
        intake_source="pdf_drop", model_version=bts.MODEL_VERSION_V2, status="ok",
        pred_vendor_id=5, pred_vendor_extracted_name=None, pred_bill_number=None,
        pred_bill_date=None, pred_total_amount=None, pred_description=None,
        pred_sub_cost_code_id=1, pred_project_id=None, coding_mode="A",
        coding_chosen_project_id=100, header_confidence=None, header_model=None,
        coding_confidence=Decimal("0.6000"), coding_model=HAIKU, latency_ms_total=None,
        timing_json=None, input_tokens=None, output_tokens=None, cost_usd=None,
        coding_source="primary", coding_escalation=None, primary_model=HAIKU,
        primary_sub_cost_code_id=1, primary_confidence=Decimal("0.6000"),
        similar_pred_sub_cost_code_id=2, similar_confidence=Decimal("0.5000"),
        similar_neighbour_count=3,
    )
    base.update(overrides)
    return BillIntakePrediction(**base)


def _route(intake: BillIntakePrediction, *, v2: bool = True) -> str:
    return tick_decision_for_intake(
        intake, extracted_datetime=AS_OF, chosen_project_id=100, vendor_id=5,
        ladder_v2_enabled=v2,
    )


def test_tick_decision_for_intake_routing():
    assert _route(_intake()) == "reuse"
    assert _route(_intake(coding_escalation="low_confidence")) == "escalate"
    assert _route(_intake(coding_escalation="low_confidence", primary_sub_cost_code_id=None)) == "full"
    assert _route(_intake(coding_escalation="claude_unavailable")) == "full"
    assert _route(_intake(pred_vendor_id=6)) == "full"
    # Legacy rows run the full V2 ladder when the flag is on; flag off reuses them as today.
    legacy = _intake(model_version=bts.MODEL_VERSION, coding_source=None)
    assert _route(legacy) == "full"
    assert _route(legacy, v2=False) == "reuse"


def _decision(**overrides) -> BillCodingDecision:
    base = dict(
        source="primary", mode="A", sub_cost_code_id=1, project_id=None,
        confidence=Decimal("0.6"), model=HAIKU,
        primary=RungAnswer(HAIKU, 1, None, Decimal("0.6"), None),
        escalate=None, fallback=RungAnswer("DeepSeek-V4-Flash", 3, None, Decimal("0.7"), None),
        similar_vote_scc_id=7, similar_vote_confidence=Decimal("0.5"), similar_neighbour_count=4,
        escalation="low_confidence", stopped_reason=None, cascades=(), history=None,
    )
    base.update(overrides)
    return BillCodingDecision(**base)


def _record_read(service: BillIntakePredictionService, **extra) -> None:
    service.record_read(
        attachment_id=2, intake_source="pdf_drop", status="ok", timing_ms={"total": 5},
        fields={}, suggestion=None, chosen_project_id=100, header_cascade=None,
        coding_cascade=None, header_model=None, vendor_id=5, vendor_extracted_name=None,
        shadow_extraction_content="body", shadow_vendor_id=5, shadow_amount="10",
        shadow_as_of=AS_OF, **extra,
    )


def test_record_read_with_decision_writes_rungs_and_vote_in_one_upsert():
    repo = MagicMock()
    with patch("entities.coding_suggestion.business.bill_text_token_hash.feature_enabled", return_value=True):
        _record_read(BillIntakePredictionService(repo=repo), coding_decision=_decision())
    params = repo.upsert.call_args.kwargs
    assert params["ModelVersion"] == bts.MODEL_VERSION_V2
    assert (params["PredSubCostCodeId"], params["CodingModel"], params["CodingMode"]) == (1, HAIKU, "A")
    assert params["CodingSource"] == "primary" and params["CodingEscalation"] == "low_confidence"
    assert (params["PrimarySubCostCodeId"], params["FallbackSubCostCodeId"]) == (1, 3)
    assert (params["SimilarPredSubCostCodeId"], params["SimilarNeighbourCount"]) == (7, 4)
    repo.update_similar_shadow.assert_not_called()


def test_record_read_without_decision_keeps_shadow_path():
    repo = MagicMock()
    with patch(
        "entities.coding_suggestion.business.bill_text_token_hash.feature_enabled", return_value=True
    ), patch.object(
        BillIntakePredictionService, "_shadow_similar_prediction", return_value=(2, Decimal("0.5"))
    ):
        _record_read(BillIntakePredictionService(repo=repo))
    params = repo.upsert.call_args.kwargs
    assert params["ModelVersion"] == bts.MODEL_VERSION
    assert "CodingSource" not in params and "SimilarPredSubCostCodeId" not in params
    repo.update_similar_shadow.assert_called_once()


# ---------------------------------------------------------------- V2 tick


def _claim(chosen_project_id: Optional[int] = 100) -> BillTextCodingClaim:
    return BillTextCodingClaim(
        bill_id=77, attachment_id=2, extracted_text_blob_url="https://b/t",
        extracted_datetime=AS_OF, vendor_id=5, chosen_project_id=chosen_project_id,
        bill_date=None, created_datetime=AS_OF,
    )


def _run_tick(
    *,
    transport: FakeTransport,
    intake: Optional[BillIntakePrediction] = None,
    actor: Optional[BillCodingActor] = BillCodingActor(42, False, 3, Decimal("-125.00")),
    similar: Optional[FakeSimilar] = None,
    history: Optional[BillSuggestion] = None,
    hash_key: Optional[str] = "k" * 40,
    claim: Optional[BillTextCodingClaim] = None,
):
    settings = SimpleNamespace(
        bill_coding_ladder_v2_enabled=True,
        bill_coding_candidate_sccs="full",
        bill_coding_text_daily_call_cap=10,
        bill_text_token_hash_key=hash_key,
    )
    repo = MagicMock()
    if isinstance(actor, Exception):
        repo.read_bill_coding_actor.side_effect = actor
    else:
        repo.read_bill_coding_actor.return_value = actor
    repo.read_vendor_scc_counts.return_value = []
    repo.complete.return_value = SimpleNamespace()
    similar = similar if similar is not None else FakeSimilar()
    blob = json.dumps({"content": "invoice body", "redaction_version": REDACTION_VERSION})
    with patch.object(bts, "history_eligible_for_text", return_value=(True, None)), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService.read_by_attachment_id",
        return_value=intake,
    ), patch.object(bts, "read_text_blob_with_retry", return_value=blob), patch.object(
        bts, "_read_vendor_name", return_value="Vendor"
    ), patch.object(
        bts, "accessible_project_ids_for_user", return_value=frozenset({501})
    ) as access, patch(
        "shared.access.resolve_accessible_project_ids"
    ) as resolve, patch.object(
        bts.SimilarBillsService, "for_company", return_value=similar
    ) as for_company:
        out = asyncio.run(bts._process_one_claim_v2(
            claim or _claim(),
            settings=settings,
            repository=repo,
            storage=MagicMock(),
            suggester=MagicMock(suggest=MagicMock(return_value=history)),
            catalog_loader=lambda _as_of: _snapshot(),
            transport_for=transport.transport_for,
        ))
    resolve.assert_not_called()
    return SimpleNamespace(out=out, repo=repo, access=access, similar=similar, for_company=for_company)


@pytest.mark.parametrize(
    "sonnet_reply,status,scc,source,escalate_scc,escalate_err",
    [
        (_ok("44.20", 0.9), "done", 2, "escalate", 2, None),
        (_err("http_500"), "done", 1, "primary", None, "http_500"),
        (_err("claude_unavailable:daily_cap"), "capped", None, None, None, None),
    ],
)
def test_tick_escalate_persists_haiku_primary_and_sonnet_escalate(
    sonnet_reply, status, scc, source, escalate_scc, escalate_err
):
    transport = FakeTransport({SONNET: sonnet_reply})
    run = _run_tick(transport=transport, intake=_intake(coding_escalation="low_confidence"))
    assert run.out["status"] == status
    assert [model for model, _ in transport.calls] == [SONNET]
    if status == "capped":
        run.repo.complete.assert_not_called()  # stays pending; re-claimed after the cap
        return
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"], kw["DecisionSource"]) == ("done", scc, source)
    assert kw["model_version"] == bts.MODEL_VERSION_V2
    assert (kw["PrimaryModel"], kw["PrimarySubCostCodeId"], kw["PrimaryFromIntake"]) == (HAIKU, 1, 1)
    assert kw["EscalateModel"] == SONNET
    assert (kw["EscalateSubCostCodeId"], kw["EscalateError"]) == (escalate_scc, escalate_err)


def test_tick_full_daily_cap_after_valid_haiku_keeps_the_answer():
    transport = FakeTransport({HAIKU: _ok("43.10", 0.5), SONNET: _err("claude_unavailable:daily_cap")})
    run = _run_tick(transport=transport)
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"], kw["DecisionSource"]) == ("done", 1, "primary")
    assert kw["EscalateError"] == "daily_cap" and kw["record_call"] is True
    assert kw["PrimaryFromIntake"] is None


def test_tick_full_daily_cap_without_haiku_answer_stays_pending():
    run = _run_tick(transport=FakeTransport({HAIKU: _err("claude_unavailable:daily_cap")}))
    assert run.out["status"] == "capped"
    run.repo.complete.assert_not_called()


def test_tick_scope_is_the_creator_with_bill_company_and_amount():
    similar = FakeSimilar([_neighbour(11, 2)])
    run = _run_tick(transport=FakeTransport({HAIKU: _ok("43.10", 0.9)}), similar=similar)
    run.access.assert_called_once_with(42, is_system_admin=False)
    run.for_company.assert_called_once()
    assert run.for_company.call_args.args[0] == 3
    call = similar.calls[0]
    assert call["exclude_bill_id"] == 77 and call["amount"] == Decimal("125.00")
    assert call["visible"].actor_user_id == 42
    assert call["visible"].accessible_project_ids == frozenset({501})
    assert run.repo.complete.call_args.kwargs["SimilarNeighbourCount"] == 1


@pytest.mark.parametrize("actor", [None, BillCodingActor(None, False, 3, Decimal("1"))])
def test_tick_without_creator_fails_closed(actor):
    similar = FakeSimilar([_neighbour(11, 2)])
    run = _run_tick(transport=FakeTransport({HAIKU: _ok("43.10", 0.9)}), actor=actor, similar=similar)
    run.access.assert_not_called()
    assert similar.calls == []
    assert run.repo.complete.call_args.kwargs["status"] == "done"


def test_tick_without_hash_key_still_codes_the_bill():
    similar = FakeSimilar([_neighbour(11, 2)])
    run = _run_tick(transport=FakeTransport({HAIKU: _ok("43.10", 0.9)}), similar=similar, hash_key=None)
    assert similar.calls == []
    kw = run.repo.complete.call_args.kwargs
    assert kw["status"] == "done" and kw["sub_cost_code_id"] == 1


def test_tick_similar_lookup_error_still_codes_the_bill():
    similar = FakeSimilar(error=RuntimeError("cache broke"))
    run = _run_tick(transport=FakeTransport({HAIKU: _ok("43.10", 0.9)}), similar=similar)
    assert run.repo.complete.call_args.kwargs["status"] == "done"


def test_tick_strong_history_decision_does_not_count_a_call():
    transport = FakeTransport({})
    run = _run_tick(transport=transport, history=_hist(1))
    kw = run.repo.complete.call_args.kwargs
    assert transport.calls == []
    assert (kw["DecisionSource"], kw["record_call"]) == ("history", False)


def test_tick_none_is_the_only_failed_decision():
    transport = FakeTransport({
        HAIKU: _err("http_500"), SONNET: _err("http_500"), "DeepSeek-V4-Flash": _ok("43.10", 0.4),
        "gpt-5.4-nano": _err("http_500"), "gpt-5.4-mini": _err("http_500"),
    })
    run = _run_tick(transport=transport)
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["failure_reason"], kw["DecisionSource"]) == ("failed", "not_accepted", "none")


def test_tick_reuse_copies_primary_and_vote_only_from_v2_rows():
    transport = FakeTransport({})
    run = _run_tick(transport=transport, intake=_intake())
    kw = run.repo.complete.call_args.kwargs
    assert transport.calls == []
    assert (kw["DecisionSource"], kw["PrimaryFromIntake"], kw["record_call"]) == ("reused_intake", 1, False)
    assert (kw["PrimaryModel"], kw["PrimarySubCostCodeId"]) == (HAIKU, 1)
    assert (kw["SimilarVoteSubCostCodeId"], kw["SimilarNeighbourCount"]) == (2, 3)

    run = _run_tick(transport=transport, intake=_intake(coding_source=None))
    kw = run.repo.complete.call_args.kwargs
    assert kw["DecisionSource"] == "reused_intake"
    assert "SimilarVoteSubCostCodeId" not in kw and "SimilarNeighbourCount" not in kw


def test_tick_legacy_intake_runs_the_full_ladder():
    transport = FakeTransport({HAIKU: _ok("43.10", 0.9)})
    run = _run_tick(
        transport=transport,
        intake=_intake(model_version=bts.MODEL_VERSION, coding_source=None),
    )
    assert [model for model, _ in transport.calls] == [HAIKU]
    assert run.repo.complete.call_args.kwargs["DecisionSource"] == "primary"


def test_flag_off_model_version_unchanged():
    assert bts.MODEL_VERSION == "u654b-text-v1"
    assert bts.active_model_version(SimpleNamespace(bill_coding_ladder_v2_enabled=False)) == "u654b-text-v1"


# ---------------------------------------------------------------- final round: Mode B = V1


def _only(*allowed: int):
    return lambda project_id: project_id in allowed


@pytest.mark.parametrize(
    "case,primary,history_p,vendor_hist,accessible,vote,expected",
    [
        # V1: text project P accessible + strong history for P -> history code on P.
        ("P strong", _ra(2, "0.9", project=501), _hist(3, project_id=501), _hist(1), (501,), None,
         ("history", 3, 501)),
        # V1: P + weak history for P -> the model's code on P.
        ("P weak", _ra(2, "0.9", project=501), _hist(3, strong=False), _hist(1), (501,), None,
         ("primary", 2, 501)),
        # V1 retain_accessible_project: SCC unresolved, P kept -> strong history for P.
        ("P only, strong", _ra(None, project=501), _hist(3, project_id=501), _hist(1), (501,), None,
         ("history", 3, 501)),
        # P known, weak, no model code: NEVER the vendor-wide history (V1 returns nothing);
        # the filtered vote is the V2 last resort, then blank.
        ("P only, weak, vote", _ra(None, project=501), _hist(3, strong=False), _hist(1), (501,), 7,
         ("similar_vote", 7, None)),
        ("P only, weak", _ra(None, project=501), _hist(3, strong=False), _hist(1), (501,), None,
         ("none", None, None)),
        # P not accessible -> as no P: vendor-wide strong history, its project access-checked.
        ("P denied, hist ok", _ra(2, "0.9", project=501), _hist(3), _hist(1, project_id=502), (502,), None,
         ("history", 1, 502)),
        ("P denied, hist proj denied", _ra(2, "0.9", project=501), _hist(3), _hist(1, project_id=900), (), None,
         ("history", 1, None)),
        ("P denied, weak", _ra(2, "0.9", project=501), None, _hist(1, strong=False), (), None,
         ("primary", 2, None)),
        # Model unavailable -> vendor-wide strong history; else vote; else blank.
        ("unavailable, strong", _ra(None, error="http_500"), None, _hist(1, project_id=900), (900,), None,
         ("history", 1, 900)),
        ("unavailable, weak, vote", _ra(None, error="http_500"), None, _hist(1, strong=False), (), 7,
         ("similar_vote", 7, None)),
        ("unavailable, weak", _ra(None, error="http_500"), None, None, (), None,
         ("none", None, None)),
    ],
)
def test_choose_final_mode_b_mirrors_v1(case, primary, history_p, vendor_hist, accessible, vote, expected):
    asked: list[int] = []

    def for_project(project_id: int):
        asked.append(project_id)
        return history_p

    out = choose_final(
        mode="B", history=vendor_hist, primary=primary, escalate=None, fallback=None,
        similar_vote_scc_id=vote, similar_vote_confidence=Decimal("0.5") if vote else None,
        history_for_project=for_project, project_accessible=_only(*accessible),
    )
    assert out[:3] == expected, case
    if primary.project_id is not None and primary.project_id in accessible:
        assert asked == [primary.project_id]
    else:
        assert asked == []


def test_decide_mode_b_access_checks_p_against_the_scope():
    transport = FakeTransport({HAIKU: _ok("44.20", 0.9, "P1")})
    asked: list[int] = []
    decision = _decide(
        _inputs(
            chosen_project_id=None, stage="drop",
            scope=SimilarScope(company_id=1, actor_user_id=42, accessible_project_ids=frozenset({502})),
        ),
        _deps(transport, history_for_project=lambda p: asked.append(p) or _hist(3, project_id=p)),
    )
    assert asked == []  # P1 (501) is not accessible -> treated as no project
    assert (decision.source, decision.sub_cost_code_id, decision.project_id) == ("primary", 2, None)


@pytest.mark.parametrize(
    "sonnet_reply,history_strong,expected",
    [
        (_ok("44.20", 0.9, "P2"), True, ("history", 3, 502)),
        (_ok("44.20", 0.9, "P2"), False, ("escalate", 2, 502)),
        (_ok("44.20", 0.9, None), True, ("history", 3, 501)),  # keeps Haiku's project
        (_ok("44.20", 0.9, None), False, ("escalate", 2, 501)),
    ],
)
def test_tick_escalate_mode_b_project_rules(sonnet_reply, history_strong, expected):
    transport = FakeTransport({SONNET: sonnet_reply})
    asked: list[int] = []

    def for_project(project_id: int):
        asked.append(project_id)
        return _hist(3, strong=history_strong, project_id=project_id)

    decision = _decide(
        _inputs(
            chosen_project_id=None, stage="tick_escalate",
            prior_primary=RungAnswer(HAIKU, 1, 501, Decimal("0.6"), None),
            scope=SimilarScope(company_id=1, actor_user_id=42, accessible_project_ids=None),
        ),
        _deps(transport, history_for_project=for_project),
    )
    assert (decision.source, decision.sub_cost_code_id, decision.project_id) == expected
    assert asked == [expected[2]]


def test_unknown_amount_means_no_similar_lookup():
    similar = FakeSimilar([_neighbour(11, 2)])
    decision = _decide(
        _inputs(amount=None),
        _deps(FakeTransport({HAIKU: _ok("43.10", 0.9)}), similar_service=similar),
    )
    assert similar.calls == [] and decision.similar_neighbour_count == 0
    assert decision.source == "primary"


def test_tick_bill_without_total_gets_no_neighbours():
    similar = FakeSimilar([_neighbour(11, 2)])
    run = _run_tick(
        transport=FakeTransport({HAIKU: _ok("43.10", 0.9)}),
        actor=BillCodingActor(42, False, 3, None),
        similar=similar,
    )
    assert similar.calls == []
    assert run.repo.complete.call_args.kwargs["status"] == "done"


def test_tick_actor_read_error_fails_closed_and_keeps_coding():
    similar = FakeSimilar([_neighbour(11, 2)])
    run = _run_tick(transport=FakeTransport({HAIKU: _ok("43.10", 0.9)}), similar=similar,
                    actor=RuntimeError("db down"))
    run.access.assert_not_called()
    run.for_company.assert_not_called()
    assert similar.calls == []
    kw = run.repo.complete.call_args.kwargs
    assert (kw["status"], kw["sub_cost_code_id"]) == ("done", 1)
