"""
U-691: read-only rolling-origin backtest — similar past bills vs vendor history (U-639 §10, Scenario 2a).

Loads completed coded Bills plus line descriptions and memo in one SELECT (rollback after read),
tokenizes text, finds IDF-weighted similar priors, and compares to U-653b history Modes A/B.

**Combined policy (this harness)** — history Mode A when ``raw_score >= HISTORY_STRONG_RAW_SCORE``,
else the calibration-selected best similarity variant, else history Mode A prediction (may abstain).
This differs from U-654a's ``combined_mode_a_predicted_scc``, which gates on
``COMBINED_HISTORY_RAW_SCORE_THRESHOLD`` and falls back to **text** suggestions only (returns
``None`` when text abstains rather than falling back to a weak history vote).

Usage (repo root):
  ./.venv/bin/python -m scripts.u691_similar_bills_backtest              # dry-run (default)
  ./.venv/bin/python -m scripts.u691_similar_bills_backtest --run
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from intelligence.cascade.bill_coding import SUSPENSE_SCC_NUMBER, normalize_scc_number

from entities.attachment.business.extraction_service import extraction_payload_is_current

from entities.coding_suggestion.business.bill_suggestion_service import (
    HISTORY_STRONG_RAW_SCORE,
    MODEL_VERSION as HISTORY_MODEL_VERSION,
)
from scripts.sync_helper import assert_cli_system_admin
from scripts.u653b_backtest import (
    DEFAULT_CAL_SINCE,
    DEFAULT_CAL_UNTIL,
    DEFAULT_EVAL_SINCE,
    DEFAULT_EVAL_UNTIL,
    MIN_PRIOR_BILLS,
    BillRow,
    CalibrationMap,
    ModePrediction,
    ScoredBill,
    _LOAD_SQL as _U653B_LOAD_SQL,
    _consistency_bucket,
    _date_only,
    _dollar_accuracy,
    _parse_dt,
    _vendor_consistency_share,
    _weighted_accuracy,
    fit_calibration,
    priors_before,
    score_bills_rolling,
    validate_windows,
)
from scripts.u654a_text_backtest import (
    _json_default,
    assert_outputs_privacy_safe,
    build_vendor_priors,
    load_blob_urls_from_db,
)
from shared.database import get_connection
from shared.text_blob_read import make_text_blob_reader, read_text_blob_with_retry

MODEL_VERSION = "u691-similar-bills-v1"

TEXT_SOURCE_DESCRIPTIONS = "descriptions"
TEXT_SOURCE_PDF = "pdf"
TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS = "pdf_plus_masked_descriptions"
TEXT_SOURCES = (
    TEXT_SOURCE_DESCRIPTIONS,
    TEXT_SOURCE_PDF,
    TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS,
)

DEFAULT_PDF_READ_WORKERS = 8
DEFAULT_PDF_READ_TIMEOUT_SECONDS = 120.0

_CATALOG_MASK_SQL = """
SELECT s.[Id], s.[Name], c.[Name] AS [CostCodeName]
FROM dbo.[SubCostCode] s
LEFT JOIN dbo.[CostCode] c ON c.[Id] = s.[CostCodeId]
"""

DEFAULT_TOP_K = 10
DEFAULT_AMOUNT_BAND_PCT = 0.25
DEFAULT_AMOUNT_BAND_CAP_RATIO = 4.0
MIN_TOKEN_LEN = 2

VARIANTS = (
    "V_same_vendor",
    "V_any_vendor",
    "V_any_vendor_same_project",
    "V_other_vendor",
)

# Variants that do not use the evaluated bill's label project (pair with history Mode B).
VARIANTS_NO_PROJECT = ("V_same_vendor", "V_any_vendor", "V_other_vendor")

# Head-to-head convention: **top-1 over all** evaluated bills — abstain counts as wrong.
# Coverage and precision-on-covered are reported separately (bill- and dollar-weighted).

_STOP_WORDS = frozenset(
    "a an and are as at be by for from in is it of on or the to was with "
    "bill invoice payment total amount due date no num number ref po".split()
)

_TOKEN_RE = re.compile(r"[a-z]+")

_ENRICHED_SQL_ANCHORS = (
    "    GROUP BY bli.[BillId]\n)\nSELECT",
    "    qbo_pull.[QboStagingCreated]\nFROM dbo.[Bill] b",
    "INNER JOIN SccCounts sc ON sc.[BillId] = b.[Id]\nWHERE",
)


def _assert_enriched_sql_anchors() -> None:
    for anchor in _ENRICHED_SQL_ANCHORS:
        n = _U653B_LOAD_SQL.count(anchor)
        if n != 1:
            raise RuntimeError(
                f"U-653b _LOAD_SQL anchor {anchor!r} appears {n} time(s), expected exactly 1"
            )


def _enriched_load_sql() -> str:
    """U-653b load SQL plus line descriptions, memo, and line count."""
    _assert_enriched_sql_anchors()
    line_text_cte = """,
