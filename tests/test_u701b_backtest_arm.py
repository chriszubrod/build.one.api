"""U-701b: escalation ladder, line-items rule, history override, extra bills (mocked)."""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from intelligence.cascade.bill_coding import BILL_CODING_SUGGESTION_TASK
from intelligence.cascade.core import Attempt, CascadeResult, Rung
from scripts.u653b_backtest import BillRow, ModePrediction
from scripts import u654a_text_backtest as u654a


def _bill(bid: int = 1) -> u654a.TextBillRow:
    return u654a.TextBillRow(
        bill_id=bid,
        vendor_id=1,
        created=datetime(2026, 5, 1),
        label_scc_id=100,
        label_project_id=10,
        total_abs_amount=Decimal("50"),
        mixed_code=False,
        extracted_text_blob_url="http://blob",
    )


def _valid_cascade(conf: float, scc_number: str = "01.01") -> CascadeResult:
    body = {"scc_number": scc_number, "confidence": conf}
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result=body,
        confidence=conf,
        winning_rung=Rung("anthropic", "claude-haiku-5-5"),
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=True,
                validated=True,
                confidence=conf,
                result=body,
            )
        ],
    )


def _invalid_cascade() -> CascadeResult:
    return CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[
            # Shape core.run_ladder records for a parsed-but-invalid reply.
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=False,
                validated=False,
                validation_reason="scc not in candidates",
                result={"scc_number": "99.99", "confidence": 0.8},
                confidence=0.8,
            )
        ],
    )


@pytest.mark.parametrize(
    "conf,expect_escalate",
    [
        (0.9, False),
        (0.69, True),
        (0.70, False),
    ],
)
def test_should_escalate_boundary(conf, expect_escalate):
    assert u654a.should_escalate_text_answer(10, conf, escalate_below=0.70) is expect_escalate


def test_low_confidence_valid_kept_with_harness_threshold():
    cascade = _valid_cascade(0.3)
    scc, _proj, conf = u654a.cascade_best_validated_prediction(
        cascade,
        scc_number_to_id={"01.01": 100},
        proj_abbrev_to_id={},
    )
    assert scc == 100
    assert conf == 0.3


def test_arm_hash_unchanged_without_u701b_flags():
    task = u654a.backtest_cascade_task(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
        line_items_rule=False,
    )
    h = u654a.arm_hash(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
        task=task,
    )
    task2 = u654a.backtest_cascade_task(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
    )
    h2 = u654a.arm_hash(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
        task=task2,
    )
    assert h == h2
    assert task is BILL_CODING_SUGGESTION_TASK


def test_line_items_rule_changes_prompt_and_arm_hash():
    off = u654a.backtest_cascade_task(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=False,
        line_items_rule=False,
    )
    on = u654a.backtest_cascade_task(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=False,
        line_items_rule=True,
    )
    assert u654a.LINE_ITEMS_RULE_LINE in on.system_prompt
    assert u654a.LINE_ITEMS_RULE_LINE not in off.system_prompt
    h_off = u654a.arm_hash(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=False,
        task=off,
    )
    h_on = u654a.arm_hash(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=False,
        task=on,
        line_items_rule=True,
    )
    assert h_off != h_on


def test_override_disagreement_thresholds():
    assert u654a.similar_evidence_disagrees_with_history(
        1,
        vote_scc_id=2,
        vote_confidence=0.5,
        neighbours=[],
    )
    assert not u654a.similar_evidence_disagrees_with_history(
        1,
        vote_scc_id=2,
        vote_confidence=0.49,
        neighbours=[],
    )

    class _N:
        match = 0.5
        scc_id = 2

    assert u654a.similar_evidence_disagrees_with_history(
        1, vote_scc_id=None, vote_confidence=None, neighbours=[_N()]
    )

    class _N49:
        match = 0.49
        scc_id = 2

    assert not u654a.similar_evidence_disagrees_with_history(
        1, vote_scc_id=None, vote_confidence=None, neighbours=[_N49()]
    )


