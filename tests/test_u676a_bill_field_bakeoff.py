"""Offline tests for U-676a bill header bake-off harness."""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.business.extraction_mapper import BillExtractionMapper, BillExtractionResult
from intelligence.cascade.bill_header_extraction import BILL_HEADER_EXTRACTION_TASK
from scripts import u654a_text_backtest as u654a_tokens
from scripts import u676a_bill_field_bakeoff as u676a


def _bill_row(**kwargs) -> u676a.HeaderBillRow:
    defaults = dict(
        bill_id=1,
        vendor_id=10,
        vendor_public_id="11111111-1111-1111-1111-111111111111",
        bill_number="INV-100",
        bill_date=date(2026, 5, 1),
        total_amount=Decimal("100.00"),
        created=datetime(2026, 5, 2, 10, 0, 0),
        intake_source="manual",
        extracted_text_blob_url="https://example/blob",
        first_line_description="Framing labor",
    )
    defaults.update(kwargs)
    return u676a.HeaderBillRow(**defaults)


def test_normalize_bill_number_and_qbo_exclusion():
    assert u676a.normalize_bill_number(" inv #100 ") == "INV100"
    assert not u676a.bill_number_is_scorable(None)
    assert not u676a.bill_number_is_scorable("")
    assert not u676a.bill_number_is_scorable("QBO-12345")
    assert u676a.bill_number_is_scorable("INV-1")


def test_score_amount_uses_decimal_tolerance():
    truth = Decimal("100.00")
    assert u676a.score_amount(Decimal("100.009"), truth)
    assert not u676a.score_amount(Decimal("100.02"), truth)


def test_score_bill_date_exact():
    assert u676a.score_bill_date("2026-05-01", date(2026, 5, 1))
    assert not u676a.score_bill_date("2026-05-02", date(2026, 5, 1))


def test_missing_prediction_counts_as_miss():
    truth = u676a.truth_from_row(_bill_row())
    assert not u676a.score_vendor(None, truth.vendor_public_id)
    assert not u676a.score_amount(None, truth.total_amount)


def test_stratified_sample_by_intake_deterministic():
    bills = [
        _bill_row(bill_id=1, intake_source="manual"),
        _bill_row(bill_id=2, intake_source="agent", created=datetime(2026, 5, 3)),
        _bill_row(bill_id=3, intake_source=None, created=datetime(2026, 5, 4)),
        _bill_row(bill_id=4, intake_source="email", created=datetime(2026, 5, 5)),
    ]
    s1 = u676a.stratified_sample_by_intake(
        bills,
        sample_size=3,
        seed=676,
        eval_since=u676a.DEFAULT_EVAL_SINCE,
        eval_until=u676a.DEFAULT_EVAL_UNTIL,
    )
    s2 = u676a.stratified_sample_by_intake(
        bills,
        sample_size=3,
        seed=676,
        eval_since=u676a.DEFAULT_EVAL_SINCE,
        eval_until=u676a.DEFAULT_EVAL_UNTIL,
    )
    assert [x[0].bill_id for x in s1] == [x[0].bill_id for x in s2]


def test_arm_a_exception_is_error_not_abort():
    mapper = BillExtractionMapper()

    def boom(*_a, **_k):
        raise RuntimeError("map failed")

    payload = {"content": "x", "key_value_pairs": []}
    with patch.object(mapper, "map", side_effect=boom):
        pred = u676a.run_arm_a_on_blob(payload, mapper)
    assert pred.arm_error
    assert not u676a.score_vendor(pred.vendor_public_id, "11111111-1111-1111-1111-111111111111")


def test_vendor_resolution_uses_matcher_not_model_id():
    mapper = BillExtractionMapper()
    fake_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    resolved = BillExtractionResult(
        vendor_match=type("VM", (), {"public_id": fake_uuid, "name": "Real Vendor"})(),
    )
    with patch.object(mapper, "resolve_entities", return_value=resolved) as mock_resolve:
        out = u676a.resolve_vendor_from_name("Real Vendor Inc", mapper)
    assert out == fake_uuid
    mock_resolve.assert_called_once()
    assert "Real Vendor" in (mock_resolve.call_args[0][0].vendor_name or "")