LineText AS (
    SELECT
        bli.[BillId],
        STRING_AGG(CAST(bli.[Description] AS NVARCHAR(MAX)), ' ')
            WITHIN GROUP (ORDER BY bli.[Id]) AS [Descriptions],
        COUNT(*) AS [LineCount]
    FROM dbo.[BillLineItem] bli
    INNER JOIN CodedBills cb ON cb.[BillId] = bli.[BillId]
    GROUP BY bli.[BillId]
)"""
    sql = _U653B_LOAD_SQL.replace(
        "    GROUP BY bli.[BillId]\n)\nSELECT",
        "    GROUP BY bli.[BillId]\n)" + line_text_cte + "\nSELECT",
        1,
    )
    sql = sql.replace(
        "    qbo_pull.[QboStagingCreated]\nFROM dbo.[Bill] b",
        "    qbo_pull.[QboStagingCreated],\n"
        "    COALESCE(lt.[Descriptions], '') AS [LineDescriptions],\n"
        "    COALESCE(b.[Memo], '') AS [Memo],\n"
        "    COALESCE(lt.[LineCount], 0) AS [LineCount]\nFROM dbo.[Bill] b",
        1,
    )
    sql = sql.replace(
        "INNER JOIN SccCounts sc ON sc.[BillId] = b.[Id]\nWHERE",
        "INNER JOIN SccCounts sc ON sc.[BillId] = b.[Id]\n"
        "LEFT JOIN LineText lt ON lt.[BillId] = b.[Id]\nWHERE",
        1,
    )
    return sql


def _bill_row_from_sql_row(row: Any) -> BillRow:
    qbo_created = row.QboStagingCreated
    return BillRow(
        bill_id=int(row.BillId),
        vendor_id=int(row.VendorId),
        created=_parse_dt(row.CreatedDatetime),
        label_scc_id=int(row.DominantSubCostCodeId),
        label_project_id=(
            int(row.DominantProjectId) if row.DominantProjectId is not None else None
        ),
        total_abs_amount=Decimal(
            str(row.TotalAbsAmount if row.TotalAbsAmount is not None else 0)
        ),
        mixed_code=bool(row.MixedCode),
        intake_source=str(row.IntakeSource) if row.IntakeSource is not None else None,
        source_email_message_id=(
            int(row.SourceEmailMessageId)
            if row.SourceEmailMessageId is not None
            else None
        ),
        creator_is_agent=bool(row.CreatorIsAgent),
        qbo_staging_created=(
            _parse_dt(qbo_created) if qbo_created is not None else None
        ),
    )


@dataclass(frozen=True)
class EnrichedBill:
    """BillRow plus text used only in-memory (never written to outputs)."""

    bill: BillRow
    memo: str
    line_descriptions: str
    line_count: int


@dataclass(frozen=True)
class TokenSourceConfig:
    """Controls which text is tokenized for similarity (U-694)."""

    text_source: str = TEXT_SOURCE_DESCRIPTIONS
    catalog_mask_tokens: frozenset[str] = frozenset()
    pdf_text_by_bill_id: Mapping[int, str] = field(default_factory=dict)
    pdf_tokens_by_bill_id: Mapping[int, frozenset[str]] = field(default_factory=dict)
    population_bill_ids: Optional[frozenset[int]] = None
    _usable_pdf_bill_ids: frozenset[int] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        ids: set[int] = set()
        for bid in self.pdf_tokens_by_bill_id:
            ids.add(int(bid))
        for bid, text in self.pdf_text_by_bill_id.items():
            if (text or "").strip():
                ids.add(int(bid))
        object.__setattr__(self, "_usable_pdf_bill_ids", frozenset(ids))

    @property
    def requires_pdf_population(self) -> bool:
        return self.text_source in (
            TEXT_SOURCE_PDF,
            TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS,
        )

    @property
    def usable_pdf_bill_ids(self) -> frozenset[int]:
        return self._usable_pdf_bill_ids


_DEFAULT_TOKEN_CONFIG = TokenSourceConfig()


@dataclass(frozen=True)
class PdfTextLoadStats:
    linked: int = 0
    read_ok: int = 0
    stale_redaction: int = 0
    read_error: int = 0
    empty_content: int = 0


@dataclass(frozen=True)
class PopulationCounts:
    all_coded_bills: int
    with_usable_pdf_text: int
    eval_all: int
    eval_pdf_population: int
    cal_all: int
    cal_pdf_population: int


@dataclass(frozen=True)
class BillTokenProfile:
    enriched: EnrichedBill
    tokens: frozenset[str]
    idf_weight_sum: float
    log_abs_amount: float


@dataclass(frozen=True)
class SimilarPrediction:
    predicted_scc_id: Optional[int]
    raw_score: Optional[Decimal]
    neighbour_count: int
    top_k_used: int


def empty_similar_predictions() -> dict[str, SimilarPrediction]:
    return {v: SimilarPrediction(None, None, 0, 0) for v in VARIANTS}


@dataclass
class FullyScoredBill:
    enriched: EnrichedBill
    history_a: ModePrediction
    history_b: ModePrediction
    similar: dict[str, SimilarPrediction]


def tokenize_bill_text(text: str) -> frozenset[str]:
    """Lowercase, strip punctuation, drop stop words and very short tokens; dedupe per bill."""
    if not text:
        return frozenset()
    tokens = _TOKEN_RE.findall(text.lower())
    out: set[str] = set()
    for tok in tokens:
        if len(tok) < MIN_TOKEN_LEN or tok in _STOP_WORDS:
            continue
        out.add(tok)
    return frozenset(out)


def _pdf_text_from_raw_blob(raw_blob_text: str) -> tuple[Optional[str], str]:
    """Parse extraction JSON once; return (text, outcome) with outcome ok|stale|empty."""
    try:
        payload = json.loads(raw_blob_text)
    except ValueError:
        return None, "stale"
    if not isinstance(payload, dict) or not extraction_payload_is_current(payload):
        return None, "stale"
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, "empty"
    return content.strip(), "ok"


def bill_text_from_extraction_blob(raw_blob_text: str) -> Optional[str]:
    """Extract invoice body text from a current-stamp extraction envelope (content field only)."""
    text, outcome = _pdf_text_from_raw_blob(raw_blob_text)
    return text if outcome == "ok" else None


def _description_memo_tokens(enriched: EnrichedBill) -> frozenset[str]:
    combined = " ".join(
        part for part in (enriched.line_descriptions, enriched.memo) if part
    )
    return tokenize_bill_text(combined)


def _apply_catalog_mask(
    tokens: frozenset[str], catalog_mask_tokens: frozenset[str]
) -> frozenset[str]:
    if not catalog_mask_tokens:
        return tokens
    return frozenset(t for t in tokens if t not in catalog_mask_tokens)


def bill_tokens(
    enriched: EnrichedBill,
    *,
    config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> frozenset[str]:
    if config.text_source == TEXT_SOURCE_DESCRIPTIONS:
        toks = _description_memo_tokens(enriched)
        return _apply_catalog_mask(toks, config.catalog_mask_tokens)
    bid = enriched.bill.bill_id
    pdf_toks = config.pdf_tokens_by_bill_id.get(bid)
    if pdf_toks is None:
        pdf_raw = config.pdf_text_by_bill_id.get(bid, "")
        pdf_toks = tokenize_bill_text(pdf_raw)
    if config.text_source == TEXT_SOURCE_PDF:
        return pdf_toks
    desc_toks = _apply_catalog_mask(
        _description_memo_tokens(enriched), config.catalog_mask_tokens
    )
    return frozenset(set(pdf_toks) | set(desc_toks))


def similarity_eligible(
    enriched: EnrichedBill, config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG
) -> bool:
    if config.population_bill_ids is not None:
        if enriched.bill.bill_id not in config.population_bill_ids:
            return False
    if not config.requires_pdf_population:
        return True
    return enriched.bill.bill_id in config.usable_pdf_bill_ids


def _log_abs_amount(total_abs_amount: Decimal) -> float:
    if total_abs_amount <= 0:
        return float("-inf")
    return math.log(float(total_abs_amount))


def token_profile(
    enriched: EnrichedBill,
    idf: dict[str, float],
    *,
    config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> BillTokenProfile:
    tokens = bill_tokens(enriched, config=config)
    wsum = sum(idf.get(t, 0.0) for t in tokens)
    return BillTokenProfile(
        enriched,
        tokens,
        wsum,
        _log_abs_amount(enriched.bill.total_abs_amount),
    )


def compute_idf_from_corpus(
    bills: Sequence[EnrichedBill],
    *,
    since: date,
    until: date,
    config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> dict[str, float]:
    """
    IDF = log((N + 1) / (df + 1)) over bills whose created date falls in [since, until].
    Eval scoring uses the calibration window as the fixed corpus (documented simplification).
    """
    doc_tokens: list[frozenset[str]] = []
    for eb in bills:
        d = _date_only(eb.bill.created)
        if since <= d <= until and similarity_eligible(eb, config):
            doc_tokens.append(bill_tokens(eb, config=config))
    return _idf_from_doc_tokens(doc_tokens)


def compute_idf_before_date(
    bills: Sequence[EnrichedBill],
    *,
    before: date,
    config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> dict[str, float]:
    """IDF from all bills with created date strictly before ``before`` (leakage-free for selection)."""
    doc_tokens = [
        bill_tokens(eb, config=config)
        for eb in bills
        if _date_only(eb.bill.created) < before and similarity_eligible(eb, config)
    ]
    return _idf_from_doc_tokens(doc_tokens)


def _idf_from_doc_tokens(doc_tokens: Sequence[frozenset[str]]) -> dict[str, float]:
    n_docs = len(doc_tokens)
    if n_docs == 0:
        return {}
    df: dict[str, int] = defaultdict(int)
    for toks in doc_tokens:
        for t in toks:
            df[t] += 1
    return {t: math.log((n_docs + 1) / (count + 1)) for t, count in df.items()}


def amount_band_factor(
    query_amount: Decimal,
    prior_amount: Decimal,
    *,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
) -> float:
    return amount_band_factor_logs(
        _log_abs_amount(query_amount),
        _log_abs_amount(prior_amount),
        band_pct=band_pct,
        cap_ratio=cap_ratio,
    )


def amount_band_factor_logs(
    query_log: float,
    prior_log: float,
    *,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
) -> float:
    if query_log == float("-inf") or prior_log == float("-inf"):
        return 0.0
    log_ratio = abs(query_log - prior_log)
    band_log = math.log(1.0 + band_pct)
    cap_log = math.log(cap_ratio)
    if log_ratio <= band_log:
        return 1.0
    if log_ratio >= cap_log:
        return 0.0
    return max(0.0, 1.0 - (log_ratio - band_log) / (cap_log - band_log))


def idf_weighted_jaccard(
    tokens_a: frozenset[str],
    weight_a: float,
    tokens_b: frozenset[str],
    weight_b: float,
    idf: dict[str, float],
) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    if not inter:
        return 0.0
    num = sum(idf.get(t, 0.0) for t in inter)
    den = weight_a + weight_b - num
    if den <= 0:
        return 0.0
    return num / den


def text_similarity_profiles(
    query: BillTokenProfile,
    prior: BillTokenProfile,
    idf: dict[str, float],
    *,
    band_pct: float,
    cap_ratio: float,
) -> float:
    jac = idf_weighted_jaccard(
        query.tokens,
        query.idf_weight_sum,
        prior.tokens,
        prior.idf_weight_sum,
        idf,
    )
    if jac <= 0:
        return 0.0
    band = amount_band_factor_logs(
        query.log_abs_amount,
        prior.log_abs_amount,
        band_pct=band_pct,
        cap_ratio=cap_ratio,
    )
    return jac * band


def _as_bill_row(bill_or_enriched: BillRow | EnrichedBill) -> BillRow:
    return bill_or_enriched.bill if isinstance(bill_or_enriched, EnrichedBill) else bill_or_enriched


def is_prior_eligible(
    prior: BillRow | EnrichedBill,
    query: BillRow | EnrichedBill,
    *,
    as_of: datetime,
) -> bool:
    """Strict rolling origin: created strictly before query; never self."""
    pb = _as_bill_row(prior)
    qb = _as_bill_row(query)
    if pb.bill_id == qb.bill_id:
        return False
    return pb.created < as_of


def _prior_matches_variant(
    prior: BillRow | EnrichedBill,
    query: BillRow | EnrichedBill,
    variant: str,
) -> bool:
    pb = _as_bill_row(prior)
    qb = _as_bill_row(query)
    if variant == "V_same_vendor":
        return pb.vendor_id == qb.vendor_id
    if variant == "V_other_vendor":
        return pb.vendor_id != qb.vendor_id
    if variant == "V_any_vendor_same_project":
        qp = qb.label_project_id
        pp = pb.label_project_id
        return qp is not None and pp is not None and qp == pp
    return True


def _vote_from_scored(
    scored: list[tuple[float, int, int]],
    top_k: int,
) -> SimilarPrediction:
    top = scored[:top_k]
    if not top:
        return SimilarPrediction(None, None, 0, 0)
    weights: dict[int, float] = defaultdict(float)
    for sim, _bid, scc_id in top:
        weights[scc_id] += sim
    ranked = sorted(weights.items(), key=lambda x: (-x[1], x[0]))
    top_scc, top_w = ranked[0]
    total_w = sum(weights.values())
    share = Decimal(str(top_w / total_w)) if total_w > 0 else None
    k_used = len(top)
    return SimilarPrediction(top_scc, share, k_used, k_used)


def _top_k_scored(
    scored: list[tuple[float, int, int]],
    top_k: int,
) -> list[tuple[float, int, int]]:
    if len(scored) <= top_k:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored
    best = heapq.nlargest(
        top_k, scored, key=lambda x: (x[0], -x[1])
    )
    best.sort(key=lambda x: (-x[0], x[1]))
    return best


def predict_all_variants(
    query: BillTokenProfile,
    _prior_profiles: Sequence[BillTokenProfile],
    idf: dict[str, float],
    *,
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    token_index: dict[str, set[int]],
    prior_by_id: dict[int, BillTokenProfile],
) -> dict[str, SimilarPrediction]:
    as_of = query.enriched.bill.created
    qbill = query.enriched.bill
    q_vendor = qbill.vendor_id
    q_project = qbill.label_project_id

    candidate_ids: set[int] = set()
    for tok in query.tokens:
        for bid in token_index.get(tok, ()):
            candidate_ids.add(bid)

    same_vendor: list[tuple[float, int, int]] = []
    any_vendor: list[tuple[float, int, int]] = []
    same_project: list[tuple[float, int, int]] = []
    other_vendor: list[tuple[float, int, int]] = []

    for bid in candidate_ids:
        prior_prof = prior_by_id.get(bid)
        if prior_prof is None:
            continue
        pb = prior_prof.enriched.bill
        if not is_prior_eligible(pb, qbill, as_of=as_of):
            continue
        sim = text_similarity_profiles(
            query, prior_prof, idf, band_pct=band_pct, cap_ratio=cap_ratio
        )
        if sim <= 0:
            continue
        row = (sim, bid, pb.label_scc_id)
        any_vendor.append(row)
        if pb.vendor_id == q_vendor:
            same_vendor.append(row)
        else:
            other_vendor.append(row)
        pp = pb.label_project_id
        if q_project is not None and pp is not None and pp == q_project:
            same_project.append(row)

    return {
        "V_same_vendor": _vote_from_scored(_top_k_scored(same_vendor, top_k), top_k),
        "V_any_vendor": _vote_from_scored(_top_k_scored(any_vendor, top_k), top_k),
        "V_any_vendor_same_project": _vote_from_scored(
            _top_k_scored(same_project, top_k), top_k
        ),
        "V_other_vendor": _vote_from_scored(_top_k_scored(other_vendor, top_k), top_k),
    }


def predict_all_variants_reference(
    query: BillTokenProfile,
    prior_profiles: Sequence[BillTokenProfile],
    idf: dict[str, float],
    *,
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    token_index: dict[str, set[int]],
    prior_by_id: dict[int, BillTokenProfile],
) -> dict[str, SimilarPrediction]:
    """Straightforward scan-all-candidates implementation (tests / parity checks)."""
    as_of = query.enriched.bill.created
    qbill = query.enriched.bill

    candidate_ids: set[int] = set()
    for tok in query.tokens:
        for bid in token_index.get(tok, ()):
            candidate_ids.add(bid)

    scored_all: list[tuple[float, int, int]] = []
    for bid in candidate_ids:
        prior_prof = prior_by_id.get(bid)
        if prior_prof is None:
            continue
        pb = prior_prof.enriched.bill
        if not is_prior_eligible(pb, qbill, as_of=as_of):
            continue
        sim = text_similarity_profiles(
            query, prior_prof, idf, band_pct=band_pct, cap_ratio=cap_ratio
        )
        if sim > 0:
            scored_all.append((sim, bid, pb.label_scc_id))

    scored_all.sort(key=lambda x: (-x[0], x[1]))

    out: dict[str, SimilarPrediction] = {}
    for variant in VARIANTS:
        filtered = [
            row
            for row in scored_all
            if _prior_matches_variant(
                prior_by_id[row[1]].enriched.bill, qbill, variant
            )
        ]
        out[variant] = _vote_from_scored(filtered[:top_k], top_k)
    return out


def predict_similarity(
    variant: str,
    query: EnrichedBill,
    priors: Sequence[EnrichedBill],
    idf: dict[str, float],
    *,
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    token_index: dict[str, set[int]],
    prior_by_id: dict[int, BillTokenProfile],
    config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> SimilarPrediction:
    """Single-variant predict (tests): builds profiles on the fly."""
    qprof = token_profile(query, idf, config=config)
    pprofiles = [token_profile(p, idf, config=config) for p in priors]
    pby_id = {p.enriched.bill.bill_id: p for p in pprofiles}
    preds = predict_all_variants(
        qprof,
        pprofiles,
        idf,
        top_k=top_k,
        band_pct=band_pct,
        cap_ratio=cap_ratio,
        token_index=token_index,
        prior_by_id=pby_id,
    )
    return preds[variant]


def text_similarity(
    query: EnrichedBill,
    prior: EnrichedBill,
    idf: dict[str, float],
    *,
    band_pct: float,
    cap_ratio: float,
    config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> float:
    return text_similarity_profiles(
        token_profile(query, idf, config=config),
        token_profile(prior, idf, config=config),
        idf,
        band_pct=band_pct,
        cap_ratio=cap_ratio,
    )


def token_match_ratio(
    query: BillTokenProfile,
    prior: BillTokenProfile,
    idf: dict[str, float],
) -> float:
    """Weighted IDF token overlap divided by the query's weighted IDF mass (0..1, 2 dp)."""
    if query.idf_weight_sum <= 0:
        return 0.0
    inter = query.tokens & prior.tokens
    if not inter:
        return 0.0
    overlap = sum(idf.get(t, 0.0) for t in inter)
    return round(overlap / query.idf_weight_sum, 2)