def test_override_fixed_broke_counts():
    hist_right = ModePrediction(
        "A", 100, 10, "h", Decimal("0.90"), [], 0
    )
    hist_wrong = ModePrediction(
        "A", 200, 10, "h", Decimal("0.90"), [], 0
    )
    text_ok = u654a.TextAttemptResult(
        "A",
        _valid_cascade(0.8),
        predicted_scc_id=100,
    )
    text_wrong = u654a.TextAttemptResult(
        "A",
        _valid_cascade(0.8, scc_number="02.02"),
        predicted_scc_id=200,
    )
    rows = [
        u654a.ScoredTextBill(
            bill=_bill(1),
            stratum=">=90%",
            history_a=hist_right,
            history_b=hist_right,
            text_a=text_wrong,
            word_vote_scc_id=200,
            word_vote_confidence=0.6,
        ),
        u654a.ScoredTextBill(
            bill=_bill(2),
            stratum=">=90%",
            history_a=hist_wrong,
            history_b=hist_wrong,
            text_a=text_ok,
            word_vote_scc_id=100,
            word_vote_confidence=0.6,
        ),
    ]
    m = u654a.compute_history_override_metrics(rows, similar_bills_evidence=True)
    a_text = m["override_mode_a_text"]
    assert a_text["fired"] == 2
    assert a_text["fixed"] == 1
    assert a_text["broke"] == 1