def test_stale_blob_skipped_via_extraction_payload_is_current():
    from entities.attachment.business.extraction_service import (
        REDACTION_VERSION,
        extraction_payload_is_current,
    )

    stale = {"redaction_version": "old", "content": "text", "key_value_pairs": []}
    assert not extraction_payload_is_current(stale)
    current = {
        "redaction_version": REDACTION_VERSION,
        "content": "masked",
        "key_value_pairs": [],
        "attachment_public_id": "00000000-0000-0000-0000-000000000001",
        "source_file_hash": "abc",
        "method": "text_layer",
        "pages_count": 1,
    }
    assert extraction_payload_is_current(current)


def test_field_verdict_withheld_when_incomplete():
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=u676a.truth_from_row(_bill_row()),
        init_arm_a=u676a.ArmPrediction(vendor_public_id="11111111-1111-1111-1111-111111111111"),
        init_arm_b=u676a.ArmPrediction(),
    )
    v = u676a.field_verdict([row], run_status="partial", arms=frozenset({"A", "B"}))
    assert v["status"] == "withheld"


def test_field_verdict_below_bar_flag():
    rows = []
    for i in range(5):
        rows.append(
            u676a.ScoredHeaderBill(
                bill=_bill_row(bill_id=i + 1),
                stratum="manual",
                truth=u676a.truth_from_row(_bill_row(bill_id=i + 1)),
                init_arm_a=u676a.ArmPrediction(
                    vendor_public_id="11111111-1111-1111-1111-111111111111"
                    if i < 3
                    else "wrong"
                ),
                init_arm_b=u676a.ArmPrediction(vendor_public_id="wrong"),
            )
        )
    v = u676a.field_verdict(rows, run_status="complete", arms=frozenset({"A", "B"}))
    assert v["status"] == "complete"
    assert v["fields"]["vendor"]["winner"] == "A"
    assert v["fields"]["vendor"]["below_bar"] is True


def test_privacy_assertion_refuses_vendor_name_in_csv():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.csv"
        bad.write_text("BillId,vendor_name\n1,Acme Supply Co\n", encoding="utf-8")
        with pytest.raises(ValueError, match="forbidden"):
            u676a.assert_outputs_privacy_safe(bad)


def test_write_outputs_privacy_safe():
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=u676a.truth_from_row(_bill_row()),
        init_arm_a=u676a.ArmPrediction(
            vendor_public_id="11111111-1111-1111-1111-111111111111",
            bill_number="INV-100",
            bill_date="2026-05-01",
            total_amount=Decimal("100"),
            description="secret desc",
            latency_ms=12,
        ),
        init_arm_b=u676a.ArmPrediction(latency_ms=50, spend_usd=0.001),
        extraction_method="text_layer",
    )
    metrics = u676a.build_metrics(
        [row],
        run_status="complete",
        cap_tripped=False,
        scored_bill_count=1,
        unscored_bill_count=0,
        blob_skip_unreadable=0,
        blob_skip_unparseable=0,
        blob_skip_stale=0,
        arm_a_errors=0,
        arms=frozenset({"A", "B"}),
    )
    with tempfile.TemporaryDirectory() as tmp:
        paths = u676a.write_report_and_outputs(
            out_dir=Path(tmp),
            stamp="test",
            scored=[row],
            metrics=metrics,
            dry_info=None,
            eval_since=u676a.DEFAULT_EVAL_SINCE,
            eval_until=u676a.DEFAULT_EVAL_UNTIL,
        )
        for p in paths:
            u676a.assert_outputs_privacy_safe(p)
        csv_text = paths[1].read_text(encoding="utf-8")
        assert "secret desc" not in csv_text
        assert "INV-100" not in csv_text


def test_dry_run_does_not_call_llm():
    fake = [_bill_row()]
    with patch.object(u676a, "load_header_bills_from_db", return_value=fake):
        with patch.object(u676a, "run_llm_arm_batch") as mock_batch:
            rc = u676a.main([])
            assert rc == 0
            mock_batch.assert_not_called()


def test_build_extraction_namespace():
    ns = u676a.build_extraction_namespace(
        {"content": "body", "key_value_pairs": [{"key": "Total", "value": "1"}]}
    )
    assert isinstance(ns, SimpleNamespace)
    assert ns.content == "body"
    assert ns.tables == []


