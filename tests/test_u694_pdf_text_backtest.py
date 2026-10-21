"""U-694: vendor PDF text layer for U-691 similar-bills backtest (fakes only)."""
from __future__ import annotations

import csv
import json
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from entities.attachment.business.extraction_service import REDACTION_VERSION
from scripts import u691_similar_bills_backtest as u691
from scripts.u653b_backtest import BillRow, ModePrediction
from scripts.u654a_text_backtest import assert_outputs_privacy_safe


def _blob_envelope(content: str, *, redaction_version: str = REDACTION_VERSION) -> str:
    return json.dumps({"content": content, "redaction_version": redaction_version})


def _bill(
    bill_id: int,
    vendor_id: int,
    created: str,
    scc: int,
    *,
    text: str = "",
    memo: str = "",
) -> u691.EnrichedBill:
    br = BillRow(
        bill_id=bill_id,
        vendor_id=vendor_id,
        created=datetime.fromisoformat(created),
        label_scc_id=scc,
        label_project_id=10,
        total_abs_amount=Decimal("100"),
        mixed_code=False,
    )
    return u691.EnrichedBill(
        bill=br, memo=memo, line_descriptions=text, line_count=1
    )


def _pdf_cfg(pdf_text: dict[int, str], *, text_source: str = u691.TEXT_SOURCE_PDF) -> u691.TokenSourceConfig:
    tokens = {
        bid: u691.tokenize_bill_text(text)
        for bid, text in pdf_text.items()
        if (text or "").strip()
    }
    return u691.TokenSourceConfig(
        text_source=text_source,
        pdf_tokens_by_bill_id=tokens,
    )


def test_bill_text_from_extraction_blob_current_stamp_and_content():
    raw = _blob_envelope("Invoice for lumber and wire")
    assert u691.bill_text_from_extraction_blob(raw) == "Invoice for lumber and wire"


def test_bill_text_from_extraction_blob_stale_and_non_json_excluded():
    assert u691.bill_text_from_extraction_blob('{"content":"x"}') is None
    assert u691.bill_text_from_extraction_blob("not json at all") is None
    stale = _blob_envelope("x", redaction_version="u535g-v1")
    assert u691.bill_text_from_extraction_blob(stale) is None


def test_pdf_tokens_never_include_json_key_names():
    raw = _blob_envelope("lumber shipment")
    assert u691.bill_text_from_extraction_blob(raw) == "lumber shipment"
    cfg = _pdf_cfg({1: "lumber shipment"})
    eb = _bill(1, 1, "2026-04-15T10:00:00", 1, text="ignored line")
    toks = u691.bill_tokens(eb, config=cfg)
    assert "content" not in toks
    assert "redaction" not in toks
    assert "lumber" in toks


def test_catalog_mask_removes_exact_catalog_tokens():
    eb = _bill(1, 1, "2026-04-15T10:00:00", 1, text="lumber plumbing wire")
    mask = frozenset({"lumber", "plumbing"})
    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_DESCRIPTIONS,
        catalog_mask_tokens=mask,
    )
    toks = u691.bill_tokens(eb, config=cfg)
    assert toks == frozenset({"wire"})


def test_pdf_population_restricts_eval_and_similarity_priors():
    cfg = _pdf_cfg({1: "wire alpha", 2: "pipe alpha", 3: "wire alpha"})
    bills = [
        _bill(1, 1, "2026-01-01T10:00:00", 10, text="line-one"),
        _bill(2, 1, "2026-02-01T10:00:00", 10, text="line-two"),
        _bill(3, 1, "2026-04-15T10:00:00", 10, text="line-three"),
        _bill(4, 1, "2026-04-20T10:00:00", 10, text="no pdf"),
    ]
    idf = u691.compute_idf_from_corpus(
        bills,
        since=date(2025, 10, 11),
        until=date(2026, 4, 10),
        config=cfg,
    )
    scored = u691.score_all_rolling(
        bills, idf, top_k=5, band_pct=0.25, cap_ratio=4.0, token_config=cfg
    )
    eval_rows = u691.eval_window_rows(
        scored,
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        token_config=cfg,
    )
    assert len(eval_rows) == 1
    assert eval_rows[0].enriched.bill.bill_id == 3
    assert eval_rows[0].similar["V_same_vendor"].top_k_used >= 1
    no_pdf_row = next(r for r in scored if r.enriched.bill.bill_id == 4)
    assert no_pdf_row.similar["V_any_vendor"].top_k_used == 0