def test_dry_run_includes_escalation_worst_case_cost():
    bill = _bill()
    sample = [(bill, "cold_start")]
    ref = u654a.ReferenceData(
        scc_by_id={100: u654a.SccRef(100, "01.01", "N", datetime(2026, 1, 1))},
        scc_number_to_id={"01.01": 100},
        proj_by_id={},
        proj_abbrev_to_id={},
        vendor_names={1: "V"},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    vv = u654a.build_vendor_priors(
        [
            BillRow(
                bill_id=1,
                vendor_id=1,
                created=datetime(2026, 5, 1),
                label_scc_id=100,
                label_project_id=10,
                total_abs_amount=Decimal("50"),
                mixed_code=False,
            )
        ]
    )
    primary = u654a.ladder_by_name("claude-haiku")
    escalate = u654a.ladder_by_name("claude-sonnet")
    out = u654a.dry_run_summary(
        sample,
        sample_size=1,
        populations={},
        ladder_name="claude-haiku",
        ladder=primary,
        ref=ref,
        vendor_votes=vv,
        candidate_scc_mode="narrowed",
        escalate_ladder_name="claude-sonnet",
        escalate_ladder=escalate,
    )
    assert out["escalation_assumed_rate"] == 1.0
    assert out["estimated_escalation_cost_usd_worst_case"] > 0
    assert (
        out["estimated_cost_usd_with_escalation_worst_case"]
        >= out["estimated_primary_cost_usd"]
    )


def test_csv_columns_unchanged_without_escalation_arm(tmp_path):
    bill = _bill()
    row = u654a.ScoredTextBill(
        bill=bill,
        stratum="cold_start",
        history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
        history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
        text_a=u654a.TextAttemptResult(
            "A",
            _valid_cascade(0.9),
            predicted_scc_id=100,
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
    _r, csv_path, _j = u654a.write_report_and_outputs(
        out_dir=tmp_path,
        stamp="pin",
        scored=[row],
        metrics=metrics,
        dry_info=None,
        eval_since=u654a.DEFAULT_EVAL_SINCE,
        eval_until=u654a.DEFAULT_EVAL_UNTIL,
    )
    with csv_path.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == [
        "BillId",
        "VendorId",
        "mode",
        "suggester",
        "predicted_scc_id",
        "label_scc_id",
        "predicted_project_id",
        "label_project_id",
        "correct_scc",
        "correct_project",
        "stratum",
        "winning_rung",
        "winning_rung_provider",
        "needs_human",
        "raw_predicted_scc_id",
        "raw_predicted_project_id",
        "raw_confidence",
        "raw_correct_scc",
        "raw_correct_project",
        "error_kind",
        "http_status",
        "candidate_scc_count",
        "candidate_scc_narrowed",
        "top_neighbour_scc_id",
        "neighbour_count",
        "followed_top_neighbour",
        "bill_text_chars_capped",
        "candidates_added_by_neighbours",
    ]


def test_resume_round_trips_escalation_columns(tmp_path):
    bill = _bill()
    text = u654a.TextAttemptResult(
        "A",
        _valid_cascade(0.9),
        predicted_scc_id=100,
        primary_scc_id=100,
        primary_confidence=0.69,
        escalated=1,
        escalate_scc_id=100,
        escalate_confidence=0.95,
        primary_cost_usd=0.01,
        escalate_cost_usd=0.02,
    )
    rows = [
        u654a.ScoredTextBill(
            bill=bill,
            stratum="cold_start",
            history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
            history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
            text_a=text,
        ),
    ]
    metrics = u654a.build_metrics(
        rows,
        cascades=[text.cascade],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.03},
        cap_tripped=False,
        scored_bill_count=1,
        unscored_bill_count=0,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    metrics["escalation_metrics"] = u654a.compute_escalation_run_metrics(rows)
    _r, csv_path, _j = u654a.write_report_and_outputs(
        out_dir=tmp_path,
        stamp="esc",
        scored=rows,
        metrics=metrics,
        dry_info=None,
        eval_since=u654a.DEFAULT_EVAL_SINCE,
        eval_until=u654a.DEFAULT_EVAL_UNTIL,
    )
    prior = u654a.load_prior_text_results(csv_path)
    loaded = prior[(1, "A")]
    assert loaded.primary_scc_id == 100
    assert loaded.primary_confidence == pytest.approx(0.69)
    assert loaded.escalated == 1
    assert loaded.escalate_scc_id == 100
    assert loaded.escalate_confidence == pytest.approx(0.95)
    assert loaded.primary_cost_usd == pytest.approx(0.01)
    assert loaded.escalate_cost_usd == pytest.approx(0.02)


def test_extra_bills_excluded_from_sample_metrics():
    sample = u654a.ScoredTextBill(
        bill=_bill(1),
        stratum="cold_start",
        history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
        history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
        text_a=u654a.TextAttemptResult("A", _valid_cascade(0.9), predicted_scc_id=100),
    )
    extra = u654a.ScoredTextBill(
        bill=_bill(99),
        stratum="cold_start",
        history_a=ModePrediction("A", 200, 10, "h", None, [], Decimal("0")),
        history_b=ModePrediction("B", 200, 10, "h", None, [], Decimal("0")),
        text_a=u654a.TextAttemptResult("A", _valid_cascade(0.9), predicted_scc_id=200),
        is_extra=True,
    )
    metrics = u654a.build_metrics(
        [sample, extra],
        cascades=[sample.text_a.cascade, extra.text_a.cascade],
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
    assert metrics["mode_a_n"] == 1
    assert metrics["extra_bill_count"] == 1


def test_priors_before_extra_bill_use_strictly_earlier_only():
    votes = u654a.build_vendor_priors(
        [
            BillRow(
                bill_id=1,
                vendor_id=1,
                created=datetime(2026, 4, 1),
                label_scc_id=100,
                label_project_id=10,
                total_abs_amount=Decimal("10"),
                mixed_code=False,
            ),
            BillRow(
                bill_id=99,
                vendor_id=1,
                created=datetime(2026, 6, 1),
                label_scc_id=200,
                label_project_id=10,
                total_abs_amount=Decimal("10"),
                mixed_code=False,
            ),
        ]
    )
    extra = u654a.TextBillRow(
        bill_id=99,
        vendor_id=1,
        created=datetime(2026, 6, 1),
        label_scc_id=200,
        label_project_id=10,
        total_abs_amount=Decimal("10"),
        mixed_code=False,
        extracted_text_blob_url="http://blob",
    )
    priors = u654a.priors_for_bill(votes, extra)
    assert all(p.bill_id != 99 for p in priors)
    assert any(p.bill_id == 1 for p in priors)


def test_escalation_batch_outcomes():
    bill = _bill()
    payload = {"candidate_sccs": [{"number": "01.01", "name": "N"}]}
    work = [
        u654a.TextCascadeWorkItem(bill, "A", "cold_start", payload, False),
    ]
    cost_state = {
        "usd": 0.0,
        "scc_number_to_id": {"01.01": 100, "02.02": 200},
        "proj_abbrev_to_id": {},
    }
    primary_ladder = (Rung("anthropic", "claude-haiku-5-5"),)
    esc_ladder = (Rung("anthropic", "claude-sonnet-5-5"),)
    call_n = {"n": 0}

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return _valid_cascade(0.69, "01.01")
        return _valid_cascade(0.95, "02.02")

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=primary_ladder,
                bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
                similar_bills_evidence=False,
                line_items_rule=False,
                escalate_ladder=esc_ladder,
                escalate_below=0.70,
            )
        )
    res = out[(1, "A")]
    assert res.escalated == 1
    assert res.predicted_scc_id == 200
    assert res.primary_confidence == pytest.approx(0.69)


def test_escalation_primary_invalid_uses_escalate_or_primary_low():
    bill = _bill()
    payload = {"candidate_sccs": [{"number": "01.01", "name": "N"}]}
    work = [u654a.TextCascadeWorkItem(bill, "A", "cold_start", payload, False)]
    cost_state = {
        "usd": 0.0,
        "scc_number_to_id": {"01.01": 100},
        "proj_abbrev_to_id": {},
    }
    seq = iter(
        [
            _invalid_cascade(),
            _valid_cascade(0.55),
        ]
    )

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        return next(seq)

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
            )
        )
    assert out[(1, "A")].predicted_scc_id == 100