def test_parse_arms_and_dry_run_estimates_llm_arms_separately():
    sample = [(_bill_row(), "manual")]
    dry = u676a.dry_run_summary(
        sample,
        sample_size=1,
        populations={},
        arms=frozenset({"A", "B", "C"}),
        claude_ladder_name="claude-haiku",
    )
    assert dry["arms"] == "A,B,C"
    assert dry["estimated_cost_usd_arm_b_first_rung_only"] > 0
    assert dry["estimated_cost_usd_arm_c_first_rung_only"] > 0
    assert (
        dry["estimated_cost_usd_arm_c_first_rung_only"]
        != dry["estimated_cost_usd_arm_b_first_rung_only"]
    )


def test_field_verdict_considers_arm_c():
    rows = []
    for i in range(5):
        rows.append(
            u676a.ScoredHeaderBill(
                bill=_bill_row(bill_id=i + 1),
                stratum="manual",
                truth=u676a.truth_from_row(_bill_row(bill_id=i + 1)),
                init_arm_a=u676a.ArmPrediction(vendor_public_id="wrong"),
                init_arm_b=u676a.ArmPrediction(vendor_public_id="wrong"),
                init_arm_c=u676a.ArmPrediction(
                    vendor_public_id="11111111-1111-1111-1111-111111111111"
                    if i < 4
                    else "wrong"
                ),
                llm_arms_scored=frozenset({"B", "C"}),
            )
        )
    v = u676a.field_verdict(rows, run_status="complete", arms=frozenset({"A", "B", "C"}))
    assert v["fields"]["vendor"]["winner"] == "C"


def test_arm_c_scoring_uses_same_rules_as_b():
    truth = u676a.truth_from_row(_bill_row())
    mapper = BillExtractionMapper()
    cascade = __import__(
        "intelligence.cascade.core", fromlist=["CascadeResult"]
    ).CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "vendor_name": "Real Vendor",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "description": "x",
            "confidence": 0.9,
        },
        confidence=0.9,
        winning_rung=None,
        attempts=[],
    )
    with patch.object(u676a, "resolve_vendor_from_name", return_value=truth.vendor_public_id):
        pred = u676a.arm_b_from_cascade(cascade, mapper, truth=truth)
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=truth,
        init_arm_b=pred,
        init_arm_c=pred,
        llm_arms_scored=frozenset({"B", "C"}),
    )
    assert u676a._field_accuracy_coverage([row], "B", "vendor").accuracy == Decimal("1")
    assert u676a._field_accuracy_coverage([row], "C", "vendor").accuracy == Decimal("1")


def test_shared_cost_cap_across_b_and_c():
    import asyncio
    from intelligence.cascade.bill_coding import BILL_CODING_FOUNDRY_LADDER, CLAUDE_HAIKU_LADDER

    bill = _bill_row()
    payload = {"bill_text": "x"}
    work_b = [(bill, payload)]
    work_c = [(bill, payload)]
    cost_state = {"usd": 0.0, "stopped": False}

    async def cheap(*_a, **_k):
        from intelligence.cascade.core import CascadeResult, Rung

        return CascadeResult(
            accepted=True,
            needs_human=False,
            result={
                "vendor_name": "v",
                "bill_number": "1",
                "bill_date": "2026-05-01",
                "total_amount": "1",
                "description": "d",
                "confidence": 0.9,
            },
            confidence=0.9,
            winning_rung=Rung("foundry", "DeepSeek-V4-Flash"),
            attempts=[],
        )

    def add_five(_cascade, state):
        state["usd"] += 5.0

    with patch.object(u676a, "run_bill_header_extraction", side_effect=cheap):
        with patch.object(u676a, "estimate_ladder_first_rung_cost_usd", return_value=6.0):
            with patch.object(
                __import__("scripts.u654a_text_backtest", fromlist=["apply_cascade_spend"]),
                "apply_cascade_spend",
                side_effect=add_five,
            ):
                with patch.object(u676a, "arm_b_from_cascade") as mock_from:
                    mock_from.return_value = u676a.ArmPrediction()
                    mapper = BillExtractionMapper()
                    asyncio.run(
                        u676a.run_llm_arm_batch(
                            work_b,
                            ladder=BILL_CODING_FOUNDRY_LADDER,
                            transport_for=lambda _p: MagicMock(),
                            concurrency=1,
                            max_cost_usd=10.0,
                            cost_state=cost_state,
                            mapper=mapper,
                            out={},
                        )
                    )
                    assert not cost_state.get("stopped")
                    asyncio.run(
                        u676a.run_llm_arm_batch(
                            work_c,
                            ladder=CLAUDE_HAIKU_LADDER,
                            transport_for=lambda _p: MagicMock(),
                            concurrency=1,
                            max_cost_usd=10.0,
                            cost_state=cost_state,
                            mapper=mapper,
                            out={},
                        )
                    )
    assert cost_state.get("stopped") is True


