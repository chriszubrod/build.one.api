"""U-701b2: tenant-scoped similar bills, visibility, access project ids (mocked)."""
from __future__ import annotations

import random
import string
import time
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business import similar_bills as sb
from entities.coding_suggestion.business.bill_text_token_hash import (
    token_hashes_from_text,
    token_hashes_from_text_int,
    token_hashes_to_storage,
    tokenizer_version_for_key,
)
from entities.coding_suggestion.business.similar_bills_service import (
    SimilarBillsService,
    _compact_token_storage,
    _tokens_frozenset,
)
from shared import access as access_mod
from shared.authz.context import set_authz_context, system_authz

TEST_KEY = "u701b2-unit-test-key-" + ("x" * 12)


@pytest.fixture(autouse=True)
def _isolate_globals():
    yield
    SimilarBillsService._reset_shared_for_tests()
    access_mod._reset_accessible_projects_cache_for_tests()


def _version(key: str = TEST_KEY) -> str:
    return tokenizer_version_for_key(key)


def _svc(company_id: int = 1) -> tuple[SimilarBillsService, MagicMock]:
    SimilarBillsService._reset_shared_for_tests()
    svc = SimilarBillsService.for_company(
        company_id,
        settings=SimpleNamespace(
            bill_text_token_hash_key=TEST_KEY,
            similar_bills_max_tokens_per_bill=50,
            similar_bills_refresh_seconds=1800,
        ),
    )
    repo = MagicMock()
    repo.read_for_attachment_ids.return_value = []
    svc._token_repo = repo
    return svc, repo


def _rand_words(n: int, rng: random.Random) -> str:
    return " ".join(
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(3, 8)))
        for _ in range(n)
    )


def _seed_visibility_corpus(repo: MagicMock, *, t0: datetime, key: str = TEST_KEY) -> None:
    """Three priors: creator-only, project 99, project 100 (no overlap with {99} except bill 2)."""
    shared = token_hashes_from_text("drywall invoice vendor payment", key)
    tok_a, _ = token_hashes_to_storage(shared)
    extra_b = next(iter(token_hashes_from_text("betaunique", key)))
    tok_b = tok_a + " " + extra_b
    noise_stored, _ = token_hashes_to_storage(token_hashes_from_text("plumbing uniquewords", key))
    ver = _version(key)
    repo.read_corpus_rows.return_value = [
        SimpleNamespace(
            AttachmentId=1,
            BillId=1,
            VendorId=1,
            CreatedDatetime=t0,
            DominantSubCostCodeId=100,
            DominantProjectId=50,
            TotalAbsAmount=Decimal("100"),
            CreatedByUserId=10,
            ProjectIdsCsv="50",
        ),
        SimpleNamespace(
            AttachmentId=2,
            BillId=2,
            VendorId=1,
            CreatedDatetime=t0 + timedelta(days=1),
            DominantSubCostCodeId=200,
            DominantProjectId=99,
            TotalAbsAmount=Decimal("100"),
            CreatedByUserId=20,
            ProjectIdsCsv="99",
        ),
        SimpleNamespace(
            AttachmentId=3,
            BillId=3,
            VendorId=1,
            CreatedDatetime=t0 + timedelta(days=2),
            DominantSubCostCodeId=300,
            DominantProjectId=100,
            TotalAbsAmount=Decimal("100"),
            CreatedByUserId=30,
            ProjectIdsCsv="100",
        ),
    ]
    repo.read_since.return_value = [
        SimpleNamespace(
            AttachmentId=1,
            TokenizerVersion=ver,
            TokenHashes=tok_a,
            TokenCount=len(shared),
            ModifiedDatetime=t0,
        ),
        SimpleNamespace(
            AttachmentId=2,
            TokenizerVersion=ver,
            TokenHashes=tok_b,
            TokenCount=len(shared) + 1,
            ModifiedDatetime=t0 + timedelta(days=1),
        ),
        SimpleNamespace(
            AttachmentId=3,
            TokenizerVersion=ver,
            TokenHashes=noise_stored,
            TokenCount=5,
            ModifiedDatetime=t0 + timedelta(days=2),
        ),
    ]


