"""U-745: M0 train/serve parity - the backtest way vs the live way on one synthetic population.

Backtest way: ``scripts/u698_decision_model_backtest`` (``build_index`` + ``query_bill`` +
``Choosable`` + ``build_context`` + ``m0_features`` + the shipped model, ``score_row``'s
ranking). Live way: ``decision_model.serve`` fed the rows ReadVendorPriorBillsForDecisionModel /
ReadSubCostCodeLabelCountsForDecisionModel would return (``POP`` is also seeded into SQL Server
by tests_sqlserver/test_u745_decision_model_priors_sql.py, which pins the sprocs to these rows),
plus live ``Neighbour`` objects filtered exactly as ``filtered_vote`` filters them, end to end
through ``decide_bill_coding``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional

import pytest

from entities.coding_suggestion.business.bill_coding_decision import (
    BillCodingCatalog,
    BillCodingDeps,
    BillCodingInputs,
    SimilarScope,
    decide_bill_coding,
)
from entities.coding_suggestion.business.decision_model import serve
from entities.coding_suggestion.business.decision_model.features import (
    NeighbourEvidence,
    m0_features,
)
from entities.coding_suggestion.business.similar_bills import (
    BillTokenProfile,
    Neighbour,
    apply_similar_evidence,
    choosable_scc_numbers,
)
from entities.coding_suggestion.persistence.decision_model_priors_repo import prior_bill_from_row
from intelligence.cascade.bill_coding_payload import build_candidate_sccs_for_bill
from intelligence.cascade.core import CascadeResult
from scripts.u653b_backtest import BillRow
from scripts import u698_decision_model_backtest as bt
from entities.coding_suggestion.business.decision_model import features as F

T0 = datetime(2026, 1, 1, 9, 0, 0)
OLD = datetime(2020, 1, 1)
COMPANY = 1


def day(n: int) -> datetime:
    return T0 + timedelta(days=n)


# ---------------------------------------------------------------------------- catalog
# id -> (number, holding). 9 = Suspense, 8 = holding, 10/11 share a number (ambiguous).
SCCS: dict[int, tuple[str, bool]] = {
    1: ("43.10", False),
    2: ("44.20", False),
    3: ("50.00", False),
    7: ("61.00", False),
    8: ("70.00", True),
    9: ("00.02", False),
    10: ("80.00", False),
    11: ("80.00", False),
}
PROJECTS = (501, 502)


# ---------------------------------------------------------------------------- population
@dataclass(frozen=True)
class SynthBill:
    bill_id: int
    vendor_id: int
    created: datetime
    lines: tuple[tuple[int, Optional[int], Optional[int], Optional[str]], ...]  # (line, scc, project, amount)
    company_id: int = COMPANY
    draft: bool = False
    pdf: bool = False          # has a current token-hash attachment
    taxpayer_pdf: bool = False  # its only hashed attachment is a TaxpayerAttachment
    tokens: int = 0

    @property
    def coded(self) -> bool:
        return any(scc is not None for _l, scc, _p, _a in self.lines)


POP: tuple[SynthBill, ...] = (
    SynthBill(101, 5, day(1), ((1001, 1, 501, "100.00"),), pdf=True, tokens=40),
    SynthBill(102, 5, day(2), ((1002, 1, 501, "200.00"),), pdf=True, tokens=55),
    SynthBill(103, 5, day(3), ((1003, 2, 502, "50.00"),)),
    # Mixed code: 3 wins by amount (30 + 60 > 80); project = 3's lowest line (1004 -> 501).
    SynthBill(104, 5, day(4), ((1004, 3, 501, "30.00"), (1005, 2, 502, "80.00"),
                               (1006, 3, 502, "60.00")), pdf=True, tokens=70),
    # Tie 40 / |-40|: the code holding the lowest line id (1007 -> code 1) wins.
    SynthBill(105, 5, day(5), ((1008, 2, 502, "40.00"), (1007, 1, 501, "-40.00"))),
    SynthBill(106, 5, day(6), ((1009, 2, 502, "75.00"),), draft=True),         # draft: out
    SynthBill(107, 5, day(7), ((1013, None, 501, "10.00"),)),                  # uncoded: out
    # An uncoded line still counts in the amount; taxpayer attachment -> has_pdf False.
    SynthBill(108, 5, day(8), ((1010, 1, 501, "120.00"), (1011, None, None, "30.00")),
              taxpayer_pdf=True),
    # The query bill (Mode A project 501) and its exact-time twin (not strictly earlier).
    SynthBill(109, 5, day(10), ((1012, 1, 501, "110.00"),), pdf=True, tokens=48),
    SynthBill(110, 5, day(10), ((1014, 2, 502, "60.00"),)),
    SynthBill(111, 5, day(11), ((1015, 2, 502, "60.00"),)),                     # later: out
    SynthBill(201, 6, day(2), ((2001, 7, 502, "90.00"),), pdf=True, tokens=30),  # other vendor
    SynthBill(202, 6, day(3), ((2002, 10, 502, "15.00"),)),                    # ambiguous code
    SynthBill(301, 5, day(3), ((3001, 2, 502, "70.00"),), company_id=2),       # other company
    SynthBill(401, 7, day(12), ((4001, 2, 501, "65.00"),), pdf=True, tokens=12),  # cold vendor
)
BY_ID = {b.bill_id: b for b in POP}


def label_of(b: SynthBill) -> tuple[int, Optional[int], Decimal]:
    """The U-653b load rule over the bill's lines: (label, project, total |amount|)."""
    coded = [(l, s, Decimal(a or "0")) for l, s, _p, a in b.lines if s is not None]
    label = F.dominant_label(coded)
    lowest = min((l, p) for l, s, p, _a in b.lines if s == label)
    total = sum((abs(Decimal(a or "0")) for _l, _s, _p, a in b.lines), Decimal("0"))
    return label, lowest[1], total