def test_descriptions_mode_regression_three_variants_unchanged():
    """Legacy U-691 predictions for the original three variants on a fixed fixture."""
    cal = [
        _bill(1, 1, "2025-11-01T10:00:00", 1, text="seed alpha"),
        _bill(2, 1, "2025-12-01T10:00:00", 1, text="seed alpha"),
    ]
    eval_bill = _bill(
        3, 1, "2026-04-15T10:00:00", 42, text="uniquewidget alpha", memo=""
    )
    prior_match = _bill(
        4, 2, "2026-01-15T10:00:00", 42, text="uniquewidget alpha", memo=""
    )
    bills = cal + [prior_match, eval_bill]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    scored = u691.score_all_rolling(
        bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0
    )
    eval_rows = u691.eval_window_rows(
        scored, eval_since=date(2026, 4, 11), eval_until=date(2026, 10, 10)
    )
    assert len(eval_rows) == 1
    row = eval_rows[0]
    assert row.similar["V_same_vendor"].predicted_scc_id is None
    assert row.similar["V_any_vendor"].predicted_scc_id == 42
    assert row.similar["V_any_vendor_same_project"].predicted_scc_id == 42


def test_v_other_vendor_excludes_same_vendor_priors():
    query = _bill(10, 1, "2026-03-01T10:00:00", 5, text="shared token")
    priors = [
        _bill(1, 1, "2026-01-01T10:00:00", 2, text="shared token"),
        _bill(2, 2, "2026-01-02T10:00:00", 3, text="shared token"),
    ]
    idf = {"shared": 1.0, "token": 1.0}
    index: dict[str, set[int]] = {}
    for p in priors:
        for t in u691.bill_tokens(p):
            index.setdefault(t, set()).add(p.bill.bill_id)
    profiles = [u691.token_profile(p, idf) for p in priors]
    by_id = {pr.enriched.bill.bill_id: pr for pr in profiles}
    qprof = u691.token_profile(query, idf)
    preds = u691.predict_all_variants(
        qprof,
        profiles,
        idf,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
        token_index=index,
        prior_by_id=by_id,
    )
    assert preds["V_same_vendor"].predicted_scc_id == 2
    assert preds["V_other_vendor"].predicted_scc_id == 3


def test_leakage_shares_on_fixture():
    scc_names = {10: frozenset({"plumbing", "fixtures"})}
    catalog = frozenset({"plumbing", "lumber"})
    bills = [
        u691.FullyScoredBill(
            enriched=_bill(1, 1, "2026-04-15T10:00:00", 10, text="plumbing parts"),
            history_a=ModePrediction("A", 10, 10, "h", Decimal("0.9"), [], 3),
            history_b=ModePrediction("B", 10, 10, "h", Decimal("0.9"), [], 3),
            similar=u691.empty_similar_predictions(),
        ),
        u691.FullyScoredBill(
            enriched=_bill(2, 1, "2026-04-16T10:00:00", 10, text="wire only"),
            history_a=ModePrediction("A", 10, 10, "h", Decimal("0.9"), [], 3),
            history_b=ModePrediction("B", 10, 10, "h", Decimal("0.9"), [], 3),
            similar=u691.empty_similar_predictions(),
        ),
    ]
    cfg = u691.TokenSourceConfig(text_source=u691.TEXT_SOURCE_DESCRIPTIONS)
    leak = u691.compute_leakage_shares(
        bills,
        token_config=cfg,
        scc_name_tokens=scc_names,
        catalog_mask_tokens=catalog,
    )
    assert leak["own_label_scc_name"] == pytest.approx(0.5)
    assert leak["any_catalog_name"] == pytest.approx(0.5)