class Neighbour(NamedTuple):
    """Top similar prior bill for harness evidence (U-696)."""

    combined: float
    prior: BillTokenProfile
    match: float
    amount_ratio: Optional[float]
    same_vendor: bool

    @property
    def scc_id(self) -> int:
        return self.prior.enriched.bill.label_scc_id


def _neighbour_amount_ratio(
    query_amount: Decimal,
    prior_amount: Decimal,
) -> Optional[float]:
    if query_amount <= 0 or prior_amount <= 0:
        return None
    return round(float(prior_amount / query_amount), 2)


def similar_bills_block_from_neighbours(
    neighbours: Sequence[Neighbour],
    scc_by_id: Mapping[int, Any],
) -> list[dict[str, Any]]:
    """Privacy-safe similar_bills block (no ids, dates, vendor names, or free text)."""
    out: list[dict[str, Any]] = []
    for n in neighbours:
        ref = scc_by_id.get(n.scc_id)
        if ref is None:
            continue
        out.append(
            {
                "scc_number": ref.number,
                "scc_name": ref.name,
                "match": n.match,
                "amount_ratio": n.amount_ratio,
                "same_vendor": n.same_vendor,
            }
        )
    return out


def apply_similar_evidence(
    neighbours: Sequence[Neighbour],
    candidate_sccs: list[dict[str, str]],
    *,
    as_of_catalog: Sequence[Mapping[str, str]],
    scc_by_id: Mapping[int, Any],
    ambiguous_numbers: frozenset[str],
) -> tuple[
    list[dict[str, str]],
    int,
    list[dict[str, Any]],
    list[Neighbour],
    dict[str, int],
]:
    """
    Filter neighbours to SCCs choosable on the bill's as-of catalog; union into
    candidates; build the model-facing similar_bills block from the same list.
    """
    choosable_nums = {
        normalize_scc_number(str(row["number"]))
        for row in as_of_catalog
        if row.get("number")
    }
    suspense_norm = normalize_scc_number(SUSPENSE_SCC_NUMBER)
    filtered: list[Neighbour] = []
    drops: dict[str, int] = {
        "neighbours_dropped_not_in_as_of_catalog": 0,
        "neighbours_dropped_suspense": 0,
        "neighbours_dropped_ambiguous": 0,
    }
    for n in neighbours:
        ref = scc_by_id.get(n.scc_id)
        if ref is None:
            drops["neighbours_dropped_not_in_as_of_catalog"] += 1
            continue
        num = normalize_scc_number(ref.number)
        if not num:
            drops["neighbours_dropped_not_in_as_of_catalog"] += 1
            continue
        if num in ambiguous_numbers:
            drops["neighbours_dropped_ambiguous"] += 1
            continue
        if num == suspense_norm:
            drops["neighbours_dropped_suspense"] += 1
            continue
        if num not in choosable_nums:
            drops["neighbours_dropped_not_in_as_of_catalog"] += 1
            continue
        filtered.append(n)

    existing = {normalize_scc_number(c["number"]) for c in candidate_sccs}
    added = 0
    merged = list(candidate_sccs)
    for n in filtered:
        ref = scc_by_id.get(n.scc_id)
        if ref is None:
            continue
        num = normalize_scc_number(ref.number)
        if not num or num in existing:
            continue
        merged.append({"number": ref.number, "name": ref.name})
        existing.add(num)
        added += 1

    block = similar_bills_block_from_neighbours(filtered, scc_by_id)
    return merged, added, block, filtered, drops


def enriched_bills_for_similarity(
    bills: Sequence[BillRow],
    blob_by_bill: Mapping[int, str],
) -> list[EnrichedBill]:
    """Minimal EnrichedBill rows for PDF-token similarity (descriptions unused)."""
    coded_ids = {b.bill_id for b in bills}
    out: list[EnrichedBill] = []
    for b in bills:
        if b.bill_id not in blob_by_bill or b.bill_id not in coded_ids:
            continue
        out.append(
            EnrichedBill(
                bill=b,
                memo="",
                line_descriptions="",
                line_count=0,
            )
        )
    return out


def top_neighbours_any_vendor(
    query: BillTokenProfile,
    idf: dict[str, float],
    *,
    top_k: int,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
    token_index: dict[str, set[int]],
    prior_by_id: dict[int, BillTokenProfile],
) -> list[Neighbour]:
    """Top-K earlier bills (any vendor) by combined IDF Jaccard × amount band."""
    as_of = query.enriched.bill.created
    qbill = query.enriched.bill
    candidate_ids: set[int] = set()
    for tok in query.tokens:
        for bid in token_index.get(tok, ()):
            candidate_ids.add(bid)

    scored: list[tuple[float, int, BillTokenProfile, float, Optional[float], bool]] = []
    for bid in candidate_ids:
        prior_prof = prior_by_id.get(bid)
        if prior_prof is None:
            continue
        pb = prior_prof.enriched.bill
        if not is_prior_eligible(pb, qbill, as_of=as_of):
            continue
        combined = text_similarity_profiles(
            query, prior_prof, idf, band_pct=band_pct, cap_ratio=cap_ratio
        )
        if combined <= 0:
            continue
        match = token_match_ratio(query, prior_prof, idf)
        amt_ratio = _neighbour_amount_ratio(qbill.total_abs_amount, pb.total_abs_amount)
        same_vendor = pb.vendor_id == qbill.vendor_id
        scored.append((combined, bid, prior_prof, match, amt_ratio, same_vendor))

    ranked = _top_k_scored(
        [(c, bid, pp.enriched.bill.label_scc_id) for c, bid, pp, *_ in scored],
        top_k,
    )
    by_bid = {row[1]: row for row in scored}
    out: list[Neighbour] = []
    for _combined, bid, _scc in ranked:
        combined, _bid, prior_prof, match, amt_ratio, same_vendor = by_bid[bid]
        out.append(
            Neighbour(combined, prior_prof, match, amt_ratio, same_vendor)
        )
    return out