def labelled(company_id: int = COMPANY) -> list[SynthBill]:
    return [b for b in POP if b.company_id == company_id and not b.draft and b.coded]


def has_pdf(b: SynthBill) -> bool:
    return b.pdf and not b.taxpayer_pdf


def expected_prior_rows(
    *, vendor_id: int, as_of: datetime, exclude_bill_id: Optional[int], company_id: int = COMPANY
) -> list[SimpleNamespace]:
    """What ReadVendorPriorBillsForDecisionModel returns for POP (pinned in tests_sqlserver)."""
    out = []
    for b in sorted(labelled(company_id), key=lambda x: (x.created, x.bill_id)):
        if b.vendor_id != vendor_id or not b.created < as_of or b.bill_id == exclude_bill_id:
            continue
        label, project, total = label_of(b)
        out.append(SimpleNamespace(
            BillId=b.bill_id, CreatedDatetime=b.created, DominantSubCostCodeId=label,
            DominantProjectId=project, TotalAbsAmount=total, HasPdf=has_pdf(b),
        ))
    return out


def expected_label_counts(company_id: int = COMPANY, *, before: Optional[datetime] = None) -> dict[int, int]:
    """ReadSubCostCodeLabelCountsForDecisionModel for POP (``before`` = the backtest's as-of cut,
    used only for exact parity; live counts carry no as-of cut - see docs/design/u745)."""
    counts: dict[int, int] = {}
    for b in labelled(company_id):
        if before is not None and not b.created < before:
            continue
        label = label_of(b)[0]
        counts[label] = counts.get(label, 0) + 1
    return counts


# ---------------------------------------------------------------------------- the backtest way
def training_population() -> bt.Population:
    rows = []
    for b in labelled():
        label, project, total = label_of(b)
        n_codes = len({s for _l, s, _p, _a in b.lines if s is not None})
        rows.append(BillRow(b.bill_id, b.vendor_id, b.created, label, project, total, n_codes > 1))
    pop = bt.Population(bills=rows)
    pop.pdf_tokens_by_bill_id = {
        b.bill_id: frozenset(f"t{i}" for i in range(b.tokens)) for b in labelled() if has_pdf(b)
    }
    pop.catalog = {
        sid: bt.CatalogRef(sid, num, OLD, holding) for sid, (num, holding) in SCCS.items()
    }
    return pop