def test_blob_read_error_counted_not_fatal():
    def fail_read(_url: str) -> str:
        raise RuntimeError("storage down")

    urls = {1: "https://blob/one", 2: "https://blob/two"}
    pdf, stats = u691.load_pdf_text_by_bill_id(urls, read_blob=fail_read, max_workers=2)
    assert pdf == {}
    assert stats.read_error == 2
    assert stats.read_ok == 0


def test_load_pdf_text_ok_stale_empty():
    def read(url: str) -> str:
        if "good" in url:
            return _blob_envelope("vendor invoice body")
        if "stale" in url:
            return _blob_envelope("x", redaction_version="old")
        return _blob_envelope("   ")

    urls = {
        1: "https://blob/good",
        2: "https://blob/stale",
        3: "https://blob/empty",
    }
    pdf, stats = u691.load_pdf_text_by_bill_id(urls, read_blob=read, max_workers=2)
    assert pdf[1] == u691.tokenize_bill_text("vendor invoice body")
    assert 2 not in pdf and 3 not in pdf
    assert stats.read_ok == 1
    assert stats.stale_redaction == 1
    assert stats.empty_content == 1


def test_privacy_guard_rejects_pdf_text_in_outputs(tmp_path: Path):
    summary = {
        "windows": {
            "eval_since": "2026-04-11",
            "eval_until": "2026-10-10",
            "cal_since": "2025-10-11",
            "cal_until": "2026-04-10",
        },
        "head_to_head": {},
        "text_source": "pdf",
    }
    bad = tmp_path / "bad.json"
    bad.write_text('{"bill_text": "vendor invoice body secret"}', encoding="utf-8")
    with pytest.raises(ValueError):
        assert_outputs_privacy_safe(bad)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(summary), encoding="utf-8")
    assert_outputs_privacy_safe(good)


def test_filter_blob_urls_skips_non_coded_bills():
    enriched = [_bill(1, 1, "2026-01-01T10:00:00", 1)]
    urls = {1: "https://blob/coded", 99: "https://blob/not-coded"}
    read_urls: list[str] = []

    def read(url: str) -> str:
        read_urls.append(url)
        return _blob_envelope("pdf body words")

    filtered = u691.filter_blob_urls_to_coded_bills(urls, enriched)
    pdf, stats = u691.load_pdf_text_by_bill_id(filtered, read_blob=read, max_workers=1)
    assert stats.linked == 1
    assert len(read_urls) == 1
    assert "not-coded" not in read_urls[0]
    assert 1 in pdf


def test_pdf_read_timeout_counts_read_error():
    def slow_read(_url: str) -> str:
        time.sleep(2.0)
        return _blob_envelope("never")

    urls = {1: "https://blob/slow"}
    t0 = time.perf_counter()
    _, stats = u691.load_pdf_text_by_bill_id(
        urls,
        read_blob=slow_read,
        max_workers=1,
        timeout_seconds=0.1,
    )
    elapsed = time.perf_counter() - t0
    assert stats.read_error == 1
    assert stats.read_ok == 0
    assert elapsed < 1.0


def test_pdf_text_from_raw_blob_invalid_json_and_non_dict_are_stale():
    _, outcome = u691._pdf_text_from_raw_blob("not json at all")
    assert outcome == "stale"
    _, outcome = u691._pdf_text_from_raw_blob("[1, 2, 3]")
    assert outcome == "stale"