def collect_top_neighbours_for_sample(
    enriched: Sequence[EnrichedBill],
    sample_bill_ids: frozenset[int],
    idf: dict[str, float],
    *,
    top_k: int = 5,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
    token_config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> dict[int, list[Neighbour]]:
    """
    Rolling as-of priors: for each sampled bill, top neighbours from strictly
    earlier PDF-population bills (never self). Keys only bills in ``sample_bill_ids``.
    """
    ordered = sorted(enriched, key=lambda eb: (eb.bill.created, eb.bill.bill_id))
    prior_profiles: list[BillTokenProfile] = []
    prior_by_id: dict[int, BillTokenProfile] = {}
    token_index: dict[str, set[int]] = defaultdict(set)
    neighbours: dict[int, list[Neighbour]] = {}

    for eb in ordered:
        bid = eb.bill.bill_id
        if similarity_eligible(eb, token_config):
            qprof = token_profile(eb, idf, config=token_config)
            if bid in sample_bill_ids:
                neighbours[bid] = top_neighbours_any_vendor(
                    qprof,
                    idf,
                    top_k=top_k,
                    band_pct=band_pct,
                    cap_ratio=cap_ratio,
                    token_index=token_index,
                    prior_by_id=prior_by_id,
                )
            prior_profiles.append(qprof)
            prior_by_id[bid] = qprof
            for tok in qprof.tokens:
                token_index[tok].add(bid)
        elif bid in sample_bill_ids:
            neighbours[bid] = []

    return neighbours


def history_is_strong(pred: ModePrediction) -> bool:
    if pred.predicted_scc_id is None:
        return False
    raw = pred.raw_score
    return raw is not None and raw >= HISTORY_STRONG_RAW_SCORE


def history_abstained(pred: ModePrediction) -> bool:
    if pred.prior_bill_count < MIN_PRIOR_BILLS:
        return True
    return pred.predicted_scc_id is None


def history_miss(pred: ModePrediction, label_scc_id: int) -> bool:
    if history_abstained(pred):
        return True
    return pred.predicted_scc_id != label_scc_id


def line_count_bucket(line_count: int) -> str:
    if line_count <= 1:
        return "1"
    if line_count <= 3:
        return "2-3"
    return "4+"


def score_all_rolling(
    enriched: Sequence[EnrichedBill],
    idf: dict[str, float],
    *,
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    token_config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> list[FullyScoredBill]:
    history_scored = score_bills_rolling([eb.bill for eb in enriched])
    history_by_id = {sb.bill.bill_id: sb for sb in history_scored}

    ordered = sorted(enriched, key=lambda eb: (eb.bill.created, eb.bill.bill_id))
    prior_profiles: list[BillTokenProfile] = []
    prior_by_id: dict[int, BillTokenProfile] = {}
    token_index: dict[str, set[int]] = defaultdict(set)

    out: list[FullyScoredBill] = []

    for eb in ordered:
        hs = history_by_id[eb.bill.bill_id]
        if similarity_eligible(eb, token_config):
            qprof = token_profile(eb, idf, config=token_config)
            sim_preds = predict_all_variants(
                qprof,
                prior_profiles,
                idf,
                top_k=top_k,
                band_pct=band_pct,
                cap_ratio=cap_ratio,
                token_index=token_index,
                prior_by_id=prior_by_id,
            )
        else:
            qprof = None
            sim_preds = empty_similar_predictions()
        out.append(
            FullyScoredBill(
                enriched=eb,
                history_a=hs.mode_a,
                history_b=hs.mode_b,
                similar=sim_preds,
            )
        )
        if qprof is not None:
            prior_profiles.append(qprof)
            prior_by_id[eb.bill.bill_id] = qprof
            for tok in qprof.tokens:
                token_index[tok].add(eb.bill.bill_id)

    return out


def rescore_similar_rolling(
    enriched: Sequence[EnrichedBill],
    idf: dict[str, float],
    *,
    history_source: Sequence[FullyScoredBill],
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    token_config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> list[FullyScoredBill]:
    """Re-run similarity only; history modes are copied from ``history_source``."""
    history_by_id = {row.enriched.bill.bill_id: row for row in history_source}

    ordered = sorted(enriched, key=lambda eb: (eb.bill.created, eb.bill.bill_id))
    prior_profiles: list[BillTokenProfile] = []
    prior_by_id: dict[int, BillTokenProfile] = {}
    token_index: dict[str, set[int]] = defaultdict(set)

    out: list[FullyScoredBill] = []

    for eb in ordered:
        hs_row = history_by_id[eb.bill.bill_id]
        if similarity_eligible(eb, token_config):
            qprof = token_profile(eb, idf, config=token_config)
            sim_preds = predict_all_variants(
                qprof,
                prior_profiles,
                idf,
                top_k=top_k,
                band_pct=band_pct,
                cap_ratio=cap_ratio,
                token_index=token_index,
                prior_by_id=prior_by_id,
            )
        else:
            qprof = None
            sim_preds = empty_similar_predictions()
        out.append(
            FullyScoredBill(
                enriched=eb,
                history_a=hs_row.history_a,
                history_b=hs_row.history_b,
                similar=sim_preds,
            )
        )
        if qprof is not None:
            prior_profiles.append(qprof)
            prior_by_id[eb.bill.bill_id] = qprof
            for tok in qprof.tokens:
                token_index[tok].add(eb.bill.bill_id)

    return out


def eval_window_rows(
    scored: Sequence[FullyScoredBill],
    *,
    eval_since: date,
    eval_until: date,
    token_config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> list[FullyScoredBill]:
    rows = [
        row
        for row in scored
        if eval_since <= _date_only(row.enriched.bill.created) <= eval_until
    ]
    if (
        token_config.requires_pdf_population
        or token_config.population_bill_ids is not None
    ):
        rows = [row for row in rows if similarity_eligible(row.enriched, token_config)]
    return rows


def filter_blob_urls_to_coded_bills(
    blob_urls: Mapping[int, str],
    enriched: Sequence[EnrichedBill],
) -> dict[int, str]:
    coded_ids = {eb.bill.bill_id for eb in enriched}
    return {bid: url for bid, url in blob_urls.items() if bid in coded_ids}


def compute_population_counts(
    enriched: Sequence[EnrichedBill],
    *,
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
    token_config: TokenSourceConfig,
) -> PopulationCounts:
    pdf_ids = token_config.usable_pdf_bill_ids
    eval_all = sum(
        1
        for eb in enriched
        if eval_since <= _date_only(eb.bill.created) <= eval_until
    )
    cal_all = sum(
        1
        for eb in enriched
        if cal_since <= _date_only(eb.bill.created) <= cal_until
    )
    eval_pdf = sum(
        1
        for eb in enriched
        if eval_since <= _date_only(eb.bill.created) <= eval_until
        and eb.bill.bill_id in pdf_ids
    )
    cal_pdf = sum(
        1
        for eb in enriched
        if cal_since <= _date_only(eb.bill.created) <= cal_until
        and eb.bill.bill_id in pdf_ids
    )
    return PopulationCounts(
        all_coded_bills=len(enriched),
        with_usable_pdf_text=len(pdf_ids),
        eval_all=eval_all,
        eval_pdf_population=eval_pdf,
        cal_all=cal_all,
        cal_pdf_population=cal_pdf,
    )


def load_scc_name_tokens_from_db() -> dict[int, frozenset[str]]:
    out: dict[int, frozenset[str]] = {}
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_CATALOG_MASK_SQL)
            for row in cur.fetchall():
                parts: list[str] = []
                if row.Name:
                    parts.append(str(row.Name))
                if row.CostCodeName:
                    parts.append(str(row.CostCodeName))
                out[int(row.Id)] = tokenize_bill_text(" ".join(parts))
        finally:
            conn.rollback()
    return out


def catalog_mask_from_scc_names(
    scc_name_tokens: Mapping[int, frozenset[str]],
) -> frozenset[str]:
    return frozenset(tok for toks in scc_name_tokens.values() for tok in toks)


def load_catalog_mask_tokens_from_db() -> frozenset[str]:
    return catalog_mask_from_scc_names(load_scc_name_tokens_from_db())


def _read_blob_with_timeout(
    read_blob: Callable[[str], str],
    url: str,
    timeout_seconds: float,
) -> str:
    if timeout_seconds <= 0:
        return read_text_blob_with_retry(read_blob, url)
    result: list[str] = []
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            result.append(read_text_blob_with_retry(read_blob, url))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(f"blob read timed out after {timeout_seconds}s")
    if error:
        raise error[0]
    if not result:
        raise RuntimeError("blob read finished without result")
    return result[0]


def _read_one_pdf_text(
    bill_id: int,
    url: str,
    read_blob: Callable[[str], str],
    timeout_seconds: float,
) -> tuple[int, Optional[frozenset[str]], str]:
    """Returns (bill_id, tokens_or_none, outcome) where outcome is ok|stale|empty|error."""
    try:
        raw = _read_blob_with_timeout(read_blob, url, timeout_seconds)
    except (TimeoutError, Exception):
        return bill_id, None, "error"
    text, outcome = _pdf_text_from_raw_blob(raw)
    if outcome != "ok" or text is None:
        return bill_id, None, outcome
    return bill_id, tokenize_bill_text(text), "ok"


def load_pdf_text_by_bill_id(
    blob_urls: Mapping[int, str],
    *,
    read_blob: Callable[[str], str],
    max_workers: int = DEFAULT_PDF_READ_WORKERS,
    timeout_seconds: float = DEFAULT_PDF_READ_TIMEOUT_SECONDS,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[int, frozenset[str]], PdfTextLoadStats]:
    linked = len(blob_urls)
    stats = PdfTextLoadStats(linked=linked)
    pdf_tokens: dict[int, frozenset[str]] = {}
    if not blob_urls:
        return pdf_tokens, stats

    items = list(blob_urls.items())
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _read_one_pdf_text, bid, url, read_blob, timeout_seconds
            ): bid
            for bid, url in items
        }
        for fut in as_completed(futures):
            done += 1
            if progress is not None:
                progress(done, linked)
            try:
                bill_id, tokens, outcome = fut.result()
            except Exception:
                stats = PdfTextLoadStats(
                    linked=stats.linked,
                    read_ok=stats.read_ok,
                    stale_redaction=stats.stale_redaction,
                    read_error=stats.read_error + 1,
                    empty_content=stats.empty_content,
                )
                continue
            if outcome == "ok" and tokens is not None:
                pdf_tokens[bill_id] = tokens
                stats = PdfTextLoadStats(
                    linked=stats.linked,
                    read_ok=stats.read_ok + 1,
                    stale_redaction=stats.stale_redaction,
                    read_error=stats.read_error,
                    empty_content=stats.empty_content,
                )
            elif outcome == "stale":
                stats = PdfTextLoadStats(
                    linked=stats.linked,
                    read_ok=stats.read_ok,
                    stale_redaction=stats.stale_redaction + 1,
                    read_error=stats.read_error,
                    empty_content=stats.empty_content,
                )
            elif outcome == "empty":
                stats = PdfTextLoadStats(
                    linked=stats.linked,
                    read_ok=stats.read_ok,
                    stale_redaction=stats.stale_redaction,
                    read_error=stats.read_error,
                    empty_content=stats.empty_content + 1,
                )
            else:
                stats = PdfTextLoadStats(
                    linked=stats.linked,
                    read_ok=stats.read_ok,
                    stale_redaction=stats.stale_redaction,
                    read_error=stats.read_error + 1,
                    empty_content=stats.empty_content,
                )
    return pdf_tokens, stats


def compute_leakage_shares(
    eval_rows: Sequence[FullyScoredBill],
    *,
    token_config: TokenSourceConfig,
    scc_name_tokens: Mapping[int, frozenset[str]],
    catalog_mask_tokens: frozenset[str],
) -> dict[str, Optional[float]]:
    """Share of eval bills whose tokens hit own label SCC name and any catalog name."""
    n = len(eval_rows)
    if n == 0:
        return {"own_label_scc_name": None, "any_catalog_name": None}
    own_hits = 0
    catalog_hits = 0
    for row in eval_rows:
        toks = bill_tokens(row.enriched, config=token_config)
        label_tokens = scc_name_tokens.get(row.enriched.bill.label_scc_id, frozenset())
        if toks & label_tokens:
            own_hits += 1
        if catalog_mask_tokens and toks & catalog_mask_tokens:
            catalog_hits += 1
    return {
        "own_label_scc_name": own_hits / n,
        "any_catalog_name": catalog_hits / n if catalog_mask_tokens else None,
    }


class _PredView:
    __slots__ = ("predicted_scc_id",)

    def __init__(self, predicted_scc_id: Optional[int]) -> None:
        self.predicted_scc_id = predicted_scc_id


def top1_metrics(
    rows: Sequence[FullyScoredBill],
    pred_fn: Callable[[FullyScoredBill], Any],
) -> dict[str, Optional[Decimal]]:
    """Top-1 over all (abstain = wrong) plus coverage and precision on covered bills."""
    all_pairs = _top1_pairs(rows, pred_fn)
    covered_pairs: list[tuple[bool, Decimal]] = []
    total_dollar = Decimal(0)
    covered_dollar = Decimal(0)
    for row in rows:
        pred = pred_fn(row)
        pred_id = (
            pred.predicted_scc_id
            if hasattr(pred, "predicted_scc_id")
            else pred
        )
        amt = row.enriched.bill.total_abs_amount
        total_dollar += amt
        if pred_id is not None:
            label = row.enriched.bill.label_scc_id
            covered_pairs.append((pred_id == label, amt))
            covered_dollar += amt
    n = len(all_pairs)
    return {
        "bill": _weighted_accuracy(all_pairs),
        "dollar": _dollar_accuracy(all_pairs),
        "coverage_bill": (
            Decimal(len(covered_pairs)) / Decimal(n) if n else None
        ),
        "coverage_dollar": (
            covered_dollar / total_dollar if total_dollar > 0 else None
        ),
        "covered_bill": _weighted_accuracy(covered_pairs),
        "covered_dollar": _dollar_accuracy(covered_pairs),
    }


def head_to_head_block(
    eval_rows: Sequence[FullyScoredBill],
    pred_fn: Callable[[FullyScoredBill], Any],
) -> dict[str, Any]:
    m = top1_metrics(eval_rows, pred_fn)
    return {
        "bill": float(m["bill"]) if m["bill"] is not None else None,
        "dollar": float(m["dollar"]) if m["dollar"] is not None else None,
        "coverage_bill": (
            float(m["coverage_bill"]) if m["coverage_bill"] is not None else None
        ),
        "coverage_dollar": (
            float(m["coverage_dollar"])
            if m["coverage_dollar"] is not None
            else None
        ),
        "covered_bill": (
            float(m["covered_bill"]) if m["covered_bill"] is not None else None
        ),
        "covered_dollar": (
            float(m["covered_dollar"])
            if m["covered_dollar"] is not None
            else None
        ),
    }


def _top1_pairs(
    rows: Sequence[FullyScoredBill],
    pred_fn: Callable[[FullyScoredBill], Any],
) -> list[tuple[bool, Decimal]]:
    """All evaluated rows; abstention counts as incorrect (common bill set)."""
    pairs: list[tuple[bool, Decimal]] = []
    for row in rows:
        pred = pred_fn(row)
        pred_id = (
            pred.predicted_scc_id
            if hasattr(pred, "predicted_scc_id")
            else pred
        )
        label = row.enriched.bill.label_scc_id
        amt = row.enriched.bill.total_abs_amount
        if pred_id is None:
            pairs.append((False, amt))
        else:
            pairs.append((pred_id == label, amt))
    return pairs


@dataclass(frozen=True)
class CalibrationSelection:
    best_variant: str
    best_variant_mode_b: str
    calibration_metrics: dict[str, dict[str, Optional[float]]]
    warnings: tuple[str, ...]


def _metrics_from_cal_records(
    records: Sequence[tuple[bool, bool, Decimal]],
) -> dict[str, Optional[float]]:
    """``had_prediction``, ``correct_if_covered``, amount per calibration bill."""
    all_pairs: list[tuple[bool, Decimal]] = []
    covered_pairs: list[tuple[bool, Decimal]] = []
    total_dollar = Decimal(0)
    covered_dollar = Decimal(0)
    n_covered = 0
    for had_pred, ok, amt in records:
        total_dollar += amt
        if had_pred:
            all_pairs.append((ok, amt))
            covered_pairs.append((ok, amt))
            covered_dollar += amt
            n_covered += 1
        else:
            all_pairs.append((False, amt))
    n = len(records)
    bill_acc = _weighted_accuracy(all_pairs)
    dollar_acc = _dollar_accuracy(all_pairs)
    cov_bill_acc = _weighted_accuracy(covered_pairs) if covered_pairs else None
    cov_dollar_acc = _dollar_accuracy(covered_pairs) if covered_pairs else None
    return {
        "bill": float(bill_acc) if bill_acc is not None else None,
        "dollar": float(dollar_acc) if dollar_acc is not None else None,
        "coverage_bill": (n_covered / n) if n else None,
        "coverage_dollar": (
            float(covered_dollar / total_dollar) if total_dollar > 0 else None
        ),
        "covered_bill": float(cov_bill_acc) if cov_bill_acc is not None else None,
        "covered_dollar": float(cov_dollar_acc) if cov_dollar_acc is not None else None,
    }


def _pick_best_by_cal_bill_acc(
    cal_metrics: dict[str, dict[str, Optional[float]]],
    candidates: Sequence[str],
) -> str:
    best = candidates[0]
    best_acc: Optional[float] = None
    for variant in candidates:
        acc = cal_metrics.get(variant, {}).get("bill")
        if acc is None:
            continue
        if best_acc is None or acc > best_acc:
            best_acc = acc
            best = variant
    return best


def _selection_warnings(
    cal_metrics: dict[str, dict[str, Optional[float]]],
    candidates: Sequence[str],
) -> list[str]:
    warnings: list[str] = []
    accs: dict[str, float] = {}
    for v in candidates:
        bill = cal_metrics.get(v, {}).get("bill")
        if bill is not None:
            accs[v] = bill
    if accs:
        top_acc = max(accs.values())
        tied = [v for v, a in accs.items() if a == top_acc]
        if len(tied) > 1:
            warnings.append(
                "Calibration variant selection tied on top-1-over-all: "
                + ", ".join(sorted(tied))
            )
    zero_cov = [
        v
        for v in candidates
        if cal_metrics.get(v, {}).get("coverage_bill") == 0.0
    ]
    if len(zero_cov) == len(candidates):
        warnings.append(
            "Every similarity variant has zero neighbour coverage on the calibration window."
        )
    return warnings


def pick_variants_on_calibration(
    enriched: Sequence[EnrichedBill],
    *,
    cal_since: date,
    cal_until: date,
    idf_for_selection: dict[str, float],
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    token_config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
) -> CalibrationSelection:
    """
    One rolling pass over bills; pick Mode A and Mode B variants from the same
    per-variant calibration scores (top-1-over-all on the calibration window only).
    """
    ordered = sorted(enriched, key=lambda eb: (eb.bill.created, eb.bill.bill_id))
    prior_profiles: list[BillTokenProfile] = []
    prior_by_id: dict[int, BillTokenProfile] = {}
    token_index: dict[str, set[int]] = defaultdict(set)

    cal_records: dict[str, list[tuple[bool, bool, Decimal]]] = {
        v: [] for v in VARIANTS
    }

    for eb in ordered:
        d = _date_only(eb.bill.created)
        eligible = similarity_eligible(eb, token_config)
        qprof = (
            token_profile(eb, idf_for_selection, config=token_config)
            if eligible
            else None
        )
        if cal_since <= d <= cal_until and qprof is not None:
            sims = predict_all_variants(
                qprof,
                prior_profiles,
                idf_for_selection,
                top_k=top_k,
                band_pct=band_pct,
                cap_ratio=cap_ratio,
                token_index=token_index,
                prior_by_id=prior_by_id,
            )
            label = eb.bill.label_scc_id
            amt = eb.bill.total_abs_amount
            for v in VARIANTS:
                sp = sims[v]
                if sp.predicted_scc_id is None:
                    cal_records[v].append((False, False, amt))
                else:
                    cal_records[v].append(
                        (True, sp.predicted_scc_id == label, amt)
                    )
        if qprof is not None:
            prior_profiles.append(qprof)
            prior_by_id[eb.bill.bill_id] = qprof
            for tok in qprof.tokens:
                token_index[tok].add(eb.bill.bill_id)

    cal_metrics = {
        v: _metrics_from_cal_records(cal_records[v]) for v in VARIANTS
    }
    warnings = _selection_warnings(cal_metrics, VARIANTS)
    return CalibrationSelection(
        best_variant=_pick_best_by_cal_bill_acc(cal_metrics, VARIANTS),
        best_variant_mode_b=_pick_best_by_cal_bill_acc(
            cal_metrics, VARIANTS_NO_PROJECT
        ),
        calibration_metrics=cal_metrics,
        warnings=tuple(warnings),
    )


def pick_best_similarity_variant(
    enriched: Sequence[EnrichedBill],
    *,
    cal_since: date,
    cal_until: date,
    idf_for_selection: dict[str, float],
    top_k: int,
    band_pct: float,
    cap_ratio: float,
    candidates: Sequence[str] = VARIANTS,
) -> str:
    """Choose variant by top-1-over-all on the calibration window (single-variant API)."""
    sel = pick_variants_on_calibration(
        enriched,
        cal_since=cal_since,
        cal_until=cal_until,
        idf_for_selection=idf_for_selection,
        top_k=top_k,
        band_pct=band_pct,
        cap_ratio=cap_ratio,
    )
    if candidates == VARIANTS_NO_PROJECT:
        return sel.best_variant_mode_b
    return sel.best_variant


def combined_prediction(
    row: FullyScoredBill,
    *,
    best_variant: str,
) -> Optional[int]:
    hist = row.history_a
    if history_is_strong(hist):
        return hist.predicted_scc_id
    sim = row.similar[best_variant]
    if sim.predicted_scc_id is not None:
        return sim.predicted_scc_id
    return hist.predicted_scc_id


def combined_prediction_mode_b(
    row: FullyScoredBill,
    *,
    best_variant: str,
) -> Optional[int]:
    hist = row.history_b
    if history_is_strong(hist):
        return hist.predicted_scc_id
    sim = row.similar[best_variant]
    if sim.predicted_scc_id is not None:
        return sim.predicted_scc_id
    return hist.predicted_scc_id


def confidence_bar_sweep(
    eval_rows: Sequence[FullyScoredBill],
    variant: str,
    *,
    bars: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.85, 0.90),
) -> list[tuple[float, Optional[Decimal], Optional[Decimal]]]:
    eval_n = len(eval_rows)
    scored_rows: list[tuple[Decimal, bool]] = []
    for row in eval_rows:
        pred = row.similar[variant]
        if pred.predicted_scc_id is None or pred.raw_score is None:
            continue
        ok = pred.predicted_scc_id == row.enriched.bill.label_scc_id
        scored_rows.append((pred.raw_score, ok))

    out: list[tuple[float, Optional[Decimal], Optional[Decimal]]] = []
    for bar in bars:
        threshold = Decimal(str(bar))
        subset = [ok for score, ok in scored_rows if score >= threshold]
        if not subset:
            out.append((bar, None, None))
            continue
        prec = _weighted_accuracy([(ok, Decimal("1")) for ok in subset])
        cov = Decimal(len(subset)) / Decimal(eval_n) if eval_n else None
        out.append((bar, prec, cov))
    return out