def test_coerce_bill_date_prefers_datetime_before_date():
    dt = datetime(2026, 5, 1, 15, 30, 0)
    assert u676a.coerce_bill_date(dt) == date(2026, 5, 1)


def test_bill_date_scoring_after_datetime_coerce():
    row = u676a.HeaderBillRow(
        **_bill_row().__dict__
        | {"bill_date": u676a.coerce_bill_date(datetime(2026, 5, 1, 12, 0, 0))}
    )
    truth = u676a.truth_from_row(row)
    pred = u676a.ArmPrediction(bill_date="2026-05-01")
    scored = u676a.ScoredHeaderBill(
        bill=row,
        stratum="manual",
        truth=truth,
        preds={"A": pred},
    )
    m = u676a._field_accuracy_coverage([scored], "A", "bill_date")
    assert m.accuracy == Decimal("1")
    assert m.n_scored == 1


def test_null_total_amount_excluded_from_amount_denominator():
    row = _bill_row(total_amount=None)
    truth = u676a.truth_from_row(row)
    scored = u676a.ScoredHeaderBill(
        bill=row,
        stratum="manual",
        truth=truth,
        preds={"A": u676a.ArmPrediction(total_amount=Decimal("0"))},
    )
    m = u676a._field_accuracy_coverage([scored], "A", "amount")
    assert m.n_scored == 0


def test_llm_vendor_match_uses_fixed_confidence_not_model():
    mapper = BillExtractionMapper()
    with patch.object(u676a, "resolve_vendor_from_name", return_value="x") as mock:
        u676a.arm_prediction_from_cascade(
            __import__(
                "intelligence.cascade.core", fromlist=["CascadeResult"]
            ).CascadeResult(
                accepted=True,
                needs_human=False,
                result={"vendor_name": "V", "confidence": 0.01},
                confidence=0.01,
                winning_rung=None,
                attempts=[],
            ),
            mapper,
            truth=u676a.truth_from_row(_bill_row()),
        )
    mock.assert_called_once()
    assert mock.call_args.kwargs.get("confidence") == u676a.LLM_VENDOR_MATCH_CONFIDENCE


def test_single_attachment_sql_counts_all_links_before_extraction_filter():
    sql = u676a._LOAD_HEADER_BILLS_SQL
    assert "AllBillAttachments" in sql
    assert "AttachmentCount" in sql
    assert "LinkCount" not in sql


def test_run_status_incomplete_when_blob_skip_share_exceeds_cap():
    status = u676a.run_status_after_unreadable("complete", unreadable=2, sampled=100)
    assert status == "complete"
    status_bad = u676a.run_status_after_unreadable("complete", unreadable=3, sampled=100)
    assert status_bad == "incomplete"


def test_unscored_llm_arm_writes_empty_correctness_and_excludes_denominator():
    truth = u676a.truth_from_row(_bill_row())
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=truth,
        init_arm_a=u676a.ArmPrediction(
            vendor_public_id=truth.vendor_public_id,
        ),
        llm_arms_scored=frozenset(),
    )
    csv_row = u676a.scored_row_to_csv_dict(row, arms=frozenset({"A", "B"}))
    assert csv_row["arm_b_scored"] == "0"
    assert csv_row["vendor_b_correct"] == ""
    assert csv_row["bill_date_b_correct"] == ""
    m_b = u676a._field_accuracy_coverage([row], "B", "vendor")
    assert m_b.n_scored == 0
    assert m_b.accuracy is None


