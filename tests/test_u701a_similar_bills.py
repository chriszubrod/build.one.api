"""U-701a: similar bills module parity, hashing, service, shadow (pure / fakes)."""
from __future__ import annotations

import itertools
import json
import logging
import random
import string
import sys
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business import similar_bills as sb
from entities.coding_suggestion.business.bill_text_token_hash import (
    feature_enabled,
    hash_token,
    hash_token_int,
    token_hashes_from_storage,
    token_hashes_from_storage_int,
    token_hashes_from_text,
    token_hashes_from_text_int,
    token_hashes_to_storage,
    tokenizer_version_for_key,
    warn_once_if_disabled,
)
from entities.coding_suggestion.business.similar_bills_service import SimilarBillsService
from entities.attachment.business.text_token_hash_service import (
    AttachmentTextTokenHashService,
    persist_token_hashes_failure_isolated,
)
from entities.attachment.business.extraction_envelope import REDACTION_VERSION
from entities.bill_intake_prediction.business.service import BillIntakePredictionService

TEST_KEY = "u701a-unit-test-key-" + ("x" * 12)  # 32+ chars


@pytest.fixture(autouse=True)
def _u701a_isolate_process_globals():
    """Similar-bills and record_read tests touch module-level singletons."""
    yield
    SimilarBillsService._reset_shared_for_tests()
    from entities.bill.business import read_attachment_service as ras

    with ras._record_read_lock:
        ras._record_read_tasks.clear()


def _rand_words(n: int, rng: random.Random) -> str:
    return " ".join(
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(2, 8)))
        for _ in range(n)
    )


def _svc_with_repo(
    key: str = TEST_KEY,
    *,
    max_tokens_per_bill: int | None = None,
    refresh_seconds: int = 1800,
) -> tuple[SimilarBillsService, MagicMock]:
    SimilarBillsService._reset_shared_for_tests()
    svc = SimilarBillsService(
        settings=SimpleNamespace(
            bill_text_token_hash_key=key,
            similar_bills_max_tokens_per_bill=max_tokens_per_bill,
            similar_bills_refresh_seconds=refresh_seconds,
        )
    )
    repo = MagicMock()
    repo.read_for_attachment_ids.return_value = []
    svc._token_repo = repo
    return svc, repo


def _version(key: str = TEST_KEY) -> str:
    return tokenizer_version_for_key(key)


def test_tokenize_parity_randomized():
    rng = random.Random(70101)
    for _ in range(200):
        text = _rand_words(rng.randint(0, 40), rng)
        a = sb.tokenize_bill_text(text)
        from scripts import u691_similar_bills_backtest as u691

        b = u691.tokenize_bill_text(text)
        assert a == b


def test_hash_stable_and_key_sensitive():
    assert hash_token("invoice", "key-a") == hash_token("invoice", "key-a")
    assert hash_token("invoice", "key-a") != hash_token("invoice", "key-b")
    assert hash_token("invoice", "key-a") != hash_token("payment", "key-a")


def test_key_fingerprint_in_tokenizer_version():
    v1 = tokenizer_version_for_key(TEST_KEY)
    v2 = tokenizer_version_for_key(TEST_KEY + "y")
    assert v1 != v2
    assert v1.startswith("u701a-v1:")
    assert len(v1.split(":", 1)[1]) == 8  # 4-byte fingerprint: a rotated key colliding is ~1 in 4 billion
    assert len(v1) <= 32  # TokenizerVersion is NVARCHAR(32)