@dataclass
class FillInMetrics:
    abstain_count: int
    abstain_correct: dict[str, int]
    weak_count: int
    weak_correct: dict[str, int]
    coverage_with_neighbour: dict[str, Decimal]


_ABSENT_SIMILAR = SimilarPrediction(None, None, 0, 0)


def _similar_prediction(row: FullyScoredBill, variant: str) -> SimilarPrediction:
    return row.similar.get(variant, _ABSENT_SIMILAR)


def compute_fill_in_metrics(
    eval_rows: Sequence[FullyScoredBill],
) -> FillInMetrics:
    abstain_n = 0
    abstain_correct: dict[str, int] = {v: 0 for v in VARIANTS}
    weak_n = 0
    weak_correct: dict[str, int] = {v: 0 for v in VARIANTS}
    neighbour_hits: dict[str, int] = {v: 0 for v in VARIANTS}
    eval_n = len(eval_rows)

    for row in eval_rows:
        hist = row.history_a
        label = row.enriched.bill.label_scc_id
        for v in VARIANTS:
            if _similar_prediction(row, v).top_k_used >= 1:
                neighbour_hits[v] += 1

        if history_abstained(hist):
            abstain_n += 1
            for v in VARIANTS:
                sp = _similar_prediction(row, v)
                if sp.predicted_scc_id is not None and sp.predicted_scc_id == label:
                    abstain_correct[v] += 1
        elif not history_is_strong(hist):
            weak_n += 1
            for v in VARIANTS:
                sp = _similar_prediction(row, v)
                if sp.predicted_scc_id is not None and sp.predicted_scc_id == label:
                    weak_correct[v] += 1

    coverage = {
        v: (Decimal(neighbour_hits[v]) / Decimal(eval_n) if eval_n else Decimal("0"))
        for v in VARIANTS
    }
    return FillInMetrics(
        abstain_count=abstain_n,
        abstain_correct=abstain_correct,
        weak_count=weak_n,
        weak_correct=weak_correct,
        coverage_with_neighbour=coverage,
    )


