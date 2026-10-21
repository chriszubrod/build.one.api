"""U-702: u654a loud failure on Claude budget skips."""
from __future__ import annotations

import asyncio
import csv
import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from intelligence.cascade.core import Attempt, CascadeResult, Rung
from scripts import u654a_text_backtest as u654a


def _bill(bill_id: int) -> u654a.TextBillRow:
    return u654a.TextBillRow(
        bill_id=bill_id,
        vendor_id=1,
        created=datetime.fromisoformat("2026-05-01T10:00:00"),
        label_scc_id=1,
        label_project_id=10,
        total_abs_amount=Decimal("100"),
        mixed_code=False,
        extracted_text_blob_url="https://example/blob",
    )


def _payload() -> dict:
    return {
        "vendor_name": "v",
        "bill_text": "t",
        "history_evidence": [],
        "candidate_sccs": [],
        "candidate_projects": None,
    }


def _work(bill_id: int, mode: str) -> u654a.TextCascadeWorkItem:
    return u654a.TextCascadeWorkItem(
        _bill(bill_id), mode, "cold_start", _payload(), False,
    )


def _skip_cascade(token: str) -> CascadeResult:
    return CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=False,
                error=f"claude_unavailable:{token}",
            )
        ],
    )


def _ok_cascade() -> CascadeResult:
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


def test_error_kind_classifies_claude_unavailable():
    kind, status = u654a.error_kind_from_error_string("claude_unavailable:daily_cap")
    assert kind == "budget_skip:daily_cap"
    assert status is None
    kind2, _ = u654a.error_kind_from_error_string("claude_unavailable:credit")
    assert kind2 == "budget_skip:credit"


@pytest.mark.parametrize(
    "token",
    ["daily_cap", "credit", "no_key", "disabled"],
)
def test_budget_skip_aborts_batch_skipped_pair_unscored(token: str):
    work = [_work(i, "A") for i in (1, 2, 3, 4)]
    call_n = 0

    async def fake_cascade(_payload, transport_for=None, ladder=None):
        nonlocal call_n
        call_n += 1
        if call_n == 3:
            return _skip_cascade(token)
        return _ok_cascade()

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {"03.01": 1},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
        with patch.object(u654a, "estimate_ladder_first_rung_cost_usd", return_value=0.01):
            out = asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: None,
                    concurrency=1,
                    max_cost_usd=100.0,
                    cost_state=cost_state,
                )
            )
    assert cost_state.get("budget_abort") is True
    assert cost_state.get("budget_skip_token") == token
    assert len(out) == 2
    assert (3, "A") not in out
    assert (4, "A") not in out


def test_budget_skip_after_foundry_accept_still_unscored():
    """Claude rung skipped but Foundry accepted — still unscored (not a finished text result)."""
    work = [_work(1, "A")]
    cascade = CascadeResult(
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
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-sonnet-5-5"),
                accepted=False,
                error="claude_unavailable:daily_cap",
            ),
            Attempt(
                rung=Rung("foundry", "DeepSeek-V4-Flash"),
                accepted=True,
                result={
                    "scc_number": "03.01",
                    "project_abbreviation": None,
                    "confidence": 0.9,
                    "rationale": "x",
                },
                confidence=0.9,
            ),
        ],
    )

    async def fake_cascade(_payload, transport_for=None, ladder=None):
        return cascade

    cost_state = {
        "usd": 0.0,
        "stopped": False,
        "scc_number_to_id": {"03.01": 1},
        "proj_abbrev_to_id": {},
    }
    with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
        with patch.object(u654a, "estimate_ladder_first_rung_cost_usd", return_value=0.01):
            out = asyncio.run(
                u654a.run_text_cascade_batch(
                    work,
                    transport_for=lambda _p: None,
                    concurrency=1,
                    max_cost_usd=100.0,
                    cost_state=cost_state,
                )
            )
    assert cost_state.get("budget_abort") is True
    assert out == {}


def test_resume_from_runs_only_missing_after_budget_abort():
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "u654a_predictions_foundry_test.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "BillId", "VendorId", "mode", "suggester", "predicted_scc_id",
                    "label_scc_id", "predicted_project_id", "label_project_id",
                    "correct_scc", "correct_project", "stratum", "winning_rung",
                    "winning_rung_provider", "needs_human", "raw_predicted_scc_id",
                    "raw_predicted_project_id", "raw_confidence", "raw_correct_scc",
                    "raw_correct_project", "error_kind", "http_status",
                    "candidate_scc_count", "candidate_scc_narrowed",
                ]
            )
            w.writerow(
                [
                    1, 1, "A", "text", 1, 1, 10, 10, 1, 1, "cold_start",
                    "DeepSeek-V4-Flash", "foundry", 0, 1, 10, 0.9, 1, 1,
                    "none", "", 1, 0,
                ]
            )
        prior = u654a.load_prior_text_results(csv_path, ladder_name="foundry")
        assert (1, "A") in prior
        assert (2, "A") not in prior
        work = [_work(2, "A")]
        call_ids: list[int] = []

        async def fake_cascade(_payload, transport_for=None, ladder=None):
            call_ids.append(2)
            return _ok_cascade()

        cost_state = {
            "usd": 0.0,
            "stopped": False,
            "scc_number_to_id": {"03.01": 1},
            "proj_abbrev_to_id": {},
        }
        results = dict(prior)
        with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
            with patch.object(u654a, "estimate_ladder_first_rung_cost_usd", return_value=0.01):
                asyncio.run(
                    u654a.run_text_cascade_batch(
                        work,
                        transport_for=lambda _p: None,
                        concurrency=1,
                        max_cost_usd=100.0,
                        cost_state=cost_state,
                        out=results,
                    )
                )
        assert call_ids == [2]
        assert (1, "A") in results
        assert (2, "A") in results