def test_escalation_both_invalid_none():
    bill = _bill()
    work = [
        u654a.TextCascadeWorkItem(
            bill, "A", "cold_start", {"candidate_sccs": []}, False
        )
    ]
    cost_state = {
        "usd": 0.0,
        "scc_number_to_id": {},
        "proj_abbrev_to_id": {},
    }

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        return _invalid_cascade()

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
            )
        )
    assert out[(1, "A")].predicted_scc_id is None


def test_escalation_required_but_escalate_leg_fails_leaves_unscored():
    bill = _bill()
    work = [
        u654a.TextCascadeWorkItem(
            bill, "A", "cold_start", {"candidate_sccs": []}, False
        )
    ]
    cost_state = {
        "usd": 0.0,
        "scc_number_to_id": {"01.01": 100},
        "proj_abbrev_to_id": {},
    }
    seq = iter([_valid_cascade(0.69), None])

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        return next(seq)

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
            )
        )
    assert (1, "A") not in out


def test_escalation_escalate_invalid_final_is_primary_low_conf():
    bill = _bill()
    work = [
        u654a.TextCascadeWorkItem(
            bill, "A", "cold_start", {"candidate_sccs": []}, False
        )
    ]
    cost_state = {
        "usd": 0.0,
        "scc_number_to_id": {"01.01": 100},
        "proj_abbrev_to_id": {},
    }
    seq = iter([_valid_cascade(0.55), _invalid_cascade()])

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        return next(seq)

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
            )
        )
    res = out[(1, "A")]
    assert res.predicted_scc_id == 100
    assert res.escalated == 1
    assert res.escalate_valid == 0