def compute_trigger_rates(
    eval_rows: Sequence[FullyScoredBill],
) -> dict[str, Optional[Decimal]]:
    miss_count = 0
    fixed: dict[str, int] = {v: 0 for v in VARIANTS}
    for row in eval_rows:
        hist = row.history_a
        if not history_miss(hist, row.enriched.bill.label_scc_id):
            continue
        miss_count += 1
        label = row.enriched.bill.label_scc_id
        for v in VARIANTS:
            sp = row.similar[v]
            if sp.predicted_scc_id is not None and sp.predicted_scc_id == label:
                fixed[v] += 1
    return {
        v: (Decimal(fixed[v]) / Decimal(miss_count) if miss_count else None)
        for v in VARIANTS
    }


def _breakdown_bucket_metrics(
    rows: Sequence[FullyScoredBill],
    variant: str,
) -> dict[str, Optional[float]]:
    m = top1_metrics(rows, lambda r: r.similar[variant])
    return {
        "n": len(rows),
        "coverage_bill": (
            float(m["coverage_bill"]) if m["coverage_bill"] is not None else None
        ),
        "bill": float(m["bill"]) if m["bill"] is not None else None,
        "covered_bill": (
            float(m["covered_bill"]) if m["covered_bill"] is not None else None
        ),
    }


def breakdown_by_vendor_consistency(
    eval_rows: Sequence[FullyScoredBill],
    all_bills: Sequence[EnrichedBill],
    variant: str,
) -> dict[str, dict[str, Optional[float]]]:
    vendor_votes = build_vendor_priors([eb.bill for eb in all_bills])
    buckets: dict[str, list[FullyScoredBill]] = defaultdict(list)
    for row in eval_rows:
        b = row.enriched.bill
        priors = priors_before(
            vendor_votes[b.vendor_id],
            as_of=b.created,
            exclude_bill_id=b.bill_id,
        )
        share = _vendor_consistency_share(priors)
        # No prior bills for this vendor: the cold-start case similar bills exist for.
        bucket = "no_prior" if share is None else _consistency_bucket(share)
        buckets[bucket].append(row)
    return {
        k: _breakdown_bucket_metrics(v, variant)
        for k, v in sorted(buckets.items())
    }


def breakdown_by_line_count(
    eval_rows: Sequence[FullyScoredBill],
    variant: str,
) -> dict[str, dict[str, Optional[float]]]:
    buckets: dict[str, list[FullyScoredBill]] = defaultdict(list)
    for row in eval_rows:
        bucket = line_count_bucket(row.enriched.line_count)
        buckets[bucket].append(row)
    return {
        k: _breakdown_bucket_metrics(v, variant)
        for k, v in sorted(buckets.items())
    }


def load_enriched_bills_from_db() -> list[EnrichedBill]:
    rows: list[EnrichedBill] = []
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_enriched_load_sql())
            for row in cur.fetchall():
                rows.append(
                    EnrichedBill(
                        bill=_bill_row_from_sql_row(row),
                        memo=str(row.Memo or ""),
                        line_descriptions=str(row.LineDescriptions or ""),
                        line_count=int(row.LineCount or 0),
                    )
                )
        finally:
            conn.rollback()
    return rows