def test_metrics_use_only_fresh_predictions_arm_a_all_correct():
    truth = u676a.truth_from_row(_bill_row())
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=truth,
        init_arm_a=u676a.ArmPrediction(
            vendor_public_id=truth.vendor_public_id,
            bill_number=truth.bill_number,
            bill_date="2026-05-01",
            total_amount=truth.total_amount,
        ),
    )
    m = u676a._field_accuracy_coverage([row], "A", "vendor")
    assert m.accuracy == Decimal("1")
    assert m.n_scored == 1


def test_description_nonempty_share_uses_fresh_predictions_only():
    truth = u676a.truth_from_row(_bill_row())
    scored_b = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=truth,
        init_arm_b=u676a.ArmPrediction(description="Line item summary"),
        llm_arms_scored=frozenset({"B"}),
    )
    assert u676a.description_nonempty_share([scored_b], "B") == Decimal("1")
    unscored_b = u676a.ScoredHeaderBill(
        bill=_bill_row(bill_id=2),
        stratum="manual",
        truth=truth,
        init_arm_b=u676a.ArmPrediction(description="ignored"),
        llm_arms_scored=frozenset(),
    )
    assert u676a.description_nonempty_share([scored_b, unscored_b], "B") == Decimal("1")


def test_dry_run_token_estimate_uses_char_budget_not_first_line():
    short = [(_bill_row(first_line_description="tiny"), "manual")]
    long_desc = [(_bill_row(first_line_description="y" * 5000), "manual")]
    t_short = u676a._dry_run_input_tokens_per_llm_call(short)
    t_long = u676a._dry_run_input_tokens_per_llm_call(long_desc)
    assert t_short == t_long
    tiny_only = u654a_tokens.estimate_payload_input_tokens({"bill_text": "tiny"}) + max(
        1, len(BILL_HEADER_EXTRACTION_TASK.system_prompt) // 4
    )
    assert t_short > tiny_only
    dry = u676a.dry_run_summary(
        short,
        sample_size=1,
        populations={},
        arms=frozenset({"A", "B"}),
        claude_ladder_name="claude-haiku",
    )
    assert dry["estimated_input_tokens_per_call_is_upper_bound"] is True
    assert dry["estimated_input_tokens_per_call"] == t_short


def test_error_kind_and_status_on_arm_prediction():
    from intelligence.cascade.core import Attempt, CascadeResult, Rung

    cascade = CascadeResult(
        False,
        True,
        None,
        None,
        None,
        [
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=False,
                error="http_429: limited",
            )
        ],
    )
    pred = u676a.arm_prediction_from_cascade(cascade, BillExtractionMapper())
    assert pred.error_kind == "rate_limit"
    assert pred.http_status == 429


def test_needs_human_raw_correctness_in_csv_without_accepted_values():
    from intelligence.cascade.core import CascadeResult, Rung

    truth = u676a.truth_from_row(_bill_row())
    cascade = CascadeResult(
        False,
        True,
        {
            "vendor_name": "Real Vendor",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "description": "line",
            "confidence": 0.55,
        },
        0.55,
        Rung("anthropic", "claude-haiku-5-5"),
        [],
    )
    with patch.object(u676a, "resolve_vendor_from_name", return_value=truth.vendor_public_id):
        pred = u676a.arm_prediction_from_cascade(
            cascade, BillExtractionMapper(), truth=truth
        )
        row = u676a.ScoredHeaderBill(
            bill=_bill_row(),
            stratum="manual",
            truth=truth,
            init_arm_b=pred,
            llm_arms_scored=frozenset({"B"}),
        )
        m_before = u676a._field_accuracy_coverage([row], "B", "vendor")
        assert m_before.n_scored == 1
        assert m_before.accuracy == Decimal("0")
        csv_row = u676a.scored_row_to_csv_dict(row, arms=frozenset({"A", "B"}))
    assert csv_row["vendor_b_correct"] == "0"
    assert csv_row["raw_vendor_b_correct"] == "1"
    assert csv_row["arm_b_error_kind"] == "none"