def test_escalation_metrics_escalated_means_paid_leg():
    sample = u654a.ScoredTextBill(
        bill=_bill(1),
        stratum="cold_start",
        history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
        history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
        text_a=u654a.TextAttemptResult(
            "A",
            _valid_cascade(0.9),
            predicted_scc_id=100,
            primary_scc_id=100,
            primary_confidence=0.69,
            escalated=1,
            escalate_valid=0,
            primary_cost_usd=0.01,
            escalate_cost_usd=0.02,
        ),
    )
    m = u654a.compute_escalation_run_metrics([sample])
    assert m["escalated_call_count"] == 1
    assert m["escalate_valid_call_count"] == 0
    assert m["sample_text_spend_usd"] == pytest.approx(0.03)
    assert "spend_reconciled" not in m  # set only by reconcile_escalation_spend


def test_reconcile_escalation_spend_checks_metered_total():
    m = {"sample_text_spend_usd": 0.03, "extra_text_spend_usd": 0.01}
    u654a.reconcile_escalation_spend(m, total_spend_usd=0.05, orphaned_spend_usd=0.01)
    assert m["unattributed_spend_usd"] == pytest.approx(0.0)
    assert m["spend_reconciled"] is True
    m2 = {"sample_text_spend_usd": 0.03, "extra_text_spend_usd": 0.0}
    u654a.reconcile_escalation_spend(m2, total_spend_usd=0.50, orphaned_spend_usd=0.0)
    assert m2["unattributed_spend_usd"] == pytest.approx(0.47)
    assert m2["spend_reconciled"] is False


def _failed_cascade(error: str) -> CascadeResult:
    return CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-sonnet-5-5"),
                accepted=False,
                validated=False,
                error=error,
            )
        ],
    )


def test_escalate_leg_completed_classification():
    assert u654a.escalate_leg_completed(_valid_cascade(0.9), 100) is True
    assert u654a.escalate_leg_completed(_invalid_cascade(), None) is True
    no_attempts = CascadeResult(
        accepted=False, needs_human=True, result=None, confidence=None,
        winning_rung=None, attempts=[],
    )
    assert u654a.escalate_leg_completed(no_attempts, None) is False
    unparseable = _failed_cascade("no parseable result")
    assert u654a.escalate_leg_completed(unparseable, None) is True
    assert (
        u654a.escalate_leg_completed(_failed_cascade("HTTP 429 rate limit exceeded"), None)
        is False
    )


@pytest.mark.parametrize(
    "esc_result",
    [
        "rate_limit",
        "no_attempts",
    ],
)
def test_escalation_call_failure_leaves_unscored_and_records_orphan(esc_result):
    bill = _bill()
    work = [
        u654a.TextCascadeWorkItem(
            bill, "A", "cold_start", {"candidate_sccs": []}, False
        )
    ]
    cost_state = {
        "usd": 0.0,
        "scc_number_to_id": {"01.01": 100},
        "proj_abbrev_to_id": {},
    }
    failed = (
        _failed_cascade("HTTP 429 rate limit exceeded")
        if esc_result == "rate_limit"
        else CascadeResult(
            accepted=False, needs_human=True, result=None, confidence=None,
            winning_rung=None, attempts=[],
        )
    )
    seq = iter([_valid_cascade(0.55), failed])

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        return next(seq)

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped), patch.object(
        u654a, "bill_cascade_spend_usd", side_effect=[0.002, 0.03]
    ):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
            )
        )
    assert (1, "A") not in out
    assert cost_state["orphaned_usd"] == pytest.approx(0.032)