def test_load_pdf_text_invalid_json_counts_stale_redaction():
    def read(url: str) -> str:
        if "bad" in url:
            return "not valid json"
        return _blob_envelope("good body words")

    urls = {1: "https://blob/good", 2: "https://blob/bad"}
    pdf, stats = u691.load_pdf_text_by_bill_id(urls, read_blob=read, max_workers=2)
    assert 1 in pdf
    assert 2 not in pdf
    assert stats.stale_redaction == 1
    assert stats.empty_content == 0


def test_pdf_empty_token_set_still_in_usable_population():
    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF,
        pdf_tokens_by_bill_id={99: frozenset()},
    )
    assert 99 in cfg.usable_pdf_bill_ids
    eb = _bill(99, 1, "2026-04-15T10:00:00", 10, text="ignored")
    assert u691.similarity_eligible(eb, cfg)
    bills = [
        _bill(20, 1, "2025-11-01T10:00:00", 10, text="seed"),
        _bill(21, 1, "2025-12-01T10:00:00", 10, text="seed"),
        _bill(99, 1, "2026-04-15T10:00:00", 10, text="ignored"),
    ]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10), config=cfg
    )
    scored = u691.score_all_rolling(
        bills, idf, top_k=5, band_pct=0.25, cap_ratio=4.0, token_config=cfg
    )
    eval_rows = u691.eval_window_rows(
        scored,
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        token_config=cfg,
    )
    assert len(eval_rows) == 1
    assert eval_rows[0].enriched.bill.bill_id == 99


def test_write_outputs_pdf_mode_requires_token_config_and_eval_rows(tmp_path: Path):
    summary = {
        "windows": {
            "eval_since": "2026-04-11",
            "eval_until": "2026-10-10",
            "cal_since": "2025-10-11",
            "cal_until": "2026-04-10",
        },
        "head_to_head": {},
        "text_source": "pdf",
    }
    with pytest.raises(ValueError, match="token_config and eval_rows"):
        u691.write_outputs(
            out_dir=tmp_path,
            stamp="x",
            scored=[],
            summary=summary,
            token_config=None,
            eval_rows=None,
        )