# ---------------------------------------------------------------------------- live catalog
def live_scc_by_id() -> dict[int, SimpleNamespace]:
    # The live loader excludes holding codes.
    return {
        sid: SimpleNamespace(number=num, name=f"n{sid}", created=OLD)
        for sid, (num, holding) in SCCS.items() if not holding
    }


AMBIGUOUS = frozenset({"80.00"})


def live_catalog(as_of: datetime) -> BillCodingCatalog:
    scc_by_id = live_scc_by_id()
    return BillCodingCatalog(
        scc_by_id=scc_by_id,
        scc_number_to_id={r.number: i for i, r in scc_by_id.items()},
        ambiguous_scc_numbers=AMBIGUOUS,
        proj_abbrev_to_id={},
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=[],
        as_of_catalog=build_candidate_sccs_for_bill(
            scc_by_id, as_of=as_of, ambiguous_numbers=AMBIGUOUS
        ),
    )


# ---------------------------------------------------------------------------- neighbours
# (prior bill id, scc, combined, match, same_vendor); priors 150 / 151 / 152 are corpus bills
# outside POP coded Suspense / holding / ambiguous - both filters must drop them.
NEIGHBOURS_109 = (
    (102, 1, 0.90, 0.81, True),
    (201, 7, 0.70, 0.62, False),
    (150, 9, 0.66, 0.60, True),
    (104, 3, 0.50, 0.44, True),
    (152, 10, 0.45, 0.41, False),
    (103, 2, 0.40, 0.30, True),
    (151, 8, 0.30, 0.20, True),
)
EXTRA_CREATED = {150: day(1), 151: day(1), 152: day(2)}


def _created(bill_id: int) -> datetime:
    return BY_ID[bill_id].created if bill_id in BY_ID else EXTRA_CREATED[bill_id]


def live_neighbours(spec) -> list[Neighbour]:
    out = []
    for bid, scc, combined, match, same_vendor in spec:
        bill = SimpleNamespace(bill_id=bid, label_scc_id=scc, created=_created(bid))
        prof = BillTokenProfile(SimpleNamespace(bill=bill), frozenset(), 0.0, 0.0)
        out.append(Neighbour(combined, prof, match, 1.0, same_vendor))
    return out


def training_neighbours(spec) -> list[NeighbourEvidence]:
    return [
        NeighbourEvidence(combined, match, scc, same_vendor, bid, _created(bid))
        for bid, scc, combined, match, same_vendor in spec
    ]


# ---------------------------------------------------------------------------- comparison
MODEL = serve.load_m0_model()


@dataclass
class Scored:
    candidates: list[int]
    features: dict[int, list[float]] = field(default_factory=dict)
    probs: list[float] = field(default_factory=list)
    pick: Optional[int] = None
    raw: float = 0.0
    prob: float = 0.0


def _features_and_probs(ctx) -> Scored:
    s = Scored(list(ctx.candidates))
    s.features = {c: m0_features(ctx, c) for c in s.candidates}
    s.probs = [MODEL.model.prob(s.features[c]) for c in s.candidates]
    return s


def _trained_score(pop, b, mode, ctx) -> Scored:
    """The backtest's own ``score_row`` decides."""
    s = _features_and_probs(ctx)
    d, raw = bt.score_row(MODEL.model, MODEL.calibration, bt.Row(b, mode, ctx))
    assert [raw[c] for c in s.candidates] == s.probs
    s.pick, s.raw, s.prob = d.scc_id, d.raw_prob, d.prob
    return s


def _live_score(ctx) -> Scored:
    """The LIVE scorer (``serve.m0_score``) decides."""
    s = _features_and_probs(ctx)
    d = serve.m0_score(MODEL, ctx)
    s.pick, s.raw, s.prob = d.scc_id, d.raw_prob, d.prob
    return s


def backtest_way(query_id: int, mode: str, spec) -> Scored:
    pop = training_population()
    index = bt.build_index(pop)
    b = next(r for r in pop.bills if r.bill_id == query_id)
    ctx = bt.make_context(pop, index, bt.Choosable(pop.catalog), b, mode,
                          {query_id: training_neighbours(spec)})
    return _trained_score(pop, b, mode, ctx)