def test_header_confidence_bar_sweep_fixture():
    from intelligence.cascade.core import CascadeResult, Rung

    truth = u676a.truth_from_row(_bill_row())
    good = CascadeResult(
        False,
        True,
        {
            "vendor_name": "V",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "confidence": 0.55,
        },
        0.55,
        Rung("foundry", "DeepSeek-V4-Flash"),
        [],
    )
    with patch.object(u676a, "resolve_vendor_from_name", return_value=truth.vendor_public_id):
        pred = u676a.arm_prediction_from_cascade(
            good, BillExtractionMapper(), truth=truth
        )
        row = u676a.ScoredHeaderBill(
            bill=_bill_row(),
            stratum="manual",
            truth=truth,
            init_arm_b=pred,
            llm_arms_scored=frozenset({"B"}),
        )
        sweep = u676a.header_confidence_bar_sweep([row], arms=frozenset({"B"}))
    assert sweep["arms"]["B"]["0.5"]["vendor"]["accuracy"] == Decimal("1")
    assert sweep["arms"]["B"]["0.6"]["vendor"]["accuracy"] == Decimal("0")


def test_claude_arm_uses_claude_concurrency():
    import asyncio

    inflight = {"n": 0, "max": 0}
    bill = _bill_row()

    async def fake_header(*_a, **_k):
        inflight["n"] += 1
        inflight["max"] = max(inflight["max"], inflight["n"])
        await asyncio.sleep(0.01)
        inflight["n"] -= 1
        from intelligence.cascade.core import CascadeResult, Rung

        return CascadeResult(
            True,
            False,
            {
                "vendor_name": "v",
                "bill_number": "1",
                "bill_date": "2026-05-01",
                "total_amount": "1",
                "description": "d",
                "confidence": 0.9,
            },
            0.9,
            Rung("anthropic", "claude-haiku-5-5"),
            [],
        )

    work = [(bill, {"bill_text": "x"})] * 4
    cost_state = {"usd": 0.0, "stopped": False}
    with patch.object(u676a, "run_bill_header_extraction", side_effect=fake_header):
        asyncio.run(
            u676a.run_llm_arm_batch(
                work,
                ladder=u676a.llm_arm_ladder_map("claude-haiku")["C"],
                transport_for=lambda _p: MagicMock(),
                concurrency=2,
                max_cost_usd=10.0,
                cost_state=cost_state,
                mapper=BillExtractionMapper(),
                out={},
            )
        )
    assert inflight["max"] <= 2


def test_raw_field_accuracy_at_bar_denominator_and_precision():
    from intelligence.cascade.core import CascadeResult, Rung

    truth = u676a.truth_from_row(_bill_row())
    rows = []
    for i in range(10):
        conf = 0.90 if i < 3 else 0.40
        cascade = CascadeResult(
            False,
            True,
            {
                "vendor_name": "V",
                "bill_number": "INV-100",
                "bill_date": "2026-05-01",
                "total_amount": "100.00",
                "confidence": conf,
            },
            conf,
            Rung("foundry", "DeepSeek-V4-Flash"),
            [],
        )
        with patch.object(u676a, "resolve_vendor_from_name", return_value=truth.vendor_public_id):
            pred = u676a.arm_prediction_from_cascade(
                cascade, BillExtractionMapper(), truth=truth
            )
        rows.append(
            u676a.ScoredHeaderBill(
                bill=_bill_row(bill_id=i + 1),
                stratum="manual",
                truth=truth,
                init_arm_b=pred,
                llm_arms_scored=frozenset({"B"}),
            )
        )
    acc, cov, prec = u676a._raw_field_accuracy_at_bar(rows, "B", "vendor", 0.85)
    assert acc == Decimal("0.3")
    assert cov == Decimal("0.3")
    assert prec == Decimal("1")


def test_vendor_resolution_at_most_once_per_prediction_in_sweep():
    from intelligence.cascade.core import CascadeResult, Rung

    truth = u676a.truth_from_row(_bill_row())
    cascade = CascadeResult(
        False,
        True,
        {
            "vendor_name": "V",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "confidence": 0.55,
        },
        0.55,
        Rung("foundry", "DeepSeek-V4-Flash"),
        [],
    )
    resolve_count = {"n": 0}
    real = u676a.resolve_vendor_from_name

    def counting(*args, **kwargs):
        resolve_count["n"] += 1
        return real(*args, **kwargs)

    with patch.object(u676a, "resolve_vendor_from_name", side_effect=counting):
        pred = u676a.arm_prediction_from_cascade(
            cascade, BillExtractionMapper(), truth=truth
        )
        row = u676a.ScoredHeaderBill(
            bill=_bill_row(),
            stratum="manual",
            truth=truth,
            init_arm_b=pred,
            llm_arms_scored=frozenset({"B"}),
        )
        u676a.header_confidence_bar_sweep([row], arms=frozenset({"B"}))
        u676a.scored_row_to_csv_dict(row, arms=frozenset({"B"}))
    assert resolve_count["n"] == 1