def test_main_pdf_plus_masked_end_to_end_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise main() PDF path: coded-only reads, CSV rows, masked priors, eval leakage."""
    cal_since = "2025-10-11"
    cal_until = "2026-04-10"
    eval_since = "2026-04-11"
    eval_until = "2026-10-10"
    catalog = frozenset({"plumbing"})
    scc_names = {10: frozenset({"plumbing", "fixtures"})}

    bills = [
        _bill(20, 1, "2025-11-01T10:00:00", 99, text="seedalpha"),
        _bill(21, 1, "2025-12-01T10:00:00", 98, text="seedalpha"),
        _bill(
            1,
            1,
            "2026-01-01T10:00:00",
            10,
            text="plumbing matchtoken extradesc",
        ),
        _bill(2, 1, "2026-02-01T10:00:00", 20, text="unrelated"),
        _bill(3, 1, "2026-04-15T10:00:00", 30, text="matchtoken extradesc"),
    ]
    blob_by_bill = {
        20: "https://blob/bill-20",
        21: "https://blob/bill-21",
        2: "https://blob/bill-2",
        3: "https://blob/bill-3",
        99: "https://blob/non-coded-99",
    }
    pdf_bodies = {
        20: "seedalpha seedbeta",
        21: "seedalpha seedgamma",
        2: "matchtoken",
        3: "matchtoken",
    }
    read_urls: list[str] = []

    def fake_load_enriched() -> list[u691.EnrichedBill]:
        return bills

    def fake_blob_urls() -> dict[int, str]:
        return dict(blob_by_bill)

    def fake_scc_names() -> dict[int, frozenset[str]]:
        return dict(scc_names)

    def fake_reader():
        def read(url: str) -> str:
            read_urls.append(url)
            for bid, u in blob_by_bill.items():
                if u == url:
                    return _blob_envelope(pdf_bodies[bid])
            raise AssertionError(f"unexpected url {url}")

        return read

    monkeypatch.setattr(u691, "load_enriched_bills_from_db", fake_load_enriched)
    monkeypatch.setattr(u691, "load_blob_urls_from_db", fake_blob_urls)
    monkeypatch.setattr(u691, "load_scc_name_tokens_from_db", fake_scc_names)
    monkeypatch.setattr(u691, "make_text_blob_reader", fake_reader)
    monkeypatch.setattr(u691, "catalog_mask_from_scc_names", lambda _m: catalog)

    argv = [
        "--run",
        "--text-source",
        u691.TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS,
        "--mask-catalog-in-descriptions",
        "--out-dir",
        str(tmp_path),
        "--cal-since",
        cal_since,
        "--cal-until",
        cal_until,
        "--eval-since",
        eval_since,
        "--eval-until",
        eval_until,
        "--top-k",
        "5",
    ]
    assert u691.main(argv) == 0

    assert not any("non-coded-99" in u for u in read_urls)
    assert len(read_urls) == 4

    summaries = list(tmp_path.glob("u691_summary_*.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["pdf_blob_load"]["linked"] == 4
    assert summary["population"]["with_usable_pdf_text"] == 4

    leak_desc = summary["leakage_check_descriptions_on_pdf_eval"]
    assert leak_desc["any_catalog_name"] == pytest.approx(0.0)

    masked = summary["masked_descriptions_on_pdf_population"]
    assert masked is not None
    assert masked["V_same_vendor"]["coverage_bill"] == pytest.approx(0.0)

    csv_files = list(tmp_path.glob("u691_predictions_*.csv"))
    assert len(csv_files) == 1
    with csv_files[0].open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    data_rows = rows[1:]
    eval_bill_count = summary["population"]["eval_window_pdf_population"]
    assert eval_bill_count == 1
    assert len(data_rows) == eval_bill_count * (len(u691.VARIANTS) + 2)

    same_vendor_rows = [r for r in data_rows if r[2] == "V_same_vendor"]
    assert len(same_vendor_rows) == 1
    assert same_vendor_rows[0][3] == "20"


def test_write_outputs_pdf_mode_csv_rows_and_privacy(tmp_path: Path):
    pdf_secret = "zebra vendor invoice lumber shipment"
    cfg = _pdf_cfg({10: pdf_secret, 20: "pipe alpha", 30: pdf_secret})
    bills = [
        _bill(20, 1, "2026-01-01T10:00:00", 100, text="ignored"),
        _bill(10, 1, "2026-04-15T10:00:00", 200, text="ignored"),
    ]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10), config=cfg
    )
    scored = u691.score_all_rolling(
        bills, idf, top_k=5, band_pct=0.25, cap_ratio=4.0, token_config=cfg
    )
    eval_rows = u691.eval_window_rows(
        scored,
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        token_config=cfg,
    )
    sel = u691.CalibrationSelection(
        best_variant="V_same_vendor",
        best_variant_mode_b="V_same_vendor",
        calibration_metrics={},
        warnings=(),
    )
    summary = u691.build_summary(
        scored,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        best_variant="V_same_vendor",
        best_variant_mode_b="V_same_vendor",
        calibration_selection=sel,
        runtime_seconds=0.01,
        bill_count=2,
        constants={"top_k": 5, "text_source": "pdf"},
        token_config=cfg,
    )
    report, csv_path, json_path = u691.write_outputs(
        out_dir=tmp_path,
        stamp="pdfout",
        scored=scored,
        summary=summary,
        token_config=cfg,
        eval_rows=eval_rows,
    )
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    data_rows = rows[1:]
    assert len(data_rows) == len(eval_rows) * (len(u691.VARIANTS) + 2)
    for path in (report, csv_path, json_path):
        assert_outputs_privacy_safe(path)
        text = path.read_text(encoding="utf-8")
        assert pdf_secret not in text
        assert "lumber" not in text.lower()


def test_pdf_plus_masked_non_pdf_prior_excluded_from_scoring_and_calibration():
    mask = frozenset({"plumbing"})
    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS,
        catalog_mask_tokens=mask,
        pdf_tokens_by_bill_id={
            20: u691.tokenize_bill_text("seedalpha seedbeta"),
            21: u691.tokenize_bill_text("seedalpha seedgamma"),
            2: u691.tokenize_bill_text("matchtoken"),
            3: u691.tokenize_bill_text("matchtoken"),
        },
    )
    bills = [
        _bill(20, 1, "2025-11-01T10:00:00", 99, text="ignored"),
        _bill(21, 1, "2025-12-01T10:00:00", 98, text="ignored"),
        _bill(
            1,
            1,
            "2026-01-01T10:00:00",
            10,
            text="matchtoken extradesc filler",
        ),
        _bill(2, 1, "2026-02-01T10:00:00", 20, text="unrelated"),
        _bill(
            3,
            1,
            "2026-04-15T10:00:00",
            30,
            text="matchtoken extradesc",
        ),
    ]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10), config=cfg
    )
    selection = u691.pick_variants_on_calibration(
        bills,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        idf_for_selection=idf,
        top_k=5,
        band_pct=0.25,
        cap_ratio=4.0,
        token_config=cfg,
    )
    scored = u691.score_all_rolling(
        bills, idf, top_k=5, band_pct=0.25, cap_ratio=4.0, token_config=cfg
    )
    eval_row = u691.eval_window_rows(
        scored,
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        token_config=cfg,
    )[0]
    assert eval_row.similar["V_same_vendor"].predicted_scc_id == 20
    assert eval_row.similar["V_same_vendor"].top_k_used >= 1
    cal_same = selection.calibration_metrics["V_same_vendor"]
    assert cal_same["coverage_bill"] == pytest.approx(1 / 3)


def test_masked_descriptions_on_pdf_population_excludes_non_pdf_priors():
    pdf_pop = frozenset({20, 21, 2, 3})
    catalog = frozenset({"plumbing"})
    masked_cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_DESCRIPTIONS,
        catalog_mask_tokens=catalog,
        population_bill_ids=pdf_pop,
        pdf_tokens_by_bill_id={
            20: u691.tokenize_bill_text("seedalpha seedbeta"),
            21: u691.tokenize_bill_text("seedalpha seedgamma"),
            2: u691.tokenize_bill_text("sharedtoken"),
            3: u691.tokenize_bill_text("sharedtoken"),
        },
    )
    bills = [
        _bill(20, 1, "2025-11-01T10:00:00", 99, text="seedalpha"),
        _bill(21, 1, "2025-12-01T10:00:00", 98, text="seedalpha"),
        _bill(1, 1, "2026-01-01T10:00:00", 10, text="sharedtoken beta filler"),
        _bill(2, 1, "2026-02-01T10:00:00", 20, text="sharedtoken"),
        _bill(3, 1, "2026-04-15T10:00:00", 30, text="sharedtoken"),
    ]
    main_cfg = _pdf_cfg(
        {
            20: "seedalpha seedbeta",
            21: "seedalpha seedgamma",
            2: "sharedtoken",
            3: "sharedtoken",
        }
    )
    idf_main = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10), config=main_cfg
    )
    scored = u691.score_all_rolling(
        bills, idf_main, top_k=5, band_pct=0.25, cap_ratio=4.0, token_config=main_cfg
    )
    idf_masked = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10), config=masked_cfg
    )
    rescored = u691.rescore_similar_rolling(
        bills,
        idf_masked,
        history_source=scored,
        top_k=5,
        band_pct=0.25,
        cap_ratio=4.0,
        token_config=masked_cfg,
    )
    eval_row = u691.eval_window_rows(
        rescored,
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        token_config=masked_cfg,
    )[0]
    assert eval_row.similar["V_same_vendor"].predicted_scc_id == 20
    non_pdf = next(b for b in bills if b.bill.bill_id == 1)
    assert not u691.similarity_eligible(non_pdf, masked_cfg)
