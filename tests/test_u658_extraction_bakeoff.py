"""Pure tests for U-658 extraction bake-off harness (no live DB/DI)."""
from __future__ import annotations

import json
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from intelligence.cascade.core import Attempt, CascadeResult, Rung
from intelligence.transport.base import Usage
from scripts import u658_extraction_bakeoff as u658


@pytest.fixture
def llm_arms_on_every_doc_type(monkeypatch):
    """The arm-B/C machinery (cost cap, refusal accounting, CSV reasons, masking
    flags) is exercised on non-W-9 docs here; production runs it on W-9 only
    (Chris 2026-10-16), pinned by test_llm_arms_never_send_a_non_w9_doc."""
    monkeypatch.setattr(u658, "LLM_DOC_TYPES", frozenset(u658._DOC_TYPES))


def _llm_invoke_kwargs(*, max_cost: float = 10.0, usd: float = 0.0, run_cascade_fn=None):
    cost_state = {"usd": usd, "stopped": False, "llm_failure_counts": {}}
    return (
        {
            "cost_state": cost_state,
            "max_cost_usd": max_cost,
            "truncation_count": [0],
            "run_cascade_fn": run_cascade_fn or u658.run_cascade,
            "transport_for": None,
        },
        cost_state,
    )


def _cascade_result(
    *,
    rung: Rung,
    result: dict | None,
    validated: bool = True,
    error: str = "",
    usage: Usage | None = None,
) -> CascadeResult:
    attempt = Attempt(
        rung=rung,
        accepted=validated and not error,
        validated=validated,
        result=result,
        usage=usage or Usage(input_tokens=1000, output_tokens=200),
        error=error or None,
        confidence=0.9 if result else None,
    )
    return CascadeResult(
        accepted=attempt.accepted,
        needs_human=not attempt.accepted,
        result=result,
        confidence=attempt.confidence,
        winning_rung=rung if attempt.accepted else None,
        attempts=[attempt],
    )


def test_normalize_string_strips_punctuation_and_case():
    assert u658.normalize_string("  Acme, LLC. ") == "acmellc"


def test_normalize_date_to_iso():
    assert u658.normalize_date("3/5/2024") == "2024-03-05"
    assert u658.normalize_date("2024-03-05") == "2024-03-05"


def test_normalize_money_decimal():
    assert u658.normalize_money("$1,234.50") == "1234.50"


def test_normalize_alnum_invoice():
    assert u658.normalize_alnum("INV-202980/1") == "inv2029801"


def test_score_scalar_exact_and_normalized():
    fs = u658.score_scalar_field("license_number", "ABC-123", "abc123")
    assert fs.exact is False
    assert fs.normalized is True
    assert fs.wrong is False


def test_score_scalar_null_vs_wrong():
    null_fs = u658.score_scalar_field("license_number", None, "X")
    assert null_fs.predicted_null is True
    assert null_fs.wrong is False
    wrong_fs = u658.score_scalar_field("license_number", "A", "B")
    assert wrong_fs.wrong is True


def test_coi_policy_matching_precision_recall():
    truth = [
        {
            "coverage_type": "GL",
            "carrier": "Acme Ins",
            "policy_number": "GL-1",
            "each_occurrence": Decimal("1000000"),
            "aggregate": Decimal("2000000"),
            "effective_date": "2024-01-01",
            "expiry_date": "2025-01-01",
        }
    ]
    pred = [
        {
            "coverage_type": "GL",
            "carrier": "Acme Ins.",
            "policy_number": "GL1",
            "each_occurrence": "$1,000,000.00",
            "aggregate": "$2,000,000.00",
            "effective_date": "01/01/2024",
            "expiry_date": "01/01/2025",
        },
        {
            "coverage_type": "WC",
            "carrier": "Extra",
            "policy_number": "WC-9",
        },
    ]
    scores, prec, rec = u658.match_coi_policies(pred, truth)
    assert prec == 0.5
    assert rec == 1.0
    gl_carrier = next(s for s in scores if s.field == "policy_GL_carrier")
    assert gl_carrier.normalized is True


def test_coi_missing_and_extra_policies():
    truth = [{"coverage_type": "GL", "carrier": "A", "policy_number": "1"}]
    pred: list[dict] = []
    scores, _, rec = u658.match_coi_policies(pred, truth)
    assert rec == 0.0
    assert any(s.predicted_null for s in scores)


def test_match_coi_two_gl_policies_by_policy_number():
    truth = [
        {"coverage_type": "GL", "carrier": "A1", "policy_number": "PN-100"},
        {"coverage_type": "GL", "carrier": "A2", "policy_number": "PN-200"},
    ]
    pred = [
        {"coverage_type": "GL", "carrier": "A2", "policy_number": "PN200"},
        {"coverage_type": "GL", "carrier": "A1", "policy_number": "PN100"},
    ]
    scores, prec, rec = u658.match_coi_policies(pred, truth)
    assert prec == 1.0
    assert rec == 1.0
    a1 = next(s for s in scores if s.field == "policy_GL_0_carrier")
    a2 = next(s for s in scores if s.field == "policy_GL_1_carrier")
    assert a1.normalized is True
    assert a2.normalized is True


@pytest.mark.parametrize(
    "unsafe",
    [
        "1 2 3 4 5 6 7 8 9",
        "123456789",
        "12 3456789",
        "123 45 6789",
        "123\n45\n6789",
        "123-45-6789",
    ],
)
def test_assert_llm_text_safe_refuses_nine_digit_runs_w9(unsafe):
    with pytest.raises(ValueError, match="9\\+ digit"):
        u658.assert_llm_text_safe(unsafe, doc_type="w9")


@pytest.mark.parametrize(
    "innocent,doc_type",
    [
        ("Agent phone 615-555-1234 M-F 8-5", "coi"),
        ("Property zip 37201-1234 Nashville TN", "bill"),
        ("2 PVC elbow 4.50 9.00 qty 3", "bill"),
        ("Policy GL 123 456 789 012 carrier Acme", "coi"),
    ],
)
def test_assert_llm_text_safe_accepts_innocent_non_w9_after_mask(innocent, doc_type):
    masked = u658.prepare_llm_read_text(innocent, doc_type)
    u658.assert_llm_text_safe(masked, doc_type=doc_type)