def test_u676a_dry_run_arm_c_uses_payload_tokens_not_17k_floor():
    sample = [(_bill_row(), "manual")]
    dry = u676a.dry_run_summary(
        sample,
        sample_size=1,
        populations={},
        arms=frozenset({"A", "B", "C"}),
        claude_ladder_name="claude-haiku",
    )
    per_call = u676a._dry_run_input_tokens_per_llm_call(sample)
    assert per_call < u654a_tokens.CLAUDE_DRY_RUN_INPUT_TOKENS_FLOOR
    assert dry["estimated_input_tokens_per_call"] == per_call
    assert (
        dry["estimated_tokens_arm_c"]
        == per_call + u654a_tokens._ESTIMATE_OUTPUT_TOKENS_PER_CALL
    )


def test_arm_prediction_spend_sums_harness_recalls():
    from intelligence.cascade.core import Attempt, CascadeResult, Rung

    rung = Rung("anthropic", "claude-haiku-5-5")
    merged = CascadeResult(
        True,
        False,
        {
            "vendor_name": "V",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "confidence": 0.9,
        },
        0.9,
        rung,
        [
            Attempt(rung, False, error="http_429: x", usage=__import__("intelligence.transport.base", fromlist=["Usage"]).Usage(input_tokens=1000)),
            Attempt(rung, True, usage=__import__("intelligence.transport.base", fromlist=["Usage"]).Usage(input_tokens=2000)),
        ],
    )
    with patch.object(u676a, "bill_cascade_spend_usd", return_value=0.42):
        pred = u676a.arm_prediction_from_cascade(
            merged, BillExtractionMapper(), truth=u676a.truth_from_row(_bill_row())
        )
    assert pred.spend_usd == pytest.approx(0.42)


def test_llm_arm_concurrency_selection():
    assert u676a.llm_arm_concurrency("C", concurrency=4, claude_concurrency=1) == 1
    assert u676a.llm_arm_concurrency("B", concurrency=4, claude_concurrency=1) == 4


def test_header_confidence_bar_sweep_non_string_raw_fields():
    """Integer / non-string raw LLM fields must not crash the confidence-bar sweep."""
    from intelligence.cascade.core import CascadeResult, Rung

    truth = u676a.truth_from_row(_bill_row())
    cascade = CascadeResult(
        False,
        True,
        {
            "vendor_name": 999,
            "bill_number": 12345,
            "bill_date": date(2026, 5, 1),
            "total_amount": "100.00",
            "confidence": 0.55,
        },
        0.55,
        Rung("foundry", "DeepSeek-V4-Flash"),
        [],
    )
    with patch.object(u676a, "resolve_vendor_from_name", return_value=truth.vendor_public_id):
        pred = u676a.arm_prediction_from_cascade(
            cascade, BillExtractionMapper(), truth=truth
        )
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=truth,
        init_arm_b=pred,
        llm_arms_scored=frozenset({"B"}),
    )
    sweep = u676a.header_confidence_bar_sweep([row], arms=frozenset({"B"}))
    assert sweep["arms"]["B"]["0.5"]["bill_number"]["coverage"] == Decimal("0")
    assert pred.raw_bill_number_correct is False
    assert u676a._raw_prediction_nonempty(pred, "bill_number") is False
    assert u676a._raw_prediction_nonempty(pred, "vendor") is False
    assert u676a._raw_prediction_nonempty(pred, "amount") is True