def test_visibility_all_neighbours():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    svc.refresh_incremental()
    q = token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)
    all_vis = sb.VisibilityFilter(actor_user_id=99, accessible_project_ids=None)
    result = svc.similar_bills_for(
        bill_text_hashes=q,
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=10),
        visible=all_vis,
    )
    assert result is not None
    assert len(result.neighbours) == 2


def test_visibility_project_overlap_only():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    svc.refresh_incremental()
    q = token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)
    vis = sb.VisibilityFilter(actor_user_id=99, accessible_project_ids=frozenset({99}))
    result = svc.similar_bills_for(
        bill_text_hashes=q,
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=10),
        visible=vis,
    )
    assert result is not None
    scc_ids = {n.scc_id for n in result.neighbours}
    assert scc_ids == {200}


def test_visibility_creator_without_project_access():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    svc.refresh_incremental()
    q = token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)
    vis = sb.VisibilityFilter(actor_user_id=10, accessible_project_ids=frozenset())
    result = svc.similar_bills_for(
        bill_text_hashes=q,
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=10),
        visible=vis,
    )
    assert result is not None
    assert {n.scc_id for n in result.neighbours} == {100}


def test_visibility_none_visible():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    svc.refresh_incremental()
    q = token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)
    vis = sb.VisibilityFilter(actor_user_id=99, accessible_project_ids=frozenset({777}))
    result = svc.similar_bills_for(
        bill_text_hashes=q,
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=10),
        visible=vis,
    )
    assert result is None or result.predicted_scc_id is None


def test_multi_project_bill_visible_on_any_overlap():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    repo.read_corpus_rows.return_value[0] = SimpleNamespace(
        AttachmentId=1,
        BillId=1,
        VendorId=1,
        CreatedDatetime=t0,
        DominantSubCostCodeId=100,
        DominantProjectId=50,
        TotalAbsAmount=Decimal("100"),
        CreatedByUserId=10,
        ProjectIdsCsv="50,99",
    )
    svc.refresh_incremental()
    q = token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)
    vis = sb.VisibilityFilter(actor_user_id=99, accessible_project_ids=frozenset({99}))
    result = svc.similar_bills_for(
        bill_text_hashes=q,
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=10),
        visible=vis,
    )
    assert result is not None
    assert {n.scc_id for n in result.neighbours} == {100, 200}


def test_exclude_bill_id_never_neighbour():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    svc.refresh_incremental()
    q = token_hashes_from_text_int("drywall invoice vendor payment", TEST_KEY)
    result = svc.similar_bills_for(
        bill_text_hashes=q,
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=10),
        exclude_bill_id=2,
    )
    assert result is not None
    assert all(n.scc_id != 200 for n in result.neighbours)


def _top_k_corpus(include_hidden: bool):
    """12 visible matching bills (project 1), each sharing 3 of the 8 query words, so
    every one is a candidate at a lower score than an exact match - more than
    DEFAULT_TOP_K of them - plus 10 visible non-matching bills that keep the query words
    rare (positive IDF), and optionally one hidden bill (project 999) that matches the
    query exactly and would win any slot it could take."""
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    ver = _version(TEST_KEY)
    qwords = ["qalpha", "qbravo", "qcharlie", "qdelta", "qecho", "qfoxtrot", "qgolf", "qhotel"]
    query_text = " ".join(qwords)
    docs: list[tuple[int, str, int, str]] = []
    for i in range(12):
        bid = i + 1
        picked = [qwords[(i + k) % 8] for k in range(3)]
        docs.append((bid, " ".join(picked + [f"fill{bid}a", f"fill{bid}b"]),
                     2000 if i % 3 else 3000, "1"))
    for j in range(10):
        bid = 20 + j
        docs.append((bid, f"noise{bid}a noise{bid}b noise{bid}c", 4000, "1"))
    if include_hidden:
        docs.append((40, query_text, 1013, "999"))
    priors, hashes = [], []
    for bid, text, scc, projects in docs:
        stored, _ = token_hashes_to_storage(token_hashes_from_text(text, TEST_KEY))
        priors.append(
            SimpleNamespace(
                AttachmentId=bid,
                BillId=bid,
                VendorId=1,
                CreatedDatetime=t0 + timedelta(days=bid),
                DominantSubCostCodeId=scc,
                DominantProjectId=int(projects),
                TotalAbsAmount=Decimal("100"),
                CreatedByUserId=1,
                ProjectIdsCsv=projects,
            )
        )
        hashes.append(
            SimpleNamespace(
                AttachmentId=bid,
                TokenizerVersion=ver,
                TokenHashes=stored,
                TokenCount=len(text.split()),
                ModifiedDatetime=t0,
            )
        )
    repo.read_corpus_rows.return_value = priors
    repo.read_since.return_value = hashes
    svc.refresh_incremental()
    vis = sb.VisibilityFilter(actor_user_id=5, accessible_project_ids=frozenset({1}))
    return svc.similar_bills_for(
        bill_text_hashes=token_hashes_from_text_int(query_text, TEST_KEY),
        amount=Decimal("100"),
        as_of=t0 + timedelta(days=60),
        visible=vis,
    )