def test_escalation_none_return_records_primary_orphan():
    bill = _bill()
    work = [
        u654a.TextCascadeWorkItem(
            bill, "A", "cold_start", {"candidate_sccs": []}, False
        )
    ]
    cost_state = {"usd": 0.0, "scc_number_to_id": {"01.01": 100}, "proj_abbrev_to_id": {}}
    seq = iter([_valid_cascade(0.55), None])

    async def fake_capped(call, *, est, cost_state, max_cost_usd):
        return next(seq)

    with patch.object(u654a, "run_capped_cascade", side_effect=fake_capped), patch.object(
        u654a, "bill_cascade_spend_usd", return_value=0.002
    ):
        out = asyncio.run(
            u654a.run_text_cascade_batch(
                work,
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=100.0,
                cost_state=cost_state,
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
            )
        )
    assert (1, "A") not in out
    assert cost_state["orphaned_usd"] == pytest.approx(0.002)


def test_word_vote_uses_combined_not_match_order():
    from entities.coding_suggestion.business.similar_bills import (
        DEFAULT_TOP_K,
        vote_from_scored_neighbours,
    )

    class _N:
        def __init__(self, combined, match, scc_id, bid):
            self.combined = combined
            self.match = match
            self.scc_id = scc_id

            class _Prior:
                class enriched:
                    class bill:
                        pass

            self.prior = _Prior()
            self.prior.enriched.bill.bill_id = bid

    raw = [
        _N(0.9, 0.2, 100, 1),
        _N(0.4, 0.95, 200, 2),
    ]
    harness_vote, _conf = u654a.word_matching_vote_from_neighbours(raw)
    prod = vote_from_scored_neighbours(
        [(0.9, 1, 100), (0.4, 2, 200)], DEFAULT_TOP_K
    )
    assert harness_vote == prod.predicted_scc_id


def test_word_vote_counts_neighbours_six_to_ten_like_production():
    from entities.coding_suggestion.business.similar_bills import (
        DEFAULT_TOP_K,
        vote_from_scored_neighbours,
    )

    class _N:
        def __init__(self, combined, scc_id, bid):
            self.combined = combined
            self.match = combined
            self.scc_id = scc_id

            class _Prior:
                class enriched:
                    class bill:
                        pass

            self.prior = _Prior()
            self.prior.enriched.bill.bill_id = bid

    # Top 5: scc 100 leads; neighbours 6-10 tip the weighted vote to scc 200.
    raw = [
        _N(0.9, 100, 1), _N(0.8, 200, 2), _N(0.7, 100, 3), _N(0.6, 300, 4), _N(0.55, 300, 5),
        _N(0.5, 200, 6), _N(0.5, 200, 7), _N(0.45, 200, 8), _N(0.4, 200, 9), _N(0.35, 200, 10),
    ]
    prod = vote_from_scored_neighbours(
        [(n.combined, n.prior.enriched.bill.bill_id, n.scc_id) for n in raw], DEFAULT_TOP_K
    )
    top5 = vote_from_scored_neighbours(
        [(n.combined, n.prior.enriched.bill.bill_id, n.scc_id) for n in raw[:5]], DEFAULT_TOP_K
    )
    assert prod.predicted_scc_id == 200 and top5.predicted_scc_id == 100
    harness_vote, _ = u654a.word_matching_vote_from_neighbours(raw)
    assert harness_vote == prod.predicted_scc_id


def test_raw_neighbours_collected_at_production_depth():
    import inspect

    from entities.coding_suggestion.business.similar_bills import DEFAULT_TOP_K

    src = inspect.getsource(u654a.compute_sample_neighbours)
    assert "top_k=DEFAULT_TOP_K" in src
    assert DEFAULT_TOP_K == 10 and u654a.SIMILAR_BILLS_TOP_K == 5
    prep = inspect.getsource(u654a.prepare_sample_per_bill_evidence)
    assert "[:SIMILAR_BILLS_TOP_K]" in prep  # the model still sees 5