def live_inputs(query_id: int, mode: str) -> BillCodingInputs:
    q = BY_ID[query_id]
    _label, project, total = label_of(q)
    return BillCodingInputs(
        stage="tick_full",
        vendor_id=q.vendor_id,
        vendor_name="v",
        chosen_project_id=project if mode == "A" else None,
        bill_text="x",
        query_token_hashes=frozenset(range(q.tokens)),
        amount=total,
        as_of=q.created,
        exclude_bill_id=q.bill_id,
        scope=SimilarScope(company_id=COMPANY, actor_user_id=1, accessible_project_ids=None),
    )


def live_priors(query_id: int, *, training_cut_counts: bool = True) -> serve.M0Priors:
    """``training_cut_counts`` True feeds base-rate counts cut at the query's as-of ON
    PURPOSE (exact parity of the arithmetic); False feeds what the live sproc returns
    (every labelled bill, no as-of cut)."""
    q = BY_ID[query_id]
    rows = expected_prior_rows(vendor_id=q.vendor_id, as_of=q.created, exclude_bill_id=q.bill_id)
    return serve.M0Priors(
        vendor_priors=tuple(prior_bill_from_row(r, vendor_id=q.vendor_id) for r in rows),
        label_counts=expected_label_counts(before=q.created if training_cut_counts else None),
    )


def live_way(query_id: int, mode: str, spec, *, training_cut_counts: bool = True) -> Scored:
    inputs = live_inputs(query_id, mode)
    cat = live_catalog(inputs.as_of)
    _m, _a, _b, filtered, _d = apply_similar_evidence(
        live_neighbours(spec), [], as_of_catalog=cat.as_of_catalog,
        scc_by_id=cat.scc_by_id, ambiguous_numbers=cat.ambiguous_scc_numbers,
    )
    q = BY_ID[query_id]
    query = F.QueryBill(q.bill_id, q.vendor_id, q.created, float(inputs.amount),
                        inputs.chosen_project_id, bool(inputs.query_token_hashes),
                        len(inputs.query_token_hashes))
    ctx = serve.m0_context(
        live_priors(query_id, training_cut_counts=training_cut_counts), query, mode=mode,
        neighbours=serve.neighbour_evidence(filtered),
        choosable=serve.catalog_choosable(
            choosable_nums=choosable_scc_numbers(cat.as_of_catalog), scc_by_id=cat.scc_by_id,
            ambiguous_numbers=cat.ambiguous_scc_numbers,
        ),
    )
    return _live_score(ctx)


class _FakeSimilar:
    def __init__(self, neighbours):
        self.neighbours = neighbours

    def neighbours_for(self, **_kw):
        return list(self.neighbours)


async def _no_cascade(_payload, _ladder, _transport):
    return CascadeResult(False, True, None, None, None, [])


def live_decision(query_id: int, mode: str, spec):
    inputs = live_inputs(query_id, mode)
    deps = BillCodingDeps(
        catalog=live_catalog(inputs.as_of),
        history_rows=[],
        history_suggestion=None,
        candidate_sccs=[],
        similar_service=_FakeSimilar(live_neighbours(spec)),
        run_cascade=_no_cascade,
        m0_priors=live_priors(query_id),
        m0_model=MODEL,
    )
    return asyncio.run(decide_bill_coding(inputs, deps))


# ---------------------------------------------------------------------------- tests
CASES = [
    pytest.param(109, "A", NEIGHBOURS_109, id="mode_a_project_scoped"),
    pytest.param(109, "B", NEIGHBOURS_109, id="mode_b"),
    pytest.param(401, "A", NEIGHBOURS_109[:2], id="cold_vendor_mode_a"),
    pytest.param(401, "B", NEIGHBOURS_109[1:4], id="cold_vendor_mode_b"),
]