def test_vendor_db_error_still_scores_other_raw_fields_and_counts_failure():
    from intelligence.cascade.core import CascadeResult, Rung

    truth = u676a.truth_from_row(_bill_row())
    cascade = CascadeResult(
        False,
        True,
        {
            "vendor_name": "Real Vendor",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "confidence": 0.55,
        },
        0.55,
        Rung("foundry", "DeepSeek-V4-Flash"),
        [],
    )

    def boom(*_a, **_k):
        raise RuntimeError("vendor db down")

    with patch.object(u676a, "resolve_vendor_from_name", side_effect=boom):
        pred = u676a.arm_prediction_from_cascade(
            cascade, BillExtractionMapper(), truth=truth
        )
    assert pred.raw_vendor_resolve_failed is True
    assert pred.raw_vendor_correct is False
    assert pred.raw_bill_number_correct is True
    assert pred.raw_bill_date_correct is True
    assert pred.raw_amount_correct is True
    row = u676a.ScoredHeaderBill(
        bill=_bill_row(),
        stratum="manual",
        truth=truth,
        init_arm_b=pred,
        llm_arms_scored=frozenset({"B"}),
    )
    metrics = u676a.build_metrics(
        [row],
        run_status="complete",
        cap_tripped=False,
        scored_bill_count=1,
        unscored_bill_count=0,
        blob_skip_unreadable=0,
        blob_skip_unparseable=0,
        blob_skip_stale=0,
        arm_a_errors=0,
        arms=frozenset({"A", "B"}),
    )
    assert metrics["raw_vendor_resolve_failed"] == 1
    with tempfile.TemporaryDirectory() as tmp:
        paths = u676a.write_report_and_outputs(
            out_dir=Path(tmp),
            stamp="vfail",
            scored=[row],
            metrics=metrics,
            dry_info=None,
            eval_since=u676a.DEFAULT_EVAL_SINCE,
            eval_until=u676a.DEFAULT_EVAL_UNTIL,
        )
        report = paths[0].read_text(encoding="utf-8")
    assert "vendor lookups failed: 1" in report


def test_accepted_cascade_resolves_vendor_without_truth():
    from intelligence.cascade.core import CascadeResult, Rung

    vid = "11111111-1111-1111-1111-111111111111"
    cascade = CascadeResult(
        True,
        False,
        {
            "vendor_name": "Real Vendor",
            "bill_number": "INV-100",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "confidence": 0.9,
        },
        0.9,
        Rung("foundry", "DeepSeek-V4-Flash"),
        [],
    )
    with patch.object(u676a, "resolve_vendor_from_name", return_value=vid) as mock:
        pred = u676a.arm_prediction_from_cascade(cascade, BillExtractionMapper())
    mock.assert_called_once()
    assert pred.vendor_public_id == vid
    assert not pred.arm_error


def test_main_uses_distinct_concurrency_for_arms_b_and_c(tmp_path):
    fake = [_bill_row()]
    seen: dict[str, int] = {}

    async def fake_batch(_work, *, ladder, concurrency=0, **_k):
        key = "C" if ladder[0].provider == "anthropic" else "B"
        seen[key] = concurrency

    from entities.attachment.business.extraction_service import REDACTION_VERSION

    payload_json = json.dumps(
        {
            "content": "t",
            "key_value_pairs": [],
            "redaction_version": REDACTION_VERSION,
            "attachment_public_id": "00000000-0000-0000-0000-000000000001",
            "source_file_hash": "a",
            "method": "text_layer",
            "pages_count": 1,
        }
    )
    with patch.object(u676a, "load_header_bills_from_db", return_value=fake):
        with patch.object(u676a, "read_text_blob_with_retry", return_value=payload_json):
            with patch.object(u676a, "run_llm_arm_batch", side_effect=fake_batch):
                with patch.object(u676a, "execute_run") as mock_exec:
                    def run_build(build, llm):
                        build()
                        llm()

                    mock_exec.side_effect = run_build
                    with patch.object(u676a, "assert_cli_system_admin"):
                        u676a.main(
                            [
                                "--out-dir",
                                str(tmp_path),
                                "--run",
                                "--sample-size",
                                "1",
                                "--arms",
                                "A,B,C",
                                "--claude-ladder",
                                "claude-haiku",
                                "--claude-concurrency",
                                "1",
                                "--concurrency",
                                "4",
                            ]
                        )
    assert seen.get("B") == 4
    assert seen.get("C") == 1