def build_summary(
    scored: Sequence[FullyScoredBill],
    *,
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
    best_variant: str,
    best_variant_mode_b: str,
    calibration_selection: CalibrationSelection,
    runtime_seconds: float,
    bill_count: int,
    constants: dict[str, Any],
    token_config: TokenSourceConfig = _DEFAULT_TOKEN_CONFIG,
    population: PopulationCounts | None = None,
    pdf_load_stats: PdfTextLoadStats | None = None,
    leakage: dict[str, Optional[float]] | None = None,
    leakage_descriptions: dict[str, Optional[float]] | None = None,
    masked_descriptions_h2h: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    eval_rows = eval_window_rows(
        scored,
        eval_since=eval_since,
        eval_until=eval_until,
        token_config=token_config,
    )
    h2h: dict[str, dict[str, Any]] = {}

    def acc_for(pred_fn: Callable[[FullyScoredBill], Any]) -> dict[str, Any]:
        return head_to_head_block(eval_rows, pred_fn)

    h2h["history_a"] = acc_for(lambda r: r.history_a)
    h2h["history_b"] = acc_for(lambda r: r.history_b)
    for v in VARIANTS:
        paired_with = "history_a" if v == "V_any_vendor_same_project" else None
        block = acc_for(lambda r, variant=v: r.similar[variant])
        if paired_with:
            block["paired_with_history"] = paired_with
            block["pairing_note"] = (
                "Uses evaluated bill label project; comparable to history Mode A only."
            )
        h2h[v] = block

    h2h["combined"] = acc_for(
        lambda r: _PredView(combined_prediction(r, best_variant=best_variant))
    )
    h2h["combined_mode_b"] = acc_for(
        lambda r: _PredView(
            combined_prediction_mode_b(r, best_variant=best_variant_mode_b)
        )
    )

    fill = compute_fill_in_metrics(eval_rows)
    abstain_acc = {
        v: (
            Decimal(fill.abstain_correct[v]) / Decimal(fill.abstain_count)
            if fill.abstain_count
            else None
        )
        for v in VARIANTS
    }
    weak_acc = {
        v: (
            Decimal(fill.weak_correct[v]) / Decimal(fill.weak_count)
            if fill.weak_count
            else None
        )
        for v in VARIANTS
    }

    trig = compute_trigger_rates(eval_rows)

    hist_acc = h2h["history_a"]["bill"]
    combined_acc = h2h["combined"]["bill"]
    hist_b_acc = h2h["history_b"]["bill"]
    combined_b_acc = h2h["combined_mode_b"]["bill"]

    cal_map = fit_calibration_similar(scored, best_variant, cal_since, cal_until)

    all_bills = [row.enriched for row in scored]

    pop_block: dict[str, Any] | None = None
    if population is not None:
        pop_block = {
            "all_coded_bills": population.all_coded_bills,
            "with_usable_pdf_text": population.with_usable_pdf_text,
            "eval_window_all": population.eval_all,
            "eval_window_pdf_population": population.eval_pdf_population,
            "cal_window_all": population.cal_all,
            "cal_window_pdf_population": population.cal_pdf_population,
        }
    pdf_stats_block: dict[str, int] | None = None
    if pdf_load_stats is not None:
        pdf_stats_block = {
            "linked": pdf_load_stats.linked,
            "read_ok": pdf_load_stats.read_ok,
            "stale_redaction": pdf_load_stats.stale_redaction,
            "read_error": pdf_load_stats.read_error,
            "empty_content": pdf_load_stats.empty_content,
        }

    return {
        "model_version": MODEL_VERSION,
        "history_model_version": HISTORY_MODEL_VERSION,
        "text_source": token_config.text_source,
        "history_prior_note": (
            "History Modes A/B use all coded bills before each eval bill (not restricted "
            "to the PDF-text population)."
            if token_config.requires_pdf_population
            else None
        ),
        "population": pop_block,
        "pdf_blob_load": pdf_stats_block,
        "leakage_check": leakage,
        "leakage_check_descriptions_on_pdf_eval": leakage_descriptions,
        "masked_descriptions_on_pdf_population": masked_descriptions_h2h,
        "scoring_convention": (
            "head_to_head top-1 over all evaluated bills; abstention counts as wrong; "
            "coverage and covered precision reported separately"
        ),
        "best_variant": best_variant,
        "best_variant_mode_b": best_variant_mode_b,
        "calibration_variant_metrics": calibration_selection.calibration_metrics,
        "calibration_selection_warnings": list(calibration_selection.warnings),
        "idf_eval_corpus": f"{cal_since}..{cal_until}",
        "idf_selection_corpus": f"{cal_since}..{cal_until}",
        "head_to_head": h2h,
        "fill_in": {
            "abstain_count": fill.abstain_count,
            "weak_count": fill.weak_count,
            "abstain_accuracy": {
                k: float(v) if v is not None else None for k, v in abstain_acc.items()
            },
            "weak_accuracy": {
                k: float(v) if v is not None else None for k, v in weak_acc.items()
            },
            "coverage": {
                k: float(v) for k, v in fill.coverage_with_neighbour.items()
            },
        },
        "trigger_on_history_miss": {
            k: float(v) if v is not None else None for k, v in trig.items()
        },
        "combined_policy": {
            "history_a": hist_acc,
            "combined": combined_acc,
            "lift_bill_top1_over_all": (
                float(Decimal(str(combined_acc)) - Decimal(str(hist_acc)))
                if combined_acc is not None and hist_acc is not None
                else None
            ),
            "history_b": hist_b_acc,
            "combined_mode_b": combined_b_acc,
            "lift_mode_b_bill_top1_over_all": (
                float(Decimal(str(combined_b_acc)) - Decimal(str(hist_b_acc)))
                if combined_b_acc is not None and hist_b_acc is not None
                else None
            ),
        },
        "calibration_best_variant": cal_map.to_json(),
        "confidence_sweep": [
            (bar, float(prec) if prec is not None else None, float(cov) if cov is not None else None)
            for bar, prec, cov in confidence_bar_sweep(eval_rows, best_variant)
        ],
        "vendor_consistency": breakdown_by_vendor_consistency(
            eval_rows, all_bills, best_variant
        ),
        "line_count": breakdown_by_line_count(eval_rows, best_variant),
        "runtime_seconds": runtime_seconds,
        "bill_count": bill_count,
        "constants": constants,
        "windows": {
            "cal_since": cal_since.isoformat(),
            "cal_until": cal_until.isoformat(),
            "eval_since": eval_since.isoformat(),
            "eval_until": eval_until.isoformat(),
        },
    }


def fit_calibration_similar(
    scored: Sequence[FullyScoredBill],
    variant: str,
    since: date,
    until: date,
) -> CalibrationMap:
    pseudo: list[ScoredBill] = []
    for row in scored:
        d = _date_only(row.enriched.bill.created)
        if d < since or d > until:
            continue
        sp = row.similar[variant]
        if sp.raw_score is None or sp.predicted_scc_id is None:
            continue
        pred = ModePrediction(
            mode="sim",
            predicted_scc_id=sp.predicted_scc_id,
            predicted_project_id=row.enriched.bill.label_project_id,
            source=variant,
            raw_score=sp.raw_score,
            top3_scc_ids=[],
            prior_bill_count=sp.neighbour_count,
        )
        pseudo.append(
            ScoredBill(bill=row.enriched.bill, mode_a=pred, mode_b=pred)
        )
    return fit_calibration(pseudo, mode="A", since=since, until=until)


def write_outputs(
    *,
    out_dir: Path,
    stamp: str,
    scored: Sequence[FullyScoredBill],
    summary: dict[str, Any],
    token_config: TokenSourceConfig | None = None,
    eval_rows: Sequence[FullyScoredBill] | None = None,
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"u691_report_{stamp}.md"
    csv_path = out_dir / f"u691_predictions_{stamp}.csv"
    json_path = out_dir / f"u691_summary_{stamp}.json"

    text_source = summary.get("text_source") or summary.get("constants", {}).get(
        "text_source", TEXT_SOURCE_DESCRIPTIONS
    )
    if text_source in (TEXT_SOURCE_PDF, TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS):
        if token_config is None or eval_rows is None:
            raise ValueError(
                "PDF mode write_outputs requires token_config and eval_rows"
            )

    json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )

    windows = summary["windows"]
    if eval_rows is None:
        eval_since = date.fromisoformat(windows["eval_since"])
        eval_until = date.fromisoformat(windows["eval_until"])
        if token_config is None:
            token_source = summary.get("text_source", TEXT_SOURCE_DESCRIPTIONS)
            token_config = TokenSourceConfig(text_source=str(token_source))
        eval_rows = eval_window_rows(
            scored,
            eval_since=eval_since,
            eval_until=eval_until,
            token_config=token_config,
        )

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "BillId",
                "VendorId",
                "variant",
                "predicted_scc_id",
                "label_scc_id",
                "raw_score",
                "neighbour_count",
                "correct",
            ]
        )
        for row in eval_rows:
            b = row.enriched.bill
            for variant in VARIANTS:
                pred = row.similar[variant]
                correct = ""
                if pred.predicted_scc_id is not None:
                    correct = (
                        "1"
                        if pred.predicted_scc_id == b.label_scc_id
                        else "0"
                    )
                writer.writerow(
                    [
                        b.bill_id,
                        b.vendor_id,
                        variant,
                        pred.predicted_scc_id if pred.predicted_scc_id is not None else "",
                        b.label_scc_id,
                        str(pred.raw_score) if pred.raw_score is not None else "",
                        pred.neighbour_count,
                        correct,
                    ]
                )
            for mode_key, hp in (("history_A", row.history_a), ("history_B", row.history_b)):
                correct = ""
                if hp.predicted_scc_id is not None:
                    correct = (
                        "1" if hp.predicted_scc_id == b.label_scc_id else "0"
                    )
                writer.writerow(
                    [
                        b.bill_id,
                        b.vendor_id,
                        mode_key,
                        hp.predicted_scc_id if hp.predicted_scc_id is not None else "",
                        b.label_scc_id,
                        str(hp.raw_score) if hp.raw_score is not None else "",
                        hp.prior_bill_count,
                        correct,
                    ]
                )

    def fmt(v: Any) -> str:
        if v is None:
            return "n/a"
        return f"{float(v):.2%}"

    h2h = summary.get("head_to_head", {})
    fill = summary.get("fill_in", {})
    trig = summary.get("trigger_on_history_miss", {})
    combined = summary.get("combined_policy", {})
    best_variant = summary.get("best_variant", "")
    best_variant_b = summary.get("best_variant_mode_b", "")
    constants = summary.get("constants", {})

    pop = summary.get("population") or {}
    pdf_load = summary.get("pdf_blob_load") or {}
    leakage = summary.get("leakage_check") or {}
    leakage_desc = summary.get("leakage_check_descriptions_on_pdf_eval") or {}
    lines = [
        f"# U-691 similar bills backtest ({stamp})",
        "",
        f"- Harness: `{MODEL_VERSION}` (history `{HISTORY_MODEL_VERSION}`)",
        f"- Text source: `{summary.get('text_source', TEXT_SOURCE_DESCRIPTIONS)}`",
        f"- Bills loaded: {summary.get('bill_count', 0)}",
        f"- Runtime (scoring): {summary.get('runtime_seconds', 0):.2f}s",
        f"- Scoring: {summary.get('scoring_convention', '')}",
        f"- Calibration window: {windows['cal_since']} .. {windows['cal_until']}",
        f"- Evaluation window: {windows['eval_since']} .. {windows['eval_until']}",
        f"- Top-K neighbours: {constants.get('top_k', DEFAULT_TOP_K)}",
        f"- Amount band: ±{constants.get('amount_band_pct', DEFAULT_AMOUNT_BAND_PCT):.0%} "
        f"full weight; decay to 0 at {constants.get('amount_cap_ratio', DEFAULT_AMOUNT_BAND_CAP_RATIO):.1f}× ratio",
        f"- IDF (eval scoring): {summary.get('idf_eval_corpus', '')}",
        f"- IDF (variant selection): {summary.get('idf_selection_corpus', '')}",
        f"- Best similarity variant (calibration top-1-over-all): `{best_variant}`",
        f"- Best variant for Mode B combined: `{best_variant_b}`",
        "",
    ]
    hist_note = summary.get("history_prior_note")
    if hist_note:
        lines.append(f"- {hist_note}")
        lines.append("")
    if pop:
        lines.extend(
            [
                "## population",
                "",
                f"- all coded bills: {pop.get('all_coded_bills', 0)}",
                f"- with usable PDF text: {pop.get('with_usable_pdf_text', 0)}",
                f"- eval window (all / PDF population): "
                f"{pop.get('eval_window_all', 0)} / {pop.get('eval_window_pdf_population', 0)}",
                f"- cal window (all / PDF population): "
                f"{pop.get('cal_window_all', 0)} / {pop.get('cal_window_pdf_population', 0)}",
                "",
            ]
        )
    if pdf_load:
        lines.extend(
            [
                "## pdf_blob_load",
                "",
                f"- linked: {pdf_load.get('linked', 0)}",
                f"- read_ok: {pdf_load.get('read_ok', 0)}",
                f"- stale_redaction: {pdf_load.get('stale_redaction', 0)}",
                f"- read_error: {pdf_load.get('read_error', 0)}",
                f"- empty_content: {pdf_load.get('empty_content', 0)}",
                "",
            ]
        )
    if leakage:
        lines.extend(
            [
                "## leakage_check (eval population)",
                "",
                f"- share tokens ∩ own label SCC name: "
                f"{fmt(leakage.get('own_label_scc_name'))}",
                f"- share tokens ∩ any catalog name: "
                f"{fmt(leakage.get('any_catalog_name'))}",
                "",
            ]
        )
    if leakage_desc:
        lines.extend(
            [
                "## leakage_check (line descriptions on same PDF eval bills)",
                "",
                f"- share tokens ∩ own label SCC name: "
                f"{fmt(leakage_desc.get('own_label_scc_name'))}",
                f"- share tokens ∩ any catalog name: "
                f"{fmt(leakage_desc.get('any_catalog_name'))}",
                "",
            ]
        )
    masked_h2h = summary.get("masked_descriptions_on_pdf_population")
    if masked_h2h:
        lines.extend(
            [
                "## descriptions_catalog_masked (same PDF eval population)",
                "",
                "| variant | bill | dollar | cov_bill | cov_$ | cov_prec_bill | cov_prec_$ |",
                "|---------|------|--------|----------|-------|---------------|-------------|",
            ]
        )
        for v in VARIANTS:
            block = masked_h2h.get(v, {})
            lines.append(
                f"| {v} | {fmt(block.get('bill'))} | {fmt(block.get('dollar'))} | "
                f"{fmt(block.get('coverage_bill'))} | {fmt(block.get('coverage_dollar'))} | "
                f"{fmt(block.get('covered_bill'))} | {fmt(block.get('covered_dollar'))} |"
            )
        lines.append("")
    lines.extend(
        [
            "## head_to_head (top-1 over all | coverage bill | covered bill | covered dollar)",
            "",
            "| method | bill | dollar | cov_bill | cov_$ | cov_prec_bill | cov_prec_$ |",
            "|--------|------|--------|----------|-------|---------------|-------------|",
        ]
    )
    for key, label in (
        ("history_a", "history_a"),
        ("history_b", "history_b"),
        ("V_same_vendor", "V_same_vendor"),
        ("V_any_vendor", "V_any_vendor"),
        ("V_other_vendor", "V_other_vendor"),
        ("V_any_vendor_same_project", "V_any_vendor_same_project (Mode-A-paired)"),
        ("combined", "combined_A"),
        ("combined_mode_b", "combined_B"),
    ):
        block = h2h.get(key, {})
        lines.append(
            f"| {label} | {fmt(block.get('bill'))} | {fmt(block.get('dollar'))} | "
            f"{fmt(block.get('coverage_bill'))} | {fmt(block.get('coverage_dollar'))} | "
            f"{fmt(block.get('covered_bill'))} | {fmt(block.get('covered_dollar'))} |"
        )
    cal_var = summary.get("calibration_variant_metrics", {})
    cal_warn = summary.get("calibration_selection_warnings", [])
    lines.extend(
        [
            "",
            "V_any_vendor_same_project: pair with history_a only (uses label project).",
            "",
            "## calibration_variant_selection (top-1 over all on calibration window)",
            "",
            "| variant | bill | cov_bill | cov_prec_bill |",
            "|---------|------|----------|---------------|",
        ]
    )
    for v in VARIANTS:
        block = cal_var.get(v, {})
        lines.append(
            f"| {v} | {fmt(block.get('bill'))} | {fmt(block.get('coverage_bill'))} | "
            f"{fmt(block.get('covered_bill'))} |"
        )
    for w in cal_warn:
        lines.append(f"- **WARNING:** {w}")
    lines.extend(
        [
            "",
            "## fill_in",
            "",
            f"- abstain_count: {fill.get('abstain_count', 0)}",
            f"- weak_count: {fill.get('weak_count', 0)}",
            "",
            "| variant | abstain_acc | weak_acc | coverage (top_k≥1) |",
            "|---------|-------------|----------|-------------------|",
        ]
    )
    for v in VARIANTS:
        aa = fill.get("abstain_accuracy", {}).get(v)
        wa = fill.get("weak_accuracy", {}).get(v)
        lines.append(
            f"| {v} | {fmt(aa)} | {fmt(wa)} | "
            f"{fmt(fill.get('coverage', {}).get(v))} |"
        )
    lines.extend(
        [
            "",
            "## trigger",
            "",
            "| variant | miss_fix_rate |",
            "|---------|---------------|",
        ]
    )
    for v in VARIANTS:
        lines.append(f"| {v} | {fmt(trig.get(v))} |")
    lines.extend(
        [
            "",
            "## combined",
            "",
            f"- history_a top-1 over all: {fmt(combined.get('history_a'))}",
            f"- combined top-1 over all: {fmt(combined.get('combined'))}",
            f"- lift (combined − history_a): {fmt(combined.get('lift_bill_top1_over_all'))}",
            f"- history_b top-1 over all: {fmt(combined.get('history_b'))}",
            f"- combined_B top-1 over all: {fmt(combined.get('combined_mode_b'))}",
            f"- lift (combined_B − history_b): {fmt(combined.get('lift_mode_b_bill_top1_over_all'))}",
            "",
            "### vendor_consistency",
            "",
            "| bucket | n | coverage | top-1 over all | cov_prec_bill |",
            "|--------|---|----------|----------------|---------------|",
        ]
    )
    for bucket, block in summary.get("vendor_consistency", {}).items():
        lines.append(
            f"| {bucket} | {block.get('n', 0)} | {fmt(block.get('coverage_bill'))} | "
            f"{fmt(block.get('bill'))} | {fmt(block.get('covered_bill'))} |"
        )
    lines.extend(
        [
            "",
            "### line_count",
            "",
            "| lines | n | coverage | top-1 over all | cov_prec_bill |",
            "|-------|---|----------|----------------|---------------|",
        ]
    )
    for bucket, block in summary.get("line_count", {}).items():
        lines.append(
            f"| {bucket} | {block.get('n', 0)} | {fmt(block.get('coverage_bill'))} | "
            f"{fmt(block.get('bill'))} | {fmt(block.get('covered_bill'))} |"
        )
    lines.extend(
        [
            "",
            "### confidence_sweep (coverage over all eval bills)",
            "",
            "| bar | precision | coverage |",
            "|-----|-----------|----------|",
        ]
    )
    for bar, prec, cov in summary.get("confidence_sweep", []):
        lines.append(f"| {bar:.2f} | {fmt(prec)} | {fmt(cov)} |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert_outputs_privacy_safe(csv_path)
    assert_outputs_privacy_safe(json_path)
    assert_outputs_privacy_safe(report_path)
    return report_path, csv_path, json_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="U-691 read-only similar-bills backtest vs history."
    )
    ap.add_argument(
        "--run",
        action="store_true",
        help="Score and write outputs (default: dry-run population only)",
    )
    ap.add_argument(
        "--cal-since",
        type=date.fromisoformat,
        default=DEFAULT_CAL_SINCE,
    )
    ap.add_argument(
        "--cal-until",
        type=date.fromisoformat,
        default=DEFAULT_CAL_UNTIL,
    )
    ap.add_argument(
        "--eval-since",
        type=date.fromisoformat,
        default=DEFAULT_EVAL_SINCE,
    )
    ap.add_argument(
        "--eval-until",
        type=date.fromisoformat,
        default=DEFAULT_EVAL_UNTIL,
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs/design/u691"),
    )
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument(
        "--amount-band",
        type=float,
        default=DEFAULT_AMOUNT_BAND_PCT,
        dest="amount_band_pct",
        help="Full-weight band as fraction (default 0.25 = ±25%%)",
    )
    ap.add_argument(
        "--amount-cap-ratio",
        type=float,
        default=DEFAULT_AMOUNT_BAND_CAP_RATIO,
        help="Zero weight beyond this total ratio (default 4.0)",
    )
    ap.add_argument(
        "--text-source",
        choices=TEXT_SOURCES,
        default=TEXT_SOURCE_DESCRIPTIONS,
        help="Token source for similarity (default: line descriptions + memo)",
    )
    ap.add_argument(
        "--mask-catalog-in-descriptions",
        action="store_true",
        help="Remove SubCostCode/CostCode name tokens from description/memo tokens",
    )
    ap.add_argument(
        "--pdf-read-workers",
        type=int,
        default=DEFAULT_PDF_READ_WORKERS,
        help="Concurrent blob reads for PDF text (default 8)",
    )
    ap.add_argument(
        "--pdf-read-timeout",
        type=float,
        default=DEFAULT_PDF_READ_TIMEOUT_SECONDS,
        help="Per-blob read timeout in seconds (default 120)",
    )
    args = ap.parse_args(argv)

    validate_windows(
        args.cal_since,
        args.cal_until,
        args.eval_since,
        args.eval_until,
    )

    enriched = load_enriched_bills_from_db()
    n = len(enriched)

    catalog_mask_tokens: frozenset[str] = frozenset()
    scc_name_tokens: dict[int, frozenset[str]] = {}

    pdf_tokens_by_bill_id: dict[int, frozenset[str]] = {}
    pdf_load_stats: PdfTextLoadStats | None = None
    if args.text_source in (TEXT_SOURCE_PDF, TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS):
        blob_urls = filter_blob_urls_to_coded_bills(
            load_blob_urls_from_db(), enriched
        )
        read_blob = make_text_blob_reader()

        def _progress(done: int, total: int) -> None:
            if done == total or done % max(1, total // 20) == 0:
                print(f"U-691: PDF blob reads {done}/{total}", flush=True)

        pdf_tokens_by_bill_id, pdf_load_stats = load_pdf_text_by_bill_id(
            blob_urls,
            read_blob=read_blob,
            max_workers=args.pdf_read_workers,
            timeout_seconds=args.pdf_read_timeout,
            progress=_progress,
        )
        if pdf_load_stats:
            print(
                "U-691: PDF load "
                f"linked={pdf_load_stats.linked} ok={pdf_load_stats.read_ok} "
                f"stale={pdf_load_stats.stale_redaction} err={pdf_load_stats.read_error} "
                f"empty={pdf_load_stats.empty_content}",
                flush=True,
            )

    mask_for_config = (
        catalog_mask_tokens
        if args.mask_catalog_in_descriptions
        or args.text_source == TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS
        else frozenset()
    )
    token_config = TokenSourceConfig(
        text_source=args.text_source,
        catalog_mask_tokens=mask_for_config,
        pdf_tokens_by_bill_id=pdf_tokens_by_bill_id,
    )
    population = compute_population_counts(
        enriched,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        token_config=token_config,
    )

    print(
        f"U-691: loaded {n} coded bills; text_source={args.text_source}; "
        f"cal {args.cal_since}..{args.cal_until}, eval {args.eval_since}..{args.eval_until}"
    )
    if token_config.requires_pdf_population:
        print(
            f"U-691: PDF population {population.with_usable_pdf_text} bills; "
            f"eval {population.eval_pdf_population}/{population.eval_all}",
            flush=True,
        )
    if not args.run:
        print("Dry-run only (pass --run to score and write outputs).")
        return 0

    needs_scc_catalog = (
        args.mask_catalog_in_descriptions
        or args.text_source
        in (TEXT_SOURCE_PDF, TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS)
    )
    if needs_scc_catalog:
        scc_name_tokens = load_scc_name_tokens_from_db()
        catalog_mask_tokens = catalog_mask_from_scc_names(scc_name_tokens)
    else:
        scc_name_tokens = {}
        catalog_mask_tokens = frozenset()
    mask_for_config = (
        catalog_mask_tokens
        if args.mask_catalog_in_descriptions
        or args.text_source == TEXT_SOURCE_PDF_PLUS_MASKED_DESCRIPTIONS
        else frozenset()
    )
    token_config = TokenSourceConfig(
        text_source=args.text_source,
        catalog_mask_tokens=mask_for_config,
        pdf_tokens_by_bill_id=pdf_tokens_by_bill_id,
    )

    t0 = time.perf_counter()
    idf_eval = compute_idf_from_corpus(
        enriched,
        since=args.cal_since,
        until=args.cal_until,
        config=token_config,
    )
    idf_selection = compute_idf_from_corpus(
        enriched,
        since=args.cal_since,
        until=args.cal_until,
        config=token_config,
    )
    selection = pick_variants_on_calibration(
        enriched,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        idf_for_selection=idf_selection,
        top_k=args.top_k,
        band_pct=args.amount_band_pct,
        cap_ratio=args.amount_cap_ratio,
        token_config=token_config,
    )
    best_variant = selection.best_variant
    best_variant_mode_b = selection.best_variant_mode_b
    for w in selection.warnings:
        print(f"U-691 WARNING: {w}", flush=True)

    scored = score_all_rolling(
        enriched,
        idf_eval,
        top_k=args.top_k,
        band_pct=args.amount_band_pct,
        cap_ratio=args.amount_cap_ratio,
        token_config=token_config,
    )
    runtime = time.perf_counter() - t0

    eval_rows = eval_window_rows(
        scored,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        token_config=token_config,
    )
    leakage = compute_leakage_shares(
        eval_rows,
        token_config=token_config,
        scc_name_tokens=scc_name_tokens,
        catalog_mask_tokens=catalog_mask_tokens,
    )
    leakage_descriptions: dict[str, Optional[float]] | None = None
    if token_config.requires_pdf_population:
        desc_config = TokenSourceConfig(text_source=TEXT_SOURCE_DESCRIPTIONS)
        leakage_descriptions = compute_leakage_shares(
            eval_rows,
            token_config=desc_config,
            scc_name_tokens=scc_name_tokens,
            catalog_mask_tokens=catalog_mask_tokens,
        )

    masked_descriptions_h2h: dict[str, dict[str, Any]] | None = None
    if token_config.requires_pdf_population and args.mask_catalog_in_descriptions:
        pdf_population = frozenset(token_config.usable_pdf_bill_ids)
        masked_config = TokenSourceConfig(
            text_source=TEXT_SOURCE_DESCRIPTIONS,
            catalog_mask_tokens=catalog_mask_tokens,
            population_bill_ids=pdf_population,
        )
        idf_masked = compute_idf_from_corpus(
            enriched,
            since=args.cal_since,
            until=args.cal_until,
            config=masked_config,
        )
        scored_masked = rescore_similar_rolling(
            enriched,
            idf_masked,
            history_source=scored,
            top_k=args.top_k,
            band_pct=args.amount_band_pct,
            cap_ratio=args.amount_cap_ratio,
            token_config=masked_config,
        )
        masked_eval = eval_window_rows(
            scored_masked,
            eval_since=args.eval_since,
            eval_until=args.eval_until,
            token_config=masked_config,
        )
        masked_descriptions_h2h = {
            v: head_to_head_block(
                masked_eval, lambda r, variant=v: r.similar[variant]
            )
            for v in VARIANTS
        }

    constants = {
        "top_k": args.top_k,
        "amount_band_pct": args.amount_band_pct,
        "amount_cap_ratio": args.amount_cap_ratio,
        "history_strong_raw_score": HISTORY_STRONG_RAW_SCORE,
        "min_prior_bills": MIN_PRIOR_BILLS,
        "text_source": args.text_source,
        "mask_catalog_in_descriptions": args.mask_catalog_in_descriptions,
    }

    summary = build_summary(
        scored,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        best_variant=best_variant,
        best_variant_mode_b=best_variant_mode_b,
        calibration_selection=selection,
        runtime_seconds=runtime,
        bill_count=n,
        constants=constants,
        token_config=token_config,
        population=population,
        pdf_load_stats=pdf_load_stats,
        leakage=leakage,
        leakage_descriptions=leakage_descriptions,
        masked_descriptions_h2h=masked_descriptions_h2h,
    )

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    report_path, csv_path, json_path = write_outputs(
        out_dir=args.out_dir,
        stamp=stamp,
        scored=scored,
        summary=summary,
        token_config=token_config,
        eval_rows=eval_rows,
    )
    print(
        f"U-691: scored in {runtime:.2f}s; best variant={best_variant}; "
        f"report={report_path.name} csv={csv_path.name} json={json_path.name}"
    )
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