def test_feature_disabled_without_key(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", "")
    import config

    cfg = config.Settings()
    assert feature_enabled(cfg) is False
    warn_once_if_disabled()


def test_feature_disabled_short_key(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", "too-short")
    import config

    assert feature_enabled(config.Settings()) is False


def test_no_plaintext_in_storage():
    tokens = token_hashes_from_text("Invoice payment drywall 12345", TEST_KEY)
    stored, count = token_hashes_to_storage(tokens)
    assert count == len(tokens)
    assert "invoice" not in stored.lower()
    assert "drywall" not in stored.lower()
    assert token_hashes_from_storage(stored) == tokens


def test_extraction_hook_never_raises_via_real_path(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from entities.attachment.business.extraction_service import AttachmentExtractionService

    envelope = {
        "attachment_public_id": "00000000-0000-0000-0000-000000000001",
        "source_file_hash": "abc",
        "redaction_version": REDACTION_VERSION,
        "method": "text_layer",
        "pages_count": 1,
        "content": "Vendor invoice drywall payment",
        "key_value_pairs": [],
    }
    att = SimpleNamespace(id=1, is_taxpayer_document=False)
    svc = AttachmentExtractionService(repo=MagicMock(), storage=MagicMock())
    with patch.object(
        AttachmentTextTokenHashService,
        "upsert_from_extraction_envelope",
        side_effect=RuntimeError("boom"),
    ):
        svc._maybe_persist_token_hashes(
            att, {"status": "completed"}, envelope, is_taxpayer_document=att.is_taxpayer_document
        )


def test_taxpayer_attachment_skips_token_write(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from entities.attachment.business.extraction_service import AttachmentExtractionService

    repo = MagicMock()
    repo.read_link_state.return_value = (True, False)
    svc = AttachmentExtractionService(repo=repo, storage=MagicMock())
    with patch(
        "entities.attachment.business.extraction_service.persist_token_hashes_failure_isolated"
    ) as persist:
        att = SimpleNamespace(id=1, is_taxpayer_document=True)
        svc._maybe_persist_token_hashes(
            att, {"status": "completed"}, {"content": "x"}, is_taxpayer_document=True
        )
        persist.assert_not_called()

        att2 = SimpleNamespace(id=2, is_taxpayer_document=None)
        svc._maybe_persist_token_hashes(
            att2, {"status": "completed"}, {"content": "y"}, is_taxpayer_document=None
        )
        repo.read_link_state.assert_called_with(2)
        persist.assert_called_once()


def test_bill_drop_non_taxpayer_persists_hashes(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from entities.attachment.business.extraction_service import AttachmentExtractionService

    att = SimpleNamespace(
        id=5,
        public_id="00000000-0000-0000-0000-000000000005",
        blob_url="https://blob/x",
        content_type="application/pdf",
        file_hash="abc",
        extraction_status=None,
        is_taxpayer_document=None,
        extracted_text_blob_url=None,
        parse_invoice_number=True,
    )
    repo = MagicMock()
    repo.read_by_id.return_value = att
    storage = MagicMock()
    storage.download_file.return_value = (b"%PDF-1.4\ninvoice drywall payment", {})
    storage.upload_file.return_value = "https://blob/extraction.json"
    repo.update_extraction = MagicMock()
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    envelope = {"content": "invoice drywall payment vendor", "redaction_version": REDACTION_VERSION}
    with patch.object(svc, "_run_di", return_value={"content": "", "pages_count": 0, "key_value_pairs": []}), patch(
        "entities.attachment.business.extraction_service._extract_text_layer",
        return_value=("invoice drywall payment vendor", 1),
    ), patch(
        "entities.attachment.business.extraction_service.persist_token_hashes_failure_isolated"
    ) as persist, patch.object(
        svc, "_commit_terminal", return_value={"status": "completed", "attachment_id": 5}
    ), patch.object(
        svc, "_persist_bill_drop_extraction_blob",
        return_value=("b", "https://blob/e.json", "masked", envelope),
    ):
        svc.extract_for_bill_drop_prefill(att, is_taxpayer_document=False)
    persist.assert_called_once()

    # Same real drop function for a TAXPAYER document: the known flag must reach the hook and
    # no hashes may be written (privacy: a W-9 is never indexed).
    with patch.object(svc, "_run_di", return_value={"content": "", "pages_count": 0, "key_value_pairs": []}), patch(
        "entities.attachment.business.extraction_service._extract_text_layer",
        return_value=("invoice drywall payment vendor", 1),
    ), patch(
        "entities.attachment.business.extraction_service.persist_token_hashes_failure_isolated"
    ) as persist2, patch.object(
        svc, "_commit_terminal", return_value={"status": "completed", "attachment_id": 5}
    ), patch.object(
        svc, "_persist_bill_drop_extraction_blob",
        return_value=("b", "https://blob/e.json", "masked", envelope),
    ):
        svc.extract_for_bill_drop_prefill(att, is_taxpayer_document=True)
    persist2.assert_not_called()
    repo.read_link_state.assert_not_called()  # the known flag is used, not re-resolved


def test_idf_on_hashes_matches_words_value_by_value():
    words = ["alpha", "beta", "gamma", "delta"]
    doc_sets_words = [
        frozenset([words[0], words[1]]),
        frozenset([words[1], words[2]]),
        frozenset([words[0], words[3]]),
    ]
    doc_sets_hashes = [
        frozenset(hash_token_int(w, TEST_KEY) for w in s) for s in doc_sets_words
    ]
    idf_w = sb.compute_idf_from_doc_token_sets(doc_sets_words)
    idf_h = sb.compute_idf_from_doc_token_sets(doc_sets_hashes)
    assert len(idf_w) == len(idf_h) == 4
    for w, idf_val in idf_w.items():
        assert idf_h[hash_token_int(w, TEST_KEY)] == idf_val


def _seed_corpus(repo, *, t0, key=TEST_KEY, extra_corpus=None, extra_hashes=None):
    shared = token_hashes_from_text("drywall invoice vendor payment", key)
    tok_a = " ".join(sorted(shared))
    extra_b = next(iter(token_hashes_from_text("betaunique", key)))
    tok_b = tok_a + " " + extra_b
    noise_stored, _ = token_hashes_to_storage(token_hashes_from_text("plumbing uniquewords", key))
    ver = _version(key)
    corpus = extra_corpus or [
        SimpleNamespace(
            AttachmentId=10,
            BillId=1,
            VendorId=5,
            CreatedDatetime=t0,
            DominantSubCostCodeId=100,
            DominantProjectId=1,
            TotalAbsAmount=Decimal("100"),
        ),
        SimpleNamespace(
            AttachmentId=11,
            BillId=2,
            VendorId=5,
            CreatedDatetime=t0 + timedelta(days=1),
            DominantSubCostCodeId=200,
            DominantProjectId=1,
            TotalAbsAmount=Decimal("110"),
        ),
        SimpleNamespace(
            AttachmentId=12,
            BillId=3,
            VendorId=6,
            CreatedDatetime=t0 + timedelta(days=2),
            DominantSubCostCodeId=300,
            DominantProjectId=1,
            TotalAbsAmount=Decimal("50"),
        ),
    ]
    hashes = extra_hashes or [
        SimpleNamespace(
            AttachmentId=10,
            TokenizerVersion=ver,
            TokenHashes=tok_a,
            TokenCount=len(shared),
            ModifiedDatetime=t0,
        ),
        SimpleNamespace(
            AttachmentId=11,
            TokenizerVersion=ver,
            TokenHashes=tok_b,
            TokenCount=len(token_hashes_from_storage(tok_b)),
            ModifiedDatetime=t0 + timedelta(days=1),
        ),
        SimpleNamespace(
            AttachmentId=12,
            TokenizerVersion=ver,
            TokenHashes=noise_stored,
            TokenCount=3,
            ModifiedDatetime=t0 + timedelta(days=2),
        ),
    ]
    repo.read_corpus_rows.return_value = corpus
    repo.read_since.return_value = hashes
    return shared, tok_b


def test_service_strictly_earlier_never_self():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    shared, tok_b = _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    assert svc.cache_warm

    query_time = t0 + timedelta(days=4)
    result = svc.similar_bills_for(
        bill_text_hashes=token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY),
        amount=Decimal("105"),
        vendor_id=5,
        exclude_bill_id=2,
        as_of=query_time,
    )
    assert result is not None
    assert result.neighbours
    assert result.predicted_scc_id == 100

    self_result = svc.similar_bills_for(
        bill_text_hashes=token_hashes_from_storage_int(tok_b),
        amount=Decimal("110"),
        vendor_id=5,
        exclude_bill_id=2,
        as_of=t0 + timedelta(days=1, hours=1),
    )
    assert self_result is not None
    assert all(n.scc_id != 200 for n in self_result.neighbours)


def test_service_later_bill_excluded():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    result = svc.similar_bills_for(
        bill_text_hashes=token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY),
        amount=Decimal("105"),
        vendor_id=5,
        exclude_bill_id=None,
        as_of=t0 + timedelta(hours=12),
    )
    assert result is not None
    assert all(n.scc_id != 200 for n in result.neighbours)


def test_bill_completed_after_first_load_appears_on_refresh():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    shared, _ = _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    new_row = SimpleNamespace(
        AttachmentId=20,
        BillId=4,
        VendorId=5,
        CreatedDatetime=t0 + timedelta(days=3),
        DominantSubCostCodeId=400,
        DominantProjectId=1,
        TotalAbsAmount=Decimal("100"),
    )
    ver = _version()
    tok_new, _ = token_hashes_to_storage(shared)
    repo.read_corpus_rows.return_value = list(repo.read_corpus_rows.return_value) + [new_row]
    repo.read_since.return_value = [
        SimpleNamespace(
            AttachmentId=20,
            TokenizerVersion=ver,
            TokenHashes=tok_new,
            TokenCount=len(shared),
            ModifiedDatetime=t0 + timedelta(days=3),
        )
    ]
    svc.refresh_incremental()
    result = svc.similar_bills_for(
        bill_text_hashes=token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY),
        amount=Decimal("100"),
        vendor_id=5,
        as_of=t0 + timedelta(days=10),
    )
    assert result is not None
    assert 400 in {n.scc_id for n in result.neighbours}


def test_bill_leaving_corpus_removed_on_refresh():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    repo.read_corpus_rows.return_value = repo.read_corpus_rows.return_value[:2]
    repo.read_since.return_value = []
    svc.refresh_incremental()
    with svc._state_lock:
        assert 3 not in svc._snapshot.profiles_by_bill


def test_shared_attachment_two_bills():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    shared, _ = _seed_corpus(repo, t0=t0)
    ver = _version()
    tok, _ = token_hashes_to_storage(shared)
    repo.read_corpus_rows.return_value = [
        SimpleNamespace(
            AttachmentId=10,
            BillId=1,
            VendorId=5,
            CreatedDatetime=t0,
            DominantSubCostCodeId=100,
            DominantProjectId=1,
            TotalAbsAmount=Decimal("100"),
        ),
        SimpleNamespace(
            AttachmentId=10,
            BillId=2,
            VendorId=5,
            CreatedDatetime=t0 + timedelta(days=1),
            DominantSubCostCodeId=200,
            DominantProjectId=1,
            TotalAbsAmount=Decimal("100"),
        ),
    ]
    repo.read_since.return_value = [
        SimpleNamespace(
            AttachmentId=10,
            TokenizerVersion=ver,
            TokenHashes=tok,
            TokenCount=len(shared),
            ModifiedDatetime=t0,
        )
    ]
    svc.refresh_incremental()
    with svc._state_lock:
        assert set(svc._snapshot.profiles_by_bill) == {1, 2}


def test_cold_query_never_blocks_on_inline_refresh():
    svc, repo = _svc_with_repo()
    t0 = datetime(2026, 1, 1)
    _seed_corpus(repo, t0=t0)

    def slow_corpus(**_kw):
        time.sleep(2)
        return repo.read_corpus_rows.return_value

    repo.read_corpus_rows.side_effect = slow_corpus
    svc._snapshot = None
    start = time.perf_counter()
    assert (
        svc.similar_bills_for(
            bill_text_hashes=frozenset({1}),
            amount=Decimal("1"),
            as_of=t0 + timedelta(days=10),
        )
        is None
    )
    assert time.perf_counter() - start < 0.5


def test_cold_query_returns_before_background_refresh_finishes():
    svc, repo = _svc_with_repo()
    t0 = datetime(2026, 1, 1)
    _seed_corpus(repo, t0=t0)
    started = threading.Event()
    release = threading.Event()

    def slow_refresh_corpus(**_kw):
        started.set()
        release.wait(timeout=5)
        return repo.read_corpus_rows.return_value

    repo.read_corpus_rows.side_effect = slow_refresh_corpus
    svc._snapshot = None
    svc._kick_background_refresh()
    assert started.wait(timeout=2)
    result = svc.similar_bills_for(
        bill_text_hashes=frozenset({1, 2}),
        amount=Decimal("10"),
        as_of=t0 + timedelta(days=10),
    )
    assert result is None
    assert svc._refresh_running is True
    release.set()
    time.sleep(0.4)
    assert svc.cache_warm


def test_single_flight_background_refresh():
    svc, repo = _svc_with_repo()
    t0 = datetime(2026, 1, 1)
    _seed_corpus(repo, t0=t0)
    started = threading.Event()
    release = threading.Event()

    def slow_corpus(**_kw):
        started.set()
        release.wait(timeout=5)
        return repo.read_corpus_rows.return_value

    repo.read_corpus_rows.side_effect = slow_corpus
    svc._snapshot = None
    svc._kick_background_refresh()
    assert started.wait(timeout=2)
    in_flight = svc._refresh_running
    svc._kick_background_refresh()
    assert in_flight is True
    release.set()
    time.sleep(0.3)
    assert svc.cache_warm
    assert repo.read_corpus_rows.call_count == 1  # the second kick did not start a refresh


def _skewed_words(rng: random.Random, n_tokens: int, *, common_pool: list[str]) -> str:
    words = []
    for _ in range(n_tokens):
        if rng.random() < 0.6:
            words.append(rng.choice(common_pool))
        else:
            words.append(_rand_words(1, rng))
    return " ".join(words)


def test_cap_tokens_by_idf_deterministic_ties():
    idf = {"a": 1.0, "b": 2.0, "c": 2.0, "d": 0.5}
    capped = sb.cap_tokens_by_idf(frozenset(idf), idf, 2)
    assert capped == frozenset({"b", "c"})


def test_warm_query_perf_skewed_corpus_cap_50():
    key = TEST_KEY
    rng = random.Random(70199)
    t0 = datetime(2020, 1, 1)
    n_bills = 4000
    n_tokens = 323
    common_pool = [_rand_words(1, rng) for _ in range(3000)]
    corpus = []
    hashes = []
    ver = _version(key)
    all_token_ints: list[int] = []
    for bid in range(1, n_bills + 1):
        text = _skewed_words(rng, n_tokens, common_pool=common_pool)
        toks = token_hashes_from_text(text, key)
        stored, _ = token_hashes_to_storage(toks)
        corpus.append(
            SimpleNamespace(
                AttachmentId=bid,
                BillId=bid,
                VendorId=bid % 50,
                CreatedDatetime=t0 + timedelta(days=bid),
                DominantSubCostCodeId=100 + (bid % 20),
                DominantProjectId=1,
                TotalAbsAmount=Decimal("1000"),
            )
        )
        hashes.append(
            SimpleNamespace(
                AttachmentId=bid,
                TokenizerVersion=ver,
                TokenHashes=stored,
                TokenCount=len(toks),
                ModifiedDatetime=t0 + timedelta(days=bid),
            )
        )
        if bid == n_bills:
            all_token_ints = list(token_hashes_from_storage_int(stored))

    svc, repo = _svc_with_repo(key, max_tokens_per_bill=50)
    repo.read_corpus_rows.return_value = corpus
    repo.read_since.return_value = hashes
    start_refresh = time.perf_counter()
    svc.refresh_incremental()
    refresh_ms = (time.perf_counter() - start_refresh) * 1000
    query_tokens = frozenset(all_token_ints[:323])
    start = time.perf_counter()
    result = svc.similar_bills_for(
        bill_text_hashes=query_tokens,
        amount=Decimal("1000"),
        vendor_id=1,
        as_of=t0 + timedelta(days=n_bills + 10),
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert result is not None
    assert elapsed_ms < 100, f"warm query took {elapsed_ms:.1f}ms (refresh {refresh_ms:.1f}ms)"
    snap = svc._snapshot
    mem_bytes = sys.getsizeof(snap.idf) + sys.getsizeof(snap.token_index) + sys.getsizeof(snap.profiles_by_bill)
    assert mem_bytes > 0


def test_refresh_backoff_limits_failed_attempts():
    svc, repo = _svc_with_repo()
    repo.read_corpus_rows.side_effect = RuntimeError("db down")
    repo.read_since.return_value = []
    svc._schedule_refresh_if_needed(force=True)
    time.sleep(0.15)
    for _ in range(4):
        svc._schedule_refresh_if_needed(force=True)
    assert repo.read_corpus_rows.call_count == 1


def test_empty_corpus_snapshot_does_not_refresh_each_query():
    svc, repo = _svc_with_repo()
    repo.read_corpus_rows.return_value = []
    repo.read_since.return_value = []
    svc.refresh_incremental()
    assert svc.snapshot_ready
    assert not svc.cache_warm
    with patch.object(svc, "_kick_background_refresh") as kick:
        assert svc.similar_bills_for(
            bill_text_hashes=frozenset({1}),
            amount=Decimal("1"),
            as_of=datetime(2026, 1, 1),
        ) is None
        kick.assert_not_called()


def test_query_token_cap_uses_idf():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo(max_tokens_per_bill=2)
    shared, _ = _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    many = token_hashes_from_text_int(
        "drywall invoice vendor payment extrauniqueword", TEST_KEY
    )
    import entities.coding_suggestion.business.similar_bills_service as svc_mod

    with patch.object(
        svc_mod, "top_neighbours_any_vendor", wraps=svc_mod.top_neighbours_any_vendor
    ) as scorer:
        result = svc.similar_bills_for(
            bill_text_hashes=many,
            amount=Decimal("100"),
            as_of=t0 + timedelta(days=10),
        )
    assert result is not None
    with svc._state_lock:
        idf = svc._snapshot.idf
    query_prof = scorer.call_args.args[0]
    expected = sb.cap_tokens_by_idf(many, idf, 2)
    assert len(many) > 2 and len(query_prof.tokens) == 2
    assert query_prof.tokens == expected  # the capped set (highest IDF, value tie-break) is what is scored


def test_recode_applied_after_refresh():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    shared, tok_b = _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    recoded, _ = token_hashes_to_storage(token_hashes_from_text("recode unique tokens", TEST_KEY))
    ver = _version()
    repo.read_since.return_value = [
        SimpleNamespace(
            AttachmentId=10,
            TokenizerVersion=ver,
            TokenHashes=recoded,
            TokenCount=5,
            ModifiedDatetime=t0 + timedelta(days=5),
        )
    ]
    svc.refresh_incremental()
    with svc._state_lock:
        stored = svc._hash_by_attachment[10]
    assert stored != token_hashes_from_storage_int(" ".join(sorted(shared)))


def test_watermark_overlap_picks_late_row():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    svc._since_watermark = t0 + timedelta(days=10)
    ver = _version()
    late_tok, _ = token_hashes_to_storage(token_hashes_from_text("late commit hash set", TEST_KEY))
    repo.read_since.return_value = [
        SimpleNamespace(
            AttachmentId=10,
            TokenizerVersion=ver,
            TokenHashes=late_tok,
            TokenCount=4,
            ModifiedDatetime=t0 + timedelta(days=1),
        )
    ]
    svc.refresh_incremental()
    with svc._state_lock:
        assert svc._hash_by_attachment[10] == token_hashes_from_storage_int(late_tok)


def test_shadow_abs_amount_and_no_vendor():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    scc, conf = svc.shadow_predict_with_budget(
        extraction_content="drywall invoice vendor payment",
        amount=Decimal("-105"),
        vendor_id=None,
        as_of=t0 + timedelta(days=10),
    )
    assert scc == 100
    assert conf is not None


def test_refresh_skips_null_vendor_and_malformed_hex(caplog):
    import logging

    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    ver = _version()
    repo.read_corpus_rows.return_value = list(repo.read_corpus_rows.return_value) + [
        SimpleNamespace(
            AttachmentId=12,
            BillId=99,
            VendorId=None,
            CreatedDatetime=t0,
            DominantSubCostCodeId=1,
            DominantProjectId=1,
            TotalAbsAmount=Decimal("1"),
        )
    ]
    repo.read_since.return_value = list(repo.read_since.return_value) + [
        SimpleNamespace(
            AttachmentId=11,
            TokenizerVersion=ver,
            TokenHashes="not-valid-hex!",
            TokenCount=1,
            ModifiedDatetime=t0,
        )
    ]
    with caplog.at_level(logging.WARNING):
        svc.refresh_incremental()
    assert svc.cache_warm
    with svc._state_lock:
        bills = set(svc._snapshot.profiles_by_bill)
    assert bills == {1, 2, 3}  # bill 99 (NULL vendor, real tokens) skipped; good rows kept
    assert "skipped_malformed_corpus_rows count=1" in caplog.text


def test_gap_fill_uses_set_based_read_with_tokenizer_version():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    row = SimpleNamespace(
        AttachmentId=10,
        BillId=1,
        VendorId=5,
        CreatedDatetime=t0,
        DominantSubCostCodeId=100,
        DominantProjectId=1,
        TotalAbsAmount=Decimal("100"),
    )
    repo.read_corpus_rows.return_value = [row]
    repo.read_since.return_value = []
    ver = _version()
    repo.read_for_attachment_ids.return_value = [
        SimpleNamespace(
            AttachmentId=10,
            TokenizerVersion=ver,
            TokenHashes="abc",
            TokenCount=1,
            ModifiedDatetime=t0,
        )
    ]
    svc.refresh_incremental()
    repo.read_for_attachment_ids.assert_called_once_with([10], tokenizer_version=ver)


def test_hash_map_keeps_only_corpus_attachments():
    """Hash rows outside the corpus are not retained; a pruned one is re-read once its bill joins."""
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    ver = _version()
    draft_tok, _ = token_hashes_to_storage(token_hashes_from_text("draft only words", TEST_KEY))
    draft_hash = SimpleNamespace(
        AttachmentId=77, TokenizerVersion=ver, TokenHashes=draft_tok, TokenCount=3, ModifiedDatetime=t0
    )
    repo.read_since.return_value = list(repo.read_since.return_value) + [draft_hash]
    svc.refresh_incremental()
    assert set(svc._hash_by_attachment) == {10, 11, 12}

    repo.read_corpus_rows.return_value = list(repo.read_corpus_rows.return_value) + [
        SimpleNamespace(
            AttachmentId=77, BillId=8, VendorId=5, CreatedDatetime=t0,
            DominantSubCostCodeId=100, DominantProjectId=1, TotalAbsAmount=Decimal("1"),
        )
    ]
    repo.read_since.return_value = []
    repo.read_for_attachment_ids.return_value = [draft_hash]
    svc.refresh_incremental()
    repo.read_for_attachment_ids.assert_called_with([77], tokenizer_version=ver)
    with svc._state_lock:
        assert 8 in svc._snapshot.profiles_by_bill


def test_old_version_hash_row_never_replaces_current_tokens():
    """A hash row written under another key/tokenizer version is ignored, never merged."""
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    with svc._state_lock:
        before = svc._hash_by_attachment[10]
    old_tok, _ = token_hashes_to_storage(token_hashes_from_text("rotated key words", "k" * 40))
    repo.read_since.return_value = [
        SimpleNamespace(
            AttachmentId=10, TokenizerVersion="u701a-v1:00000000", TokenHashes=old_tok,
            TokenCount=3, ModifiedDatetime=t0 + timedelta(days=30),
        )
    ]
    svc.refresh_incremental()
    with svc._state_lock:
        assert svc._hash_by_attachment[10] == before


def test_cold_shadow_kicks_refresh_without_hashing_the_pdf():
    svc, _repo = _svc_with_repo()
    with patch.object(svc, "_kick_background_refresh") as kick, patch(
        "entities.coding_suggestion.business.similar_bills_service.token_hashes_from_text_int"
    ) as hasher:
        out = svc.shadow_predict_with_budget(
            extraction_content="drywall invoice", amount=Decimal("10"), as_of=datetime(2026, 1, 1)
        )
    assert out == (None, None)
    kick.assert_called_once()
    hasher.assert_not_called()


def test_neighbour_display_fields():
    """match / amount_ratio / same_vendor are computed for each returned neighbour."""
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    shared, _tok_b = _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    result = svc.similar_bills_for(
        bill_text_hashes=frozenset(token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)),
        amount=Decimal("100"),
        vendor_id=6,
        as_of=t0 + timedelta(days=10),
    )
    assert result is not None and result.neighbours
    for n in result.neighbours:
        assert n.match == 1.0  # every query token appears in bills 1 and 2
        assert n.same_vendor is False  # both are vendor 5; the query is vendor 6
        assert n.amount_ratio is not None and n.amount_ratio > 0


def test_repo_read_for_attachment_ids_is_one_call():
    """The gap fill is one set-based sproc call with the ids as a CSV, never a per-id loop."""
    from entities.attachment.persistence import text_token_hash_repo as repo_mod

    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.fetchall.return_value = ["r"]
    with patch.object(repo_mod, "get_connection", return_value=conn), patch.object(
        repo_mod, "call_procedure"
    ) as call:
        rows = repo_mod.AttachmentTextTokenHashRepository().read_for_attachment_ids(
            [3, 1, 2], tokenizer_version="u701a-v1:abcd"
        )
    assert rows == ["r"]
    call.assert_called_once()
    assert call.call_args.kwargs["name"] == "ReadAttachmentTextTokenHashesForAttachmentIds"
    assert call.call_args.kwargs["params"] == {
        "AttachmentIds": "3,1,2",
        "TokenizerVersion": "u701a-v1:abcd",
    }


def test_every_sixth_refresh_rereads_all_hashes():
    """Watermark reads catch new rows; every 6th refresh re-reads everything so deletes/rewrites
    the watermark missed are reconciled."""
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    floor = datetime(2000, 1, 1)
    for _ in range(12):
        svc.refresh_incremental()
    sinces = [c.args[0] for c in repo.read_since.call_args_list]
    assert len(sinces) == 12
    full = [i + 1 for i, since in enumerate(sinces) if since == floor]
    # refresh 1 is cold (watermark unset); 6 and 12 are the scheduled full re-reads
    assert full == [1, 6, 12]


def test_record_read_skips_shadow_when_feature_disabled(monkeypatch):
    monkeypatch.delenv("BILL_TEXT_TOKEN_HASH_KEY", raising=False)
    intake = BillIntakePredictionService(repo=MagicMock())
    intake.build_upsert_params_from_read = MagicMock(return_value={"AttachmentId": 9})
    intake.repo.upsert.return_value = SimpleNamespace(id=1)
    with patch.object(
        BillIntakePredictionService,
        "_shadow_similar_prediction",
        return_value=(42, Decimal("0.8")),
    ) as shadow:
        intake.record_read(
            attachment_id=9,
            intake_source="pdf_drop",
            status="ok",
            timing_ms={"total": 1},
            fields={},
            suggestion=None,
            chosen_project_id=None,
            header_cascade=None,
            coding_cascade=None,
            header_model=None,
            vendor_id=1,
            vendor_extracted_name=None,
            shadow_extraction_content="text",
            shadow_amount=Decimal("10"),
            shadow_as_of=datetime(2026, 1, 1),
        )
    shadow.assert_not_called()
    intake.repo.update_similar_shadow.assert_not_called()


def test_record_read_upsert_then_shadow_narrow_write(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    intake = BillIntakePredictionService(repo=MagicMock())
    intake.build_upsert_params_from_read = MagicMock(return_value={"AttachmentId": 9})
    intake.repo.upsert.return_value = SimpleNamespace(id=1)
    intake.repo.update_similar_shadow.return_value = True
    with patch.object(
        BillIntakePredictionService,
        "_shadow_similar_prediction",
        return_value=(42, Decimal("0.8")),
    ):
        intake.record_read(
            attachment_id=9,
            intake_source="pdf_drop",
            status="ok",
            timing_ms={"total": 1},
            fields={},
            suggestion=None,
            chosen_project_id=None,
            header_cascade=None,
            coding_cascade=None,
            header_model=None,
            vendor_id=1,
            vendor_extracted_name=None,
            shadow_extraction_content="text",
            shadow_amount=Decimal("10"),
            shadow_as_of=datetime(2026, 1, 1),
        )
    intake.repo.upsert.assert_called_once()
    intake.repo.update_similar_shadow.assert_called_once_with(
        attachment_id=9,
        similar_pred_sub_cost_code_id=42,
        similar_confidence=Decimal("0.8"),
    )


def test_record_read_prediction_survives_shadow_failure(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    intake = BillIntakePredictionService(repo=MagicMock())
    intake.build_upsert_params_from_read = MagicMock(return_value={"AttachmentId": 9})
    intake.repo.upsert.return_value = SimpleNamespace(id=1)
    intake.repo.update_similar_shadow.side_effect = RuntimeError("missing column")
    with patch.object(
        BillIntakePredictionService,
        "_shadow_similar_prediction",
        return_value=(1, Decimal("0.5")),
    ):
        intake.record_read(
            attachment_id=9,
            intake_source="pdf_drop",
            status="ok",
            timing_ms={"total": 1},
            fields={},
            suggestion=None,
            chosen_project_id=None,
            header_cascade=None,
            coding_cascade=None,
            header_model=None,
            vendor_id=1,
            vendor_extracted_name=None,
            shadow_extraction_content="t",
            shadow_amount=Decimal("1"),
            shadow_as_of=datetime(2026, 1, 1),
        )
    intake.repo.upsert.assert_called_once()
    intake.repo.update_similar_shadow.assert_called_once()  # the failing write really ran


def test_backfill_dry_run_counts_only(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from scripts import u701a_backfill_token_hashes as bf

    with patch.object(bf, "assert_cli_system_admin"), patch.object(
        bf, "get_connection"
    ) as gc:
        conn = MagicMock()
        gc.return_value.__enter__.return_value = conn
        conn.cursor.return_value.fetchall.return_value = [
            SimpleNamespace(Id=1, ExtractedTextBlobUrl="url")
        ]
        assert bf.main([]) == 0


def test_schedule_record_read_does_not_mutate_drop_payload(monkeypatch):
    # Feature off at its single switch (env AND .env-backed settings): never let this test start the
    # real singleton, whose repo reads the worktree .env.
    from entities.coding_suggestion.business import bill_text_token_hash as tth

    monkeypatch.setattr(tth, "feature_enabled", lambda settings=None: False)
    from entities.bill.business.read_attachment_service import _schedule_record_read

    intake = BillIntakePredictionService(repo=MagicMock())
    payload = {"status": "ok", "fields": {"total_amount": {"value": "10"}}}
    frozen = json.dumps(payload, sort_keys=True)
    def _submit_sync(_ctx, fn):
        fn()
        done = Future()
        done.set_result(None)
        return done

    with patch(
        "entities.bill.business.read_attachment_service._record_read_executor"
    ) as pool:
        pool.submit.side_effect = _submit_sync
        _schedule_record_read(
            intake,
            attachment_id=1,
            intake_source="pdf_drop",
            status="ok",
            timing_ms={"total": 1},
            fields=payload["fields"],
            suggestion=None,
            chosen_project_id=None,
            header_cascade=None,
            coding_cascade=None,
            header_model=None,
            vendor_id=1,
            vendor_extracted_name=None,
            shadow_extraction_content="invoice text",
            shadow_vendor_id=1,
            shadow_amount=Decimal("10"),
            shadow_as_of=datetime(2026, 1, 1),
        )
    assert json.dumps(payload, sort_keys=True) == frozen


def test_record_read_reread_without_shadow_writes_nulls(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    intake = BillIntakePredictionService(repo=MagicMock())
    intake.build_upsert_params_from_read = MagicMock(return_value={"AttachmentId": 9})
    intake.repo.upsert.return_value = SimpleNamespace(id=1)
    with patch.object(
        BillIntakePredictionService,
        "_shadow_similar_prediction",
        return_value=(None, None),
    ):
        intake.record_read(
            attachment_id=9,
            intake_source="reread",
            status="ok",
            timing_ms={"total": 1},
            fields={},
            suggestion=None,
            chosen_project_id=None,
            header_cascade=None,
            coding_cascade=None,
            header_model=None,
            vendor_id=1,
            vendor_extracted_name=None,
            shadow_extraction_content="text",
            shadow_amount=Decimal("10"),
            shadow_as_of=datetime(2026, 1, 1),
        )
    intake.repo.update_similar_shadow.assert_called_once_with(
        attachment_id=9,
        similar_pred_sub_cost_code_id=None,
        similar_confidence=None,
    )


_SENSITIVE_PDF_SNIPPET = "SECRET_INVOICE_DRYWALL_PAYMENT_701d"


def _shadow_messages(caplog) -> list[str]:
    return [r.message for r in caplog.records if r.message.startswith("similar_bills.shadow")]


def test_shadow_log_no_text(caplog):
    with caplog.at_level(logging.INFO):
        BillIntakePredictionService._shadow_similar_prediction(
            attachment_id=42,
            shadow_extraction_content=None,
            shadow_amount=Decimal("10"),
            shadow_as_of=datetime(2026, 1, 1),
        )
    assert any("reason=no_text" in m and "attachment_id=42" in m for m in _shadow_messages(caplog))
    assert _SENSITIVE_PDF_SNIPPET not in caplog.text


def test_shadow_log_no_amount_unparseable(caplog):
    with caplog.at_level(logging.INFO):
        BillIntakePredictionService._shadow_similar_prediction(
            attachment_id=7,
            shadow_extraction_content=_SENSITIVE_PDF_SNIPPET,
            shadow_amount="not-a-decimal",
            shadow_as_of=datetime(2026, 1, 1),
        )
    assert any("reason=no_amount" in m and "attachment_id=7" in m for m in _shadow_messages(caplog))
    assert _SENSITIVE_PDF_SNIPPET not in caplog.text


def test_shadow_log_cold(caplog):
    svc, _repo = _svc_with_repo()
    with caplog.at_level(logging.INFO):
        svc.shadow_predict_with_budget(
            extraction_content=_SENSITIVE_PDF_SNIPPET,
            amount=Decimal("10"),
            as_of=datetime(2026, 1, 1),
            attachment_id=99,
        )
    assert any("reason=cold" in m and "attachment_id=99" in m for m in _shadow_messages(caplog))
    assert _SENSITIVE_PDF_SNIPPET not in caplog.text


def test_shadow_log_empty_corpus(caplog):
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    repo.read_corpus_rows.return_value = []
    repo.read_since.return_value = []
    svc.refresh_incremental()
    assert svc.snapshot_ready and not svc.cache_warm
    with caplog.at_level(logging.INFO):
        svc.shadow_predict_with_budget(
            extraction_content="anything",
            amount=Decimal("10"),
            as_of=t0,
            attachment_id=55,
        )
    assert any(
        "reason=empty_corpus" in m and "attachment_id=55" in m for m in _shadow_messages(caplog)
    )


def test_shadow_log_over_budget_discards_pick(caplog):
    t0 = datetime(2026, 1, 1)
    SimilarBillsService._reset_shared_for_tests()
    svc = SimilarBillsService(
        settings=SimpleNamespace(
            bill_text_token_hash_key=TEST_KEY,
            similar_bills_shadow_budget_ms=100,
        )
    )
    repo = MagicMock()
    repo.read_for_attachment_ids.return_value = []
    svc._token_repo = repo
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    import entities.coding_suggestion.business.similar_bills_service as svc_mod

    # start, refresh-schedule check, elapsed — then hold the last reading so an extra clock read cannot raise.
    times = itertools.chain([100.0, 100.0, 100.25], itertools.repeat(100.25))
    with caplog.at_level(logging.INFO), patch.object(
        svc_mod.time, "monotonic", side_effect=lambda: next(times)
    ):
        scc, conf = svc.shadow_predict_with_budget(
            extraction_content="drywall invoice vendor payment",
            amount=Decimal("105"),
            as_of=t0 + timedelta(days=10),
            attachment_id=12,
        )
    assert scc is None and conf is None
    over = [m for m in _shadow_messages(caplog) if "reason=over_budget" in m]
    assert len(over) == 1
    assert "elapsed_ms=250" in over[0]
    assert "budget_ms=100" in over[0]
    assert "attachment_id=12" in over[0]


def test_shadow_log_no_neighbours(caplog):
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    with caplog.at_level(logging.INFO):
        scc, conf = svc.shadow_predict_with_budget(
            extraction_content="zzqqonlynomatch words unique",
            amount=Decimal("999999"),
            as_of=t0 + timedelta(days=10),
            attachment_id=3,
        )
    assert scc is None and conf is None
    assert any(
        "reason=no_neighbours" in m and "attachment_id=3" in m for m in _shadow_messages(caplog)
    )


def test_shadow_log_ok_warm_corpus(caplog):
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc_with_repo()
    _seed_corpus(repo, t0=t0)
    svc.refresh_incremental()
    with caplog.at_level(logging.INFO):
        scc, conf = svc.shadow_predict_with_budget(
            extraction_content="drywall invoice vendor payment",
            amount=Decimal("105"),
            as_of=t0 + timedelta(days=10),
            attachment_id=8,
        )
    assert scc == 100 and conf is not None
    ok = [m for m in _shadow_messages(caplog) if m.startswith("similar_bills.shadow_ok")]
    assert len(ok) == 1
    assert "scc=100" in ok[0]
    assert "corpus_bills=3" in ok[0]
    assert "attachment_id=8" in ok[0]
    assert "confidence=" in ok[0]
    assert "elapsed_ms=" in ok[0]


def test_similar_bills_shadow_budget_ms_default_and_override(monkeypatch):
    monkeypatch.delenv("SIMILAR_BILLS_SHADOW_BUDGET_MS", raising=False)
    import config

    assert config.Settings().similar_bills_shadow_budget_ms == 2000
    monkeypatch.setenv("SIMILAR_BILLS_SHADOW_BUDGET_MS", "500")
    assert config.Settings().similar_bills_shadow_budget_ms == 500

    svc = SimilarBillsService(
        settings=SimpleNamespace(
            bill_text_token_hash_key=TEST_KEY,
            similar_bills_shadow_budget_ms=750,
        )
    )
    assert svc._shadow_budget_ms() == 750
    svc_bad = SimilarBillsService(
        settings=SimpleNamespace(
            bill_text_token_hash_key=TEST_KEY,
            similar_bills_shadow_budget_ms="nope",
        )
    )
    assert svc_bad._shadow_budget_ms() == 2000


def test_backfill_run_upserts_with_fakes(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from scripts import u701a_backfill_token_hashes as bf

    envelope = {
        "attachment_public_id": "p",
        "source_file_hash": "h",
        "redaction_version": REDACTION_VERSION,
        "method": "t",
        "pages_count": 1,
        "content": "invoice drywall payment",
        "key_value_pairs": [],
    }
    svc = MagicMock()
    with patch.object(bf, "_read_blob_text_with_timeout", return_value=(json.dumps(envelope), "ok")):
        assert bf._process_one(1, "u", svc=svc) == "upserted"
    svc.upsert_from_extraction_envelope.assert_called_once()


def test_backfill_resume_skips_current_version_row(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from scripts import u701a_backfill_token_hashes as bf

    ver = bf.current_tokenizer_version()
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    with patch.object(bf, "get_connection") as gc:
        gc.return_value.__enter__.return_value = conn
        bf._list_candidates(conn, tokenizer_version=ver)
    sql = conn.cursor.return_value.execute.call_args[0][0]
    assert "TokenizerVersion" in sql
    assert "IsTaxpayerDocument" not in sql
    assert "TaxpayerAttachment" in sql


def test_backfill_hung_read_times_out(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from scripts import u701a_backfill_token_hashes as bf

    def slow_download(*_a, **_k):
        time.sleep(2.0)
        return (b"{}", {})

    with patch.object(bf, "READ_TIMEOUT_SECONDS", 0.05):
        with patch(
            "shared.storage.AzureBlobStorage.download_file",
            side_effect=slow_download,
        ):
            raw, outcome = bf._read_blob_text_with_timeout("url", bf.READ_TIMEOUT_SECONDS)
    assert outcome == "read_timeout"
    assert raw is None


def test_backfill_empty_content_counted(monkeypatch):
    monkeypatch.setenv("BILL_TEXT_TOKEN_HASH_KEY", TEST_KEY)
    from scripts import u701a_backfill_token_hashes as bf

    raw = json.dumps(
        {
            "attachment_public_id": "p",
            "source_file_hash": "h",
            "redaction_version": REDACTION_VERSION,
            "method": "t",
            "pages_count": 1,
            "content": "   ",
            "key_value_pairs": [],
        }
    )
    with patch.object(bf, "_read_blob_text_with_timeout", return_value=(raw, "ok")):
        assert bf._process_one(1, "u", svc=MagicMock()) == "empty_content"
