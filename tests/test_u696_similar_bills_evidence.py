"""U-696: similar-bills evidence + bill-text modes in u654a harness (fakes only)."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from entities.attachment.business.extraction_service import REDACTION_VERSION
from intelligence.cascade.bill_coding import BILL_CODING_SUGGESTION_TASK, SUSPENSE_SCC_NUMBER
from scripts.u653b_backtest import BillRow, ModePrediction
from intelligence.cascade.core import CascadeResult
from scripts.u691_similar_bills_backtest import (
    BillTokenProfile,
    EnrichedBill,
    Neighbour,
    TEXT_SOURCE_PDF,
    TokenSourceConfig,
    apply_similar_evidence,
    collect_top_neighbours_for_sample,
    compute_idf_from_corpus,
    token_match_ratio,
    tokenize_bill_text,
    top_neighbours_any_vendor,
)
from intelligence.cascade.bill_coding_payload import build_candidate_sccs_for_bill
from scripts import u654a_text_backtest as u654a


def _current_envelope(content: str, **extra: str) -> str:
    payload = {
        "redaction_version": REDACTION_VERSION,
        "content": content,
        "key_value_pairs": [],
        "attachment_public_id": "00000000-0000-0000-0000-000000000001",
        "source_file_hash": "abc",
        "method": "text_layer",
        "pages_count": 1,
        **extra,
    }
    return json.dumps(payload)


def _bill_row(
    bill_id: int,
    vendor_id: int,
    created: str,
    scc: int,
    *,
    amount: str = "100.00",
) -> BillRow:
    return BillRow(
        bill_id=bill_id,
        vendor_id=vendor_id,
        created=datetime.fromisoformat(created),
        label_scc_id=scc,
        label_project_id=10,
        total_abs_amount=Decimal(amount),
        mixed_code=False,
    )


def _enriched(b: BillRow, *, desc: str = "") -> EnrichedBill:
    return EnrichedBill(b, memo="", line_descriptions=desc, line_count=1)


def test_neighbours_are_strictly_earlier_and_never_self():
    """PDF tokens only; priors must be created strictly before the query bill."""
    bills = [
        _bill_row(1, 1, "2026-03-01T10:00:00", 100),
        _bill_row(2, 1, "2026-04-01T10:00:00", 101),
        _bill_row(3, 2, "2026-05-01T10:00:00", 102),
    ]
    pdf_tokens = {
        1: tokenize_bill_text("roofing shingles alpha"),
        2: tokenize_bill_text("roofing gutters bravo"),
        3: tokenize_bill_text("roofing tiles alpha"),
    }
    cfg = TokenSourceConfig(text_source=TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=pdf_tokens)
    enriched = [_enriched(b) for b in bills]
    idf = compute_idf_from_corpus(
        enriched,
        since=datetime(2026, 1, 1).date(),
        until=datetime(2026, 12, 31).date(),
        config=cfg,
    )
    neighbours = collect_top_neighbours_for_sample(
        enriched,
        frozenset({3}),
        idf,
        top_k=5,
        token_config=cfg,
    )
    n_rows = neighbours[3]
    assert n_rows
    for n in n_rows:
        assert n.prior.enriched.bill.bill_id != 3
        assert n.prior.enriched.bill.created < bills[2].created


def test_line_descriptions_do_not_change_pdf_neighbours():
    b_weak = _bill_row(10, 5, "2026-03-15T10:00:00", 200)
    b_desc_only = _bill_row(12, 7, "2026-04-01T10:00:00", 202)
    b_query = _bill_row(11, 6, "2026-05-15T10:00:00", 201)
    pdf_tokens = {
        10: tokenize_bill_text("drywall sheetrock gamma"),
        11: tokenize_bill_text("drywall sheetrock delta"),
        12: tokenize_bill_text("zzzzzzzzzzzz"),
    }
    cfg = TokenSourceConfig(text_source=TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=pdf_tokens)
    enriched_with = [
        _enriched(b_weak),
        _enriched(b_desc_only, desc="UNIQUE_DESC_TOKEN_XYZ"),
        _enriched(b_query, desc="UNIQUE_DESC_TOKEN_XYZ"),
    ]
    enriched_without = [
        _enriched(b_weak),
        _enriched(b_desc_only, desc="UNIQUE_DESC_TOKEN_XYZ"),
        _enriched(b_query, desc=""),
    ]
    idf = compute_idf_from_corpus(
        enriched_with,
        since=datetime(2026, 1, 1).date(),
        until=datetime(2026, 12, 31).date(),
        config=cfg,
    )
    with_desc = collect_top_neighbours_for_sample(
        enriched_with, frozenset({11}), idf, token_config=cfg
    )
    without_desc = collect_top_neighbours_for_sample(
        enriched_without, frozenset({11}), idf, token_config=cfg
    )
    assert with_desc[11]
    assert [r.prior.enriched.bill.bill_id for r in with_desc[11]] == [
        r.prior.enriched.bill.bill_id for r in without_desc[11]
    ]


def test_similar_bills_block_shape():
    scc_by_id = {
        5: u654a.SccRef(5, "03.01", "Framing", datetime(2026, 1, 1)),
    }
    prior_b = _bill_row(1, 1, "2026-01-01T10:00:00", 5)
    enriched_prior = _enriched(prior_b)
    prof = BillTokenProfile(enriched_prior, frozenset(["a"]), 1.0, 0.0)
    rows = [Neighbour(0.9, prof, 0.42, 1.0, True)]
    block = u654a.similar_bills_evidence_from_neighbours(rows, scc_by_id)
    assert block == [
        {
            "scc_number": "03.01",
            "scc_name": "Framing",
            "match": 0.42,
            "amount_ratio": 1.0,
            "same_vendor": True,
        }
    ]
    assert set(block[0]) == {
        "scc_number",
        "scc_name",
        "match",
        "amount_ratio",
        "same_vendor",
    }


def test_production_user_message_omits_similar_bills():
    payload = {
        "vendor_name": "V",
        "bill_text": "body",
        "history_evidence": [],
        "candidate_sccs": [{"number": "01.01", "name": "X"}],
        "candidate_projects": None,
        "similar_bills": [{"scc_number": "02.02", "scc_name": "Y", "match": 1.0}],
    }
    with pytest.raises(ValueError, match="unexpected payload keys"):
        BILL_CODING_SUGGESTION_TASK.build_user_message(payload)


def test_backtest_user_message_includes_similar_bills():
    task = u654a.backtest_cascade_task(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=True,
    )
    payload = {
        "vendor_name": "V",
        "bill_text": "body",
        "history_evidence": [],
        "candidate_sccs": [{"number": "01.01", "name": "X"}],
        "candidate_projects": None,
        "similar_bills": [
            {
                "scc_number": "02.02",
                "scc_name": "Y",
                "match": 0.5,
                "amount_ratio": 1.0,
                "same_vendor": False,
            }
        ],
    }
    msg = task.build_user_message(payload)
    parsed = json.loads(msg)
    assert "similar_bills" in parsed
    assert parsed["similar_bills"][0]["scc_number"] == "02.02"


def test_backtest_prompt_is_production_plus_one_rule_line():
    prod = BILL_CODING_SUGGESTION_TASK.system_prompt
    with_sim = u654a.backtest_cascade_task(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=True,
    ).system_prompt
    assert with_sim == prod + "\n" + u654a.SIMILAR_BILLS_RULE_LINE
    assert with_sim.count(u654a.SIMILAR_BILLS_RULE_LINE) == 1
    full_only = u654a.backtest_cascade_task(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=False,
    ).system_prompt
    assert full_only == prod


def test_union_adds_neighbour_out_of_family_scc():
    scc_by_id = {
        1: u654a.SccRef(1, "01.01", "A", datetime(2026, 1, 1)),
        99: u654a.SccRef(99, "99.99", "Out", datetime(2026, 1, 1)),
    }
    narrowed = [{"number": "01.01", "name": "A"}]
    prior_b = _bill_row(2, 1, "2026-04-01T10:00:00", 99)
    p_prof = BillTokenProfile(_enriched(prior_b), frozenset(), 0.0, 0.0)
    neighbours = [Neighbour(0.9, p_prof, 0.5, 1.0, True)]
    catalog = build_candidate_sccs_for_bill(
        scc_by_id,
        as_of=datetime(2026, 5, 1),
        ambiguous_numbers=frozenset(),
    )
    merged, added, _block, _filtered, _drops = apply_similar_evidence(
        neighbours,
        narrowed,
        as_of_catalog=catalog,
        scc_by_id=scc_by_id,
        ambiguous_numbers=frozenset(),
    )
    assert added == 1
    assert {c["number"] for c in merged} == {"01.01", "99.99"}


def test_full_content_uses_whole_content_and_counts_cap():
    long_body = "z" * 70_000
    raw = _current_envelope(long_body)
    text, capped = u654a.resolve_bill_text_from_raw_blob(
        raw, bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT
    )
    assert capped is True
    assert len(text) <= u654a.FULL_CONTENT_CHAR_CAP + 20
    assert "truncated" in text


def test_stale_envelope_yields_no_full_content_text():
    stale = json.dumps({"redaction_version": "old", "content": "hello"})
    text, capped = u654a.resolve_bill_text_from_raw_blob(
        stale, bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT
    )
    assert text == ""
    assert capped is False


def test_envelope_truncated_default_unchanged():
    body = "x" * 8000
    raw = _current_envelope(body)
    text, capped = u654a.resolve_bill_text_from_raw_blob(
        raw, bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE
    )
    assert text == raw
    assert capped is False
    bill = u654a.TextBillRow(
        bill_id=1,
        vendor_id=1,
        created=datetime(2026, 5, 1),
        label_scc_id=1,
        label_project_id=1,
        total_abs_amount=Decimal("1"),
        mixed_code=False,
        extracted_text_blob_url="u",
    )
    payload = u654a.build_payload(
        bill,
        bill_text=raw,
        vendor_name="V",
        mode="A",
        priors=[],
        scc_by_id={1: u654a.SccRef(1, "01.01", "N", datetime(2026, 1, 1))},
        candidate_sccs=[{"number": "01.01", "name": "N"}],
        candidate_projects=None,
    )
    assert len(payload["bill_text"]) <= u654a.BILL_TEXT_CHAR_BUDGET + 20


def test_bill_ids_from_pins_sample():
    bills = [
        u654a.TextBillRow(
            bill_id=42,
            vendor_id=1,
            created=datetime(2026, 5, 2),
            label_scc_id=1,
            label_project_id=1,
            total_abs_amount=Decimal("10"),
            mixed_code=False,
            extracted_text_blob_url="u",
        ),
        u654a.TextBillRow(
            bill_id=99,
            vendor_id=1,
            created=datetime(2026, 5, 3),
            label_scc_id=1,
            label_project_id=1,
            total_abs_amount=Decimal("10"),
            mixed_code=False,
            extracted_text_blob_url="u",
        ),
    ]
    vv = u654a.build_vendor_priors(bills)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["BillId", "mode"])
        w.writerow([42, "A"])
        w.writerow([42, "B"])
        w.writerow([99, "A"])
        path = Path(fh.name)
    try:
        pinned = u654a.load_distinct_bill_ids_from_predictions_csv(path)
        assert pinned == [42, 99]
        sample, stats = u654a.sample_from_pinned_bill_ids(
            bills,
            pinned,
            vv,
            eval_since=u654a.DEFAULT_EVAL_SINCE,
            eval_until=u654a.DEFAULT_EVAL_UNTIL,
        )
        assert [b.bill_id for b, _ in sample] == [42, 99]
        assert stats["bill_ids_found"] == 2
    finally:
        path.unlink()


def test_outputs_privacy_safe_with_u696_columns():
    row = u654a.ScoredTextBill(
        bill=u654a.TextBillRow(
            bill_id=1,
            vendor_id=2,
            created=datetime(2026, 5, 1),
            label_scc_id=10,
            label_project_id=11,
            total_abs_amount=Decimal("100"),
            mixed_code=False,
            extracted_text_blob_url="u",
        ),
        stratum=">=90%",
        history_a=ModePrediction("A", 10, 11, "vendor_history", Decimal("0.9"), [], 5),
        history_b=ModePrediction("B", 10, 11, "vendor_history", Decimal("0.9"), [], 5),
        text_a=u654a.TextAttemptResult(
            "A",
            CascadeResult(True, False, {}, 0.9, None, []),
            predicted_scc_id=10,
        ),
        text_b=u654a.TextAttemptResult(
            "B",
            CascadeResult(True, False, {}, 0.9, None, []),
            predicted_scc_id=10,
            predicted_project_id=11,
        ),
        top_neighbour_scc_id=12,
        neighbour_count=3,
        followed_top_neighbour=True,
        candidates_added_by_neighbours=1,
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        u654a.write_report_and_outputs(
            out_dir=out,
            stamp="test",
            scored=[row],
            metrics=u654a.build_metrics(
                [row],
                cascades=[row.text_a.cascade, row.text_b.cascade],
                usage_totals={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                cap_tripped=False,
                scored_bill_count=1,
                unscored_bill_count=0,
                throttle_retry_count=0,
                throttle_failure_count=0,
                cascade_error_count=0,
                duplicate_scc_number_count=0,
                duplicate_project_abbrev_count=0,
            ),
            dry_info=None,
            eval_since=u654a.DEFAULT_EVAL_SINCE,
            eval_until=u654a.DEFAULT_EVAL_UNTIL,
        )
        csvs = list(out.glob("*.csv"))
        assert csvs
        u654a.assert_outputs_privacy_safe(csvs[0])


def _minimal_ref() -> u654a.ReferenceData:
    return u654a.ReferenceData(
        scc_by_id={1: u654a.SccRef(1, "01.01", "N", datetime(2026, 1, 1))},
        scc_number_to_id={"01.01": 1},
        proj_by_id={},
        proj_abbrev_to_id={},
        vendor_names={1: "Vendor"},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )


def _text_bill(bill_id: int, *, blob: str | None = "http://blob") -> u654a.TextBillRow:
    return u654a.TextBillRow(
        bill_id=bill_id,
        vendor_id=1,
        created=datetime(2026, 5, 2),
        label_scc_id=1,
        label_project_id=1,
        total_abs_amount=Decimal("10"),
        mixed_code=False,
        extracted_text_blob_url=blob,
    )


def _u696_resume_summary(**overrides: object) -> dict[str, object]:
    task = u654a.backtest_cascade_task(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
    )
    base: dict[str, object] = {
        "ladder": "foundry",
        "candidate_sccs": "full",
        "bill_text_mode": u654a.DEFAULT_BILL_TEXT_MODE,
        "similar_bills_evidence": False,
        "arm_hash": u654a.arm_hash(
            bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
            similar_bills_evidence=False,
            task=task,
        ),
        "metrics": {
            "usage": {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0},
        },
    }
    base.update(overrides)
    return base


def test_dry_run_token_estimates_differ_by_arm():
    bill = u654a.TextBillRow(
        bill_id=1,
        vendor_id=1,
        created=datetime(2026, 5, 1),
        label_scc_id=1,
        label_project_id=1,
        total_abs_amount=Decimal("100"),
        mixed_code=False,
        extracted_text_blob_url="u",
    )
    sample = [(bill, ">=90%")]
    ref = u654a.ReferenceData(
        scc_by_id={1: u654a.SccRef(1, "01.01", "N", datetime(2026, 1, 1))},
        scc_number_to_id={"01.01": 1},
        proj_by_id={},
        proj_abbrev_to_id={},
        vendor_names={1: "Vendor"},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    vv = u654a.build_vendor_priors([bill])
    h0, _ = u654a.estimate_dry_run_payload_stats(
        sample,
        ref=ref,
        vendor_votes=vv,
        candidate_scc_mode="narrowed",
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
    )
    long_text = "y" * u654a.FULL_CONTENT_CHAR_CAP
    ctx = u654a.SampleSimilarityContext(
        neighbours_raw_by_bill={},
        per_bill={1: u654a.SampleBillHarnessFields()},
        pdf_load_stats=None,
        bill_text_for_estimate={1: long_text},
        union_addition_bills=0,
        full_content_cap_hits=0,
        full_content_empty_or_stale=0,
        bills_with_pdf_read_failure=0,
        bills_with_no_linked_pdf=0,
        neighbour_filter_drops={},
    )
    h2, _ = u654a.estimate_dry_run_payload_stats(
        sample,
        ref=ref,
        vendor_votes=vv,
        candidate_scc_mode="narrowed",
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=True,
        similarity_ctx=ctx,
    )
    assert h2 > h0


def test_dry_run_non_legacy_full_and_narrowed_use_mean_not_max():
    ref = _minimal_ref()
    short = _text_bill(1)
    long = _text_bill(2)
    sample = [(short, "cold_start"), (long, "cold_start")]
    vv = u654a.build_vendor_priors([short, long])
    per_bill: dict[int, u654a.SampleBillHarnessFields] = {}
    bill_text_for_estimate = {
        1: "a" * 2000,
        2: "b" * 50_000,
    }
    for bid in (1, 2):
        per_bill[bid] = u654a.SampleBillHarnessFields(
            candidate_sccs=[{"number": "01.01", "name": "N"}],
        )
    ctx = u654a.SampleSimilarityContext(
        neighbours_raw_by_bill={},
        per_bill=per_bill,
        pdf_load_stats=None,
        bill_text_for_estimate=bill_text_for_estimate,
        union_addition_bills=0,
        full_content_cap_hits=1,
        full_content_empty_or_stale=0,
        bills_with_pdf_read_failure=0,
        bills_with_no_linked_pdf=0,
        neighbour_filter_drops={},
    )
    for mode in ("full", "narrowed"):
        mean_in, _ = u654a.estimate_dry_run_payload_stats(
            sample,
            ref=ref,
            vendor_votes=vv,
            candidate_scc_mode=mode,
            bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
            similar_bills_evidence=True,
            similarity_ctx=ctx,
        )
        total = u654a.estimate_dry_run_sample_one_mode_input_total(
            sample,
            ref=ref,
            vendor_votes=vv,
            candidate_scc_mode=mode,
            bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
            similar_bills_evidence=True,
            similarity_ctx=ctx,
        )
        task = u654a.backtest_cascade_task(
            bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
            similar_bills_evidence=True,
        )
        sys_tok = max(1, len(task.system_prompt) // 4)
        per_call = []
        for bill, _ in sample:
            priors = u654a.priors_for_bill(vv, bill)
            fields = per_bill[bill.bill_id]
            payload = u654a.build_backtest_payload(
                bill,
                bill_text=bill_text_for_estimate[bill.bill_id],
                vendor_name="Vendor",
                mode="A",
                priors=priors,
                scc_by_id=ref.scc_by_id,
                candidate_sccs=fields.candidate_sccs,
                candidate_projects=None,
                bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
                similar_bills=fields.similar_block,
            )
            user_msg = task.build_user_message(payload)
            per_call.append(max(1, len(user_msg) // 4) + sys_tok)
        assert mean_in == round(sum(per_call) / len(per_call))
        assert mean_in < max(per_call)
        assert total == sum(per_call)
        worst = u654a.estimate_dry_run_worst_case_input_tokens(
            sample,
            ref=ref,
            vendor_votes=vv,
            candidate_scc_mode=mode,
            bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
            similar_bills_evidence=True,
            similarity_ctx=ctx,
        )
        assert worst is not None
        assert worst >= max(per_call)


def test_backtest_task_equals_production_when_defaults():
    t = u654a.backtest_cascade_task(
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills_evidence=False,
    )
    assert t is BILL_CODING_SUGGESTION_TASK


def test_harness_payload_matches_production_envelope_default():
    bill = u654a.TextBillRow(
        bill_id=1,
        vendor_id=1,
        created=datetime(2026, 5, 1),
        label_scc_id=1,
        label_project_id=1,
        total_abs_amount=Decimal("1"),
        mixed_code=False,
        extracted_text_blob_url="u",
    )
    raw = _current_envelope("body text here")
    prod = u654a.build_payload(
        bill,
        bill_text=raw,
        vendor_name="V",
        mode="A",
        priors=[],
        scc_by_id={1: u654a.SccRef(1, "01.01", "N", datetime(2026, 1, 1))},
        candidate_sccs=[{"number": "01.01", "name": "N"}],
        candidate_projects=None,
    )
    harness = u654a.build_harness_payload(
        bill,
        raw_blob=raw,
        resolved_bill_text="ignored",
        vendor_name="V",
        mode="A",
        priors=[],
        scc_by_id={1: u654a.SccRef(1, "01.01", "N", datetime(2026, 1, 1))},
        candidate_sccs=[{"number": "01.01", "name": "N"}],
        candidate_projects=None,
        bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE,
        similar_bills=None,
    )
    assert harness == prod


def test_neighbours_exclude_later_bills():
    """Guard b: overlapping tokens on a LATER bill must not appear as a neighbour."""
    bills = [
        _bill_row(2, 1, "2026-03-01T10:00:00", 101),
        _bill_row(1, 1, "2026-04-01T10:00:00", 100),
        _bill_row(3, 1, "2026-04-05T10:00:00", 102),
        _bill_row(4, 1, "2026-02-01T10:00:00", 103),
    ]
    pdf_tokens = {
        2: tokenize_bill_text("roofing shingles alpha bravo"),
        1: tokenize_bill_text("roofing shingles alpha delta"),
        3: tokenize_bill_text("roofing shingles alpha echo"),
        4: tokenize_bill_text("plumbing pipes unrelated"),
    }
    cfg = TokenSourceConfig(text_source=TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=pdf_tokens)
    enriched = [_enriched(b) for b in bills]
    idf = compute_idf_from_corpus(
        enriched,
        since=u654a.DEFAULT_CAL_SINCE,
        until=u654a.DEFAULT_CAL_UNTIL,
        config=cfg,
    )
    neighbours = collect_top_neighbours_for_sample(
        enriched, frozenset({1}), idf, token_config=cfg
    )
    assert neighbours[1], "expected prior bill 2 as neighbour"
    assert all(n.prior.enriched.bill.bill_id != 3 for n in neighbours[1])
    assert neighbours[1][0].prior.enriched.bill.bill_id == 2


def test_apply_similar_evidence_filters_future_scc():
    scc_by_id = {
        1: u654a.SccRef(1, "01.01", "Old", datetime(2026, 1, 1)),
        2: u654a.SccRef(2, "02.02", "Future", datetime(2026, 6, 1)),
    }
    query_b = _bill_row(10, 1, "2026-05-15T10:00:00", 1)
    prior_b = _bill_row(9, 1, "2026-04-01T10:00:00", 2)
    q_prof = BillTokenProfile(_enriched(query_b), frozenset(["x"]), 1.0, 0.0)
    p_prof = BillTokenProfile(_enriched(prior_b), frozenset(["x"]), 1.0, 0.0)
    neighbours = [Neighbour(0.8, p_prof, 0.5, 1.0, True)]
    catalog = build_candidate_sccs_for_bill(
        scc_by_id, as_of=query_b.created, ambiguous_numbers=frozenset()
    )
    merged, added, block, filtered, drops = apply_similar_evidence(
        neighbours,
        [{"number": "01.01", "name": "Old"}],
        as_of_catalog=catalog,
        scc_by_id=scc_by_id,
        ambiguous_numbers=frozenset(),
    )
    assert added == 0
    assert block == []
    assert filtered == []
    assert drops["neighbours_dropped_not_in_as_of_catalog"] == 1


def test_long_full_content_survives_payload_and_user_message():
    long_body = "word " * 2500
    raw = _current_envelope(long_body)
    text, _ = u654a.resolve_bill_text_from_raw_blob(
        raw, bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT
    )
    assert len(text) > 6000
    bill = u654a.TextBillRow(
        bill_id=1,
        vendor_id=1,
        created=datetime(2026, 5, 1),
        label_scc_id=1,
        label_project_id=1,
        total_abs_amount=Decimal("1"),
        mixed_code=False,
        extracted_text_blob_url="u",
    )
    payload = u654a.build_backtest_payload(
        bill,
        bill_text=text,
        vendor_name="V",
        mode="A",
        priors=[],
        scc_by_id={1: u654a.SccRef(1, "01.01", "N", datetime(2026, 1, 1))},
        candidate_sccs=[{"number": "01.01", "name": "N"}],
        candidate_projects=None,
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
    )
    assert len(payload["bill_text"]) > 6000
    task = u654a.backtest_cascade_task(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=False,
    )
    msg = json.loads(task.build_user_message(payload))
    assert len(msg["bill_text"]) > 6000


def test_token_match_ratio_fixture():
    idf = {"alpha": 1.0, "beta": 1.0, "gamma": 1.0}
    q = BillTokenProfile(
        _enriched(_bill_row(1, 1, "2026-01-01T10:00:00", 1)),
        frozenset(["alpha", "beta"]),
        2.0,
        0.0,
    )
    p = BillTokenProfile(
        _enriched(_bill_row(2, 1, "2026-01-02T10:00:00", 2)),
        frozenset(["alpha", "gamma"]),
        2.0,
        0.0,
    )
    assert token_match_ratio(q, p, idf) == 0.5


def test_neighbour_ordering_combined_then_id_tiebreak():
    idf = {"shared": 1.0}
    query_b = _bill_row(100, 1, "2026-06-01T10:00:00", 1, amount="100")
    enriched_q = _enriched(query_b)
    q_prof = BillTokenProfile(enriched_q, frozenset(["shared"]), 1.0, 0.0)

    def prof(bid: int, created: str) -> BillTokenProfile:
        b = _bill_row(bid, 1, created, bid, amount="100")
        return BillTokenProfile(_enriched(b), frozenset(["shared"]), 1.0, 0.0)

    prior_by_id = {5: prof(5, "2026-05-01T10:00:00"), 3: prof(3, "2026-05-02T10:00:00")}
    token_index = {"shared": {5, 3}}
    ordered = top_neighbours_any_vendor(
        q_prof,
        idf,
        top_k=2,
        token_index=token_index,
        prior_by_id=prior_by_id,
    )
    assert [n.prior.enriched.bill.bill_id for n in ordered] == [3, 5]


def test_apply_similar_evidence_dedupes_union():
    scc_by_id = {
        1: u654a.SccRef(1, "01.01", "A", datetime(2026, 1, 1)),
        2: u654a.SccRef(2, "02.02", "B", datetime(2026, 1, 1)),
    }
    prior_b = _bill_row(2, 1, "2026-04-01T10:00:00", 2)
    p_prof = BillTokenProfile(_enriched(prior_b), frozenset(), 0.0, 0.0)
    neighbours = [
        Neighbour(0.9, p_prof, 0.5, 1.0, True),
        Neighbour(0.8, p_prof, 0.4, 1.0, True),
    ]
    catalog = build_candidate_sccs_for_bill(
        scc_by_id,
        as_of=datetime(2026, 5, 1),
        ambiguous_numbers=frozenset(),
    )
    merged, added, _block, filtered, _drops = apply_similar_evidence(
        neighbours,
        [{"number": "01.01", "name": "A"}],
        as_of_catalog=catalog,
        scc_by_id=scc_by_id,
        ambiguous_numbers=frozenset(),
    )
    assert added == 1
    assert len(filtered) == 2
    assert len(merged) == 2


def test_compute_sample_neighbours_pdf_failure_counts_not_double_no_link():
    bills = [
        _bill_row(1, 1, "2026-03-01T10:00:00", 100),
        _bill_row(2, 1, "2026-04-01T10:00:00", 101),
        _bill_row(3, 1, "2026-05-01T10:00:00", 102),
    ]
    blob_map = {1: "http://a", 2: "http://b", 3: "http://bad"}
    sample = [
        (_text_bill(3, blob="http://bad"), "x"),
        (_text_bill(4, blob=None), "y"),
    ]
    ref = _minimal_ref()

    def read_blob(url: str) -> str:
        if url == "http://bad":
            raise OSError("read failed")
        return _current_envelope("roofing alpha beta")

    ctx = u654a.compute_sample_neighbours(
        bills,
        blob_map,
        sample,
        read_blob=read_blob,
        ref=ref,
        vendor_votes={1: []},
        candidate_scc_mode="narrowed",
        similar_bills_evidence=True,
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        pdf_read_workers=1,
    )
    assert ctx.bills_with_pdf_read_failure == 1
    assert ctx.bills_with_no_linked_pdf == 1


def test_prepare_sample_per_bill_evidence_filters_raw_neighbours_for_counts():
    scc_by_id = {
        1: u654a.SccRef(1, "01.01", "Ok", datetime(2026, 1, 1)),
        2: u654a.SccRef(2, "02.02", "Future", datetime(2026, 6, 1)),
        3: u654a.SccRef(3, SUSPENSE_SCC_NUMBER, "Suspense", datetime(2026, 1, 1)),
    }
    ref = u654a.ReferenceData(
        scc_by_id=scc_by_id,
        scc_number_to_id={"01.01": 1, "02.02": 2, SUSPENSE_SCC_NUMBER: 3},
        proj_by_id={},
        proj_abbrev_to_id={},
        vendor_names={1: "V"},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    query = _text_bill(10)
    sample = [(query, "cold_start")]
    ok_prior = _bill_row(8, 1, "2026-04-01T10:00:00", 1)
    future_prior = _bill_row(9, 1, "2026-04-02T10:00:00", 2)
    susp_prior = _bill_row(7, 1, "2026-04-03T10:00:00", 3)
    raw = [
        Neighbour(0.95, BillTokenProfile(_enriched(ok_prior), frozenset(), 0.0, 0.0), 0.9, 1.0, True),
        Neighbour(0.90, BillTokenProfile(_enriched(future_prior), frozenset(), 0.0, 0.0), 0.8, 1.0, True),
        Neighbour(0.85, BillTokenProfile(_enriched(susp_prior), frozenset(), 0.0, 0.0), 0.7, 1.0, True),
    ]
    ctx = u654a.prepare_sample_per_bill_evidence(
        sample,
        neighbours_raw_by_bill={10: raw},
        ref=ref,
        vendor_votes={1: []},
        candidate_scc_mode="narrowed",
        similar_bills_evidence=True,
        bill_text_resolved={},
    )
    fields = ctx.per_bill[10]
    assert fields.neighbour_count == 1
    assert fields.top_neighbour_scc_id == 1
    assert len(fields.similar_block) == 1
    assert fields.similar_block[0]["scc_number"] == "01.01"
    top, n, followed = u654a.neighbour_metadata_for_bill(
        fields.filtered_neighbours,
        predicted_scc_id=1,
    )
    assert top == 1 and n == 1 and followed is True
    top_wrong, _, followed_wrong = u654a.neighbour_metadata_for_bill(
        fields.filtered_neighbours,
        predicted_scc_id=2,
    )
    assert top_wrong == 1 and followed_wrong is False


def test_apply_similar_evidence_drop_counters_precise():
    scc_by_id = {
        1: u654a.SccRef(1, "01.01", "Ok", datetime(2026, 1, 1)),
        2: u654a.SccRef(2, "02.02", "Future", datetime(2026, 6, 1)),
        3: u654a.SccRef(3, SUSPENSE_SCC_NUMBER, "Susp", datetime(2026, 1, 1)),
        4: u654a.SccRef(4, "04.04", "Ambig", datetime(2026, 1, 1)),
    }
    ambiguous = frozenset({"04.04"})
    as_of = build_candidate_sccs_for_bill(
        scc_by_id,
        as_of=datetime(2026, 5, 1),
        ambiguous_numbers=ambiguous,
    )
    priors = [
        _bill_row(11, 1, "2026-04-01T10:00:00", 1),
        _bill_row(12, 1, "2026-04-02T10:00:00", 2),
        _bill_row(13, 1, "2026-04-03T10:00:00", 3),
        _bill_row(14, 1, "2026-04-04T10:00:00", 4),
    ]
    neighbours = [
        Neighbour(0.9, BillTokenProfile(_enriched(p), frozenset(), 0.0, 0.0), 0.5, 1.0, True)
        for p in priors
    ]
    _merged, _added, block, filtered, drops = apply_similar_evidence(
        neighbours,
        [{"number": "01.01", "name": "Ok"}],
        as_of_catalog=as_of,
        scc_by_id=scc_by_id,
        ambiguous_numbers=ambiguous,
    )
    assert len(filtered) == 1
    assert len(block) == 1
    assert drops["neighbours_dropped_ambiguous"] == 1
    assert drops["neighbours_dropped_suspense"] == 1
    assert drops["neighbours_dropped_not_in_as_of_catalog"] == 1


def _run_resume_main(tmp_path: Path, summary: dict, csv_path: Path, extra_argv: list[str]):
    summary_path = tmp_path / "u654a_summary_foundry_t.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    argv = [
        "--run",
        "--sample-size",
        "0",
        "--out-dir",
        str(tmp_path),
        "--resume-from",
        str(csv_path),
        "--ladder",
        "foundry",
        "--candidate-sccs",
        "full",
        *extra_argv,
    ]
    with patch.object(u654a, "load_bills_from_db", return_value=[]):
        with patch.object(u654a, "load_blob_urls_from_db", return_value={}):
            with patch.object(u654a, "merge_text_bills", return_value=[]):
                with patch.object(u654a, "build_vendor_priors", return_value={}):
                    with patch.object(u654a, "count_eval_populations", return_value={}):
                        with patch.object(u654a, "stratified_sample", return_value=[]):
                            with patch.object(
                                u654a, "load_reference_data", return_value=_minimal_ref()
                            ):
                                with patch.object(
                                    u654a,
                                    "write_report_and_outputs",
                                    return_value=(None, csv_path, None),
                                ):
                                    with patch.object(
                                        u654a, "execute_run", return_value="complete"
                                    ):
                                        return u654a.main(argv)


def test_resume_refuses_h0_summary_into_h2_arm(tmp_path):
    csv_path = tmp_path / "u654a_predictions_foundry_t.csv"
    csv_path.write_text(
        "BillId,VendorId,mode,suggester,predicted_scc_id,label_scc_id,"
        "predicted_project_id,label_project_id,correct_scc,correct_project,"
        "stratum,winning_rung,winning_rung_provider,needs_human\n",
        encoding="utf-8",
    )
    summary = _u696_resume_summary(bill_text_mode=u654a.DEFAULT_BILL_TEXT_MODE)
    with pytest.raises(SystemExit, match="bill_text_mode"):
        _run_resume_main(
            tmp_path,
            summary,
            csv_path,
            ["--bill-text", u654a.BILL_TEXT_MODE_FULL_CONTENT],
        )


def test_resume_refuses_legacy_summary_without_u696_keys(tmp_path):
    csv_path = tmp_path / "u654a_predictions_foundry_t.csv"
    csv_path.write_text(
        "BillId,VendorId,mode,suggester,predicted_scc_id,label_scc_id,"
        "predicted_project_id,label_project_id,correct_scc,correct_project,"
        "stratum,winning_rung,winning_rung_provider,needs_human\n",
        encoding="utf-8",
    )
    legacy = {
        "ladder": "foundry",
        "candidate_sccs": "full",
        "metrics": {"usage": {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}},
    }
    with pytest.raises(SystemExit, match="pre-U-696"):
        _run_resume_main(tmp_path, legacy, csv_path, [])


def test_resume_refuses_similar_bills_evidence_mismatch(tmp_path):
    csv_path = tmp_path / "u654a_predictions_foundry_t.csv"
    csv_path.write_text(
        "BillId,VendorId,mode,suggester,predicted_scc_id,label_scc_id,"
        "predicted_project_id,label_project_id,correct_scc,correct_project,"
        "stratum,winning_rung,winning_rung_provider,needs_human\n",
        encoding="utf-8",
    )
    summary = _u696_resume_summary(similar_bills_evidence=False)
    with pytest.raises(SystemExit, match="similar_bills_evidence"):
        _run_resume_main(tmp_path, summary, csv_path, ["--similar-bills-evidence"])


def test_resume_refuses_arm_hash_mismatch(tmp_path):
    csv_path = tmp_path / "u654a_predictions_foundry_t.csv"
    csv_path.write_text(
        "BillId,VendorId,mode,suggester,predicted_scc_id,label_scc_id,"
        "predicted_project_id,label_project_id,correct_scc,correct_project,"
        "stratum,winning_rung,winning_rung_provider,needs_human\n",
        encoding="utf-8",
    )
    summary = _u696_resume_summary(arm_hash="deadbeefdeadbeef")
    with pytest.raises(SystemExit, match="arm_hash"):
        _run_resume_main(tmp_path, summary, csv_path, [])


def test_resume_matching_u696_summary_proceeds(tmp_path, capsys):
    csv_path = tmp_path / "u654a_predictions_foundry_t.csv"
    csv_path.write_text(
        "BillId,VendorId,mode,suggester,predicted_scc_id,label_scc_id,"
        "predicted_project_id,label_project_id,correct_scc,correct_project,"
        "stratum,winning_rung,winning_rung_provider,needs_human\n",
        encoding="utf-8",
    )
    summary = _u696_resume_summary()
    rc = _run_resume_main(tmp_path, summary, csv_path, [])
    assert rc == 0
    assert "Resuming:" in capsys.readouterr().out


def test_main_dry_run_bill_ids_from_wiring(monkeypatch):
    """Guard f: --bill-ids-from pins the sample in a dry-run main()."""
    bills = [
        _text_bill(77),
        _text_bill(88),
        _text_bill(99),
    ]

    def fake_load_bills():
        return bills

    def fake_blob():
        return {77: "http://example/77", 88: "http://example/88", 99: "http://example/99"}

    def fake_ref():
        return _minimal_ref()

    captured: dict[str, object] = {}

    def fake_dry_run_summary(sample, **_k):
        captured["ids"] = [b.bill_id for b, _ in sample]
        return {"sample_selected": len(sample)}

    monkeypatch.setattr(u654a, "load_bills_from_db", fake_load_bills)
    monkeypatch.setattr(u654a, "load_blob_urls_from_db", fake_blob)
    monkeypatch.setattr(u654a, "load_reference_data", fake_ref)
    monkeypatch.setattr(u654a, "validate_windows", lambda *a, **k: None)
    monkeypatch.setattr(u654a, "dry_run_summary", fake_dry_run_summary)
    monkeypatch.setattr(u654a, "catalog_diagnostics_for_sample", lambda *a, **k: {})
    monkeypatch.setattr(u654a, "compute_sample_neighbours", lambda *a, **k: None)
    monkeypatch.setattr(u654a, "merge_text_bills", lambda b, m: b)
    monkeypatch.setattr(u654a, "build_vendor_priors", lambda b: {1: []})
    monkeypatch.setattr(u654a, "count_eval_populations", lambda *a, **k: {})

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["BillId", "mode"])
        w.writerow([77, "A"])
        w.writerow([99, "A"])
        csv_path = Path(fh.name)
    try:
        rc = u654a.main(
            [
                "--bill-ids-from",
                str(csv_path),
                "--eval-since",
                "2026-05-01",
                "--eval-until",
                "2026-05-31",
            ]
        )
        assert rc == 0
        assert captured["ids"] == [77, 99]
    finally:
        csv_path.unlink()


def test_main_dry_run_bill_ids_from_records_pin_stats(monkeypatch):
    bills = [_text_bill(77), _text_bill(88), _text_bill(99)]

    monkeypatch.setattr(u654a, "load_bills_from_db", lambda: bills)
    monkeypatch.setattr(
        u654a,
        "load_blob_urls_from_db",
        lambda: {77: "u", 88: "u", 99: "u"},
    )
    monkeypatch.setattr(u654a, "load_reference_data", _minimal_ref)
    monkeypatch.setattr(u654a, "validate_windows", lambda *a, **k: None)
    monkeypatch.setattr(u654a, "catalog_diagnostics_for_sample", lambda *a, **k: {})
    monkeypatch.setattr(u654a, "compute_sample_neighbours", lambda *a, **k: None)
    monkeypatch.setattr(u654a, "merge_text_bills", lambda b, m: b)
    monkeypatch.setattr(u654a, "build_vendor_priors", lambda b: {1: []})
    monkeypatch.setattr(u654a, "count_eval_populations", lambda *a, **k: {})

    captured: dict[str, object] = {}

    def fake_dry_run_summary(sample, **kwargs):
        captured["ids"] = [b.bill_id for b, _ in sample]
        captured["bill_ids_pin_stats"] = kwargs.get("bill_ids_pin_stats")
        return {"sample_selected": len(sample)}

    monkeypatch.setattr(u654a, "dry_run_summary", fake_dry_run_summary)

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["BillId", "mode"])
        w.writerow([77, "A"])
        w.writerow([99, "B"])
        csv_path = Path(fh.name)
    try:
        u654a.main(
            [
                "--bill-ids-from",
                str(csv_path),
                "--eval-since",
                "2026-05-01",
                "--eval-until",
                "2026-05-31",
            ]
        )
        assert captured["ids"] == [77, 99]
        stats = captured["bill_ids_pin_stats"]
        assert stats["bill_ids_requested"] == 2
        assert stats["bill_ids_found"] == 2
    finally:
        csv_path.unlink()
