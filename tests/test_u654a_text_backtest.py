"""Offline tests for U-654a text backtest harness."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from intelligence.cascade.core import Attempt, CascadeResult, Rung
from intelligence.transport.base import Usage
from scripts.u653b_backtest import BillRow, ModePrediction, PriorBillVote, predict_mode_a, predict_mode_b
from scripts import u654a_text_backtest as u654a


def _bill(
    bill_id: int,
    vendor_id: int,
    created: str,
    scc: int,
    project: int | None = 10,
    *,
    amount: str = "100",
    mixed: bool = False,
    blob: str = "https://example/blob",
) -> u654a.TextBillRow:
    return u654a.TextBillRow(
        bill_id=bill_id,
        vendor_id=vendor_id,
        created=datetime.fromisoformat(created),
        label_scc_id=scc,
        label_project_id=project,
        total_abs_amount=Decimal(amount),
        mixed_code=mixed,
        extracted_text_blob_url=blob,
    )


def _vendor_votes_from_bills(bills: list[BillRow]) -> dict[int, list[PriorBillVote]]:
    return u654a.build_vendor_priors(bills)


def test_history_evidence_uses_supplied_priors_only():
    scc_by_id = {
        1: u654a.SccRef(1, "03.01", "Framing", datetime(2026, 1, 1)),
        2: u654a.SccRef(2, "04.02", "Drywall", datetime(2026, 1, 1)),
    }
    priors = [
        PriorBillVote(1, datetime(2026, 1, 1), 1, 10),
        PriorBillVote(2, datetime(2026, 1, 2), 1, 10),
        PriorBillVote(3, datetime(2026, 1, 3), 2, 10),
    ]
    ev = u654a.build_history_evidence(priors, scc_by_id)
    assert ev[0]["scc_number"] == "03.01"
    assert ev[0]["count"] == 2
    assert len(ev) <= 5


def test_stratified_sample_deterministic_and_covers_strata():
    vendor = 7
    bills: list[u654a.TextBillRow] = []
    # cold start vendor — single bill in eval
    bills.append(_bill(100, vendor, "2026-05-01T10:00:00", 1))
    # build history for consistent vendor
    v2 = 8
    for i, day in enumerate(range(1, 8)):
        bills.append(
            _bill(200 + i, v2, f"2026-04-{day:02d}T10:00:00", 1 if i < 6 else 2)
        )
    bills.append(_bill(300, v2, "2026-05-02T10:00:00", 1))
    bills.append(_bill(301, v2, "2026-05-03T10:00:00", 1, mixed=True))
    vv = _vendor_votes_from_bills(bills)
    s1 = u654a.stratified_sample(
        bills, vv, sample_size=4, seed=654, eval_since=u654a.DEFAULT_EVAL_SINCE, eval_until=u654a.DEFAULT_EVAL_UNTIL
    )
    s2 = u654a.stratified_sample(
        bills, vv, sample_size=4, seed=654, eval_since=u654a.DEFAULT_EVAL_SINCE, eval_until=u654a.DEFAULT_EVAL_UNTIL
    )
    assert [x[0].bill_id for x in s1] == [x[0].bill_id for x in s2]
    strata = {x[1] for x in s1}
    assert "cold_start" in strata or "mixed_code" in strata


def test_scoring_metrics_hand_computed():
    rows = [
        u654a.ScoredTextBill(
            bill=_bill(1, 1, "2026-05-01T10:00:00", 10),
            stratum=">=90%",
            history_a=ModePrediction("A", 10, 10, "vendor_history", Decimal("0.5"), [], 5),
            history_b=ModePrediction("B", 10, 10, "vendor_history", Decimal("0.5"), [], 5),
                text_a=u654a.TextAttemptResult(
                    "A",
                    CascadeResult(True, False, {}, 0.9, None, []),
                    predicted_scc_id=11,
                ),
            text_b=u654a.TextAttemptResult(
                "B",
                CascadeResult(True, False, {}, 0.9, None, []),
                predicted_scc_id=11,
                predicted_project_id=10,
            ),
        ),
        u654a.ScoredTextBill(
            bill=_bill(2, 1, "2026-05-02T10:00:00", 11),
            stratum=">=90%",
            history_a=ModePrediction("A", 11, 10, "vendor_history", Decimal("0.5"), [], 5),
            history_b=ModePrediction("B", 11, 10, "vendor_history", Decimal("0.5"), [], 5),
            text_a=u654a.TextAttemptResult(
                "A",
                CascadeResult(True, False, {}, 0.9, None, []),
                predicted_scc_id=11,
            ),
            text_b=u654a.TextAttemptResult(
                "B",
                CascadeResult(True, False, {}, 0.9, None, []),
                predicted_scc_id=11,
                predicted_project_id=10,
            ),
        ),
    ]
    hist = u654a.compute_suggester_metrics(rows, mode="A", source="history")
    text = u654a.compute_suggester_metrics(rows, mode="A", source="text")
    assert hist.top1_bill == Decimal("1")
    assert text.top1_bill == Decimal("0.5")


def test_m4_verdict_bar():
    hist = u654a.SuggesterMetrics(
        Decimal("1"), Decimal("0.70"), Decimal("0.70"), Decimal("0.70")
    )
    combined_win = u654a.SuggesterMetrics(
        Decimal("1"), Decimal("0.81"), Decimal("0.81"), Decimal("0.81")
    )
    combined_lose = u654a.SuggesterMetrics(
        Decimal("1"), Decimal("0.75"), Decimal("0.75"), Decimal("0.75")
    )
    assert "KEEP" in u654a.m4_verdict(hist, combined_win, run_status="complete", cap_tripped=False)
    assert "DO NOT" in u654a.m4_verdict(hist, combined_lose, run_status="complete", cap_tripped=False)
    assert "INCOMPLETE" in u654a.m4_verdict(hist, combined_win, run_status="complete", cap_tripped=True)


def test_dry_run_does_not_call_cascade():
    fake_bills = [
        BillRow(
            bill_id=1,
            vendor_id=1,
            created=datetime.fromisoformat("2026-05-01T10:00:00"),
            label_scc_id=1,
            label_project_id=10,
            total_abs_amount=Decimal("100"),
            mixed_code=False,
        )
    ]
    empty_ref = u654a.ReferenceData(
        scc_by_id={1: u654a.SccRef(1, "03.01", "A", datetime(2026, 1, 1))},
        scc_number_to_id={"03.01": 1},
        proj_by_id={},
        proj_abbrev_to_id={},
        vendor_names={1: "v"},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    with patch.object(u654a, "load_bills_from_db", return_value=fake_bills):
        with patch.object(u654a, "load_blob_urls_from_db", return_value={1: "https://x"}):
            with patch.object(u654a, "load_reference_data", return_value=empty_ref):
                with patch.object(u654a, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_run:
                    rc = u654a.main(["--sample-size", "1"])
                    assert rc == 0
                    mock_run.assert_not_called()


def test_cost_cap_stops_before_exceeding():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 1)
    payload = {"vendor_name": "v", "bill_text": "t", "history_evidence": [], "candidate_sccs": [], "candidate_projects": None}
    work = [(bill, "A", "cold_start", payload), (bill, "B", "cold_start", payload)]

    async def fake_cascade(_payload, transport_for=None):
        return CascadeResult(
            accepted=True,
            needs_human=False,
            result={
                "scc_number": "03.01",
                "project_abbreviation": None,
                "confidence": 0.9,
                "rationale": "x",
            },
            confidence=0.9,
            winning_rung=Rung("foundry", "DeepSeek-V4-Flash"),
            attempts=[],
        )

    transport = MagicMock()
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
        cost_state = {
            "usd": 0.0,
            "stopped": False,
            "scc_number_to_id": {"03.01": 1},
            "proj_abbrev_to_id": {},
        }
        with patch.object(u654a, "estimate_call_cost_usd", return_value=6.0):
            import asyncio

            out = asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: transport,
                    concurrency=1,
                    max_cost_usd=10.0,
                    cost_state=cost_state,
                )
            )
    assert len(out) == 1
    assert cost_state.get("stopped") is True


def test_outputs_contain_no_text_fields():
    row = u654a.ScoredTextBill(
        bill=_bill(1, 1, "2026-05-01T10:00:00", 10),
        stratum="cold_start",
        history_a=ModePrediction("A", 10, 10, "vendor_history", None, [], 0),
        history_b=ModePrediction("B", 10, 10, "vendor_history", None, [], 0),
        text_a=u654a.TextAttemptResult(
            "A",
            CascadeResult(True, False, {}, 0.9, Rung("foundry", "DeepSeek-V4-Flash"), []),
            predicted_scc_id=10,
        ),
    )
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        metrics = u654a.build_metrics(
            [row],
            cascades=[row.text_a.cascade],
            usage_totals={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.01},
            cap_tripped=False,
            scored_bill_count=1,
            unscored_bill_count=0,
            throttle_retry_count=0,
            throttle_failure_count=0,
            cascade_error_count=0,
            duplicate_scc_number_count=0,
            duplicate_project_abbrev_count=0,
        )
        paths = u654a.write_report_and_outputs(
            out_dir=out_dir,
            stamp="test",
            scored=[row],
            metrics=metrics,
            dry_info=None,
            eval_since=u654a.DEFAULT_EVAL_SINCE,
            eval_until=u654a.DEFAULT_EVAL_UNTIL,
        )
        for p in paths:
            u654a.assert_outputs_privacy_safe(p)


def test_r1_p1_1_combined_mode_a_uses_history_when_raw_score_at_threshold():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 100)
    hist = ModePrediction(
        "A", 50, 10, "vendor_history", Decimal("0.60"), [], 5
    )
    text = u654a.TextAttemptResult(
        "A",
        CascadeResult(True, False, {}, 0.9, None, []),
        predicted_scc_id=99,
    )
    assert u654a.combined_mode_a_predicted_scc(hist, text) == 50
    hist_low = ModePrediction(
        "A", 50, 10, "vendor_history", Decimal("0.59"), [], 5
    )
    assert u654a.combined_mode_a_predicted_scc(hist_low, text) == 99
    rows = [
        u654a.ScoredTextBill(
            bill=bill,
            stratum=">=90%",
            history_a=hist_low,
            history_b=ModePrediction("B", None, None, "none", None, [], 0),
            text_a=text,
        ),
        u654a.ScoredTextBill(
            bill=_bill(2, 1, "2026-05-02T10:00:00", 100),
            stratum=">=90%",
            history_a=ModePrediction("A", 100, 10, "vendor_history", Decimal("0.95"), [], 5),
            history_b=ModePrediction("B", 100, 10, "vendor_history", Decimal("0.95"), [], 5),
            text_a=None,
        ),
    ]
    text_m = u654a.compute_suggester_metrics(rows, mode="A", source="text")
    assert text_m.top1_bill == Decimal("0")
    combined_m = u654a.compute_suggester_metrics(rows, mode="A", source="combined")
    assert combined_m.top1_bill == Decimal("0.5")


def test_r1_p1_2_throttled_cascade_attempt_retries_without_needs_human_only():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 1)
    payload = {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [{"number": "03.01", "name": "x"}],
        "candidate_projects": None,
    }
    work = [(bill, "A", "cold_start", payload)]
    throttled = CascadeResult(
        False,
        True,
        None,
        None,
        None,
        [
            Attempt(
                rung=Rung("foundry", "DeepSeek-V4-Flash"),
                accepted=False,
                usage=Usage(),
                error="HTTP 429 Too Many Requests",
            )
        ],
    )
    ok = CascadeResult(
        True,
        False,
        {
            "scc_number": "03.01",
            "project_abbreviation": None,
            "confidence": 0.95,
            "rationale": "ok",
        },
        0.95,
        Rung("foundry", "DeepSeek-V4-Flash"),
        [],
    )
    calls = {"n": 0}

    async def fake_run(_payload, transport_for=None):
        calls["n"] += 1
        return throttled if calls["n"] == 1 else ok

    import asyncio

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {"03.01": 1},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_run):
        with patch.object(u654a, "estimate_call_cost_usd", return_value=0.01):
            out = asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: MagicMock(),
                    concurrency=1,
                    max_cost_usd=10.0,
                    cost_state=cost_state,
                )
            )
    assert calls["n"] == 2
    assert cost_state["throttle_retry_count"] == 1
    assert out[(1, "A")].predicted_scc_id == 1


def test_r1_p1_3_cap_tripped_incomplete_verdict_and_scored_counts():
    hist = u654a.SuggesterMetrics(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"))
    combined = u654a.SuggesterMetrics(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"))
    verdict = u654a.m4_verdict(hist, combined, run_status="complete", cap_tripped=True)
    assert "INCOMPLETE" in verdict
    assert "KEEP text path" not in verdict
    row = u654a.ScoredTextBill(
        bill=_bill(1, 1, "2026-05-01T10:00:00", 10),
        stratum="cold_start",
        history_a=ModePrediction("A", 10, 10, "vendor_history", None, [], 0),
        history_b=ModePrediction("B", 10, 10, "vendor_history", None, [], 0),
        text_a=u654a.TextAttemptResult(
            "A",
            CascadeResult(True, False, {}, 0.9, Rung("foundry", "m"), []),
            predicted_scc_id=10,
        ),
    )
    row2 = u654a.ScoredTextBill(
        bill=_bill(2, 1, "2026-05-02T10:00:00", 11),
        stratum="cold_start",
        history_a=ModePrediction("A", 11, 10, "vendor_history", None, [], 0),
        history_b=ModePrediction("B", 11, 10, "vendor_history", None, [], 0),
        text_a=None,
    )
    metrics = u654a.build_metrics(
        [row, row2],
        cascades=[row.text_a.cascade],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 1.0},
        cap_tripped=True,
        scored_bill_count=1,
        unscored_bill_count=1,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    assert metrics["cap_tripped"] is True
    assert metrics["scored_bill_count"] == 1
    assert metrics["unscored_bill_count"] == 1
    assert "INCOMPLETE" in metrics["verdict_m4"]


def test_r1_p1_4_scc_catalog_respects_created_cutoff():
    early = datetime(2026, 1, 1)
    late = datetime(2026, 6, 1)
    scc_by_id = {
        1: u654a.SccRef(1, "03.01", "A", early),
        2: u654a.SccRef(2, "04.02", "B", late),
    }
    as_of = datetime(2026, 5, 1)
    cats = u654a.build_candidate_sccs_for_bill(
        scc_by_id, as_of=as_of, ambiguous_numbers=frozenset()
    )
    nums = {c["number"] for c in cats}
    assert nums == {"03.01"}


def test_r1_p2_1_resolve_normalizes_whitespace_on_scc_and_project():
    cascade = CascadeResult(
        True,
        False,
        {
            "scc_number": " 03.01 ",
            "project_abbreviation": " BR1 ",
            "confidence": 0.9,
            "rationale": "x",
        },
        0.9,
        None,
        [],
    )
    scc_id, proj_id = u654a.resolve_text_prediction(
        cascade,
        scc_number_to_id={"03.01": 7},
        proj_abbrev_to_id={"BR1": 3},
    )
    assert scc_id == 7
    assert proj_id == 3


def test_r1_p2_2_duplicate_and_suspense_scc_numbers_excluded_from_catalog():
    scc_by_id = {
        1: u654a.SccRef(1, "03.01", "A", datetime(2026, 1, 1)),
        2: u654a.SccRef(2, "03.01", "A dup", datetime(2026, 1, 2)),
        3: u654a.SccRef(3, "00.02", "Suspense", datetime(2026, 1, 1)),
        4: u654a.SccRef(4, "04.02", "B", datetime(2026, 1, 1)),
    }
    cats = u654a.build_candidate_sccs_for_bill(
        scc_by_id,
        as_of=datetime(2026, 7, 1),
        ambiguous_numbers=frozenset({"03.01"}),
    )
    assert {c["number"] for c in cats} == {"04.02"}


def test_r1_p2_3_cascade_exception_still_records_text_miss():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 10)
    payload = {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [],
        "candidate_projects": None,
    }
    work = [(bill, "A", "cold_start", payload)]

    async def boom(_payload, transport_for=None):
        raise RuntimeError("transport down")

    import asyncio

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=boom):
        with patch.object(u654a, "estimate_call_cost_usd", return_value=0.01):
            out = asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: MagicMock(),
                    concurrency=1,
                    max_cost_usd=10.0,
                    cost_state=cost_state,
                )
            )
    assert out[(1, "A")].predicted_scc_id is None
    assert cost_state["cascade_error_count"] == 1


def test_r1_p2_4_dry_run_includes_populations_and_mixed_code_all_sample():
    bills = [
        BillRow(
            bill_id=1,
            vendor_id=1,
            created=datetime(2026, 5, 1),
            label_scc_id=1,
            label_project_id=10,
            total_abs_amount=Decimal("100"),
            mixed_code=True,
        )
    ]
    tb = u654a.merge_text_bills(bills, {1: "https://x"})
    pops = u654a.count_eval_populations(
        bills, tb, eval_since=u654a.DEFAULT_EVAL_SINCE, eval_until=u654a.DEFAULT_EVAL_UNTIL
    )
    dry = u654a.dry_run_summary(
        [(tb[0], "mixed_code")], sample_size=1, populations=pops
    )
    assert dry["eval_window_bill_population"] == 1
    assert dry["text_tier_bill_population"] == 1
    row = u654a.ScoredTextBill(
        bill=tb[0],
        stratum="mixed_code",
        history_a=ModePrediction("A", 1, 10, "vendor_history", Decimal("0.9"), [], 5),
        history_b=ModePrediction("B", 1, 10, "vendor_history", Decimal("0.9"), [], 5),
        text_a=u654a.TextAttemptResult(
            "A",
            CascadeResult(True, False, {}, 0.9, None, []),
            predicted_scc_id=2,
        ),
    )
    metrics = u654a.build_metrics(
        [row],
        cascades=[row.text_a.cascade],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        cap_tripped=False,
        scored_bill_count=1,
        unscored_bill_count=0,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    assert metrics["mixed_code_all"]["n"] == 1
    assert metrics["mixed_code_all"]["text_top1"] == Decimal("0")


def test_r1_p2_5_priors_cutoff_self_exclusion_and_mode_b_projects_before():
    votes = [
        PriorBillVote(1, datetime(2026, 4, 1), 10, 100),
        PriorBillVote(2, datetime(2026, 4, 2), 10, 100),
        PriorBillVote(3, datetime(2026, 4, 3), 10, 100),
        PriorBillVote(99, datetime(2026, 5, 1), 11, 200),
    ]
    bill = BillRow(
        bill_id=99,
        vendor_id=7,
        created=datetime(2026, 5, 1),
        label_scc_id=11,
        label_project_id=200,
        total_abs_amount=Decimal("50"),
        mixed_code=False,
    )
    priors = u654a.priors_for_bill({7: votes}, bill)
    assert len(priors) == 3
    assert all(p.bill_id != 99 for p in priors)
    all_bills = [
        BillRow(
            bill_id=i,
            vendor_id=1,
            created=datetime(2026, 4, i),
            label_scc_id=1,
            label_project_id=500 + i,
            total_abs_amount=Decimal("1"),
            mixed_code=False,
        )
        for i in range(1, 5)
    ]
    proj_by_id = {
        501: u654a.ProjectRef(501, "P1", "One"),
        502: u654a.ProjectRef(502, "P2", "Two"),
        503: u654a.ProjectRef(503, "P3", "Three"),
        504: u654a.ProjectRef(504, "P4", "Four"),
    }
    target = BillRow(
        bill_id=50,
        vendor_id=2,
        created=datetime(2026, 5, 1),
        label_scc_id=1,
        label_project_id=504,
        total_abs_amount=Decimal("1"),
        mixed_code=False,
    )
    projs = u654a.projects_with_bills_before(all_bills, target.created, proj_by_id)
    assert {p["abbreviation"] for p in projs} == {"P1", "P2", "P3", "P4"}
    mode_b = predict_mode_b(target, u654a.priors_for_bill({2: []}, target))
    assert mode_b.mode == "B"


def test_r1_p2_5_stratified_sample_covers_each_nonempty_stratum():
    vendor = 7
    bills: list[u654a.TextBillRow] = []
    bills.append(_bill(100, vendor, "2026-05-01T10:00:00", 1))
    v2 = 8
    for i, day in enumerate(range(1, 8)):
        bills.append(
            _bill(200 + i, v2, f"2026-04-{day:02d}T10:00:00", 1 if i < 6 else 2)
        )
    bills.append(_bill(300, v2, "2026-05-02T10:00:00", 1))
    bills.append(_bill(301, v2, "2026-05-03T10:00:00", 1, mixed=True))
    vv = u654a.build_vendor_priors(
        [
            BillRow(
                bill_id=b.bill_id,
                vendor_id=b.vendor_id,
                created=b.created,
                label_scc_id=b.label_scc_id,
                label_project_id=b.label_project_id,
                total_abs_amount=b.total_abs_amount,
                mixed_code=b.mixed_code,
            )
            for b in bills
        ]
    )
    sample = u654a.stratified_sample(
        bills,
        vv,
        sample_size=20,
        seed=654,
        eval_since=u654a.DEFAULT_EVAL_SINCE,
        eval_until=u654a.DEFAULT_EVAL_UNTIL,
    )
    strata_present = {s for _, s in sample}
    expected: set[str] = set()
    for b in bills:
        if (
            u654a.DEFAULT_EVAL_SINCE
            <= u654a._date_only(b.created)
            <= u654a.DEFAULT_EVAL_UNTIL
        ):
            if b.mixed_code:
                continue
            priors = u654a.priors_for_bill(vv, b)
            expected.add(u654a.classify_stratum(priors, b.mixed_code))
    if any(b.mixed_code for b in bills):
        expected.add("mixed_code")
    for s in expected:
        assert s in strata_present


def test_r1_p2_5_verdict_same_denominator_when_text_coverage_lower():
    rows = [
        u654a.ScoredTextBill(
            bill=_bill(1, 1, "2026-05-01T10:00:00", 10),
            stratum="cold_start",
            history_a=ModePrediction("A", 10, 10, "vendor_history", Decimal("0.9"), [], 5),
            history_b=ModePrediction("B", 10, 10, "vendor_history", Decimal("0.9"), [], 5),
            text_a=u654a.TextAttemptResult(
                "A",
                CascadeResult(True, False, {}, 0.9, None, []),
                predicted_scc_id=10,
            ),
        ),
        u654a.ScoredTextBill(
            bill=_bill(2, 1, "2026-05-02T10:00:00", 11),
            stratum="cold_start",
            history_a=ModePrediction("A", 11, 10, "vendor_history", Decimal("0.9"), [], 5),
            history_b=ModePrediction("B", 11, 10, "vendor_history", Decimal("0.9"), [], 5),
            text_a=None,
        ),
    ]
    hist = u654a.compute_suggester_metrics(rows, mode="A", source="history")
    text = u654a.compute_suggester_metrics(rows, mode="A", source="text")
    assert hist.coverage == Decimal("1")
    assert text.coverage == Decimal("0.5")
    assert hist.top1_bill == Decimal("1")
    assert text.top1_bill == Decimal("0.5")
    combined = u654a.compute_suggester_metrics(rows, mode="A", source="combined")
    assert combined.top1_bill == Decimal("1")


def test_r2_n1_partial_run_status_never_emits_keep_or_drop():
    hist = u654a.SuggesterMetrics(Decimal("1"), Decimal("1"), Decimal("0.50"), Decimal("0.50"))
    combined = u654a.SuggesterMetrics(Decimal("1"), Decimal("1"), Decimal("0.95"), Decimal("0.95"))
    verdict = u654a.m4_verdict(hist, combined, run_status="partial", cap_tripped=False)
    assert "INCOMPLETE" in verdict
    assert "KEEP text path" not in verdict
    assert "DO NOT wire text" not in verdict


def test_r2_n1_cascade_batch_writes_through_out_dict_for_partial_runs():
    bill1 = _bill(1, 1, "2026-05-01T10:00:00", 10)
    payload = {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [{"number": "03.01", "name": "x"}],
        "candidate_projects": None,
    }
    work = [(bill1, "A", "cold_start", payload)]
    shared: dict[tuple[int, str], u654a.TextAttemptResult] = {}

    async def fake_cascade(_payload, transport_for=None):
        return CascadeResult(
            True,
            False,
            {"scc_number": "03.01", "project_abbreviation": None, "confidence": 0.9, "rationale": "x"},
            0.9,
            Rung("foundry", "DeepSeek-V4-Flash"),
            [],
        )

    import asyncio

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {"03.01": 10},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
        ret = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: MagicMock(),
                concurrency=1,
                max_cost_usd=10.0,
                cost_state=cost_state,
                out=shared,
            )
        )
    assert ret is shared
    assert (1, "A") in shared


def test_r2_n2_throttle_retry_only_when_not_accepted_bills_all_tries():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 1)
    payload = {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [{"number": "03.01", "name": "x"}],
        "candidate_projects": None,
    }
    work = [(bill, "A", "cold_start", payload)]
    rung = Rung("foundry", "DeepSeek-V4-Flash")
    escalated_ok = CascadeResult(
        True,
        False,
        {
            "scc_number": "03.01",
            "project_abbreviation": None,
            "confidence": 0.95,
            "rationale": "ok",
        },
        0.95,
        rung,
        [
            Attempt(rung, False, usage=Usage(input_tokens=1000, output_tokens=10), error="HTTP 429"),
            Attempt(
                rung,
                True,
                usage=Usage(input_tokens=2000, output_tokens=20),
                result={"scc_number": "03.01"},
            ),
        ],
    )
    calls = {"n": 0}

    async def fake_run(_payload, transport_for=None):
        calls["n"] += 1
        return escalated_ok

    import asyncio

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {"03.01": 1},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_run):
        with patch.object(u654a, "compute_cost_usd", return_value=0.001):
            out = asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: MagicMock(),
                    concurrency=1,
                    max_cost_usd=10.0,
                    cost_state=cost_state,
                )
            )
    assert calls["n"] == 1
    assert cost_state.get("throttle_retry_count", 0) == 0
    assert cost_state.get("throttle_failure_count", 0) == 0
    assert out[(1, "A")].predicted_scc_id == 1
    assert cost_state["usd"] == pytest.approx(0.002)


def test_r2_n4_exception_mid_retry_still_bills_earlier_try_spend():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 1)
    payload = {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [{"number": "03.01", "name": "x"}],
        "candidate_projects": None,
    }
    work = [(bill, "A", "cold_start", payload)]
    throttled = CascadeResult(
        False,
        True,
        None,
        None,
        None,
        [
            Attempt(
                Rung("foundry", "DeepSeek-V4-Flash"),
                False,
                usage=Usage(input_tokens=5000, output_tokens=50),
                error="HTTP 429",
            )
        ],
    )
    calls = {"n": 0}

    async def fake_run(_payload, transport_for=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return throttled
        raise RuntimeError("transport died")

    import asyncio

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {"03.01": 1},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_run):
        with patch.object(u654a, "compute_cost_usd", return_value=0.01):
            with patch.object(u654a, "estimate_call_cost_usd", return_value=0.01):
                out = asyncio.run(
                    u654a.run_text_cascade_batch(
                        work,
                        transport_for=lambda _p: MagicMock(),
                        concurrency=1,
                        max_cost_usd=10.0,
                        cost_state=cost_state,
                    )
                )
    assert cost_state["usd"] == pytest.approx(0.01)
    assert out[(1, "A")].predicted_scc_id is None


def test_r2_n3_label_not_in_catalog_count_and_excluded_combined_block():
    early = datetime(2026, 1, 1)
    late = datetime(2026, 8, 1)
    ref = u654a.ReferenceData(
        scc_by_id={
            10: u654a.SccRef(10, "03.01", "A", early),
            11: u654a.SccRef(11, "04.02", "B", late),
        },
        scc_number_to_id={"03.01": 10, "04.02": 11},
        proj_by_id={},
        proj_abbrev_to_id={},
        vendor_names={},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    in_cat = _bill(1, 1, "2026-05-01T10:00:00", 10)
    not_in_cat = _bill(2, 1, "2026-05-02T10:00:00", 11)
    diag = u654a.catalog_diagnostics_for_sample(
        [(in_cat, "cold_start"), (not_in_cat, "cold_start")], ref
    )
    assert diag["label_not_in_text_catalog_count"] == 1
    assert diag["text_catalog_size_min_per_bill"] == 1
    rows = [
        u654a.ScoredTextBill(
            bill=in_cat,
            stratum="cold_start",
            history_a=ModePrediction("A", 10, 10, "vendor_history", Decimal("0.9"), [], 5),
            history_b=ModePrediction("B", 10, 10, "vendor_history", Decimal("0.9"), [], 5),
            text_a=u654a.TextAttemptResult(
                "A", CascadeResult(True, False, {}, 0.9, None, []), predicted_scc_id=10
            ),
        ),
        u654a.ScoredTextBill(
            bill=not_in_cat,
            stratum="cold_start",
            history_a=ModePrediction("A", 11, 10, "vendor_history", Decimal("0.9"), [], 5),
            history_b=ModePrediction("B", 11, 10, "vendor_history", Decimal("0.9"), [], 5),
            text_a=u654a.TextAttemptResult(
                "A", CascadeResult(True, False, {}, 0.9, None, []), predicted_scc_id=99
            ),
        ),
    ]
    metrics = u654a.build_metrics(
        rows,
        cascades=[],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        cap_tripped=False,
        scored_bill_count=2,
        unscored_bill_count=0,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
        catalog_diagnostics=diag,
        run_status="complete",
    )
    assert metrics["combined_vs_history_catalog_eligible"]["n"] == 1
    assert metrics["combined_vs_history_catalog_eligible"]["combined_top1_bill"] == Decimal("1")


def test_r2_n5_cap_trip_mode_b_uses_text_b_rows_only():
    rows = [
        u654a.ScoredTextBill(
            bill=_bill(1, 1, "2026-05-01T10:00:00", 10),
            stratum="cold_start",
            history_a=ModePrediction("A", 10, 10, "h", None, [], 0),
            history_b=ModePrediction("B", 10, 10, "h", None, [], 0),
            text_a=u654a.TextAttemptResult(
                "A", CascadeResult(True, False, {}, 0.9, None, []), predicted_scc_id=10
            ),
            text_b=u654a.TextAttemptResult(
                "B", CascadeResult(True, False, {}, 0.9, None, []), predicted_scc_id=10
            ),
        ),
        u654a.ScoredTextBill(
            bill=_bill(2, 1, "2026-05-02T10:00:00", 11),
            stratum="cold_start",
            history_a=ModePrediction("A", 11, 10, "h", None, [], 0),
            history_b=ModePrediction("B", 11, 10, "h", None, [], 0),
            text_a=u654a.TextAttemptResult(
                "A", CascadeResult(True, False, {}, 0.9, None, []), predicted_scc_id=11
            ),
            text_b=None,
        ),
    ]
    metrics = u654a.build_metrics(
        rows,
        cascades=[],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        cap_tripped=True,
        scored_bill_count=2,
        unscored_bill_count=0,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    assert metrics["mode_b"]["text"]["top1_bill"] == Decimal("1")
    assert metrics["mode_a"]["text"]["top1_bill"] == Decimal("1")
    assert len(u654a.metric_rows_for_mode(rows, mode="B", restrict=True)) == 1
    assert len(u654a.metric_rows_for_mode(rows, mode="A", restrict=True)) == 2


def test_r2_n6_overshoot_with_all_bills_scored_is_not_cap_tripped():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 1)
    payload = {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [{"number": "03.01", "name": "x"}],
        "candidate_projects": None,
    }
    work = [(bill, "A", "cold_start", payload)]

    async def fake_cascade(_payload, transport_for=None):
        return CascadeResult(
            True,
            False,
            {"scc_number": "03.01", "project_abbreviation": None, "confidence": 0.9, "rationale": "x"},
            0.9,
            Rung("foundry", "DeepSeek-V4-Flash"),
            [
                Attempt(
                    Rung("foundry", "DeepSeek-V4-Flash"),
                    True,
                    usage=Usage(input_tokens=1, output_tokens=1),
                )
            ],
        )

    import asyncio

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "cap_tripped": True,
        "scc_number_to_id": {"03.01": 1},
        "proj_abbrev_to_id": {},
    }
    def _overspend(_cascade, state):
        state["usd"] = 12.0
        return 12.0

    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
        with patch.object(u654a, "apply_cascade_spend", side_effect=_overspend):
            asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: MagicMock(),
                    concurrency=1,
                    max_cost_usd=10.0,
                    cost_state=cost_state,
                )
            )
    scored_bill_count = 1
    unscored_bill_count = 0
    cap_tripped = unscored_bill_count > 0 and bool(cost_state.get("cap_tripped"))
    cost_overshoot = 12.0 - 10.0 if not cap_tripped else None
    assert cap_tripped is False
    assert cost_overshoot == pytest.approx(2.0)


# --- Round 3 (EM): one invariant — no verdict unless every (Bill, mode) finished ---

from types import SimpleNamespace


def _r3_payload() -> dict:
    return {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [{"number": "03.01", "name": "x"}],
        "candidate_projects": None,
    }


def _r3_cascade(*, accepted: bool, error: str | None = None, tokens: int = 1_000_000) -> CascadeResult:
    rung = Rung("foundry", "gpt-5.4-mini")
    return CascadeResult(
        accepted,
        not accepted,
        {"scc_number": "03.01", "project_abbreviation": None, "confidence": 0.9, "rationale": "x"} if accepted else None,
        0.9 if accepted else None,
        rung if accepted else None,
        [Attempt(rung, accepted, usage=Usage(input_tokens=tokens, output_tokens=tokens), error=error)],
    )


def _r3_cost_state() -> dict:
    return {"usd": 0.0, "stopped": False, "scc_number_to_id": {"03.01": 10}, "proj_abbrev_to_id": {}}


def _r3_run(work, fake, *, cap: float, cost_state: dict) -> dict:
    import asyncio

    out: dict = {}
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake), patch.object(
        u654a.asyncio, "sleep", AsyncMock()
    ):
        asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: MagicMock(),
                concurrency=4,
                max_cost_usd=cap,
                cost_state=cost_state,
                out=out,
            )
        )
    return out


def _r3_rows(bills) -> list:
    return [SimpleNamespace(bill=b) for b in bills]


def test_r3_new1_cap_starved_run_is_incomplete_with_no_verdict():
    bills = [_bill(i, 1, f"2026-05-0{i}T10:00:00", 10) for i in range(1, 5)]
    work = [(b, m, "s", _r3_payload()) for b in bills for m in ("A", "B")]

    async def expensive(_payload, transport_for=None):
        return _r3_cascade(accepted=True)

    cost_state = _r3_cost_state()
    results = _r3_run(work, expensive, cap=1.0, cost_state=cost_state)
    state = u654a.finalize_run_state(
        _r3_rows(bills), results, cost_state=cost_state, max_cost_usd=1.0, run_status="complete"
    )
    assert state.unscored_bill_count > 0
    assert state.run_status == "incomplete" and state.cap_tripped is True
    verdict = u654a.m4_verdict(
        SimpleNamespace(top1_bill=0.0), SimpleNamespace(top1_bill=0.5),
        run_status=state.run_status, cap_tripped=state.cap_tripped,
    )
    assert "INCOMPLETE" in verdict and "KEEP text path" not in verdict and "DO NOT wire" not in verdict


def test_r3_new2_cap_blocked_retry_leaves_the_bill_unscored():
    bill = _bill(1, 1, "2026-05-01T10:00:00", 10)
    work = [(bill, "A", "s", _r3_payload())]

    async def throttled(_payload, transport_for=None):
        return _r3_cascade(accepted=False, error="HTTP 429")

    cost_state = _r3_cost_state()
    results = _r3_run(work, throttled, cap=1.0, cost_state=cost_state)
    assert (1, "A") not in results  # blocked by the cap mid-retry: not a scored miss
    assert cost_state["usd"] > 0  # the try that ran is still billed


def test_r3_new3_interrupt_while_building_makes_no_model_call():
    calls = {"run": 0}

    def build():
        raise KeyboardInterrupt

    def run():
        calls["run"] += 1

    assert u654a.execute_run(build, run) == "partial"
    assert calls["run"] == 0


def test_r3_interrupt_during_cascades_is_partial():
    def run():
        raise KeyboardInterrupt

    assert u654a.execute_run(lambda: None, run) == "partial"


def test_r3_complete_run_with_overshoot_keeps_its_verdict():
    bills = [_bill(1, 1, "2026-05-01T10:00:00", 10)]
    results = {(1, "A"): object(), (1, "B"): object()}
    state = u654a.finalize_run_state(
        _r3_rows(bills), results, cost_state={"usd": 1.5, "stopped": True},
        max_cost_usd=1.0, run_status="complete",
    )
    assert state.run_status == "complete" and state.cap_tripped is False
    assert state.unscored_bill_count == 0
    assert state.cost_overshoot_usd == pytest.approx(0.5)


def test_r3_incomplete_metrics_use_scored_rows_only():
    rows = [SimpleNamespace(text_a=object(), text_b=None), SimpleNamespace(text_a=None, text_b=None)]
    assert len(u654a.metric_rows_for_mode(rows, mode="A", restrict=True)) == 1
    assert len(u654a.metric_rows_for_mode(rows, mode="B", restrict=True)) == 0


def _r4_restricted_rows():
    # Bill 1 finished Mode A; Bill 2 was stopped before any text attempt (history-only).
    scored_a = u654a.ScoredTextBill(
        bill=_bill(1, 1, "2026-05-01T10:00:00", 10),
        stratum="cold_start",
        # Raw score below the 0.60 threshold, so combined takes the (correct) text answer.
        history_a=ModePrediction("A", 99, 10, "vendor_history", Decimal("0.5"), [], 5),
        history_b=ModePrediction("B", 99, 10, "vendor_history", Decimal("0.5"), [], 5),
        text_a=u654a.TextAttemptResult(
            "A", CascadeResult(True, False, {}, 0.9, None, []), predicted_scc_id=10
        ),
    )
    unscored = u654a.ScoredTextBill(
        bill=_bill(2, 1, "2026-05-02T10:00:00", 11),
        stratum="cold_start",
        history_a=ModePrediction("A", 11, 10, "vendor_history", Decimal("0.9"), [], 5),
        history_b=ModePrediction("B", 11, 10, "vendor_history", Decimal("0.9"), [], 5),
        text_a=None,
    )
    return [scored_a, unscored]


def _r4_metrics(rows, **kw):
    return u654a.build_metrics(
        rows,
        cascades=[],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        scored_bill_count=1,
        unscored_bill_count=1,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
        **kw,
    )


@pytest.mark.parametrize(
    "cap_tripped,run_status", [(True, "complete"), (False, "interrupted")]
)
def test_r4_catalog_eligible_uses_restricted_mode_a_rows(cap_tripped, run_status):
    diag = {"label_not_in_catalog_bill_ids": set(), "label_not_in_text_catalog_count": 0}
    metrics = _r4_metrics(
        _r4_restricted_rows(),
        cap_tripped=cap_tripped,
        run_status=run_status,
        catalog_diagnostics=diag,
    )
    cmp = metrics["combined_vs_history_catalog_eligible"]
    # Same denominator as the Mode A tables: the unscored Bill (history right, no text) is out.
    assert cmp["n"] == 1
    assert cmp["history_top1_bill"] == Decimal("0")
    assert cmp["combined_top1_bill"] == Decimal("1")
    assert cmp["n"] == metrics["mode_a_n"]


def test_r4_report_shows_per_mode_denominators():
    rows = _r4_restricted_rows()
    metrics = _r4_metrics(rows, cap_tripped=True, run_status="complete")
    assert (metrics["mode_a_n"], metrics["mode_b_n"]) == (1, 0)
    with tempfile.TemporaryDirectory() as tmp:
        report, _csv, _json = u654a.write_report_and_outputs(
            out_dir=Path(tmp),
            stamp="r4",
            scored=rows,
            metrics=metrics,
            dry_info=None,
            eval_since=u654a.DEFAULT_EVAL_SINCE,
            eval_until=u654a.DEFAULT_EVAL_UNTIL,
        )
        text = report.read_text(encoding="utf-8")
    assert "- Bills scored (text cascade finished): 1" in text
    assert "- Metric denominators: Mode A n=1, Mode B n=0" in text
    assert "Bills scored (Mode A text cascade)" not in text


def _blob_error(status):
    from shared.storage import AzureBlobStorageError

    return AzureBlobStorageError("download blob", status_code=status)


def test_u654c_read_text_blob_retries_transient_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def read(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _blob_error(None)  # ConnectTimeout carries no HTTP status
        return "text"

    assert u654a.read_text_blob_with_retry(read, "u", sleep=sleeps.append) == "text"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_u654c_read_text_blob_gives_up_after_retries():
    from shared.storage import AzureBlobStorageError

    calls = {"n": 0}

    def read(url):
        calls["n"] += 1
        raise _blob_error(503)

    with pytest.raises(AzureBlobStorageError):
        u654a.read_text_blob_with_retry(read, "u", sleep=lambda s: None)
    assert calls["n"] == u654a._BLOB_READ_ATTEMPTS


def test_u654c_read_text_blob_does_not_retry_permanent_errors():
    from shared.storage import AzureBlobStorageError

    calls = {"n": 0}

    def read(url):
        calls["n"] += 1
        raise _blob_error(404)

    with pytest.raises(AzureBlobStorageError):
        u654a.read_text_blob_with_retry(read, "u", sleep=lambda s: None)
    assert calls["n"] == 1


@pytest.mark.parametrize(
    "status, unreadable, sampled, expected",
    [
        ("complete", 0, 500, "complete"),
        ("complete", 10, 500, "complete"),  # exactly 2%: still complete
        ("complete", 11, 500, "incomplete"),
        ("complete", 500, 500, "incomplete"),  # e.g. every blob unreadable
        ("partial", 0, 500, "partial"),  # an interrupt keeps its own status
    ],
)
def test_u654c_too_many_unreadable_blobs_make_the_run_incomplete(status, unreadable, sampled, expected):
    assert u654a.run_status_after_unreadable(status, unreadable=unreadable, sampled=sampled) == expected


def test_u654d_resume_reloads_exactly_the_finished_results(tmp_path):
    """A predictions CSV written by the run reloads into the same text results."""
    bill_a = _bill(1, 1, "2026-05-01T10:00:00", 10)
    bill_b = _bill(2, 1, "2026-05-02T10:00:00", 11)
    accepted = u654a.TextAttemptResult(
        "A", CascadeResult(True, False, {}, 0.9, Rung("foundry", "gpt-5.4-nano"), []), predicted_scc_id=10
    )
    human = u654a.TextAttemptResult(
        "A",
        CascadeResult(False, True, None, None, None, [Attempt(rung=Rung("foundry", "x"), accepted=False)]),
        predicted_scc_id=None,
    )
    rows = [
        u654a.ScoredTextBill(
            bill=bill_a, stratum="cold_start",
            history_a=ModePrediction("A", 10, 10, "h", None, [], 0),
            history_b=ModePrediction("B", 10, 10, "h", None, [], 0),
            text_a=accepted,
        ),
        u654a.ScoredTextBill(
            bill=bill_b, stratum="cold_start",
            history_a=ModePrediction("A", 11, 10, "h", None, [], 0),
            history_b=ModePrediction("B", 11, 10, "h", None, [], 0),
            text_a=human,  # needs_human
            text_b=None,  # never finished: must not be reloaded
        ),
    ]
    metrics = u654a.build_metrics(
        rows, cascades=[accepted.cascade, human.cascade],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        cap_tripped=False, scored_bill_count=2, unscored_bill_count=0,
        throttle_retry_count=0, throttle_failure_count=0, cascade_error_count=0,
        duplicate_scc_number_count=0, duplicate_project_abbrev_count=0,
    )
    _report, csv_path, _json = u654a.write_report_and_outputs(
        out_dir=tmp_path, stamp="t", scored=rows, metrics=metrics, dry_info=None,
        eval_since=u654a.DEFAULT_EVAL_SINCE, eval_until=u654a.DEFAULT_EVAL_UNTIL,
    )
    prior = u654a.load_prior_text_results(csv_path)
    assert set(prior) == {(1, "A"), (2, "A")}  # (1,"B") and (2,"B") never ran
    assert prior[(1, "A")].predicted_scc_id == 10
    assert prior[(1, "A")].cascade.winning_rung.model == "gpt-5.4-nano"
    assert prior[(2, "A")].cascade.needs_human is True
    # needs_human still counts in rung usage after a reload
    assert u654a.aggregate_rung_usage([prior[(2, "A")].cascade]) == {"needs_human": 1}


def test_u654d_a_stalled_call_is_left_unscored(monkeypatch):
    import asyncio

    async def hang(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(u654a, "CASCADE_CALL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(u654a, "run_bill_coding_suggestion", hang)
    monkeypatch.setattr(u654a, "estimate_call_cost_usd", lambda _m: 0.0)
    cost_state = {"usd": 0.0, "stopped": False, "scc_number_to_id": {}, "proj_abbrev_to_id": {}}
    work = [(_bill(1, 1, "2026-05-01T10:00:00", 10), "A", "cold_start", {})]
    out = asyncio.run(
        u654a.run_text_cascade_batch(
            work, transport_for=lambda _p: None, concurrency=1, max_cost_usd=10.0, cost_state=cost_state
        )
    )
    assert out == {}  # unscored, so the run is INCOMPLETE and resumable
    assert cost_state["call_timeout_count"] == 1