def test_hidden_high_score_does_not_win_top_k():
    """Filter BEFORE top-K: with a hidden exact match in the corpus, the result must be
    identical to the corpus without it (same neighbours, vote and confidence). A filter
    applied after top-K would give the hidden bill a slot and then drop it, leaving one
    neighbour fewer."""
    with_hidden = _top_k_corpus(include_hidden=True)
    without_hidden = _top_k_corpus(include_hidden=False)
    assert without_hidden is not None and with_hidden is not None
    # IDF is company-wide (hidden bills included, by design), so raw scores shift
    # slightly; which bills are picked, their order and the vote must not.
    def picked(r):
        return [(n.scc_id, n.match) for n in r.neighbours]

    assert len(without_hidden.neighbours) == len(with_hidden.neighbours) > 0
    assert picked(with_hidden) == picked(without_hidden)
    assert with_hidden.predicted_scc_id == without_hidden.predicted_scc_id
    assert with_hidden.confidence == pytest.approx(without_hidden.confidence, abs=0.02)
    assert all(n.scc_id != 1013 for n in with_hidden.neighbours)


def test_per_company_registry_and_shared_alias():
    SimilarBillsService._reset_shared_for_tests()
    settings = SimpleNamespace(
        bill_text_token_hash_key=TEST_KEY,
        similar_bills_max_tokens_per_bill=50,
        similar_bills_refresh_seconds=1800,
    )
    svc1 = SimilarBillsService.for_company(1, settings=settings)
    svc2 = SimilarBillsService.for_company(2, settings=settings)
    assert svc1 is not svc2
    assert SimilarBillsService.for_company(1) is svc1
    assert SimilarBillsService.shared() is svc1


def test_malformed_hash_attachment_skipped_for_lifetime():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    repo.read_since.return_value[0] = SimpleNamespace(
        AttachmentId=1,
        TokenizerVersion=_version(),
        TokenHashes="not-hex garbage",
        TokenCount=1,
        ModifiedDatetime=t0,
    )
    svc.refresh_incremental()
    assert 1 in svc._skipped_malformed_attachment_ids
    repo.read_for_attachment_ids.reset_mock()
    svc.refresh_incremental()
    repo.read_for_attachment_ids.assert_not_called()