@pytest.mark.parametrize(
    "raw",
    [
        "Fed ID 12/3456789",
        "SSN 123|45|6789",
        "123_45_6789",
        "12·3456789",
        "12⁄3456789",
        "12:3456789",
        "(12)3456789",
        "12,345,6789",
        "123456789",
    ],
)
def test_prepare_llm_read_text_masks_non_w9_tin_shapes(raw):
    masked = u658.prepare_llm_read_text(raw, "coi")
    u658.assert_llm_text_safe(masked, doc_type="coi")
    collapsed = re.sub(r"\D", "", raw)
    if len(re.sub(r"\D", "", raw)) >= 9:
        tin_digits = re.findall(r"\d", raw)
        if len(tin_digits) >= 9:
            assert collapsed[:9] not in re.sub(r"[^\d]", "", masked), (
                f"full TIN digit run sent intact in {masked!r}"
            )


def test_assert_llm_text_safe_accepts_aggressive_w9_mask():
    raw = "Employer identification 12 3456789 on Form W-9"
    masked, _ = u658.mask_tins(raw, aggressive=True)
    u658.assert_llm_text_safe(masked, doc_type="w9")


def test_assert_llm_text_safe_accepts_non_w9_dashed_mask():
    raw = "SSN 123-45-6789 on invoice"
    masked = u658.prepare_llm_read_text(raw, "bill")
    u658.assert_llm_text_safe(masked, doc_type="bill")
    u658.assert_llm_text_safe("no digits like tin here 12-34", doc_type="bill")


def test_cost_cap_allows_over_budget():
    assert u658.cost_cap_allows(9.0, 0.5, 10.0) is True
    assert u658.cost_cap_allows(9.5, 0.51, 10.0) is False


def test_record_spend_accrues_actual_when_over_cap():
    cost_state = {"usd": 9.5, "stopped": False}
    u658.record_spend(cost_state, 0.75, 10.0)
    assert cost_state["usd"] == pytest.approx(10.25)
    assert cost_state["stopped"] is True


def test_main_rejects_max_cost_above_ceiling():
    with pytest.raises(SystemExit):
        u658.main(["--max-cost-usd", "25"])


def test_bill_truth_filter_sql_contains_exclusions():
    sql = u658._BILL_TRUTH_FILTER
    assert "QBO-" in sql
    assert "SourceEmailMessageId" in sql
    assert "ContractLabor" in sql
    assert "ContractLaborLineItem" in sql


def test_w9_classification_unscored_without_labels():
    truth = u658.TruthRecord(fields={"entity_name": "Acme"})
    truth._tin_digits = "123456789"
    scored = u658.score_prediction(
        "w9",
        {"entity_name": "Acme", "taxpayer_id_number": "xxxxx6789", "classification": "LLC"},
        truth,
        label_classification=None,
    )
    assert scored.classification_scored is False
    assert not any(fs.field == "classification" for fs in scored.field_scores)


def test_w9_classification_scored_with_label():
    truth = u658.TruthRecord(fields={"entity_name": "Acme"})
    truth._tin_digits = "123456789"
    scored = u658.score_prediction(
        "w9",
        {"entity_name": "Acme", "taxpayer_id_number": "xxxxx6789", "classification": "LLC"},
        truth,
        label_classification="LLC",
    )
    cls = next(fs for fs in scored.field_scores if fs.field == "classification")
    assert cls.normalized is True
    assert scored.classification_scored is True


def _arm_rates(core, rate, n=25):
    return {f: {"normalized_rate": rate, "n": float(n)} for f in core}


def test_verdict_switch_candidate_when_b_beats_a():
    core = ["entity_name"]
    a_rates = _arm_rates(core, 0.8)
    b_rates = _arm_rates(core, 0.9)
    c_rates = _arm_rates(core, 0.9)
    v = u658.verdict_for_doc_type(
        "w9",
        a_rates,
        b_rates,
        c_rates,
        w9_labels=False,
        incomplete=False,
        arms_run=["A", "B", "C"],
    )
    assert v == "SWITCH-CANDIDATE"


def test_verdict_keep_layout_on_tie():
    core = ["entity_name"]
    rates = _arm_rates(core, 0.85)
    v = u658.verdict_for_doc_type(
        "w9",
        rates,
        rates,
        rates,
        w9_labels=False,
        incomplete=False,
        arms_run=["A", "B", "C"],
    )
    assert v == "KEEP-LAYOUT"


def test_verdict_insufficient_sample_on_small_n():
    core = ["entity_name"]
    a_rates = _arm_rates(core, 0.8, n=5)
    b_rates = _arm_rates(core, 0.9, n=5)
    v = u658.verdict_for_doc_type(
        "w9",
        a_rates,
        b_rates,
        {},
        w9_labels=False,
        incomplete=False,
        arms_run=["A", "B"],
    )
    assert v == "INSUFFICIENT-SAMPLE"


def test_verdict_not_evaluated_when_arm_a_missing():
    core = ["entity_name"]
    b_rates = _arm_rates(core, 0.9)
    v = u658.verdict_for_doc_type(
        "w9",
        {},
        b_rates,
        {},
        w9_labels=False,
        incomplete=False,
        arms_run=["B"],
    )
    assert v == "NOT-EVALUATED"


def test_verdict_not_evaluated_when_challenger_not_in_arms():
    core = ["invoice_number"]
    a_rates = _arm_rates(core, 0.8)
    v = u658.verdict_for_doc_type(
        "bill",
        a_rates,
        {},
        {},
        w9_labels=False,
        incomplete=False,
        arms_run=["A"],
    )
    assert v == "NOT-EVALUATED"


def test_verdict_not_evaluated_empty_challenger_metrics():
    core = ["entity_name"]
    a_rates = _arm_rates(core, 0.8)
    v = u658.verdict_for_doc_type(
        "w9",
        a_rates,
        {},
        {},
        w9_labels=False,
        incomplete=False,
        arms_run=["A", "B"],
    )
    assert v == "NOT-EVALUATED"


def test_verdict_incomplete_on_cap():
    v = u658.verdict_for_doc_type(
        "w9",
        {},
        {},
        {},
        w9_labels=False,
        incomplete=True,
        arms_run=["A", "B", "C"],
    )
    assert v == "INCOMPLETE"