def test_resume_allows_extra_bills_in_prior_csv(tmp_path):
    bill = _bill(1)
    extra = _bill(99)
    text = u654a.TextAttemptResult("A", _valid_cascade(0.9), predicted_scc_id=100)
    rows = [
        u654a.ScoredTextBill(
            bill=bill,
            stratum="cold_start",
            history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
            history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
            text_a=text,
        ),
        u654a.ScoredTextBill(
            bill=extra,
            stratum="cold_start",
            history_a=ModePrediction("A", 200, 10, "h", None, [], Decimal("0")),
            history_b=ModePrediction("B", 200, 10, "h", None, [], Decimal("0")),
            text_a=text,
            is_extra=True,
        ),
    ]
    metrics = u654a.build_metrics(
        rows,
        cascades=[text.cascade],
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
    u654a.write_report_and_outputs(
        out_dir=tmp_path,
        stamp="x",
        scored=rows,
        metrics=metrics,
        dry_info=None,
        eval_since=u654a.DEFAULT_EVAL_SINCE,
        eval_until=u654a.DEFAULT_EVAL_UNTIL,
    )
    csv_path = tmp_path / "u654a_predictions_foundry_x.csv"
    summary_path = tmp_path / "u654a_summary_foundry_x.json"
    sample_ids = {1}
    allowed_extra = {99}
    prior = u654a.load_prior_text_results(csv_path)
    stray = {bid for bid, _ in prior} - sample_ids - allowed_extra
    assert not stray


def test_resume_stray_sample_bill_still_detected(tmp_path):
    bill = _bill(1)
    stranger = _bill(77)
    text = u654a.TextAttemptResult("A", _valid_cascade(0.9), predicted_scc_id=100)
    rows = [
        u654a.ScoredTextBill(
            bill=bill,
            stratum="cold_start",
            history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
            history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
            text_a=text,
        ),
        u654a.ScoredTextBill(
            bill=stranger,
            stratum="cold_start",
            history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
            history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
            text_a=text,
        ),
    ]
    metrics = u654a.build_metrics(
        rows,
        cascades=[text.cascade, text.cascade],
        usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        cap_tripped=False,
        scored_bill_count=2,
        unscored_bill_count=0,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    u654a.write_report_and_outputs(
        out_dir=tmp_path,
        stamp="y",
        scored=rows,
        metrics=metrics,
        dry_info=None,
        eval_since=u654a.DEFAULT_EVAL_SINCE,
        eval_until=u654a.DEFAULT_EVAL_UNTIL,
    )
    csv_path = tmp_path / "u654a_predictions_foundry_y.csv"
    prior = u654a.load_prior_text_results(csv_path)
    sample_ids = {1}
    stray = {bid for bid, _ in prior} - sample_ids
    assert 77 in stray


def test_master_metrics_key_set_without_u701b_flags():
    row = u654a.ScoredTextBill(
        bill=_bill(),
        stratum="cold_start",
        history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
        history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
        text_a=u654a.TextAttemptResult("A", _valid_cascade(0.9), predicted_scc_id=100),
    )
    metrics = u654a.build_metrics(
        [row],
        cascades=[row.text_a.cascade],
        usage_totals={"input_tokens": 1, "output_tokens": 2, "cost_usd": 0.0},
        cap_tripped=False,
        scored_bill_count=1,
        unscored_bill_count=0,
        throttle_retry_count=0,
        throttle_failure_count=0,
        cascade_error_count=0,
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    forbidden = {
        "line_items_rule",
        "escalate_ladder",
        "escalation_metrics",
        "history_override_scoring",
        "extra_bill_count",
    }
    assert forbidden.isdisjoint(metrics.keys())
    dry = u654a.dry_run_summary(
        [(row.bill, row.stratum)],
        sample_size=1,
        populations={},
        ref=u654a.ReferenceData(
            scc_by_id={100: u654a.SccRef(100, "01.01", "N", datetime(2026, 1, 1))},
            scc_number_to_id={"01.01": 100},
            proj_by_id={},
            proj_abbrev_to_id={},
            vendor_names={1: "V"},
            ambiguous_scc_numbers=frozenset(),
            ambiguous_project_abbrevs=frozenset(),
            duplicate_scc_number_count=0,
            duplicate_project_abbrev_count=0,
        ),
        vendor_votes=u654a.build_vendor_priors(
            [
                BillRow(
                    bill_id=1,
                    vendor_id=1,
                    created=datetime(2026, 5, 1),
                    label_scc_id=100,
                    label_project_id=10,
                    total_abs_amount=Decimal("50"),
                    mixed_code=False,
                )
            ]
        ),
    )
    assert forbidden.isdisjoint(dry.keys())


def test_escalation_arm_passes_zero_threshold():
    captured: dict[str, float] = {}

    async def capture_backtest(*_a, **kw):
        captured["threshold"] = kw.get("acceptance_threshold")
        return _valid_cascade(0.3)

    payload = {"candidate_sccs": [{"number": "01.01", "name": "N"}]}
    with patch.object(
        u654a, "run_backtest_bill_coding_suggestion", side_effect=capture_backtest
    ):
        asyncio.run(
            u654a.run_text_cascade_batch(
                [u654a.TextCascadeWorkItem(_bill(), "A", "s", payload, False)],
                transport_for=lambda _p: None,
                concurrency=1,
                max_cost_usd=10.0,
                cost_state={
                    "usd": 0.0,
                    "scc_number_to_id": {"01.01": 100},
                    "proj_abbrev_to_id": {},
                },
                ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                escalate_ladder=(Rung("anthropic", "claude-sonnet-5-5"),),
                bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
            )
        )
    assert captured["threshold"] == 0.0


def test_summary_extra_ids_are_selected_extras_not_scored(tmp_path):
    """An extra dropped as unreadable must stay in the summary so resume matches."""
    import json

    bill = _bill(1)
    text = u654a.TextAttemptResult("A", _valid_cascade(0.9), predicted_scc_id=100)
    rows = [
        u654a.ScoredTextBill(
            bill=bill,
            stratum="cold_start",
            history_a=ModePrediction("A", 100, 10, "h", None, [], Decimal("0")),
            history_b=ModePrediction("B", 100, 10, "h", None, [], Decimal("0")),
            text_a=text,
        ),
    ]
    metrics = u654a.build_metrics(
        rows,
        cascades=[text.cascade],
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
    _r, _c, json_path = u654a.write_report_and_outputs(
        out_dir=tmp_path,
        stamp="x",
        scored=rows,
        metrics=metrics,
        dry_info=None,
        eval_since=u654a.DEFAULT_EVAL_SINCE,
        eval_until=u654a.DEFAULT_EVAL_UNTIL,
        extra_bill_ids=[99, 100],  # both selected; neither scored (unreadable)
    )
    summary = json.loads(Path(json_path).read_text())
    assert summary["extra_bill_ids"] == [99, 100]


@pytest.mark.parametrize(
    "error,completed",
    [
        ("HTTP 400 bad request", True),
        ("HTTP 401 unauthorized", True),
        ("HTTP 413 payload too large", True),
        ("HTTP 429 rate limit exceeded", False),
        ("HTTP 529 overloaded", False),
        ("HTTP 500 internal error", False),
        ("request timeout", False),
    ],
)
def test_escalate_permanent_errors_count_as_completed(error, completed):
    assert u654a.escalate_leg_completed(_failed_cascade(error), None) is completed


def test_escalate_mixed_permanent_then_transient_is_not_completed():
    mixed = CascadeResult(
        accepted=False, needs_human=True, result=None, confidence=None, winning_rung=None,
        attempts=[
            Attempt(rung=Rung("foundry", "a"), accepted=False, validated=False, error="HTTP 400 bad request"),
            Attempt(rung=Rung("foundry", "b"), accepted=False, validated=False, error="HTTP 429 rate limit"),
        ],
    )
    assert u654a.escalate_leg_completed(mixed, None) is False
