"""U-697: vector-embedding similar-bills backtest (fakes only: MockTransport, fake loader)."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from entities.coding_suggestion.business.similar_bills import (
    idf_weighted_jaccard,
    vote_from_scored_neighbours,
)
from scripts import u691_similar_bills_backtest as u691
from scripts import u697_vector_similar_bills_backtest as u697
from scripts.u653b_backtest import BillRow, ModePrediction

MODULE_PATH = Path(u697.__file__)
ENDPOINT = "https://res-x.services.ai.azure.com/api/projects/proj-1"


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------


def _bill(
    bill_id: int,
    vendor_id: int,
    created: str | datetime,
    scc: int,
    *,
    amount: str = "100",
    project: int | None = 10,
) -> u691.EnrichedBill:
    br = BillRow(
        bill_id=bill_id,
        vendor_id=vendor_id,
        created=created if isinstance(created, datetime) else datetime.fromisoformat(created),
        label_scc_id=scc,
        label_project_id=project,
        total_abs_amount=Decimal(amount),
        mixed_code=False,
    )
    return u691.EnrichedBill(bill=br, memo="", line_descriptions="", line_count=1)


def _vec(*xs: float):
    return u697.normalise(xs)


def _vp(eb: u691.EnrichedBill, *xs: float) -> u697.VectorProfile:
    return u697.vector_profile(eb, _vec(*xs) if xs else None)


def _text_vector(text: str, dims: int = 8) -> list[float]:
    """Deterministic fake embedding: letter-bucket counts (similar words => similar vectors)."""
    v = [0.0] * dims
    for ch in text.lower():
        if ch.isalpha():
            v[(ord(ch) - 97) % dims] += 1.0
    v[0] += 0.01
    return v


class FakeServer:
    def __init__(
        self,
        *,
        usage_per_request: int | None = 10,
        script: list[httpx.Response] | None = None,
        context_limit_chars: int | None = None,
    ) -> None:
        self.requests: list[dict] = []
        self.urls: list[str] = []
        self.headers: list[dict] = []
        self.usage_per_request = usage_per_request
        self.script = list(script or [])
        self.context_limit_chars = context_limit_chars

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.urls.append(str(request.url))
        self.headers.append(dict(request.headers))
        if self.script:
            return self.script.pop(0)
        inputs = body["input"]
        if self.context_limit_chars is not None and any(
            len(t) > self.context_limit_chars for t in inputs
        ):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "This model's maximum context length is 8192 tokens",
                    }
                },
            )
        data = [
            {"index": i, "embedding": _text_vector(t, body.get("dimensions", 8))}
            for i, t in enumerate(inputs)
        ]
        payload: dict = {"data": data}
        if self.usage_per_request is not None:
            payload["usage"] = {"prompt_tokens": self.usage_per_request}
        return httpx.Response(200, json=payload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _embedder(server: FakeServer, **kw) -> u697.FoundryEmbedder:
    sleeps: list[float] = []
    kw.setdefault("max_cost_usd", Decimal("1.00"))
    emb = u697.FoundryEmbedder(
        endpoint=ENDPOINT,
        api_key="test-key",
        transport=server.transport,
        sleep=sleeps.append,
        **kw,
    )
    emb.sleeps = sleeps  # type: ignore[attr-defined]
    return emb


# ---------------------------------------------------------------------------------------------
# embedder
# ---------------------------------------------------------------------------------------------


def test_url_styles_and_body():
    server = FakeServer()
    emb = _embedder(server, dimensions=256)
    assert emb.url == (
        "https://res-x.services.ai.azure.com/openai/deployments/text-embedding-3-small/"
        "embeddings?api-version=2024-10-21"
    )
    emb.embed_texts(["lumber"])
    assert server.requests[0] == {"input": ["lumber"], "dimensions": 256}
    assert server.headers[0]["api-key"] == "test-key"

    server2 = FakeServer()
    emb2 = _embedder(server2, url_style=u697.URL_STYLE_MODELS)
    emb2.embed_texts(["lumber"])
    assert server2.urls[0].startswith("https://res-x.services.ai.azure.com/models/embeddings")
    assert server2.requests[0]["model"] == "text-embedding-3-small"
    assert "dimensions" not in server2.requests[0]


def test_vectors_are_unit_normalised():
    server = FakeServer()
    emb = _embedder(server)
    (v,) = emb.embed_texts(["abcabc lumber"])
    assert abs(u697.dot(v, v) - 1.0) < 1e-5


def test_batching_smoke_call_first_then_batches_of_64(tmp_path):
    server = FakeServer()
    emb = _embedder(server)
    cache = u697.EmbeddingCache(tmp_path)
    texts = {i: f"bill text number {i}" for i in range(1, 132)}  # 131 misses
    vecs = u697.embed_population(texts, emb, cache, batch_size=64)
    assert len(vecs) == 131
    assert [len(r["input"]) for r in server.requests] == [1, 64, 64, 2]
    assert emb.spend.requests == 4


def test_429_retried_honouring_retry_after():
    server = FakeServer(
        script=[
            httpx.Response(429, headers={"retry-after": "7"}, json={"error": {}}),
            httpx.Response(503, json={"error": {}}),
        ]
    )
    emb = _embedder(server)
    (v,) = emb.embed_texts(["lumber"])
    assert len(server.requests) == 3
    assert emb.sleeps[0] == 7.0  # type: ignore[attr-defined]
    assert len(emb.sleeps) == 2  # type: ignore[attr-defined]
    assert emb.spend.requests == 1  # only the successful request is metered


def test_retries_give_up_after_five_attempts():
    server = FakeServer(script=[httpx.Response(500, json={}) for _ in range(5)])
    emb = _embedder(server)
    with pytest.raises(u697.EmbeddingError):
        emb.embed_texts(["lumber"])
    assert len(server.requests) == u697.MAX_ATTEMPTS


def test_context_length_400_halves_that_input_only():
    server = FakeServer(context_limit_chars=600)
    emb = _embedder(server)
    short, long = "short text", "x" * 1000
    vecs = emb.embed_texts([short, long])
    assert len(vecs) == 2
    sizes = [[len(t) for t in r["input"]] for r in server.requests]
    # batch rejected -> singles: short ok, long rejected, long halved to 500 ok
    assert sizes == [[10, 1000], [10], [1000], [500]]
    assert emb.spend.retruncated == 1
    assert emb.spend.requests == 2  # only successful (billed) requests are metered


def test_head_truncation_to_max_chars_counted():
    server = FakeServer()
    emb = _embedder(server, max_chars=50)
    emb.embed_texts(["a" * 80, "b" * 10])
    assert [len(t) for t in server.requests[0]["input"]] == [50, 10]
    assert server.requests[0]["input"][0] == "a" * 50  # head kept
    assert emb.spend.truncated == 1


def test_cache_hit_makes_no_call_and_text_change_reembeds(tmp_path):
    texts = {1: "lumber invoice", 2: "electrical wire", 3: "concrete pour"}
    server = FakeServer()
    u697.embed_population(texts, _embedder(server), u697.EmbeddingCache(tmp_path))
    assert len(server.requests) == 2  # smoke + one batch

    server2 = FakeServer()
    emb2 = _embedder(server2)
    vecs = u697.embed_population(texts, emb2, u697.EmbeddingCache(tmp_path))
    assert server2.requests == []
    assert emb2.spend.cache_hits == 3 and len(vecs) == 3

    server3 = FakeServer()
    emb3 = _embedder(server3)
    changed = dict(texts)
    changed[2] = "electrical wire and breakers"
    u697.embed_population(changed, emb3, u697.EmbeddingCache(tmp_path))
    assert [r["input"] for r in server3.requests] == [["electrical wire and breakers"]]
    assert emb3.spend.cache_hits == 2


def test_policy_or_deployment_change_misses_cache(tmp_path):
    texts = {1: "lumber invoice"}
    u697.embed_population(texts, _embedder(FakeServer()), u697.EmbeddingCache(tmp_path))
    server = FakeServer()
    u697.embed_population(
        texts, _embedder(server, max_chars=1000), u697.EmbeddingCache(tmp_path)
    )
    assert len(server.requests) == 1


def test_cap_aborts_before_any_over_cap_request(tmp_path):
    server = FakeServer()
    # 3000 chars -> 1000 estimated tokens -> $0.00002 > cap
    emb = _embedder(server, max_cost_usd=Decimal("0.00001"))
    with pytest.raises(u697.SpendCapExceeded):
        u697.embed_population({1: "y" * 3000}, emb, u697.EmbeddingCache(tmp_path))
    assert server.requests == []


def test_cap_aborts_before_the_batch_that_would_cross_it(tmp_path):
    # each request reports 400 tokens ($0.000008); each batch estimate is ~334 tokens.
    server = FakeServer(usage_per_request=400)
    emb = _embedder(server, max_cost_usd=Decimal("0.000015"))
    texts = {i: "z" * 1000 for i in range(1, 6)}
    with pytest.raises(u697.SpendCapExceeded):
        u697.embed_population(texts, emb, u697.EmbeddingCache(tmp_path), batch_size=1)
    # smoke (400 tok) ok; next: 400 + 334 = 734 tok = $0.0000147 <= cap -> ok;
    # third: 800 + 334 -> $0.0000227 > cap -> aborted before sending
    assert len(server.requests) == 2
    assert emb.spend.prompt_tokens == 800
    rows = (tmp_path / u697.CACHE_FILENAME).read_text().strip().splitlines()
    assert len(rows) == 2  # completed work is kept


def test_spend_sums_usage_prompt_tokens(tmp_path):
    server = FakeServer(usage_per_request=1234)
    emb = _embedder(server)
    u697.embed_population(
        {i: f"text {i}" for i in range(1, 70)}, emb, u697.EmbeddingCache(tmp_path)
    )
    assert emb.spend.requests == 3  # 1 + 64 + 4
    assert emb.spend.prompt_tokens == 3 * 1234
    assert emb.spend.usd == Decimal(3 * 1234) * Decimal("0.02") / Decimal(1_000_000)
    js = emb.spend.to_json()
    for k in ("model", "deployment", "dimensions", "requests", "prompt_tokens", "usd",
              "cap", "cache_hits", "truncated"):
        assert k in js


def test_cache_holds_no_raw_text(tmp_path):
    secret = "Quartzite countertop fabrication for Hollister residence"
    u697.embed_population({7: secret}, _embedder(FakeServer()), u697.EmbeddingCache(tmp_path))
    raw = (tmp_path / u697.CACHE_FILENAME).read_text()
    assert "Quartzite" not in raw and "Hollister" not in raw
    row = json.loads(raw.strip())
    assert set(row) == {
        "bill_id", "text_sha256", "model", "deployment", "dimensions",
        "truncation_policy_version", "vector",
    }
    assert row["text_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
    v = u697.decode_vector(row["vector"])
    assert abs(u697.dot(v, v) - 1.0) < 1e-5


def test_module_does_not_import_claude_budget_or_anthropic():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.append(mod)
            imported += [f"{mod}.{a.name}" for a in node.names]
    for name in imported:
        assert "claude_budget" not in name, name
        assert "anthropic" not in name.lower(), name
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "claude_budget." not in src
    assert "import anthropic" not in src


# ---------------------------------------------------------------------------------------------
# vector arm
# ---------------------------------------------------------------------------------------------


def _predict(query, priors, **kw):
    kw.setdefault("top_k", 10)
    kw.setdefault("band_pct", 0.25)
    kw.setdefault("cap_ratio", 4.0)
    return u697.predict_all_variants_vector(query, priors, **kw)


def test_strictly_earlier_only_same_timestamp_and_self_excluded():
    q = _vp(_bill(10, 1, "2026-05-01T10:00:00", 1), 1, 0)
    earlier = _vp(_bill(1, 1, "2026-05-01T09:59:59", 7), 1, 0)
    same_ts = _vp(_bill(2, 1, "2026-05-01T10:00:00", 8), 1, 0)
    later = _vp(_bill(3, 1, "2026-05-02T10:00:00", 9), 1, 0)
    self_copy = _vp(_bill(10, 1, "2026-04-01T10:00:00", 9), 1, 0)
    preds = _predict(q, [earlier, same_ts, later, self_copy])
    any_v = preds["V_any_vendor"]
    assert any_v.predicted_scc_id == 7
    assert any_v.neighbour_count == 1


def test_band_zeroes_priors_beyond_cap_and_skips_their_dot_product():
    q = _vp(_bill(10, 1, "2026-05-01", 1, amount="100"), 1, 0)
    far = _vp(_bill(1, 1, "2026-04-01", 7, amount="500"), 1, 0)  # 5x -> band 0
    near = _vp(_bill(2, 1, "2026-04-02", 8, amount="300"), 1, 0)  # 3x -> 0 < band < 1
    calls: list[int] = []

    def sim(a, b):
        calls.append(b.enriched.bill.bill_id)
        return u697.cosine_similarity(a, b)

    preds = _predict(q, [far, near], similarity=sim)
    assert calls == [2]
    sp = preds["V_any_vendor"]
    assert sp.predicted_scc_id == 8 and sp.neighbour_count == 1
    banded = u697.eligible_banded_priors(q, [far, near], band_pct=0.25, cap_ratio=4.0)
    assert [p.enriched.bill.bill_id for p, _ in banded] == [2]
    assert 0 < banded[0][1] < 1


def test_non_positive_cosine_is_dropped():
    q = _vp(_bill(10, 1, "2026-05-01", 1), 1, 0)
    opposite = _vp(_bill(1, 1, "2026-04-01", 7), -1, 0)
    orth = _vp(_bill(2, 1, "2026-04-01", 8), 0, 1)
    preds = _predict(q, [opposite, orth])
    assert all(p.predicted_scc_id is None for p in preds.values())


def test_variant_partition():
    q = _vp(_bill(10, 1, "2026-05-01", 1, project=50), 1, 0)
    same_v_same_p = _vp(_bill(1, 1, "2026-04-01", 11, project=50), 1, 0.1)
    same_v_other_p = _vp(_bill(2, 1, "2026-04-01", 12, project=51), 1, 0.2)
    other_v_same_p = _vp(_bill(3, 2, "2026-04-01", 13, project=50), 1, 0.3)
    other_v_no_p = _vp(_bill(4, 3, "2026-04-01", 14, project=None), 1, 0.4)
    preds = _predict(q, [same_v_same_p, same_v_other_p, other_v_same_p, other_v_no_p])
    assert preds["V_any_vendor"].neighbour_count == 4
    assert preds["V_same_vendor"].neighbour_count == 2
    assert preds["V_other_vendor"].neighbour_count == 2
    assert preds["V_any_vendor_same_project"].neighbour_count == 2
    assert preds["V_same_vendor"].predicted_scc_id == 11  # highest cosine within vendor
    assert preds["V_other_vendor"].predicted_scc_id == 13


def test_vote_ties_match_vote_from_scored_neighbours():
    q = _vp(_bill(10, 1, "2026-05-01", 1), 1, 0)
    a = _vp(_bill(1, 1, "2026-04-01", 30), 1, 0)
    b = _vp(_bill(2, 1, "2026-04-01", 20), 1, 0)
    preds = _predict(q, [a, b])
    ref = vote_from_scored_neighbours(
        [(1.0, 1, 30), (1.0, 2, 20)], 10
    )
    sp = preds["V_any_vendor"]
    assert sp.predicted_scc_id == ref.predicted_scc_id == 20
    assert sp.raw_score == ref.confidence


def _token_fixture():
    texts = {
        1: ("2026-01-05T09:00:00", 1, 100, "100", "lumber studs plywood framing"),
        2: ("2026-01-06T09:00:00", 1, 100, "120", "lumber plywood sheathing"),
        3: ("2026-01-07T09:00:00", 2, 200, "90", "copper wire breakers panel"),
        4: ("2026-01-08T09:00:00", 2, 200, "400", "wire conduit panel"),
        5: ("2026-01-09T09:00:00", 3, 100, "110", "framing lumber nails"),
        6: ("2026-01-09T09:00:00", 1, 300, "105", "plywood lumber delivery"),
        7: ("2026-01-10T09:00:00", 3, 200, "95", "panel breakers wire lumber"),
        8: ("2026-01-11T09:00:00", 1, 100, "1000", "lumber studs"),
    }
    bills = [
        _bill(bid, vendor, created, scc, amount=amt, project=10 + (bid % 2))
        for bid, (created, vendor, scc, amt, _t) in texts.items()
    ]
    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF,
        pdf_tokens_by_bill_id={
            bid: u691.tokenize_bill_text(t[4]) for bid, t in texts.items()
        },
    )
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2026, 1, 1), until=date(2026, 12, 31), config=cfg
    )
    return bills, cfg, idf


def test_parity_word_score_reproduces_u691_predict_all_variants():
    bills, cfg, idf = _token_fixture()
    profiles = [u691.token_profile(eb, idf, config=cfg) for eb in bills]
    by_id = {p.enriched.bill.bill_id: p for p in profiles}
    token_index: dict[str, set[int]] = defaultdict(set)
    for p in profiles:
        for t in p.tokens:
            token_index[t].add(p.enriched.bill.bill_id)

    def word_sim(q, p):
        return idf_weighted_jaccard(q.tokens, q.idf_weight_sum, p.tokens, p.idf_weight_sum, idf)

    compared = 0
    for q in profiles:
        ref = u691.predict_all_variants(
            q, profiles, idf, top_k=3, band_pct=0.25, cap_ratio=4.0,
            token_index=token_index, prior_by_id=by_id,
        )
        got = _predict(q, profiles, top_k=3, similarity=word_sim)
        assert got == ref
        compared += sum(1 for v in ref.values() if v.predicted_scc_id is not None)
    assert compared > 0


def test_parity_rolling_word_generic_matches_u691_score_all_rolling():
    bills, cfg, idf = _token_fixture()
    w = u691.score_all_rolling(bills, idf, top_k=3, band_pct=0.25, cap_ratio=4.0, token_config=cfg)
    vectors = {eb.bill.bill_id: u697.normalise(_text_vector(str(eb.bill.bill_id))) for eb in bills}
    vp = u697.score_vector_rolling(
        bills, vectors=vectors, history_source=w, token_config=cfg, idf=idf,
        top_k=3, band_pct=0.25, cap_ratio=4.0,
    )
    w_by = {r.enriched.bill.bill_id: r.similar for r in w}
    for r in vp.arms["W_generic"]:
        assert r.similar == w_by[r.enriched.bill.bill_id]


def test_rolling_vector_pass_matches_single_query_predict():
    bills, cfg, idf = _token_fixture()
    w = u691.score_all_rolling(bills, idf, top_k=3, band_pct=0.25, cap_ratio=4.0, token_config=cfg)
    vectors = {
        eb.bill.bill_id: u697.normalise(_text_vector(f"v{eb.bill.label_scc_id}x{eb.bill.bill_id}"))
        for eb in bills
    }
    vp = u697.score_vector_rolling(
        bills, vectors=vectors, history_source=w, token_config=cfg, idf=idf,
        top_k=3, band_pct=0.25, cap_ratio=4.0, v_prime_grid=(0.3,),
    )
    profs = [u697.vector_profile(eb, vectors[eb.bill.bill_id]) for eb in bills]
    for row in vp.arms[u697.ARM_V]:
        q = next(p for p in profs if p.enriched.bill.bill_id == row.enriched.bill.bill_id)
        assert row.similar == _predict(q, profs, top_k=3)
    for row in vp.arms["V_prime_c0.3"]:
        q = next(p for p in profs if p.enriched.bill.bill_id == row.enriched.bill.bill_id)
        assert row.similar == _predict(
            q, profs, top_k=3, weight=u697.shifted_cosine_weight(0.3)
        )


def _scored_row(bill_id, created, label, pred):
    eb = _bill(bill_id, 1, created, label)
    mp = ModePrediction("A", None, None, "h", None, [], 0)
    sim = {v: u691.SimilarPrediction(pred, Decimal("1"), 1, 1) for v in u691.VARIANTS}
    sim["V_other_vendor"] = u691.SimilarPrediction(None, None, 0, 0)
    return u691.FullyScoredBill(eb, mp, mp, sim)


def test_calibration_only_selection_ignores_evaluation_rows():
    cfg = u691.TokenSourceConfig(text_source=u691.TEXT_SOURCE_DESCRIPTIONS)
    cal_rows = [_scored_row(i, "2026-01-0%d" % i, 5, 5) for i in range(1, 5)]
    eval_a = [_scored_row(10 + i, "2026-06-0%d" % i, 5, 5) for i in range(1, 5)]
    eval_b = [_scored_row(10 + i, "2026-06-0%d" % i, 5, 99) for i in range(1, 5)]
    kw = dict(cal_since=date(2026, 1, 1), cal_until=date(2026, 3, 31), token_config=cfg)
    sel_a = u697.select_variants_from_scored(cal_rows + eval_a, **kw)
    sel_b = u697.select_variants_from_scored(cal_rows + eval_b, **kw)
    assert sel_a == sel_b
    assert sel_a.calibration_metrics["V_any_vendor"]["bill"] == 1.0
    assert sel_a.calibration_metrics["V_other_vendor"]["bill"] == 0.0
    # and a calibration-row change DOES move it
    cal_bad = [_scored_row(i, "2026-01-0%d" % i, 5, 99) for i in range(1, 5)]
    sel_c = u697.select_variants_from_scored(cal_bad + eval_a, **kw)
    assert sel_c.calibration_metrics["V_any_vendor"]["bill"] == 0.0


def test_pick_v_prime_c0_ties_take_smaller():
    def sel(acc):
        return u691.CalibrationSelection(
            "V_any_vendor", "V_any_vendor", {"V_any_vendor": {"bill": acc}}, ()
        )

    assert u697.pick_v_prime_c0({0.3: sel(0.5), 0.1: sel(0.6), 0.2: sel(0.6)}) == 0.1


def test_rrf_fused_order():
    a = [(0.9, 1, 100), (0.8, 2, 200)]
    b = [(0.95, 2, 200), (0.5, 3, 300)]
    fused = u697.rrf_fuse(a, b, k=60)
    assert [bid for _s, bid, _c in fused] == [2, 1, 3]
    assert fused[0][0] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[1][0] == pytest.approx(1 / 61)
    assert fused[2][0] == pytest.approx(1 / 62)
    # depth truncates each list before fusing
    assert [bid for _s, bid, _c in u697.rrf_fuse(a, b, k=60, depth=1)] == [1, 2]


def test_stratum_no_earlier_same_vendor_pdf_bill():
    bills = [
        _bill(1, 1, "2026-01-01T09:00:00", 5),
        _bill(2, 1, "2026-01-01T09:00:00", 5),  # same timestamp: not strictly earlier
        _bill(3, 1, "2026-01-02T09:00:00", 5),
        _bill(4, 2, "2026-01-03T09:00:00", 5),
        _bill(5, 2, "2025-12-01T09:00:00", 5),  # vendor 2's earliest is NOT in PDF population
        _bill(6, 3, "2026-02-01T09:00:00", 5),
    ]
    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF,
        pdf_tokens_by_bill_id={b: frozenset({"x"}) for b in (1, 2, 3, 4, 6)},
    )
    assert u697.no_earlier_same_vendor_ids(bills, cfg) == frozenset({1, 2, 4, 6})


def test_reidentification_rate():
    reid = {1: (3, 4), 2: (0, 2), 3: (5, 5)}
    out = u697.reidentification_rate(reid, [1, 2])
    assert out["neighbour_share"] == pytest.approx(3 / 6)
    assert out["mean_per_bill"] == pytest.approx((0.75 + 0.0) / 2)
    assert out["neighbours"] == 6
    assert u697.reidentification_rate({}, [1])["neighbour_share"] is None


def test_rolling_reidentification_counts_same_vendor_neighbours():
    bills, cfg, idf = _token_fixture()
    w = u691.score_all_rolling(bills, idf, top_k=3, band_pct=0.25, cap_ratio=4.0, token_config=cfg)
    vectors = {eb.bill.bill_id: _vec(1, 0) for eb in bills}
    vp = u697.score_vector_rolling(
        bills, vectors=vectors, history_source=w, token_config=cfg, idf=idf,
        top_k=10, band_pct=0.25, cap_ratio=4.0,
    )
    # bill 6 (vendor 1, $105): strictly-earlier in-band priors 1, 2 (vendor 1) and 3, 4
    # (vendor 2; $400 is 3.8x, still inside the 4x cap). Bill 5 shares bill 6's timestamp.
    same, total = vp.reidentification[u697.ARM_V][6]
    assert (same, total) == (2, 4)


def test_sign_test():
    assert u697.sign_test_p(0, 0) is None
    assert u697.sign_test_p(5, 5) == 1.0
    assert u697.sign_test_p(29, 13) == pytest.approx(0.0199, abs=0.001)
    assert u697.sign_test_p(3000, 2900) <= 1.0  # big n does not overflow


def test_bill_ids_from_csv_unique(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("BillId,mode\n5,A\n5,B\n7,A\nx,A\n", encoding="utf-8")
    assert u697.load_bill_ids_from_csv(p) == [5, 7]


# ---------------------------------------------------------------------------------------------
# end to end (fake loader + fake embedder)
# ---------------------------------------------------------------------------------------------

_SECRET_WORDS = ("Hollister", "Quartzite", "Wickersham")


def _fake_population() -> u697.Population:
    themes = [
        (101, "lumber studs plywood framing nails Hollister"),
        (202, "copper wire breakers panel conduit Quartzite"),
        (303, "concrete pour rebar forms Wickersham"),
    ]
    enriched, tokens, texts = [], {}, {}
    start = datetime(2025, 10, 15, 9, 0, 0)
    bid = 1000
    for i in range(120):
        theme_scc, text = themes[i % 3]
        vendor = 1 + (i % 5)
        created = start + timedelta(days=3 * i)
        bid += 1
        eb = _bill(bid, vendor, created, theme_scc, amount=str(100 + (i % 7) * 10),
                   project=40 + (i % 4))
        enriched.append(eb)
        if i % 11 == 0:
            continue  # no usable PDF text: outside the population
        full = f"{text} invoice {i}"
        texts[bid] = full
        tokens[bid] = u691.tokenize_bill_text(full)
    return u697.Population(enriched, tokens, texts, u691.PdfTextLoadStats(linked=len(texts)))


def _args(tmp_path: Path, *extra: str) -> argparse.Namespace:
    ids_csv = tmp_path / "ids.csv"
    # 1050: calibration window; 1071/1072: eval window + PDF; 1078: no PDF text; 999999: unknown
    ids_csv.write_text(
        "BillId,mode\n1050,A\n1050,B\n1071,A\n1072,A\n1078,A\n999999,A\n", encoding="utf-8"
    )
    return u697.build_arg_parser().parse_args(
        [
            "--out-dir", str(tmp_path / "out"),
            "--cache-dir", str(tmp_path / "cache"),
            "--bill-ids-from", str(ids_csv),
            *extra,
        ]
    )


def test_dry_run_makes_zero_embedding_calls(tmp_path):
    made: list[int] = []
    server = FakeServer()

    def factory():
        made.append(1)
        return _embedder(server)

    logs: list[str] = []
    rc = u697.run_backtest(
        _args(tmp_path), load_population=_fake_population, embedder_factory=factory,
        log=logs.append,
    )
    assert rc == 0
    assert made == [] and server.requests == []
    assert not (tmp_path / "out").exists()
    assert any("projected $" in m for m in logs)


def test_end_to_end_with_fakes_privacy_guard_passes(tmp_path):
    server = FakeServer()
    logs: list[str] = []
    rc = u697.run_backtest(
        _args(tmp_path, "--run", "--vector-prime"),
        load_population=_fake_population,
        embedder_factory=lambda: _embedder(server),
        stamp="20261028T000000Z",
        log=logs.append,
    )
    assert rc == 0
    out = tmp_path / "out"
    files = sorted(p.name for p in out.iterdir())
    assert "u697_comparison_20261028T000000Z.md" in files
    assert "u697_comparison_20261028T000000Z.json" in files
    for arm in ("W", "V", "V_prime", "H"):
        assert f"u691_summary_20261028T000000Z_u697_{arm}.json" in files
    for p in out.iterdir():
        body = p.read_text(encoding="utf-8")
        for word in _SECRET_WORDS:
            assert word not in body, (p.name, word)
    cache_raw = (tmp_path / "cache" / u697.CACHE_FILENAME).read_text()
    for word in _SECRET_WORDS:
        assert word not in cache_raw

    comp = json.loads((out / "u697_comparison_20261028T000000Z.json").read_text())
    assert comp["extra"]["word_generic_parity_mismatches"] == 0
    assert comp["extra"]["missing_vectors"] == 0
    assert comp["embedding_spend"]["requests"] == len(server.requests) >= 2
    assert server.requests[0]["input"].__len__() == 1  # smoke call first
    assert set(comp["paired"]) == {"W_vs_V", "W_vs_V_prime", "W_vs_H"}
    sample = comp["sample_500"]
    assert sample["ids_in_file"] == 5
    assert sample["evaluated"] == 2
    assert sample["dropped"] == {
        "not_loaded": 1,
        "not_in_pdf_population": 1,
        "calibration_window": 1,
        "outside_both_windows": 0,
    }
    assert {a["n"] for a in sample["arms"].values()} == {2}
    md = (out / "u697_comparison_20261028T000000Z.md").read_text()
    assert "| calibration_window | 1 |" in md
    assert "| word_generic_parity_mismatches | 0 |" in md
    assert "| missing_vectors | 0 |" in md
    assert "| v_prime_c0 |" in md and "| v_prime_grid_cal_bill_c0.1 |" in md
    assert comp["population"]["delta_vs_u694"]["pdf_bills"] == 109 - 3757
    assert "V" in comp["vendor_reidentification"]

    # a second run is served entirely from the cache
    server2 = FakeServer()
    u697.run_backtest(
        _args(tmp_path, "--run"),
        load_population=_fake_population,
        embedder_factory=lambda: _embedder(server2),
        stamp="20261028T000001Z",
        log=logs.append,
    )
    assert server2.requests == []


def test_run_aborts_when_projection_exceeds_cap(tmp_path):
    server = FakeServer()
    with pytest.raises(u697.SpendCapExceeded):
        u697.run_backtest(
            _args(tmp_path, "--run", "--max-cost-usd", "0.0000001"),
            load_population=_fake_population,
            embedder_factory=lambda: _embedder(server),
            log=lambda m: None,
        )
    assert server.requests == []


def test_write_comparison_privacy_guard_rejects_text(tmp_path, monkeypatch):
    monkeypatch.setattr(u697, "render_comparison_md", lambda comp, stamp: "ok\n")
    with pytest.raises(ValueError):
        u697.write_comparison(tmp_path, "s1", {"extra": {"memo": "lumber"}})
    monkeypatch.setattr(
        u697,
        "render_comparison_md",
        lambda comp, stamp: "| a | b |\n|---|---|\n| x | lumber for the Hollister job |\n",
    )
    with pytest.raises(ValueError):
        u697.write_comparison(tmp_path, "s2", {"extra": {"n": 1}})


# ---------------------------------------------------------------------------------------------
# round 2: parity stops, arm-output privacy, sample window, error codes, W re-id, retry meter
# ---------------------------------------------------------------------------------------------


def test_run_stops_on_missing_vectors_before_any_output(tmp_path, monkeypatch):
    real = u697.embed_population

    def drop_one(texts, *a, **kw):
        vecs = real(texts, *a, **kw)
        vecs.pop(min(vecs))
        return vecs

    monkeypatch.setattr(u697, "embed_population", drop_one)
    with pytest.raises(u697.ParityError, match="no vector"):
        u697.run_backtest(
            _args(tmp_path, "--run"),
            load_population=_fake_population,
            embedder_factory=lambda: _embedder(FakeServer()),
            log=lambda m: None,
        )
    assert not (tmp_path / "out").exists()


def test_run_stops_on_word_parity_mismatch_before_any_output(tmp_path, monkeypatch):
    real = u691.score_all_rolling

    def drifted(*a, **kw):
        rows = real(*a, **kw)
        for r in rows:
            if r.similar["V_any_vendor"].predicted_scc_id is not None:
                r.similar["V_any_vendor"] = u691.SimilarPrediction(-1, Decimal("1"), 1, 1)
                break
        return rows

    monkeypatch.setattr(u691, "score_all_rolling", drifted)
    with pytest.raises(u697.ParityError, match="differ"):
        u697.run_backtest(
            _args(tmp_path, "--run"),
            load_population=_fake_population,
            embedder_factory=lambda: _embedder(FakeServer()),
            log=lambda m: None,
        )
    assert not (tmp_path / "out").exists()


def test_assert_parity_requires_the_check_to_have_run():
    with pytest.raises(u697.ParityError):
        u697.assert_parity({"missing_vectors": 0})
    u697.assert_parity({"missing_vectors": 0, "word_generic_parity_mismatches": 0})


def test_arm_output_with_text_is_rejected_by_the_script(tmp_path, monkeypatch):
    def leaky_write_outputs(*, out_dir, stamp, **_kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        report = out_dir / f"u691_report_{stamp}.md"
        report.write_text(
            "| a | b |\n|---|---|\n| x | quartzite countertop for the hollister job |\n",
            encoding="utf-8",
        )
        return report, report, report  # u691's own guard is bypassed here

    monkeypatch.setattr(u691, "write_outputs", leaky_write_outputs)
    with pytest.raises(ValueError, match="free-text"):
        u697.run_backtest(
            _args(tmp_path, "--run"),
            load_population=_fake_population,
            embedder_factory=lambda: _embedder(FakeServer()),
            log=lambda m: None,
        )
    assert not list((tmp_path / "out").glob("u697_comparison_*"))


def test_partition_sample_ids_keeps_eval_window_only():
    bills = [
        _bill(1, 1, "2026-01-05T09:00:00", 5),  # cal
        _bill(2, 1, "2026-06-05T09:00:00", 5),  # eval
        _bill(3, 1, "2026-06-06T09:00:00", 5),  # eval, no PDF text
        _bill(4, 1, "2025-01-05T09:00:00", 5),  # before cal
    ]
    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF,
        pdf_tokens_by_bill_id={b: frozenset({"x"}) for b in (1, 2, 4)},
    )
    kept, dropped = u697.partition_sample_ids(
        [1, 2, 2, 3, 4, 9],
        enriched=bills,
        token_config=cfg,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
    )
    assert kept == frozenset({2})
    assert dropped == {
        "not_loaded": 1,
        "not_in_pdf_population": 1,
        "calibration_window": 1,
        "outside_both_windows": 1,
    }


def _envelope(status: int, code: str, message: str = "Request rejected.") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


def test_error_code_from_envelope_never_the_message():
    secret_msg = "Access denied for key ending abcd on resource buildonefoundry"
    server = FakeServer(script=[_envelope(401, "PermissionDenied", secret_msg)])
    emb = _embedder(server)
    with pytest.raises(u697.EmbeddingError) as ei:
        emb.embed_texts(["lumber"])
    text = str(ei.value)
    assert "HTTP 401" in text and "error.code=PermissionDenied" in text
    for word in ("abcd", "ending", "buildonefoundry", "Access"):
        assert word not in text

    server2 = FakeServer(script=[httpx.Response(404, text="<html>not found page</html>")])
    with pytest.raises(u697.EmbeddingError) as ei2:
        _embedder(server2).embed_texts(["lumber"])
    assert "error.code=n/a" in str(ei2.value) and "html" not in str(ei2.value)

    server3 = FakeServer(script=[_envelope(400, "bad code with spaces")])
    with pytest.raises(u697.EmbeddingError) as ei3:
        _embedder(server3).embed_texts(["lumber"])
    assert "error.code=unparseable" in str(ei3.value)


def test_too_many_tokens_error_code_splits_the_batch():
    class Server(FakeServer):
        def handler(self, request):
            body = json.loads(request.content)
            if len(body["input"]) > 2:
                self.requests.append(body)
                return _envelope(400, "max_tokens_per_request")
            return super().handler(request)

    server = Server()
    emb = _embedder(server)
    vecs = emb.embed_texts(["a1", "b2", "c3", "d4", "e5"])
    assert len(vecs) == 5
    assert [len(r["input"]) for r in server.requests] == [5, 2, 3, 1, 2]
    assert emb.spend.retruncated == 0


def test_too_many_tokens_error_code_halves_a_single_input():
    class Server(FakeServer):
        def handler(self, request):
            body = json.loads(request.content)
            if len(body["input"][0]) > 400:
                self.requests.append(body)
                return _envelope(400, "context_length_exceeded")
            return super().handler(request)

    server = Server()
    emb = _embedder(server)
    emb.embed_texts(["q" * 1000])
    assert [len(r["input"][0]) for r in server.requests] == [1000, 500, 256]
    assert emb.spend.retruncated == 1


def test_w_reidentification_does_not_depend_on_the_hybrid(tmp_path):
    rc = u697.run_backtest(
        _args(tmp_path, "--run", "--no-hybrid"),
        load_population=_fake_population,
        embedder_factory=lambda: _embedder(FakeServer()),
        stamp="20261028T000002Z",
        log=lambda m: None,
    )
    assert rc == 0
    comp = json.loads(
        (tmp_path / "out" / "u697_comparison_20261028T000002Z.json").read_text()
    )
    assert "H" not in comp["head_to_head"]
    assert "W" in comp["vendor_reidentification"]
    assert comp["vendor_reidentification"]["W"]["neighbours"] > 0
    assert comp["extra"]["word_generic_parity_mismatches"] == 0


def test_rolling_word_arm_without_hybrid():
    bills, cfg, idf = _token_fixture()
    w = u691.score_all_rolling(bills, idf, top_k=3, band_pct=0.25, cap_ratio=4.0, token_config=cfg)
    vectors = {eb.bill.bill_id: _vec(1, 0) for eb in bills}
    vp = u697.score_vector_rolling(
        bills, vectors=vectors, history_source=w, token_config=cfg, idf=idf,
        top_k=3, band_pct=0.25, cap_ratio=4.0, include_hybrid=False,
    )
    assert u697.ARM_H not in vp.arms
    assert vp.reidentification[u697.ARM_W_GENERIC]
    with pytest.raises(ValueError):
        u697.score_vector_rolling(
            bills, vectors=vectors, history_source=w, token_config=cfg, idf=idf,
            top_k=3, band_pct=0.25, cap_ratio=4.0, include_word=False, include_hybrid=True,
        )


def test_every_resend_after_a_timeout_is_metered_against_the_cap():
    sent: list[int] = []

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        sent.append(1)
        raise httpx.ReadTimeout("timed out", request=request)

    emb = u697.FoundryEmbedder(
        endpoint=ENDPOINT,
        api_key="test-key",
        transport=httpx.MockTransport(timeout_handler),
        sleep=lambda s: None,
        # 3000 chars -> 1000 estimated tokens = $0.00002 per send; cap allows two sends
        max_cost_usd=Decimal("0.00005"),
    )
    with pytest.raises(u697.SpendCapExceeded):
        emb.embed_texts(["t" * 3000])
    assert len(sent) == 2  # the third re-send is refused before it leaves
    assert emb.spend.unconfirmed_attempts == 2
    assert emb.spend.prompt_tokens == 2000
    assert emb.spend.usd <= Decimal("0.00005")


def test_429_resends_are_reserved_but_not_charged():
    server = FakeServer(
        script=[httpx.Response(429, headers={"retry-after": "1"}, json={}) for _ in range(2)]
    )
    emb = _embedder(server, max_cost_usd=Decimal("0.00003"))  # one send's estimate fits
    emb.embed_texts(["t" * 3000])
    assert len(server.requests) == 3
    assert emb.spend.unconfirmed_attempts == 0
    assert emb.spend.prompt_tokens == 10


def test_a_200_with_an_unusable_body_is_charged_as_spent():
    sent: list[int] = []

    def garbled_handler(request: httpx.Request) -> httpx.Response:
        sent.append(1)
        return httpx.Response(200, content=b"not json")

    emb = u697.FoundryEmbedder(
        endpoint=ENDPOINT,
        api_key="test-key",
        transport=httpx.MockTransport(garbled_handler),
        sleep=lambda s: None,
        max_cost_usd=Decimal("1.00"),
    )
    with pytest.raises(u697.EmbeddingError):
        emb.embed_texts(["t" * 3000])
    assert len(sent) == 1
    assert emb.spend.unconfirmed_attempts == 1
    assert emb.spend.prompt_tokens == 1000


def test_a_200_with_too_few_vectors_is_charged_as_spent():
    def short_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "usage": {"prompt_tokens": 5}})

    emb = u697.FoundryEmbedder(
        endpoint=ENDPOINT,
        api_key="test-key",
        transport=httpx.MockTransport(short_handler),
        sleep=lambda s: None,
        max_cost_usd=Decimal("1.00"),
    )
    with pytest.raises(u697.EmbeddingError):
        emb.embed_texts(["t" * 3000])
    assert emb.spend.unconfirmed_attempts == 1


def test_azure_maximum_input_length_400_is_treated_as_context_length():
    # Live wording seen 2026-10-29, error.code is null.
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["input"][0]
        sizes.append(len(text))
        if len(text) > 1000:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Invalid 'input[0]': maximum input length is 8192 tokens.",
                        "type": "invalid_request_error",
                        "code": None,
                    }
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"prompt_tokens": 3}},
        )

    emb = u697.FoundryEmbedder(
        endpoint=ENDPOINT,
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        max_cost_usd=Decimal("1.00"),
    )
    vecs = emb.embed_texts(["t" * 3000])
    assert len(vecs) == 1
    assert sizes[0] == 3000 and sizes[-1] <= 1000