def test_outputs_privacy_rejects_planted_tin():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(json.dumps({"counts": 1, "note": "123-45-6789"}), encoding="utf-8")
        with pytest.raises(ValueError, match="TIN-shaped"):
            u658.assert_outputs_privacy_safe(p)


def test_outputs_privacy_ignores_uuid_nine_digit_substring():
    uuid_with_nine = "123456789-1234-5678-9012-345678901234"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ok.json"
        p.write_text(json.dumps({"attachment_public_id": uuid_with_nine}), encoding="utf-8")
        u658.assert_outputs_privacy_safe(p)


def test_outputs_privacy_rejects_planted_name_in_json():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(
            json.dumps({"entity_name": "Acme Construction LLC"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="forbidden JSON key"):
            u658.assert_outputs_privacy_safe(p)


def _fake_db_ctx():
    cm = MagicMock()
    cm.__enter__.return_value = MagicMock()
    cm.__exit__.return_value = False
    return cm


def test_dry_run_main_makes_no_di_calls(tmp_path):
    with patch.object(u658, "get_connection", return_value=_fake_db_ctx()):
        with patch.object(
            u658,
            "load_samples_and_truth",
            return_value=([], {}, [], {k: 0 for k in u658._DOC_TYPES}),
        ):
            with patch.object(u658, "run_bakeoff") as mock_run:
                rc = u658.main(["--doc-types", "w9", "--out-dir", str(tmp_path)])
                assert rc == 0
                mock_run.assert_not_called()


def test_dry_run_llm_estimate_matches_live_estimator():
    populations = {dt: 10 for dt in u658._DOC_TYPES}
    sample_counts = {"w9": 5, "_total": 5}
    dry = u658.dry_run_summary(populations, ["w9"], ["B"], sample_counts)
    expected_llm = 5 * u658.estimate_llm_call_cost_usd(
        u658.ARM_TO_LLM_MODEL["B"],
        u658._LLM_ESTIMATE_INPUT_CHARS,
    )
    assert dry["estimated_llm_usd"] == pytest.approx(round(expected_llm, 4))


def test_load_samples_returns_population_counts_without_second_query():
    populations = {"w9": 42, "coi": 0, "bl": 0, "cl": 0, "bill": 0}
    fetch_calls = {"n": 0}

    def counting_fetch(conn):
        fetch_calls["n"] += 1
        return populations

    with patch.object(u658, "get_connection", return_value=_fake_db_ctx()):
        with patch.object(u658, "fetch_population_counts", side_effect=counting_fetch):
            with patch.object(u658, "dedupe_w9_attachment_rows", side_effect=lambda r: r):
                _, _, _, pops = u658.load_samples_and_truth(
                    ["w9"],
                    sample_per_type={"w9": 0},
                    seed=1,
                )
    assert pops["w9"] == 42
    assert fetch_calls["n"] == 1


def test_dedupe_w9_samples_picks_min_vendor_id():
    rows = [
        SimpleNamespace(AttachmentId=1, VendorId=50),
        SimpleNamespace(AttachmentId=1, VendorId=10),
        SimpleNamespace(AttachmentId=2, VendorId=99),
    ]
    deduped = u658.dedupe_w9_attachment_rows(rows)
    by_id = {int(r.AttachmentId): r for r in deduped}
    assert len(deduped) == 2
    assert int(by_id[1].VendorId) == 10


def test_truth_lookup_key_avoids_cross_doc_type_collision():
    pid = "00000000-0000-0000-0000-000000000099"
    truth = {
        u658.truth_lookup_key("w9", pid): u658.TruthRecord(fields={"entity_name": "W9"}),
        u658.truth_lookup_key("bill", pid): u658.TruthRecord(fields={"invoice_number": "INV"}),
    }
    assert truth[u658.truth_lookup_key("w9", pid)].fields["entity_name"] == "W9"
    assert truth[u658.truth_lookup_key("bill", pid)].fields["invoice_number"] == "INV"


def test_run_refuses_arm_c_on_direct_anthropic_route(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL", raising=False)
    with patch.object(u658, "get_connection", return_value=_fake_db_ctx()):
        with patch.object(
            u658,
            "load_samples_and_truth",
            return_value=([], {}, [], {k: 1 for k in u658._DOC_TYPES}),
        ):
            with patch.object(u658, "write_w9_labelling_sheet"):
                with patch.object(u658, "arm_c_route_is_foundry", return_value=False):
                    rc = u658.main(["--run", "--arms", "C", "--out-dir", str(tmp_path)])
                    assert rc == 2


def test_arm_c_route_is_foundry_when_resolve_not_direct():
    foundry_url = "https://example.services.ai.azure.com/anthropic/v1/messages"
    with patch(
        "intelligence.transport.anthropic._resolve_messages_route",
        return_value=(foundry_url, "key", None),
    ):
        assert u658.arm_c_route_is_foundry() is True


def test_run_bakeoff_arm_a_only_mock_di():
    doc = u658.SampleDoc(
        doc_type="bill",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000001",
    )
    truth = u658.TruthRecord(fields={"invoice_number": "INV-9"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_invoice.return_value = {
        "content": "Invoice # INV-9",
        "key_value_pairs": [{"key": "Invoice #", "value": "INV-9"}],
        "pages_count": 1,
    }

    results, meta, incomplete = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["A"],
        max_cost_usd=10.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
    )
    assert not incomplete
    assert len(results) == 1
    inv = next(fs for fs in results[0].field_scores if fs.field == "invoice_number")
    assert inv.normalized is True
    di.extract_text.assert_not_called()


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_run_bakeoff_bc_charges_read_once_per_doc():
    doc = u658.SampleDoc(
        doc_type="bill",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000010",
    )
    truth = u658.TruthRecord(fields={"invoice_number": "INV-9"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": "Invoice INV-9", "pages_count": 2}
    downloads = {"n": 0}

    def download(_d):
        downloads["n"] += 1
        return b"%PDF", "application/pdf"

    rung_b = u658.ARM_B_LADDER[0]
    rung_c = u658.ARM_C_LADDER[0]

    async def fake_cascade(_task, payload, **kwargs):
        model = kwargs["ladder"][0].model
        if model == u658.ARM_TO_LLM_MODEL["B"]:
            rung = rung_b
            result = {"invoice_number": "INV-9", "confidence": 1.0}
        else:
            rung = rung_c
            result = {"invoice_number": "INV-9", "confidence": 1.0}
        return _cascade_result(rung=rung, result=result)

    results, meta, _ = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["B", "C"],
        max_cost_usd=50.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=download,
        run_cascade_fn=fake_cascade,
    )
    assert downloads["n"] == 1
    assert di.extract_text.call_count == 1
    read_cost_once = u658.di_cost_usd("prebuilt-read", 2)
    assert meta["total_cost_usd"] >= read_cost_once
    assert meta["total_cost_usd"] < read_cost_once * 1.5 + 1.0
    assert len(results) == 2


def test_run_bakeoff_reports_cost_overshoot():
    doc = u658.SampleDoc(
        doc_type="bill",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000011",
    )
    truth = u658.TruthRecord(fields={"invoice_number": "INV-9"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_invoice.return_value = {
        "content": "inv",
        "key_value_pairs": [],
        "pages_count": 100,
    }
    _, meta, _ = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["A"],
        max_cost_usd=0.04,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
    )
    assert meta["cost_overshoot_usd"] > 0
    assert meta["total_cost_usd"] > meta["cost_cap_usd"]


def test_validate_w9_accepts_good_rejects_bad():
    good = {
        "entity_name": "Acme",
        "business_name": None,
        "classification": "LLC",
        "is_signed": True,
        "signature_date": "2024-01-15",
        "confidence": 0.9,
    }
    ok, _ = u658.validate_w9_extraction(good)
    assert ok
    bad = dict(good, classification="NOT_A_REAL_CLASS")
    ok2, _ = u658.validate_w9_extraction(bad)
    assert not ok2


def test_validate_coi_rejects_free_text_coverage_type():
    good = {
        "issuing_authority": "Agency",
        "issue_date": "2024-02-01",
        "policies": [
            {
                "coverage_type": "GL",
                "carrier": "Carrier",
                "policy_number": "P1",
            }
        ],
    }
    bad = {
        **good,
        "policies": [{**good["policies"][0], "coverage_type": "Commercial General Liability"}],
    }
    assert not u658.validate_coi_extraction(bad)[0]


def test_coi_csv_field_uses_enum_code_not_model_text(tmp_path):
    truth = u658.TruthRecord(
        fields={"issuing_authority": "Agency", "issue_date": "2024-01-01"},
        coi_policies=[{"coverage_type": "GL", "carrier": "A", "policy_number": "1"}],
    )
    scored = u658.score_prediction(
        "coi",
        {
            "issuing_authority": "Agency",
            "issue_date": "2024-01-01",
            "policies": [{"coverage_type": "GL", "carrier": "A", "policy_number": "1"}],
        },
        truth,
        arm="B",
    )
    scored.attachment_public_id = "00000000-0000-0000-0000-000000000099"
    scored.arm = "B"
    summary = {"run_status": "complete", "verdicts": {}, "arm_metrics": {}, "populations": {}}
    _, csv_path, _ = u658.write_outputs(tmp_path, "coi_enum", [scored], summary)
    body = csv_path.read_text(encoding="utf-8")
    assert "Commercial General Liability" not in body
    assert "policy_GL_carrier" in body


def test_validate_coi_policy_types():
    good = {
        "issuing_authority": "Agency",
        "issue_date": "2024-02-01",
        "policies": [
            {
                "coverage_type": "GL",
                "carrier": "Carrier",
                "policy_number": "P1",
                "each_occurrence": 1_000_000,
                "aggregate": 2_000_000,
                "effective_date": "2024-01-01",
                "expiry_date": "2025-01-01",
            }
        ],
    }
    assert u658.validate_coi_extraction(good)[0]
    assert not u658.validate_coi_extraction({**good, "issue_date": "not-a-date"})[0]


def test_validate_bl_cl_bill():
    bl = {
        "license_number": "BL-1",
        "issuing_authority": "State",
        "issue_date": "2020-01-01",
        "expiry_date": "2025-01-01",
    }
    assert u658.validate_bl_extraction(bl)[0]
    cl = {**bl, "classification": "General"}
    assert u658.validate_cl_extraction(cl)[0]
    assert u658.validate_bill_extraction({"invoice_number": "INV-9"})[0]
    assert not u658.validate_bill_extraction({"invoice_number": 9})[0]


def test_masked_text_assertion_blocks_llm_seam():
    unsafe = "123456789"

    async def boom(*_a, **_k):
        raise AssertionError("must not call cascade")

    kwargs, _ = _llm_invoke_kwargs(run_cascade_fn=boom)
    out = u658.extract_with_llm("w9", unsafe, "DeepSeek-V4-Flash", **kwargs)
    assert out.failure_reason == "payload_refused"


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_run_bakeoff_innocent_coi_text_not_incomplete():
    innocent = "Contact 615-555-1234 zip 37201-1234 line 2 PVC 4.50 9.00"
    doc = u658.SampleDoc(
        doc_type="coi",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000099",
    )
    truth = u658.TruthRecord(fields={"issuing_authority": "Agency", "issue_date": "2024-01-01"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": innocent, "pages_count": 1}
    rung = u658.ARM_B_LADDER[0]
    calls = {"n": 0}

    async def ok_cascade(_task, _payload, **kwargs):
        calls["n"] += 1
        return _cascade_result(
            rung=rung,
            result={
                "issuing_authority": "Agency",
                "issue_date": "2024-01-01",
                "policies": [],
                "confidence": 1.0,
            },
        )

    results, meta, incomplete = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["B"],
        max_cost_usd=10.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
        run_cascade_fn=ok_cascade,
    )
    assert not incomplete
    assert meta["failed_doc_runs"] == 0
    assert calls["n"] == 1
    assert len(results) == 1
    assert results[0].llm_failure_reason == ""
    assert meta["total_cost_usd"] >= u658.di_cost_usd("prebuilt-read", 1)


def test_extract_with_llm_payload_refused_skips_cascade():
    async def boom(*_a, **_k):
        raise AssertionError("must not call cascade")

    kwargs, _ = _llm_invoke_kwargs(run_cascade_fn=boom)
    out = u658.extract_with_llm("coi", "123456789", "DeepSeek-V4-Flash", **kwargs)
    assert out.failure_reason == "payload_refused"


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_run_bakeoff_payload_refused_does_not_mark_incomplete():
    doc = u658.SampleDoc(
        doc_type="bill",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000088",
    )
    truth = u658.TruthRecord(fields={"invoice_number": "INV-1"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": "Invoice INV-1", "pages_count": 1}

    def refuse_payload(*_a, **_k):
        return u658.LlmExtractResult(
            fields={}, llm_cost_usd=0.0, failure_reason="payload_refused"
        )

    with patch.object(u658, "extract_with_llm", side_effect=refuse_payload):
        results, meta, incomplete = u658.run_bakeoff(
            [doc],
            {tkey: truth},
            arms=["B"],
            max_cost_usd=10.0,
            w9_labels={},
            di_service_factory=lambda: di,
            download_attachment=lambda _d: (b"%PDF", "application/pdf"),
        )
    assert not incomplete
    assert meta["failed_doc_runs"] == 0
    assert meta["llm_failure_counts"].get("payload_refused") == 1
    assert len(results) == 1
    assert results[0].llm_failure_reason == "payload_refused"


def test_w9_entity_name_metrics_exclude_truth_names_with_digits():
    pid_plain = "00000000-0000-0000-0000-000000000001"
    pid_digit = "00000000-0000-0000-0000-000000000002"
    results = [
        u658.DocArmResult(
            attachment_public_id=pid_plain,
            doc_type="w9",
            arm="B",
            field_scores=[
                u658.FieldScore("entity_name", True, True, False, False, False),
            ],
        ),
        u658.DocArmResult(
            attachment_public_id=pid_digit,
            doc_type="w9",
            arm="B",
            field_scores=[
                u658.FieldScore("entity_name", False, False, False, False, True),
            ],
        ),
    ]
    truth = {
        u658.truth_lookup_key("w9", pid_plain): u658.TruthRecord(fields={"entity_name": "Acme LLC"}),
        u658.truth_lookup_key("w9", pid_digit): u658.TruthRecord(
            fields={"entity_name": "A1 Plumbing"}
        ),
    }
    skip = lambda res, f: u658.w9_entity_name_skip(res, f, truth)
    rates = u658.aggregate_field_rates(results, ["entity_name"], skip_field=skip)
    assert rates["entity_name"]["n"] == 1.0
    assert rates["entity_name"]["normalized_rate"] == 1.0
    assert u658.count_w9_entity_name_digit_exclusions(results, truth) == 1


def test_run_bakeoff_w9_aggressive_masking_on_llm_payload():
    raw = "Name Acme LLC TIN 12/3456789 and 123 45 6789"
    doc = u658.SampleDoc(
        doc_type="w9",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000020",
    )
    truth = u658.TruthRecord(fields={"entity_name": "Acme LLC"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": raw, "pages_count": 1}
    payloads: list[str] = []
    rung_b = u658.ARM_B_LADDER[0]
    rung_c = u658.ARM_C_LADDER[0]

    async def spy_cascade(_task, payload, **kwargs):
        payloads.append(str(payload.get("text") or ""))
        model = kwargs["ladder"][0].model
        rung = rung_b if model == u658.ARM_TO_LLM_MODEL["B"] else rung_c
        fields = {
            "entity_name": "Acme LLC",
            "business_name": None,
            "classification": None,
            "is_signed": None,
            "signature_date": None,
            "confidence": 1.0,
        }
        return _cascade_result(rung=rung, result=fields)

    u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["B", "C"],
        max_cost_usd=50.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
        run_cascade_fn=spy_cascade,
    )
    assert len(payloads) == 2
    for text in payloads:
        assert not any(ch.isdigit() for ch in text), f"digits in LLM payload: {text!r}"


def test_extract_with_llm_accrues_spend_from_usage():
    rung = u658.ARM_B_LADDER[0]

    async def fake_cascade(_task, _payload, **kwargs):
        return _cascade_result(
            rung=rung,
            result={
                "entity_name": "X",
                "business_name": None,
                "classification": None,
                "is_signed": None,
                "signature_date": None,
                "confidence": 1.0,
            },
        )

    kwargs, cost_state = _llm_invoke_kwargs(max_cost=10.0, usd=0.0, run_cascade_fn=fake_cascade)
    out = u658.extract_with_llm("w9", "safe text", "DeepSeek-V4-Flash", **kwargs)
    assert out.failure_reason == ""
    assert out.llm_cost_usd > 0
    assert cost_state["usd"] == pytest.approx(out.llm_cost_usd)


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_extract_with_llm_accrues_spend_when_call_overshoots_cap():
    rung = u658.ARM_B_LADDER[0]
    heavy_usage = Usage(input_tokens=500_000, output_tokens=50_000)

    async def expensive_cascade(_task, _payload, **kwargs):
        return _cascade_result(
            rung=rung,
            result={"invoice_number": "X", "confidence": 1.0},
            usage=heavy_usage,
        )

    kwargs, cost_state = _llm_invoke_kwargs(max_cost=0.001, usd=0.0, run_cascade_fn=expensive_cascade)
    out = u658.extract_with_llm("bill", "safe text", "DeepSeek-V4-Flash", **kwargs)
    assert out.llm_cost_usd > 0
    assert cost_state["usd"] == pytest.approx(out.llm_cost_usd)
    assert cost_state["stopped"] is True
    assert out.failure_reason == ""


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_extract_cost_cap_stops_before_cascade():
    async def should_not_run(*_a, **_k):
        raise AssertionError("cap should block call")

    kwargs, _ = _llm_invoke_kwargs(max_cost=0.0, usd=0.0, run_cascade_fn=should_not_run)
    out = u658.extract_with_llm("bill", "text", "DeepSeek-V4-Flash", **kwargs)
    assert out.failure_reason == "cost_cap"


def test_classify_llm_failure_reasons():
    rung = u658.ARM_B_LADDER[0]
    throttled = Attempt(rung=rung, accepted=False, error="429: rate limit")
    assert u658.classify_llm_attempt(throttled)[1] == "throttled"
    invalid = Attempt(rung=rung, accepted=False, error="no parseable result")
    assert u658.classify_llm_attempt(invalid)[1] == "invalid_json"
    bad_val = Attempt(
        rung=rung,
        accepted=False,
        validated=False,
        result={"invoice_number": 123},
    )
    assert u658.classify_llm_attempt(bad_val)[1] == "validation_failed"


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_run_bakeoff_arm_b_records_validation_failure_in_csv(tmp_path):
    doc = u658.SampleDoc(
        doc_type="bill",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-000000000002",
    )
    truth = u658.TruthRecord(fields={"invoice_number": "INV-9"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": "Invoice 123", "pages_count": 1}
    rung = u658.ARM_B_LADDER[0]

    async def bad_json_cascade(_task, _payload, **kwargs):
        return _cascade_result(rung=rung, result=None, error="no parseable result")

    results, _, _ = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["B"],
        max_cost_usd=10.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
        run_cascade_fn=bad_json_cascade,
    )
    assert len(results) == 1
    assert results[0].llm_failure_reason == "invalid_json"
    summary = {"run_status": "INCOMPLETE", "verdicts": {}, "arm_metrics": {}, "populations": {}}
    _, csv_path, _ = u658.write_outputs(tmp_path, "test", results, summary)
    rows = csv_path.read_text(encoding="utf-8")
    assert "invalid_json" in rows


def test_keyboard_interrupt_mid_run_writes_partial(tmp_path):
    docs = [
        u658.SampleDoc(
            doc_type="bill",
            attachment_id=i,
            attachment_public_id=f"00000000-0000-0000-0000-00000000000{i}",
        )
        for i in range(1, 4)
    ]
    truth = {
        u658.truth_lookup_key(d.doc_type, d.attachment_public_id): u658.TruthRecord(
            fields={"invoice_number": "X"}
        )
        for d in docs
    }
    di = MagicMock()
    di.extract_invoice.return_value = {
        "content": "inv",
        "key_value_pairs": [],
        "pages_count": 1,
    }
    calls = {"n": 0}

    def download(_d):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt
        return b"%PDF", "application/pdf"

    results, meta, incomplete = u658.run_bakeoff(
        docs,
        truth,
        arms=["A"],
        max_cost_usd=10.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=download,
    )
    assert incomplete
    assert meta.get("interrupted")
    assert len(results) >= 1
    summary = {
        "run_status": "partial",
        "verdicts": {"bill": "INCOMPLETE"},
        "arm_metrics": {},
        "populations": {},
    }
    _, csv_path, json_path = u658.write_outputs(tmp_path, "partial", results, summary)
    assert "partial" in json_path.read_text(encoding="utf-8")
    assert csv_path.exists()


def test_truncate_llm_text_counts():
    long = "a" * (u658.LLM_TEXT_CHAR_BUDGET + 100)
    truncated, did = u658.truncate_llm_text(long)
    assert did
    assert len(truncated) <= u658.LLM_TEXT_CHAR_BUDGET + 20


# --- U-658 round 3: one digit-run scanner for non-W-9 TIN masking ---------------

_R3_TIN_SHAPES = [
    "12 / 3456789",
    "12//3456789",
    "(12) 3456789",
    "123  45  6789",
    "123 / 45 / 6789",
    "¹²³⁴⁵⁶⁷⁸⁹",
    "TIN 12345 6789",
    "1 2 3 4 5 6 7 8 9",
    "Fed ID 12/3456789",
    "SSN 123|45|6789",
    "123_45_6789",
    "12·3456789",
    "12:3456789",
    "(12)3456789",
    "12,345,6789",
    "123456789",
    "１２３４５６７８９",
    "12 - 3456789",
    "12.345.6789",
]

_R3_INNOCENT = [
    "Phone (615) 555-1234",
    "615.555.1235",
    "615 555 1234",
    "1-800-555-1212",
    "Nashville TN 37201-1234",
    "Each Occurrence 1,000,000 Aggregate 2,000,000",
    "10,000,000",
    "12,345,678.90",
    "1,234,567",
    "10/15/2026",
    "2026-10-15",
    "2 PVC elbow 4.50 9.00",
    "Qty 125 1,250.00 125.00",
    "10/15/2026 12:30 PM",
]


@pytest.mark.parametrize("text", _R3_TIN_SHAPES)
def test_r3_non_w9_tin_shapes_are_masked_and_pass_the_guard(text):
    for doc_type in ("coi", "bill", "bl", "cl"):
        out = u658.prepare_llm_read_text(text, doc_type)
        digits = [ch for ch in out if ch.isdigit()]
        assert len(digits) <= 4, (doc_type, text, out)  # at most the last 4 survive
        u658.assert_llm_text_safe(out, doc_type=doc_type)  # and the guard agrees


@pytest.mark.parametrize("text", _R3_INNOCENT)
def test_r3_innocent_non_w9_text_is_untouched(text):
    out, fired = u658.prepare_llm_read_text_with_flag(text, "coi")
    assert out == text
    assert fired is False
    u658.assert_llm_text_safe(out, doc_type="coi")


def test_r3_guard_is_independent_of_the_mask():
    # A 20-space gap is wider than the mask joins (12) but within the guard (40):
    # the mask leaves it, the guard refuses it — never sent.
    text = "ref 12" + " " * 20 + "3456789"
    out, fired = u658.prepare_llm_read_text_with_flag(text, "coi")
    assert "3456789" in out and fired is False
    with pytest.raises(ValueError):
        u658.assert_llm_text_safe(out, doc_type="coi")


def _r3_result(pid, arm, *, normalized, excluded=False, doc_type="bill", field="invoice_number"):
    return u658.DocArmResult(
        attachment_public_id=pid,
        doc_type=doc_type,
        arm=arm,
        field_scores=[
            u658.FieldScore(
                field=field,
                exact=normalized,
                normalized=normalized,
                predicted_null=False,
                truth_null=False,
                wrong=not normalized,
            )
        ],
        masking_excluded=excluded,
    )


def test_r3_build_arm_metrics_excludes_masked_docs_from_every_arm():
    results = [
        _r3_result("p1", "A", normalized=True),
        _r3_result("p1", "B", normalized=False, excluded=True),  # masked 9-digit ID
        _r3_result("p2", "A", normalized=True),
        _r3_result("p2", "B", normalized=True),
    ]
    metrics, counts = u658.build_arm_metrics(
        results, ["bill"], ["A", "B"], w9_labels=False, truth_by_pid={}
    )
    assert counts == {"bill": 1}
    # p1 is gone from A as well as B: both arms are scored on p2 only.
    assert metrics["bill"]["A"]["invoice_number"]["n"] == 1.0
    assert metrics["bill"]["B"]["invoice_number"]["n"] == 1.0
    assert metrics["bill"]["B"]["invoice_number"]["normalized_rate"] == 1.0


def test_r3_build_arm_metrics_applies_the_w9_digit_name_skip():
    """Pins the W-9 skip wiring that main relies on (round-2 P3)."""
    pid = "00000000-0000-0000-0000-0000000000a1"
    truth = u658.TruthRecord(fields={"entity_name": "84 Lumber Co"})
    results = [
        _r3_result(pid, "A", normalized=True, doc_type="w9", field="entity_name"),
        _r3_result(pid, "B", normalized=False, doc_type="w9", field="entity_name"),
    ]
    metrics, _ = u658.build_arm_metrics(
        results,
        ["w9"],
        ["A", "B"],
        w9_labels=False,
        truth_by_pid={u658.truth_lookup_key("w9", pid): truth},
    )
    assert metrics["w9"]["A"]["entity_name"]["n"] == 0.0
    assert metrics["w9"]["B"]["entity_name"]["n"] == 0.0


@pytest.mark.usefixtures("llm_arms_on_every_doc_type")
def test_r3_run_bakeoff_flags_extra_masked_doc():
    doc = u658.SampleDoc(
        doc_type="bill",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-0000000000b2",
    )
    truth = u658.TruthRecord(fields={"invoice_number": "INV-1"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": "Invoice INV-1 Fed ID 12 / 3456789", "pages_count": 1}
    rung = u658.ARM_B_LADDER[0]
    sent = []

    async def spy(_task, payload, **kwargs):
        sent.append(payload["text"])
        return _cascade_result(rung=rung, result={"invoice_number": "INV-1", "confidence": 1.0})

    results, _meta, incomplete = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["B"],
        max_cost_usd=10.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
        run_cascade_fn=spy,
    )
    assert not incomplete
    assert "3456789" not in sent[0] and "12 / " not in sent[0]
    assert results[0].masking_excluded is True


# --- U-658 round 4: TIN shapes + labels, not "any 9 digits" ---------------------

_R4_TIN_SHAPES = [
    "SSN 123.45.6789",
    "SSN 123.45-6789",
    "SSN 123.45 6789",
    "12       3456789",
    "123  -/-  45 6789",
    "TIN 12345 6789",
    "EIN: 12-34567 89",
    "Tax ID 1234 56789",
    "Federal Tax I.D. 1 2 3 4 5 6 7 8 9",
]

# Every one of these fired the round-3 scanner (reviewer probe, 2026-10-15).
_R4_INNOCENT = [
    "POLICY NUMBER\n0123456\n01/01/2026\n01/01/2027",
    "12345\n06/01/2026\n06/01/2027",
    "WC 9876543 03/15/2026",
    "1,000,000\n100,000\n5,000\n1,000,000\n2,000,000\n2,000,000",
    "$ 2,000,000\n$ 50,000",
    "123456 2 45.00 90.00",
    "123456\n2\n45.00\n90.00",
    "615-555-1234\n12",
    "Nashville, TN 37201\n1234 Main St",
    "37201 1234",
    "10/15/2026 1",
    "LIC# 1234\n56789",
    "PROTEIN 4 PACK 12.99",
    "CERTIFICATE NUMBER: 2026-0001",
]


@pytest.mark.parametrize("text", _R4_TIN_SHAPES)
def test_r4_tin_shapes_and_labelled_tins_are_masked(text):
    for doc_type in ("coi", "bill", "bl", "cl"):
        out = u658.prepare_llm_read_text(text, doc_type)
        assert sum(ch.isdigit() for ch in out) <= 4, (doc_type, text, out)
        u658.assert_llm_text_safe(out, doc_type=doc_type)


@pytest.mark.parametrize("text", _R4_INNOCENT)
def test_r4_non_tin_groupings_are_not_masked(text):
    out, fired = u658.prepare_llm_read_text_with_flag(text, "coi")
    assert (out, fired) == (text, False)
    u658.assert_llm_text_safe(out, doc_type="coi")


def test_r4_a_mask_character_breaks_a_join():
    """Pins the '*' rule: an already-masked span never chains to a neighbour."""
    assert u658._gap_joins(" ", 3) is True
    assert u658._gap_joins("*", 3) is False
    assert u658._gap_joins(" a ", 3) is False


def test_r4_summary_reports_exclusion_next_to_sample(tmp_path):
    results = [
        u658.DocArmResult(
            attachment_public_id="p1", doc_type="bill", arm="B", field_scores=[], masking_excluded=True
        )
    ]
    _, counts = u658.build_arm_metrics(results, ["bill"], ["B"], w9_labels=False, truth_by_pid={})
    assert counts == {"bill": 1}


def test_r4_documented_residual_unlabelled_off_shape_grouping():
    """Accepted trade-off (Chris, round 4): an UNLABELLED 9-digit run in a non-TIN
    grouping is not masked, so policy numbers beside dates stay readable. The same
    digits after a TIN label are masked."""
    assert u658.prepare_llm_read_text("ref 12-34567 89", "coi") == "ref 12-34567 89"
    assert sum(ch.isdigit() for ch in u658.prepare_llm_read_text("EIN 12-34567 89", "coi")) == 0


# --- U-658 round 5: invisible characters, '*' separators, tighter labels --------

_R5_INVISIBLE = {
    "zero-width space": "​",
    "zero-width joiner": "‍",
    "soft hyphen": "­",
    "BOM": "﻿",
    "word joiner": "⁠",
    "combining acute": "́",
}


@pytest.mark.parametrize("name", sorted(_R5_INVISIBLE))
def test_r5_invisible_character_inside_a_tin_does_not_hide_it(name):
    ch = _R5_INVISIBLE[name]
    for text in (f"Acct 12{ch}3-45-6789", f"Acct 1234{ch}56789", f"Acct 123-45-67{ch}89"):
        out = u658.prepare_llm_read_text(text, "bill")
        assert sum(c.isdigit() for c in out) <= 4, (name, text, out)
        assert ch not in out
        u658.assert_llm_text_safe(out, doc_type="bill")


@pytest.mark.parametrize(
    "text",
    [
        "１２３-４５-６７８９",  # full-width: the guard's own digit normalisation
        "123-45-67﻿89",  # BOM: the guard's own invisible strip
        "Acct 123*45*6789",  # lone '*' separators
        "Acct 12*3456789",
    ],
)
def test_r5_guard_catches_raw_tins_on_its_own(text):
    assert u658._non_w9_payload_has_unmasked_tin(text) is True


def test_r5_guard_does_not_join_across_masked_runs():
    assert u658._non_w9_payload_has_unmasked_tin("Acct **-***6789 and ***-**-6789") is False
    assert u658._non_w9_payload_has_unmasked_tin("Qty 2 * 45.00 = 90.00") is False


@pytest.mark.parametrize(
    "text",
    [
        "E.I.N. 12 345 67 89",
        "SS# 12345 6789",
        "Soc Sec No 1234 56789",
        "Social Security Number 12345 6789",
        "Employer Identification Number 1234-567-89",
        "Tax Identification Number 12345 6789",
        "TIN: 12345 6789",
        "TIN # 1234 56789",
        "Taxpayer ID 1234 567 89",
        "TIN 1234 56789",
    ],
)
def test_r5_label_variants_mask_every_digit(text):
    out = u658.prepare_llm_read_text(text, "bill")
    assert sum(c.isdigit() for c in out) == 0, (text, out)


@pytest.mark.parametrize(
    "text",
    [
        "TIN FLASHING 10 LF 2.50 25.00\n2 BX 778100 4.00 8.00",
        "Galv tin snips 1 EA 14.99",
        "Tin Roof Co Invoice 4471",
        "Taxpayer copy - keep for your records 2026",
        "Fed Ex ID 1234567",
        "NAIC ID 12345",
    ],
)
def test_r5_construction_words_do_not_fire_the_label_rule(text):
    out, fired = u658.prepare_llm_read_text_with_flag(text, "bill")
    assert (out, fired) == (text, False)


# --- U-658 round 6: label separators, SS#/EIN over-firing, NFC ------------------


@pytest.mark.parametrize(
    "text",
    [
        "TIN – 1234 56789",
        "TIN — 1234 56789",
        "TIN | 1234 56789",
        "TIN = 1234 56789",
        "TIN (1234 56789)",
        "TIN / 1234 56789",
        "TIN, 1234 56789",
        "TIN\n\n\n\n1234 56789",
        "Taxpayer – 1234 56789",
        "S.S. No. 12 345 67 89",
        "Tax Payer ID 1234 56789",
        "Tax Payer Identification Number 12345 6789",
    ],
)
def test_r6_labelled_tin_after_any_separator_is_masked(text):
    out = u658.prepare_llm_read_text(text, "bill")
    assert sum(c.isdigit() for c in out) == 0, (text, out)


@pytest.mark.parametrize(
    "text",
    [
        "SS #8 x 1-1/4 SCREW 100 PK 12.99",
        "Grade E in 2 colors 3 4",
        "TIN FLASHING 10 LF 2.50 25.00",
        "Employers' Liability E.L. EACH ACCIDENT 1,000,000",
    ],
)
def test_r6_trade_text_does_not_fire_the_label_rule(text):
    out, fired = u658.prepare_llm_read_text_with_flag(text, "bill")
    assert (out, fired) == (text, False)


def test_r6_decomposed_accents_survive_the_invisible_strip():
    import unicodedata

    nfd = unicodedata.normalize("NFD", "José Peña Construcción LLC")
    assert u658.strip_invisible(nfd) == "José Peña Construcción LLC"
    # ...while a combining mark inside a digit run still goes
    assert u658.strip_invisible("12́3") == "123"


# --- Chris 2026-10-16 (decision b): LLM arms on W-9 only ----------------------


def test_llm_arms_never_send_a_non_w9_doc():
    async def boom(*_a, **_k):
        raise AssertionError("a non-W-9 doc must never reach the cascade")

    for doc_type in ("coi", "bl", "cl", "bill"):
        kwargs, _ = _llm_invoke_kwargs(run_cascade_fn=boom)
        out = u658.extract_with_llm(doc_type, "plain text", "DeepSeek-V4-Flash", **kwargs)
        assert out.failure_reason == "payload_refused"


def test_run_bakeoff_runs_only_arm_a_on_non_w9_docs():
    doc = u658.SampleDoc(
        doc_type="coi",
        attachment_id=1,
        attachment_public_id="00000000-0000-0000-0000-0000000000c3",
    )
    truth = u658.TruthRecord(fields={"issuing_authority": "Agency", "issue_date": "2024-01-01"})
    tkey = u658.truth_lookup_key(doc.doc_type, doc.attachment_public_id)
    di = MagicMock()
    di.extract_text.return_value = {"content": "text", "pages_count": 1}

    async def boom(*_a, **_k):
        raise AssertionError("no LLM call for a COI")

    results, _meta, _incomplete = u658.run_bakeoff(
        [doc],
        {tkey: truth},
        arms=["B", "C"],
        max_cost_usd=10.0,
        w9_labels={},
        di_service_factory=lambda: di,
        download_attachment=lambda _d: (b"%PDF", "application/pdf"),
        run_cascade_fn=boom,
    )
    assert results == []
    di.extract_text.assert_not_called()  # no Read DI spend for an arm that cannot run


def test_non_w9_llm_arm_verdict_reads_not_evaluated():
    a_rates = {f: {"n": 30.0, "normalized_rate": 0.9} for f in u658.comparison_core_fields("coi", w9_labels=False)}
    empty = {f: {"n": 0.0, "normalized_rate": 0.0} for f in u658.comparison_core_fields("coi", w9_labels=False)}
    verdict = u658.verdict_for_doc_type(
        "coi", a_rates, empty, empty, w9_labels=False, incomplete=False, arms_run=["A", "B", "C"]
    )
    assert verdict == "NOT-EVALUATED"


def test_dry_run_budgets_llm_and_read_only_for_w9():
    counts = {"coi": 50, "w9": 50, "_total": 100}
    w9_only = u658.dry_run_summary({}, ["coi", "w9"], ["A", "B", "C"], counts)
    both = u658.dry_run_summary({}, ["w9"], ["A", "B", "C"], {"w9": 50, "_total": 50})
    assert w9_only["estimated_llm_usd"] == both["estimated_llm_usd"]
    assert w9_only["estimated_di_read_usd"] == both["estimated_di_read_usd"]
    # Layout runs on every doc, counted once (not doubled by "_total").
    assert w9_only["estimated_di_layout_usd"] == pytest.approx(2 * both["estimated_di_layout_usd"], abs=1e-4)