def test_compaction_equivalence_on_synthetic_corpus():
    rng = random.Random(70182)
    t0 = datetime(2020, 1, 1)
    n_bills = 250
    key = TEST_KEY
    ver = _version(key)
    corpus = []
    hashes = []
    query_text = _rand_words(40, rng)
    query_tokens = token_hashes_from_text_int(query_text, key)
    bill_texts: list[str] = []
    for bid in range(1, n_bills + 1):
        text = query_text if bid == n_bills else _rand_words(25, rng)
        bill_texts.append(text)
        toks = token_hashes_from_text(text, key)
        stored, _ = token_hashes_to_storage(toks)
        corpus.append(
            SimpleNamespace(
                AttachmentId=bid,
                BillId=bid,
                VendorId=bid % 7,
                CreatedDatetime=t0 + timedelta(days=bid),
                DominantSubCostCodeId=100 + (bid % 15),
                DominantProjectId=1,
                TotalAbsAmount=Decimal("1000"),
                CreatedByUserId=17,
                ProjectIdsCsv="1",
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

    svc, repo = _svc()
    repo.read_corpus_rows.return_value = corpus
    repo.read_since.return_value = hashes
    svc.refresh_incremental()
    as_of = t0 + timedelta(days=n_bills + 5)
    live = svc.similar_bills_for(
        bill_text_hashes=query_tokens,
        amount=Decimal("1000"),
        vendor_id=1,
        as_of=as_of,
    )

    doc_tokens = [
        _tokens_frozenset(_compact_token_storage(token_hashes_from_text_int(txt, key)))
        for txt in bill_texts
    ]
    legacy_profiles: dict[int, sb.BillTokenProfile] = {}
    idf = sb.compute_idf_from_doc_token_sets(doc_tokens)
    token_index: dict[int, list[int]] = {}
    for bid, text in enumerate(bill_texts, start=1):
        toks = token_hashes_from_text_int(text, key)
        capped = sb.cap_tokens_by_idf(toks, idf, 50)
        bill_row = SimpleNamespace(
            bill_id=bid,
            vendor_id=bid % 7,
            created=t0 + timedelta(days=bid),
            label_scc_id=100 + (bid % 15),
            total_abs_amount=Decimal("1000"),
        )
        prof = sb.BillTokenProfile(
            enriched=SimpleNamespace(bill=bill_row),
            tokens=capped,
            idf_weight_sum=sum(idf.get(t, 0.0) for t in capped),
            log_abs_amount=sb._log_abs_amount(Decimal("1000")),
        )
        legacy_profiles[bid] = prof
        for tok in capped:
            token_index.setdefault(tok, []).append(bid)
    frozen_index = {tok: tuple(sorted(bids)) for tok, bids in token_index.items()}
    qbill = SimpleNamespace(
        bill_id=-1,
        vendor_id=1,
        created=as_of,
        label_scc_id=0,
        total_abs_amount=Decimal("1000"),
    )
    qtok = sb.cap_tokens_by_idf(query_tokens, idf, 50)
    qprof = sb.BillTokenProfile(
        enriched=SimpleNamespace(bill=qbill),
        tokens=qtok,
        idf_weight_sum=sum(idf.get(t, 0.0) for t in qtok),
        log_abs_amount=sb._log_abs_amount(Decimal("1000")),
    )
    neighbours = sb.top_neighbours_any_vendor(
        qprof,
        idf,
        top_k=sb.DEFAULT_TOP_K,
        token_index=frozen_index,
        prior_by_id=legacy_profiles,
    )
    vote = sb.vote_from_scored_neighbours(
        [(n.combined, n.prior.enriched.bill.bill_id, n.scc_id) for n in neighbours],
        sb.DEFAULT_TOP_K,
    )
    assert live is not None
    assert live.predicted_scc_id == vote.predicted_scc_id
    assert live.confidence == vote.confidence
    assert [n.scc_id for n in live.neighbours] == [n.scc_id for n in neighbours[: sb.DEFAULT_NEIGHBOUR_DISPLAY_K]]


def test_shadow_path_unchanged_without_visible():
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    svc.refresh_incremental()
    q_text = "drywall invoice vendor payment"
    scc, conf = svc.shadow_predict_with_budget(
        extraction_content=q_text,
        amount=Decimal("100"),
        vendor_id=1,
        as_of=t0 + timedelta(days=10),
        attachment_id=99,
    )
    assert scc is not None
    assert conf is not None


def test_filtered_vote_excludes_suspense_and_out_of_catalog():
    bill_row = SimpleNamespace(
        bill_id=1,
        vendor_id=1,
        created=datetime(2026, 1, 1),
        label_scc_id=1,
        total_abs_amount=Decimal("100"),
    )
    prof = sb.BillTokenProfile(
        enriched=SimpleNamespace(bill=bill_row),
        tokens=frozenset({"a"}),
        idf_weight_sum=1.0,
        log_abs_amount=0.0,
    )
    n_ok = sb.Neighbour(0.9, prof, 0.8, 1.0, True)
    n_susp = sb.Neighbour(
        0.95,
        sb.BillTokenProfile(
            enriched=SimpleNamespace(
                bill=SimpleNamespace(
                    bill_id=2,
                    vendor_id=1,
                    created=datetime(2026, 1, 1),
                    label_scc_id=2,
                    total_abs_amount=Decimal("100"),
                )
            ),
            tokens=frozenset({"b"}),
            idf_weight_sum=1.0,
            log_abs_amount=0.0,
        ),
        0.8,
        1.0,
        True,
    )
    scc_by_id = {
        1: SimpleNamespace(number="10.01", name="ok"),
        2: SimpleNamespace(number=sb.SUSPENSE_SCC_NUMBER, name="hold"),
    }
    scc_id, conf = sb.filtered_vote(
        [n_susp, n_ok],
        as_of_catalog=[{"number": "10.01", "name": "ok"}],
        scc_by_id=scc_by_id,
        ambiguous_numbers=frozenset(),
    )
    assert scc_id == 1
    assert conf is not None


def test_resolve_accessible_project_ids_bypass():
    set_authz_context(user_id=5, company_id=1, is_system_admin=True)
    assert access_mod.resolve_accessible_project_ids() is None
    with system_authz():
        assert access_mod.resolve_accessible_project_ids() is None


def test_resolve_accessible_project_ids_normal_user():
    set_authz_context(user_id=42, company_id=1, is_system_admin=False)
    with patch(
        "entities.user_project.persistence.repo.UserProjectRepository.read_bill_access_project_ids_for_user",
        return_value={10, 20},
    ) as read_fn:
        got = access_mod.resolve_accessible_project_ids()
        assert got == frozenset({10, 20})
        read_fn.assert_called_once_with(42)
        read_fn.reset_mock()
        cached = access_mod.resolve_accessible_project_ids()
        assert cached == frozenset({10, 20})
        read_fn.assert_not_called()


def test_resolve_accessible_project_ids_fail_closed():
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)
    with patch(
        "entities.user_project.persistence.repo.UserProjectRepository.read_bill_access_project_ids_for_user",
        side_effect=RuntimeError("db down"),
    ):
        assert access_mod.resolve_accessible_project_ids() == frozenset()


def test_corpus_read_passes_company_id():
    svc, repo = _svc(company_id=3)
    repo.read_corpus_rows.return_value = []
    repo.read_since.return_value = []
    svc.refresh_incremental()
    kw = repo.read_corpus_rows.call_args.kwargs
    assert kw.get("company_id") == 3


def test_explicit_actor_not_widened_by_system_context():
    """A non-admin actor's scope must not become 'all' because the caller runs under
    system_authz (e.g. a scheduler tick acting for the bill's creator)."""
    with system_authz():
        with patch(
            "entities.user_project.persistence.repo.UserProjectRepository.read_bill_access_project_ids_for_user",
            return_value={10},
        ):
            got = access_mod.accessible_project_ids_for_user(42, is_system_admin=False)
    assert got == frozenset({10})
    assert access_mod.accessible_project_ids_for_user(42, is_system_admin=True) is None


def test_access_cache_isolated_per_user_and_company():
    calls = []

    def fake(_self, user_id):
        calls.append(user_id)
        return {100 + user_id}

    with patch(
        "entities.user_project.persistence.repo.UserProjectRepository.read_bill_access_project_ids_for_user",
        fake,
    ):
        set_authz_context(user_id=1, company_id=1, is_system_admin=False)
        assert access_mod.resolve_accessible_project_ids() == frozenset({101})
        set_authz_context(user_id=2, company_id=1, is_system_admin=False)
        assert access_mod.resolve_accessible_project_ids() == frozenset({102})
        set_authz_context(user_id=1, company_id=2, is_system_admin=False)
        assert access_mod.resolve_accessible_project_ids() == frozenset({101})
    assert calls == [1, 2, 1]


@pytest.mark.parametrize("bad", ["-ff", "1" * 17])
def test_out_of_range_hash_row_skipped_not_fatal(bad):
    """A negative or >64-bit hex value parses but overflows the compact array; it must
    be skipped like any malformed row, not abort the whole snapshot."""
    t0 = datetime(2026, 1, 1)
    svc, repo = _svc()
    _seed_visibility_corpus(repo, t0=t0)
    repo.read_since.return_value[0] = SimpleNamespace(
        AttachmentId=1,
        TokenizerVersion=_version(),
        TokenHashes=bad,
        TokenCount=1,
        ModifiedDatetime=t0,
    )
    svc.refresh_incremental()
    assert 1 in svc._skipped_malformed_attachment_ids
    assert len(svc._hash_by_attachment) >= 1