def _minimal_u654a_run_patches(tmp_path: Path, fake_bills: list):
    empty_ref = u654a.ReferenceData(
        scc_by_id={1: u654a.SccRef(1, "03.01", "n", datetime(2026, 1, 1))},
        scc_number_to_id={"03.01": 1},
        proj_by_id={10: u654a.ProjectRef(10, "p", "P")},
        proj_abbrev_to_id={"p": 10},
        vendor_names={1: "V"},
        ambiguous_scc_numbers=frozenset(),
        ambiguous_project_abbrevs=frozenset(),
        duplicate_scc_number_count=0,
        duplicate_project_abbrev_count=0,
    )
    sample = [(b, "cold_start") for b in fake_bills]

    def run_build(build, cascades):
        build()
        cascades()
        return "complete"

    return (
        patch.object(u654a, "load_bills_from_db", return_value=fake_bills),
        patch.object(
            u654a,
            "load_blob_urls_from_db",
            return_value={b.bill_id: "https://x" for b in fake_bills},
        ),
        patch.object(u654a, "load_reference_data", return_value=empty_ref),
        patch.object(u654a, "stratified_sample", return_value=sample),
        patch.object(u654a, "read_text_blob_with_retry", return_value='{"content":"x"}'),
        patch.object(u654a, "execute_run", side_effect=run_build),
        patch.object(u654a, "assert_cli_system_admin"),
    )


def test_main_budget_abort_then_resume_from(tmp_path: Path):
    fake_bills = [
        u654a.TextBillRow(
            bill_id=i,
            vendor_id=1,
            created=datetime(2026, 5, 1),
            label_scc_id=1,
            label_project_id=10,
            total_abs_amount=Decimal("100"),
            mixed_code=False,
            extracted_text_blob_url="https://x",
        )
        for i in (1, 2, 3)
    ]
    call_n = 0
    phase = {"resume": False}
    calls_by_phase: dict[str, int] = {"initial": 0, "resume": 0}

    async def fake_cascade(_payload, transport_for=None, ladder=None):
        nonlocal call_n
        call_n += 1
        key = "resume" if phase["resume"] else "initial"
        calls_by_phase[key] += 1
        if call_n == 3 and not phase["resume"]:
            return _skip_cascade("daily_cap")
        return _ok_cascade()

    patches = _minimal_u654a_run_patches(tmp_path, fake_bills)
    for p in patches:
        p.start()
    try:
        with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
            with patch.object(u654a, "estimate_ladder_first_rung_cost_usd", return_value=0.01):
                code = u654a.main(
                    [
                        "--out-dir",
                        str(tmp_path),
                        "--run",
                        "--sample-size",
                        "3",
                        "--ladder",
                        "foundry",
                        "--concurrency",
                        "1",
                    ]
                )
    finally:
        for p in patches:
            p.stop()

    assert code == 1
    assert calls_by_phase["initial"] == 3
    csv_files = list(tmp_path.glob("u654a_predictions_foundry_*.csv"))
    assert len(csv_files) == 1
    csv_path = csv_files[0]
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    text_rows = [r for r in rows if r.get("suggester") == "text"]
    finished_pairs = {(int(r["BillId"]), r["mode"]) for r in text_rows}
    assert not any(bid == 3 for bid, _ in finished_pairs)
    assert len(finished_pairs) == 2

    phase["resume"] = True
    call_n = 0
    patches2 = _minimal_u654a_run_patches(tmp_path, fake_bills)
    for p in patches2:
        p.start()
    try:
        with patch.object(u654a, "run_bill_coding_suggestion", side_effect=fake_cascade):
            with patch.object(u654a, "estimate_ladder_first_rung_cost_usd", return_value=0.01):
                code2 = u654a.main(
                    [
                        "--out-dir",
                        str(tmp_path),
                        "--run",
                        "--sample-size",
                        "3",
                        "--ladder",
                        "foundry",
                        "--concurrency",
                        "1",
                        "--resume-from",
                        str(csv_path),
                    ]
                )
    finally:
        for p in patches2:
            p.stop()

    assert code2 == 0
    missing_pairs = 6 - len(finished_pairs)
    assert calls_by_phase["resume"] == missing_pairs
    csv_files2 = sorted(tmp_path.glob("u654a_predictions_foundry_*.csv"))
    rows2 = list(csv.DictReader(csv_files2[-1].open(encoding="utf-8")))
    text_rows2 = [r for r in rows2 if r.get("suggester") == "text"]
    pairs2 = {(int(r["BillId"]), r["mode"]) for r in text_rows2}
    assert len(pairs2) == 6
    summary_files = sorted(tmp_path.glob("u654a_summary_foundry_*.json"))
    summary = json.loads(summary_files[-1].read_text(encoding="utf-8"))
    assert summary["run_status"] == "complete"