def test_choosable_matches_the_backtest_catalog_rule():
    pop = training_population()
    for as_of in (day(10), day(12)):
        trained = bt.Choosable(pop.catalog).at(as_of)
        cat = live_catalog(as_of)
        live = serve.catalog_choosable(
            choosable_nums=choosable_scc_numbers(cat.as_of_catalog), scc_by_id=cat.scc_by_id,
            ambiguous_numbers=cat.ambiguous_scc_numbers,
        )
        assert {c: trained(c) for c in SCCS} == {c: live(c) for c in SCCS}
        assert {c for c in SCCS if live(c)} == {1, 2, 3, 7}


def test_live_priors_reproduce_the_training_index():
    pop = training_population()
    index = bt.build_index(pop)
    for qid in (109, 401):
        q = BY_ID[qid]
        trained = index.vendor_priors(q.vendor_id, as_of=q.created, exclude_bill_id=qid)
        live = list(live_priors(qid).vendor_priors)
        assert live == trained
    ids = [p.bill_id for p in live_priors(109).vendor_priors]
    # drafts, uncoded, self, equal-time and later bills, other company: all out.
    assert ids == [101, 102, 103, 104, 105, 108]
    by = {p.bill_id: p for p in live_priors(109).vendor_priors}
    assert (by[104].label_scc_id, by[104].project_id) == (3, 501)  # dominant by amount
    assert (by[105].label_scc_id, by[105].project_id) == (1, 501)  # tie -> lowest line id
    assert by[108].amount == 150.0 and by[108].has_pdf is False


@pytest.mark.parametrize("query_id,mode,spec", CASES)
def test_train_serve_parity_with_training_cut_base_rates(query_id, mode, spec):
    """Exact parity. NOTE: the live side is fed base-rate counts cut at the query's as-of
    on purpose (live counts carry no as-of cut); the uncut case is the next test."""
    trained = backtest_way(query_id, mode, spec)
    live = live_way(query_id, mode, spec)
    assert trained.candidates and live.candidates == trained.candidates
    assert live.features == trained.features
    assert live.probs == trained.probs
    assert (live.pick, live.raw, live.prob) == (trained.pick, trained.raw, trained.prob)

    decision = live_decision(query_id, mode, spec)
    assert decision.m0_scc_id == trained.pick
    assert decision.m0_confidence == Decimal(str(trained.prob)).quantize(Decimal("0.0001"))
    assert decision.source != "m0"  # shadow only: the rung switch is off


def test_mode_a_history_is_project_scoped():
    trained = backtest_way(109, "A", NEIGHBOURS_109)
    pop = training_population()
    index = bt.build_index(pop)
    b = next(r for r in pop.bills if r.bill_id == 109)
    ctx = bt.make_context(pop, index, bt.Choosable(pop.catalog), b, "A",
                          {109: training_neighbours(NEIGHBOURS_109)})
    assert ctx.scoped_to_project and ctx.history.total == 5  # project 501 priors only
    assert trained.features[1][M0_IDX["F14_mode_a"]] == 1.0


def test_cold_vendor_flags():
    live = live_way(401, "B", NEIGHBOURS_109[1:4])
    for feats in live.features.values():
        assert feats[M0_IDX["F12_cold_vendor"]] == 1.0
        assert feats[M0_IDX["F3_hist_log_total"]] == 0.0


M0_IDX = {name: i for i, name in enumerate(F.M0_FEATURES)}


@pytest.mark.parametrize("query_id,mode,spec", CASES)
def test_live_uncut_label_counts_change_only_the_base_rate(query_id, mode, spec):
    """With the LIVE (uncut) label counts the only feature that may differ is F15."""
    trained = backtest_way(query_id, mode, spec)
    live = live_way(query_id, mode, spec, training_cut_counts=False)
    assert live.candidates == trained.candidates
    f15 = M0_IDX["F15_base_rate"]
    differing = {
        F.M0_FEATURES[i]
        for c in trained.candidates
        for i, (a, b) in enumerate(zip(trained.features[c], live.features[c]))
        if a != b
    }
    assert differing <= {"F15_base_rate"}
    if query_id == 109:  # bills 110 / 111 / 401 come after 109: the uncut counts differ
        assert differing == {"F15_base_rate"}
        assert any(trained.features[c][f15] != live.features[c][f15] for c in trained.candidates)
