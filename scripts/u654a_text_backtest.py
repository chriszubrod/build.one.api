"""
U-654a: offline-capable text-tier backtest for bill SCC suggestion (U-639 §6 M4, §10).

Reuses U-653b rolling history priors and metrics helpers. Compares LLM cascade vs
history-only on the same stratified evaluation sample (Bills with completed text extraction).

Usage (repo root):
  ./.venv/bin/python -m scripts.u654a_text_backtest              # dry-run (default)
  ./.venv/bin/python -m scripts.u654a_text_backtest --run
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from intelligence.cascade.bill_coding import (
    BILL_CODING_FOUNDRY_LADDER,
    BILL_CODING_SUGGESTION_TASK,
    BILL_TEXT_CHAR_BUDGET,
    SUSPENSE_SCC_NUMBER,
    BACKTEST_LADDERS,
    ladder_by_name,
    normalize_project_abbreviation,
    normalize_scc_number,
    run_bill_coding_suggestion,
    truncate_bill_text,
)
from intelligence.observability.pricing import PRICING
from intelligence.cascade.bill_coding_payload import (
    build_candidate_projects_for_bill,
    build_candidate_sccs_for_bill,
    build_history_evidence,
    build_payload,
    resolve_text_prediction,
)
from intelligence.cascade.core import Attempt, CascadeResult, Rung
from intelligence.observability.pricing import compute_cost_usd
from intelligence.transport.base import Usage
from scripts.sync_helper import assert_cli_system_admin
from intelligence.cascade.bill_coding_payload import PriorBillVote
from scripts.u653b_backtest import (
    DEFAULT_EVAL_SINCE,
    DEFAULT_EVAL_UNTIL,
    MIN_PRIOR_BILLS,
    BillRow,
    ModePrediction,
    _consistency_bucket,
    _count_votes,
    _date_only,
    _dollar_accuracy,
    _parse_dt,
    _vendor_consistency_share,
    _weighted_accuracy,
    load_bills_from_db,
    predict_mode_a,
    predict_mode_b,
    priors_before,
    validate_windows,
)
from shared.database import get_connection

MODEL_VERSION = "u654a-text-backtest-v1"
DEFAULT_SAMPLE_SIZE = 500
DEFAULT_SEED = 654
DEFAULT_MAX_COST_USD = 10.0
DEFAULT_CONCURRENCY = 4
DEFAULT_CLAUDE_CONCURRENCY = 2

# Per-million-token estimates for dry-run budgeting — confirm against Foundry list prices.
# intelligence/observability/pricing.PRICING is authoritative at run time.
_ESTIMATE_INPUT_TOKENS_PER_CALL = 4500
_ESTIMATE_OUTPUT_TOKENS_PER_CALL = 120
RUNG_PRICE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "DeepSeek-V4-Flash": (0.14, 0.28),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4-mini": (0.75, 4.50),
}

DEFAULT_BACKTEST_LADDER_NAME = "foundry"

M4_MODE_A_MARGIN = Decimal("0.10")
# §10.3 combined rule: history wins when raw smoothed modal share clears this bar (U-639 §10.5).
COMBINED_HISTORY_RAW_SCORE_THRESHOLD = Decimal("0.60")

_HTTP_STATUS_IN_ERROR_RE = re.compile(r"http_(\d+)", re.I)
# Anthropic SSE in-stream errors use type prefixes before the first colon (e.g. overloaded_error: …).
_INSTREAM_ERROR_PREFIX_KIND: tuple[tuple[re.Pattern[str], str, int], ...] = (
    (re.compile(r"rate[\s_\-]*limit", re.I), "rate_limit", 429),
    (re.compile(r"overload", re.I), "overloaded", 529),
    (re.compile(r"^api[\s_\-]*error", re.I), "server", 500),
    (re.compile(r"invalid[\s_\-]*request", re.I), "bad_request", 400),
    (re.compile(r"authentication", re.I), "auth", 401),
    (re.compile(r"permission", re.I), "auth", 403),
    (re.compile(r"not[\s_\-]*found", re.I), "not_found", 404),
    (re.compile(r"request[\s_\-]*too[\s_\-]*large", re.I), "too_large", 413),
)

# Measured ~17k input tokens/call on 2026-10-17 Claude Haiku backtest (incl. SCC catalog).
CLAUDE_DRY_RUN_INPUT_TOKENS_FLOOR = 17_000
HARNESS_RATE_LIMIT_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)
CONFIDENCE_BAR_SWEEP_BARS: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.85, 0.90)

_STRATA_ORDER = (
    "cold_start",
    ">=90%",
    "70-90%",
    "50-70%",
    "<50%",
    "mixed_code",
)

_FORBIDDEN_OUTPUT_COLUMNS = frozenset(
    {
        "vendor_name",
        "bill_text",
        "rationale",
        "memo",
        "body",
        "subject",
        "name",
    }
)

_BLOB_URL_SQL = """
WITH Linked AS (
    SELECT
        bli.[BillId],
        a.[ExtractedTextBlobUrl],
        ROW_NUMBER() OVER (
            PARTITION BY bli.[BillId]
            ORDER BY blia.[Id] ASC
        ) AS [Rn]
    FROM dbo.[BillLineItem] bli
    INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
    INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
    WHERE a.[ExtractionStatus] = 'completed'
      AND a.[ExtractedTextBlobUrl] IS NOT NULL
      AND LTRIM(RTRIM(a.[ExtractedTextBlobUrl])) <> ''
)
SELECT [BillId], [ExtractedTextBlobUrl]
FROM Linked
WHERE [Rn] = 1
"""

_SCC_CATALOG_SQL = """
SELECT [Id], [Number], [Name], [CreatedDatetime]
FROM dbo.[SubCostCode]
WHERE [Number] IS NOT NULL
ORDER BY [Number]
"""

_PROJECT_SQL = """
SELECT [Id], [Abbreviation], [Name]
FROM dbo.[Project]
WHERE [Abbreviation] IS NOT NULL
ORDER BY [Id]
"""

_VENDOR_SQL = """
SELECT [Id], [Name]
FROM dbo.[Vendor]
ORDER BY [Id]
"""


@dataclass(frozen=True)
class SccRef:
    scc_id: int
    number: str
    name: str
    created: datetime


@dataclass(frozen=True)
class ProjectRef:
    project_id: int
    abbreviation: str
    name: str


@dataclass(frozen=True)
class TextBillRow:
    """BillRow fields plus extracted text blob URL (no subclass — BillRow has trailing defaults)."""

    bill_id: int
    vendor_id: int
    created: datetime
    label_scc_id: int
    label_project_id: Optional[int]
    total_abs_amount: Decimal
    mixed_code: bool
    extracted_text_blob_url: str
    intake_source: Optional[str] = None
    source_email_message_id: Optional[int] = None
    creator_is_agent: bool = False
    qbo_staging_created: Optional[datetime] = None


@dataclass
class TextAttemptResult:
    mode: str
    cascade: CascadeResult
    predicted_scc_id: Optional[int]
    predicted_project_id: Optional[int] = None
    source: str = "llm_text"
    raw_predicted_scc_id: Optional[int] = None
    raw_predicted_project_id: Optional[int] = None
    raw_confidence: Optional[float] = None
    error_kind: str = "none"
    http_status: Optional[int] = None


@dataclass
class ScoredTextBill:
    bill: TextBillRow
    stratum: str
    history_a: ModePrediction
    history_b: ModePrediction
    text_a: Optional[TextAttemptResult] = None
    text_b: Optional[TextAttemptResult] = None


def load_blob_urls_from_db() -> dict[int, str]:
    out: dict[int, str] = {}
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_BLOB_URL_SQL)
            for row in cur.fetchall():
                out[int(row.BillId)] = str(row.ExtractedTextBlobUrl)
        finally:
            conn.rollback()
    return out


@dataclass(frozen=True)
class ReferenceData:
    scc_by_id: dict[int, SccRef]
    scc_number_to_id: dict[str, int]
    proj_by_id: dict[int, ProjectRef]
    proj_abbrev_to_id: dict[str, int]
    vendor_names: dict[int, str]
    ambiguous_scc_numbers: frozenset[str]
    ambiguous_project_abbrevs: frozenset[str]
    duplicate_scc_number_count: int
    duplicate_project_abbrev_count: int


def _unique_string_keys(rows: Iterable[tuple[str, int]]) -> tuple[dict[str, int], frozenset[str], int]:
    by_key: dict[str, list[int]] = defaultdict(list)
    for key, row_id in rows:
        by_key[key].append(row_id)
    ambiguous = frozenset(k for k, ids in by_key.items() if len(ids) > 1)
    unique = {k: ids[0] for k, ids in by_key.items() if len(ids) == 1}
    return unique, ambiguous, len(ambiguous)


def load_reference_data() -> ReferenceData:
    scc_by_id: dict[int, SccRef] = {}
    scc_key_rows: list[tuple[str, int]] = []
    proj_by_id: dict[int, ProjectRef] = {}
    proj_key_rows: list[tuple[str, int]] = []
    vendor_names: dict[int, str] = {}
    suspense_norm = normalize_scc_number(SUSPENSE_SCC_NUMBER)
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_SCC_CATALOG_SQL)
            for row in cur.fetchall():
                number = normalize_scc_number(str(row.Number))
                created = _parse_dt(row.CreatedDatetime)
                ref = SccRef(int(row.Id), number, str(row.Name or ""), created)
                scc_by_id[ref.scc_id] = ref
                if number and number != suspense_norm:
                    scc_key_rows.append((number, ref.scc_id))
            cur.execute(_PROJECT_SQL)
            for row in cur.fetchall():
                abbrev = normalize_project_abbreviation(str(row.Abbreviation))
                ref = ProjectRef(int(row.Id), abbrev, str(row.Name or ""))
                proj_by_id[ref.project_id] = ref
                if abbrev:
                    proj_key_rows.append((abbrev, ref.project_id))
            cur.execute(_VENDOR_SQL)
            for row in cur.fetchall():
                vendor_names[int(row.Id)] = str(row.Name or "")
        finally:
            conn.rollback()
    scc_number_to_id, amb_scc, dup_scc = _unique_string_keys(scc_key_rows)
    proj_abbrev_to_id, amb_proj, dup_proj = _unique_string_keys(proj_key_rows)
    return ReferenceData(
        scc_by_id=scc_by_id,
        scc_number_to_id=scc_number_to_id,
        proj_by_id=proj_by_id,
        proj_abbrev_to_id=proj_abbrev_to_id,
        vendor_names=vendor_names,
        ambiguous_scc_numbers=amb_scc,
        ambiguous_project_abbrevs=amb_proj,
        duplicate_scc_number_count=dup_scc,
        duplicate_project_abbrev_count=dup_proj,
    )


def merge_text_bills(
    bills: Sequence[BillRow],
    blob_by_bill: dict[int, str],
) -> list[TextBillRow]:
    out: list[TextBillRow] = []
    for b in bills:
        url = blob_by_bill.get(b.bill_id)
        if not url:
            continue
        out.append(
            TextBillRow(
                bill_id=b.bill_id,
                vendor_id=b.vendor_id,
                created=b.created,
                label_scc_id=b.label_scc_id,
                label_project_id=b.label_project_id,
                total_abs_amount=b.total_abs_amount,
                mixed_code=b.mixed_code,
                intake_source=b.intake_source,
                source_email_message_id=b.source_email_message_id,
                creator_is_agent=b.creator_is_agent,
                qbo_staging_created=b.qbo_staging_created,
                extracted_text_blob_url=url,
            )
        )
    return out


def build_vendor_priors(all_bills: Sequence[BillRow]) -> dict[int, list[PriorBillVote]]:
    acc: dict[int, list[PriorBillVote]] = defaultdict(list)
    for b in all_bills:
        acc[b.vendor_id].append(
            PriorBillVote(
                bill_id=b.bill_id,
                created=b.created,
                dominant_scc_id=b.label_scc_id,
                dominant_project_id=b.label_project_id,
            )
        )
    return acc


def priors_for_bill(
    vendor_votes: dict[int, list[PriorBillVote]],
    bill: BillRow,
) -> list[PriorBillVote]:
    return priors_before(
        vendor_votes.get(bill.vendor_id, []),
        as_of=bill.created,
        exclude_bill_id=bill.bill_id,
    )


def projects_with_bills_before(
    all_bills: Sequence[BillRow],
    as_of: datetime,
    proj_by_id: dict[int, ProjectRef],
) -> list[dict[str, str]]:
    seen: set[int] = set()
    rows: list[dict[str, str]] = []
    for b in sorted(all_bills, key=lambda x: (x.created, x.bill_id)):
        if b.created >= as_of:
            continue
        pid = b.label_project_id
        if pid is None or pid in seen:
            continue
        ref = proj_by_id.get(pid)
        if ref is None:
            continue
        seen.add(pid)
        rows.append({"abbreviation": ref.abbreviation, "name": ref.name})
    return rows


def count_eval_populations(
    all_bills: Sequence[BillRow],
    text_bills: Sequence[TextBillRow],
    *,
    eval_since: date,
    eval_until: date,
) -> dict[str, int]:
    eval_all = sum(
        1
        for b in all_bills
        if eval_since <= _date_only(b.created) <= eval_until
    )
    eval_text = sum(
        1
        for b in text_bills
        if eval_since <= _date_only(b.created) <= eval_until
    )
    return {
        "eval_window_bill_population": eval_all,
        "text_tier_bill_population": eval_text,
    }


def combined_mode_a_predicted_scc(
    history: ModePrediction,
    text: Optional[TextAttemptResult],
) -> Optional[int]:
    if (
        history.predicted_scc_id is not None
        and history.raw_score is not None
        and history.raw_score >= COMBINED_HISTORY_RAW_SCORE_THRESHOLD
    ):
        return history.predicted_scc_id
    if text is None:
        return None
    return text.predicted_scc_id


def cascade_has_throttling(cascade: CascadeResult) -> bool:
    for att in cascade.attempts:
        kind, _ = error_kind_from_error_string(att.error)
        if kind in ("rate_limit", "overloaded"):
            return True
    return False


def _instream_error_kind(err: str) -> tuple[str, Optional[int]] | None:
    head = err.split(":", 1)[0].strip()
    for pattern, kind, status in _INSTREAM_ERROR_PREFIX_KIND:
        if pattern.search(head):
            return kind, status
    return None


def error_kind_from_error_string(err: str | None) -> tuple[str, Optional[int]]:
    """Map transport/cascade error text to a privacy-safe kind + HTTP status (no message body)."""
    if not err:
        return "none", None
    if "no parseable" in err.lower():
        return "parse", None
    match = _HTTP_STATUS_IN_ERROR_RE.search(err)
    if match:
        status = int(match.group(1))
    else:
        instream = _instream_error_kind(err)
        if instream is not None:
            return instream
        if re.search(r"\b429\b", err):
            status = 429
        elif re.search(r"\b529\b", err):
            status = 529
        elif re.search(r"\b503\b", err):
            status = 503
        elif re.search(r"\b400\b", err):
            status = 400
        elif re.search(r"\b401\b", err):
            status = 401
        elif re.search(r"\b403\b", err):
            status = 403
        elif re.search(r"\b413\b", err):
            status = 413
        elif re.search(r"\b404\b", err):
            status = 404
        else:
            if re.search(r"timeout", err, re.I):
                return "timeout", None
            return "transport_other", None
    if status == 429:
        return "rate_limit", status
    if status in (529, 503):
        return "overloaded", status
    if status == 400:
        return "bad_request", status
    if status in (401, 403):
        return "auth", status
    if status == 404:
        return "not_found", status
    if status == 413:
        return "too_large", status
    if 500 <= status <= 599:
        return "server", status
    return "transport_other", status


def cascade_call_error_summary(cascade: CascadeResult) -> tuple[str, Optional[int]]:
    """Primary error kind for a finished cascade (accepted calls → none)."""
    if cascade.accepted:
        return "none", None
    for att in reversed(cascade.attempts):
        if att.error:
            kind, status = error_kind_from_error_string(att.error)
            if kind != "transport_other" or status is not None:
                return kind, status
        if att.result is not None and not att.validated:
            return "validation", None
    for att in reversed(cascade.attempts):
        if att.error and "no parseable" in att.error.lower():
            return "parse", None
    if cascade.result is not None:
        return "none", None
    if not cascade.attempts:
        return "transport_other", None
    return "none", None


def cascade_only_rate_or_overloaded_failures(cascade: CascadeResult) -> bool:
    """True when every attempt failed at transport with rate-limit or overload only."""
    if cascade.accepted or not cascade.attempts:
        return False
    saw_transport = False
    for att in cascade.attempts:
        if att.error:
            kind, _ = error_kind_from_error_string(att.error)
            if kind not in ("rate_limit", "overloaded"):
                return False
            saw_transport = True
        elif att.result is not None or att.confidence is not None:
            return False
    return saw_transport


def resolve_raw_text_prediction(
    cascade: CascadeResult,
    *,
    scc_number_to_id: dict[str, int],
    proj_abbrev_to_id: dict[str, int],
) -> tuple[Optional[int], Optional[int], Optional[float]]:
    if cascade.result is None:
        return None, None, cascade.confidence
    shim = CascadeResult(
        accepted=True,
        needs_human=False,
        result=cascade.result,
        confidence=cascade.confidence,
        winning_rung=cascade.winning_rung,
        attempts=[],
    )
    scc_id, proj_id = resolve_text_prediction(
        shim,
        scc_number_to_id=scc_number_to_id,
        proj_abbrev_to_id=proj_abbrev_to_id,
    )
    return scc_id, proj_id, cascade.confidence


def llm_backtest_cascade_concurrency(
    ladder_name: str,
    *,
    concurrency: int,
    claude_concurrency: int,
) -> int:
    """Parallel in-flight cascades for a backtest ladder (Claude vs Foundry)."""
    if ladder_name.startswith("claude"):
        return claude_concurrency
    return concurrency


def _ladder_uses_claude(
    ladder: tuple[Rung, ...] | None,
    *,
    ladder_name: str | None = None,
) -> bool:
    if ladder_name and ladder_name.startswith("claude"):
        return True
    if ladder and ladder[0].provider == "anthropic":
        return True
    return False


def floor_dry_run_input_tokens(
    input_tokens: int,
    *,
    ladder: tuple[Rung, ...] | None = None,
    ladder_name: str | None = None,
) -> int:
    if _ladder_uses_claude(ladder, ladder_name=ladder_name):
        return max(input_tokens, CLAUDE_DRY_RUN_INPUT_TOKENS_FLOOR)
    return input_tokens


def bill_cascade_spend_usd(cascade: CascadeResult) -> float:
    """Sum USD for every attempt on this cascade (all rungs / throttle retries)."""
    total = 0.0
    for att in cascade.attempts:
        if att.rung:
            c = compute_cost_usd(
                provider=att.rung.provider,
                model=att.rung.model,
                usage=att.usage,
            )
            if c:
                total += c
    if total == 0.0 and cascade.winning_rung:
        total = estimate_call_cost_usd(
            cascade.winning_rung.model,
            provider=cascade.winning_rung.provider,
        )
    return total


def apply_cascade_spend(cascade: CascadeResult, cost_state: dict[str, Any]) -> float:
    added = bill_cascade_spend_usd(cascade)
    if added:
        cost_state["usd"] = float(cost_state.get("usd", 0.0)) + added
    return added


def label_scc_number_for_bill(
    bill: TextBillRow,
    scc_by_id: dict[int, SccRef],
) -> Optional[str]:
    ref = scc_by_id.get(bill.label_scc_id)
    if ref is None:
        return None
    return ref.number


def catalog_diagnostics_for_sample(
    sample: Sequence[tuple[TextBillRow, str]],
    ref: ReferenceData,
) -> dict[str, Any]:
    sizes: list[int] = []
    label_not_in = 0
    label_not_in_ids: set[int] = set()
    for bill, _stratum in sample:
        catalog = build_candidate_sccs_for_bill(
            ref.scc_by_id,
            as_of=bill.created,
            ambiguous_numbers=ref.ambiguous_scc_numbers,
        )
        sizes.append(len(catalog))
        label_num = label_scc_number_for_bill(bill, ref.scc_by_id)
        if label_num is None or label_num not in {c["number"] for c in catalog}:
            label_not_in += 1
            label_not_in_ids.add(bill.bill_id)
    sizes_sorted = sorted(sizes)
    mid = len(sizes_sorted) // 2
    if not sizes_sorted:
        median = 0
    elif len(sizes_sorted) % 2:
        median = sizes_sorted[mid]
    else:
        median = (sizes_sorted[mid - 1] + sizes_sorted[mid]) // 2  # floored, int
    return {
        "label_not_in_text_catalog_count": label_not_in,
        "label_not_in_catalog_bill_ids": label_not_in_ids,
        "text_catalog_size_min_per_bill": min(sizes) if sizes else 0,
        "text_catalog_size_median_per_bill": median,
    }


def classify_stratum(priors: Sequence[PriorBillVote], mixed_code: bool) -> str:
    if len(priors) < MIN_PRIOR_BILLS:
        return "cold_start"
    share = _vendor_consistency_share(priors)
    if share is None:
        return "cold_start"
    return _consistency_bucket(share)


def stratified_sample(
    bills: Sequence[TextBillRow],
    vendor_votes: dict[int, list[PriorBillVote]],
    *,
    sample_size: int,
    seed: int,
    eval_since: date,
    eval_until: date,
) -> list[tuple[TextBillRow, str]]:
    eval_bills = [
        b
        for b in bills
        if eval_since <= _date_only(b.created) <= eval_until
    ]
    keyed: dict[str, list[TextBillRow]] = defaultdict(list)
    mixed_only: list[TextBillRow] = []
    for b in eval_bills:
        priors = priors_for_bill(vendor_votes, b)
        primary = classify_stratum(priors, b.mixed_code)
        if b.mixed_code:
            mixed_only.append(b)
        else:
            keyed[primary].append(b)

    non_empty = [s for s in _STRATA_ORDER if s != "mixed_code" and keyed.get(s)]
    if mixed_only:
        non_empty.append("mixed_code")

    if not non_empty:
        return []

    rng = random.Random(seed)
    per = max(1, sample_size // len(non_empty))
    chosen_ids: set[int] = set()
    picked: list[tuple[TextBillRow, str]] = []

    def take_from(pool: list[TextBillRow], stratum: str, n: int) -> None:
        pool = [b for b in pool if b.bill_id not in chosen_ids]
        if not pool:
            return
        rng.shuffle(pool)
        for b in pool[:n]:
            if b.bill_id in chosen_ids:
                continue
            chosen_ids.add(b.bill_id)
            picked.append((b, stratum))

    for s in _STRATA_ORDER:
        if s == "mixed_code":
            take_from(mixed_only, "mixed_code", per)
        elif keyed.get(s):
            take_from(keyed[s], s, per)

    remainder = sample_size - len(picked)
    if remainder > 0:
        rest = [b for b in eval_bills if b.bill_id not in chosen_ids]
        rng.shuffle(rest)
        for b in rest[:remainder]:
            priors = priors_for_bill(vendor_votes, b)
            picked.append((b, classify_stratum(priors, b.mixed_code)))
            chosen_ids.add(b.bill_id)

    picked.sort(key=lambda x: (x[0].created, x[0].bill_id))
    return picked[:sample_size]


def estimate_payload_input_tokens(payload: dict[str, Any]) -> int:
    """Heuristic: serialized user payload chars / 4 (Foundry runs ~18k+ with full SCC catalog)."""
    return max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)


def estimate_call_cost_usd(
    rung_model: str,
    *,
    provider: str = "foundry",
    input_tokens: int | None = None,
) -> float:
    rates = RUNG_PRICE_USD_PER_MTOK.get(rung_model)
    if not rates:
        mp = PRICING.get(provider, {}).get(rung_model)
        if mp is not None:
            rates = (mp.input, mp.output)
    if not rates:
        return 0.0
    inp, out = rates
    in_tok = input_tokens if input_tokens is not None else _ESTIMATE_INPUT_TOKENS_PER_CALL
    return (in_tok * inp + _ESTIMATE_OUTPUT_TOKENS_PER_CALL * out) / 1_000_000.0


def estimate_ladder_first_rung_cost_usd(
    ladder: tuple[Rung, ...],
    *,
    input_tokens: int | None = None,
) -> float:
    if not ladder:
        return 0.0
    first = ladder[0]
    return estimate_call_cost_usd(
        first.model, provider=first.provider, input_tokens=input_tokens
    )


def estimate_dry_run_input_tokens_per_call(
    sample: Sequence[tuple[TextBillRow, str]],
    *,
    ref: "ReferenceData",
    vendor_votes: dict[int, list[PriorBillVote]],
) -> int:
    if not sample:
        return _ESTIMATE_INPUT_TOKENS_PER_CALL
    per_call: list[int] = []
    for bill, _stratum in sample:
        priors = priors_for_bill(vendor_votes, bill)
        candidate_sccs = build_candidate_sccs_for_bill(
            ref.scc_by_id,
            as_of=bill.created,
            ambiguous_numbers=ref.ambiguous_scc_numbers,
        )
        vname = ref.vendor_names.get(bill.vendor_id, "")
        payload = build_payload(
            bill,
            bill_text="",
            vendor_name=vname,
            mode="A",
            priors=priors,
            scc_by_id=ref.scc_by_id,
            candidate_sccs=candidate_sccs,
            candidate_projects=None,
        )
        per_call.append(estimate_payload_input_tokens(payload))
    return max(per_call)


def dry_run_summary(
    sample: Sequence[tuple[TextBillRow, str]],
    *,
    sample_size: int,
    populations: dict[str, int],
    ladder_name: str = DEFAULT_BACKTEST_LADDER_NAME,
    ladder: tuple[Rung, ...] | None = None,
    ref: Optional["ReferenceData"] = None,
    vendor_votes: Optional[dict[int, list[PriorBillVote]]] = None,
) -> dict[str, Any]:
    by_stratum: dict[str, int] = defaultdict(int)
    mixed = 0
    for _, s in sample:
        by_stratum[s] += 1
    for b, _ in sample:
        if b.mixed_code:
            mixed += 1
    calls = len(sample) * 2  # Mode A + Mode B
    resolved_ladder = ladder if ladder is not None else ladder_by_name(ladder_name)
    input_per_call = _ESTIMATE_INPUT_TOKENS_PER_CALL
    if ref is not None and vendor_votes is not None and sample:
        input_per_call = estimate_dry_run_input_tokens_per_call(
            sample, ref=ref, vendor_votes=vendor_votes
        )
    input_per_call = floor_dry_run_input_tokens(
        input_per_call, ladder=resolved_ladder, ladder_name=ladder_name
    )
    cost_per_call = estimate_ladder_first_rung_cost_usd(
        resolved_ladder, input_tokens=input_per_call
    )
    est_cost = calls * cost_per_call
    est_tokens = calls * (input_per_call + _ESTIMATE_OUTPUT_TOKENS_PER_CALL)
    return {
        **populations,
        "ladder": ladder_name,
        "sample_requested": sample_size,
        "sample_selected": len(sample),
        "stratum_counts": dict(by_stratum),
        "mixed_code_bills_in_sample": mixed,
        "estimated_input_tokens_per_call": input_per_call,
        "estimated_cascade_calls": calls,
        "estimated_tokens": est_tokens,
        "estimated_cost_usd_first_rung_only": round(est_cost, 4),
        "note": "Dry-run assumes every call stops at the first rung; actual spend may be higher on escalation.",
    }


@dataclass
class SuggesterMetrics:
    coverage: Decimal
    precision: Optional[Decimal]
    top1_bill: Optional[Decimal]
    top1_dollar: Optional[Decimal]
    project_top1: Optional[Decimal] = None


def _metrics_to_dict(m: SuggesterMetrics) -> dict[str, Any]:
    return {
        "coverage": m.coverage,
        "precision": m.precision,
        "top1_bill": m.top1_bill,
        "top1_dollar": m.top1_dollar,
        "project_top1": m.project_top1,
    }


def _predicted_scc_id(
    row: ScoredTextBill,
    *,
    mode: str,
    source: str,
) -> Optional[int]:
    if source == "history":
        pred = row.history_a if mode == "A" else row.history_b
        return pred.predicted_scc_id
    if source == "text":
        text = row.text_a if mode == "A" else row.text_b
        return text.predicted_scc_id if text is not None else None
    if source == "combined":
        if mode != "A":
            raise ValueError("combined metrics are Mode A only")
        return combined_mode_a_predicted_scc(row.history_a, row.text_a)
    raise ValueError(f"unknown source {source!r}")


def _top1_pairs(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    source: str,
    denominator: str,
) -> list[tuple[bool, Decimal]]:
    pairs: list[tuple[bool, Decimal]] = []
    for row in rows:
        pred_scc = _predicted_scc_id(row, mode=mode, source=source)
        if denominator == "covered":
            if pred_scc is None:
                continue
            pairs.append((pred_scc == row.bill.label_scc_id, row.bill.total_abs_amount))
        elif denominator == "all":
            ok = pred_scc is not None and pred_scc == row.bill.label_scc_id
            pairs.append((ok, row.bill.total_abs_amount))
        else:
            raise ValueError(denominator)
    return pairs


def _project_pairs(
    rows: Sequence[ScoredTextBill],
    *,
    source: str,
) -> list[tuple[bool, Decimal]]:
    pairs: list[tuple[bool, Decimal]] = []
    for row in rows:
        if row.bill.label_project_id is None:
            continue
        if source == "history":
            pred_pid = row.history_b.predicted_project_id
        else:
            if row.text_b is None:
                continue
            pred_pid = row.text_b.predicted_project_id
        if pred_pid is None:
            continue
        pairs.append(
            (pred_pid == row.bill.label_project_id, row.bill.total_abs_amount)
        )
    return pairs


def compute_suggester_metrics(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    source: str,
) -> SuggesterMetrics:
    n = len(rows)
    covered = _top1_pairs(rows, mode=mode, source=source, denominator="covered")
    all_bills = _top1_pairs(rows, mode=mode, source=source, denominator="all")
    coverage = Decimal(len(covered)) / Decimal(n) if n else Decimal("0")
    proj = (
        _weighted_accuracy(_project_pairs(rows, source=source))
        if mode == "B"
        else None
    )
    return SuggesterMetrics(
        coverage=coverage,
        precision=_weighted_accuracy(covered),
        top1_bill=_weighted_accuracy(all_bills),
        top1_dollar=_dollar_accuracy(all_bills),
        project_top1=proj,
    )


def combined_mode_b_predicted_scc(
    history: ModePrediction,
    text: Optional[TextAttemptResult],
) -> Optional[int]:
    if (
        history.predicted_scc_id is not None
        and history.raw_score is not None
        and history.raw_score >= COMBINED_HISTORY_RAW_SCORE_THRESHOLD
    ):
        return history.predicted_scc_id
    if text is None:
        return None
    return text.predicted_scc_id


def combined_mode_b_predicted_scc_at_bar(
    history: ModePrediction,
    text: Optional[TextAttemptResult],
    bar: float,
) -> Optional[int]:
    if (
        history.predicted_scc_id is not None
        and history.raw_score is not None
        and history.raw_score >= COMBINED_HISTORY_RAW_SCORE_THRESHOLD
    ):
        return history.predicted_scc_id
    if text is None or text.raw_confidence is None or text.raw_confidence < bar:
        return None
    return text.raw_predicted_scc_id


def combined_mode_a_predicted_scc_at_bar(
    history: ModePrediction,
    text: Optional[TextAttemptResult],
    bar: float,
) -> Optional[int]:
    if (
        history.predicted_scc_id is not None
        and history.raw_score is not None
        and history.raw_score >= COMBINED_HISTORY_RAW_SCORE_THRESHOLD
    ):
        return history.predicted_scc_id
    if text is None or text.raw_confidence is None or text.raw_confidence < bar:
        return None
    return text.raw_predicted_scc_id


def _raw_text_scc_at_bar(
    text: Optional[TextAttemptResult],
    bar: float,
) -> Optional[int]:
    if text is None or text.raw_confidence is None or text.raw_confidence < bar:
        return None
    return text.raw_predicted_scc_id


def _top1_pairs_raw_at_bar(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    bar: float,
    denominator: str,
) -> list[tuple[bool, Decimal]]:
    pairs: list[tuple[bool, Decimal]] = []
    for row in rows:
        text = row.text_a if mode == "A" else row.text_b
        pred_scc = _raw_text_scc_at_bar(text, bar)
        if denominator == "covered":
            if pred_scc is None:
                continue
            pairs.append((pred_scc == row.bill.label_scc_id, row.bill.total_abs_amount))
        elif denominator == "all":
            ok = pred_scc is not None and pred_scc == row.bill.label_scc_id
            pairs.append((ok, row.bill.total_abs_amount))
        else:
            raise ValueError(denominator)
    return pairs


def _top1_pairs_combined_at_bar(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    bar: float,
    denominator: str,
) -> list[tuple[bool, Decimal]]:
    pairs: list[tuple[bool, Decimal]] = []
    for row in rows:
        if mode == "A":
            pred_scc = combined_mode_a_predicted_scc_at_bar(
                row.history_a, row.text_a, bar
            )
        else:
            pred_scc = combined_mode_b_predicted_scc_at_bar(
                row.history_b, row.text_b, bar
            )
        if denominator == "covered":
            if pred_scc is None:
                continue
            pairs.append((pred_scc == row.bill.label_scc_id, row.bill.total_abs_amount))
        elif denominator == "all":
            ok = pred_scc is not None and pred_scc == row.bill.label_scc_id
            pairs.append((ok, row.bill.total_abs_amount))
        else:
            raise ValueError(denominator)
    return pairs


def compute_text_metrics_at_bar(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    bar: float,
) -> SuggesterMetrics:
    n = len(rows)
    covered = _top1_pairs_raw_at_bar(rows, mode=mode, bar=bar, denominator="covered")
    all_bills = _top1_pairs_raw_at_bar(rows, mode=mode, bar=bar, denominator="all")
    coverage = Decimal(len(covered)) / Decimal(n) if n else Decimal("0")
    proj = None
    if mode == "B":
        proj_pairs: list[tuple[bool, Decimal]] = []
        for row in rows:
            if row.bill.label_project_id is None or row.text_b is None:
                continue
            if row.text_b.raw_confidence is None or row.text_b.raw_confidence < bar:
                continue
            pred_pid = row.text_b.raw_predicted_project_id
            if pred_pid is None:
                continue
            proj_pairs.append(
                (pred_pid == row.bill.label_project_id, row.bill.total_abs_amount)
            )
        proj = _weighted_accuracy(proj_pairs) if proj_pairs else None
    return SuggesterMetrics(
        coverage=coverage,
        precision=_weighted_accuracy(covered),
        top1_bill=_weighted_accuracy(all_bills),
        top1_dollar=_dollar_accuracy(all_bills),
        project_top1=proj,
    )


def compute_combined_metrics_at_bar(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    bar: float,
) -> SuggesterMetrics:
    n = len(rows)
    covered = _top1_pairs_combined_at_bar(
        rows, mode=mode, bar=bar, denominator="covered"
    )
    all_bills = _top1_pairs_combined_at_bar(rows, mode=mode, bar=bar, denominator="all")
    coverage = Decimal(len(covered)) / Decimal(n) if n else Decimal("0")
    return SuggesterMetrics(
        coverage=coverage,
        precision=_weighted_accuracy(covered),
        top1_bill=_weighted_accuracy(all_bills),
        top1_dollar=_dollar_accuracy(all_bills),
        project_top1=None,
    )


def confidence_bar_sweep(rows: Sequence[ScoredTextBill]) -> dict[str, Any]:
    rows_a = [r for r in rows if r.text_a is not None]
    rows_b = [r for r in rows if r.text_b is not None]
    hist_a = compute_suggester_metrics(rows_a, mode="A", source="history")
    sweep: dict[str, Any] = {"bars": list(CONFIDENCE_BAR_SWEEP_BARS), "mode_a": {}, "mode_b": {}}
    best_bar: Optional[float] = None
    best_top1: Optional[Decimal] = None
    for bar in CONFIDENCE_BAR_SWEEP_BARS:
        text_a = compute_text_metrics_at_bar(rows_a, mode="A", bar=bar)
        combined_a = compute_combined_metrics_at_bar(rows_a, mode="A", bar=bar)
        delta = None
        if combined_a.top1_bill is not None and hist_a.top1_bill is not None:
            delta = combined_a.top1_bill - hist_a.top1_bill
        sweep["mode_a"][str(bar)] = {
            "text": _metrics_to_dict(text_a),
            "combined_top1_bill": combined_a.top1_bill,
            "combined_delta_vs_history": delta,
        }
        if combined_a.top1_bill is not None and (
            best_top1 is None or combined_a.top1_bill > best_top1
        ):
            best_top1 = combined_a.top1_bill
            best_bar = bar
        text_b = compute_text_metrics_at_bar(rows_b, mode="B", bar=bar)
        combined_b = compute_combined_metrics_at_bar(rows_b, mode="B", bar=bar)
        sweep["mode_b"][str(bar)] = {
            "text": _metrics_to_dict(text_b),
            "combined_top1_bill": combined_b.top1_bill,
        }
    sweep["best_combined_top1_bar_mode_a"] = best_bar
    return sweep


def aggregate_call_failures_by_kind(
    attempts: Iterable[TextAttemptResult],
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in attempts:
        counts[item.error_kind or "none"] += 1
    return dict(counts)


def stratum_table(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[ScoredTextBill]] = defaultdict(list)
    for row in rows:
        buckets[row.stratum].append(row)
    out: dict[str, dict[str, Any]] = {}
    for key, chunk in sorted(buckets.items()):
        hist = compute_suggester_metrics(chunk, mode=mode, source="history")
        text = compute_suggester_metrics(chunk, mode=mode, source="text")
        out[key] = {
            "n": len(chunk),
            "history_top1": hist.top1_bill,
            "text_top1": text.top1_bill,
        }
    return out


def m4_verdict(
    history_a: SuggesterMetrics,
    combined_a: SuggesterMetrics,
    *,
    run_status: str,
    cap_tripped: bool,
) -> str:
    if run_status != "complete":
        return (
            f"§10.5 M4: INCOMPLETE — run status {run_status!r}; metrics restricted to scored "
            "Bills; M4 gate not evaluated (no KEEP/DROP)"
        )
    if cap_tripped:
        return (
            "§10.5 M4: INCOMPLETE — cost cap tripped; metrics restricted to scored Bills; "
            "M4 gate not evaluated"
        )
    if history_a.top1_bill is None or combined_a.top1_bill is None:
        return "§10.5 M4: insufficient Mode A top-1 data to compare combined vs history"
    delta = combined_a.top1_bill - history_a.top1_bill
    if delta >= M4_MODE_A_MARGIN:
        return (
            f"§10.5 M4: KEEP text path — Mode A combined top-1 {combined_a.top1_bill:.1%} beats "
            f"history-only {history_a.top1_bill:.1%} by {delta:.1%} (≥ {M4_MODE_A_MARGIN:.0%} bar)"
        )
    return (
        f"§10.5 M4: DO NOT wire text — Mode A combined top-1 {combined_a.top1_bill:.1%} vs "
        f"history-only {history_a.top1_bill:.1%} (delta {delta:.1%}, need ≥ {M4_MODE_A_MARGIN:.0%})"
    )


def aggregate_rung_usage(results: Iterable[CascadeResult]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for res in results:
        if res.winning_rung:
            counts[res.winning_rung.model] += 1
        elif res.attempts:
            counts["needs_human"] += 1
    return dict(counts)


def assert_outputs_privacy_safe(path: Path) -> None:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
        for col in header:
            if col.lower() in _FORBIDDEN_OUTPUT_COLUMNS:
                raise ValueError(f"forbidden CSV column {col!r} in {path}")
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                for col, val in row.items():
                    if col and col.lower() in _FORBIDDEN_OUTPUT_COLUMNS:
                        raise ValueError(f"forbidden CSV column {col!r} in {path}")
                    if val and _looks_like_free_text(val):
                        raise ValueError(
                            f"forbidden free-text CSV value in column {col!r} ({path})"
                        )
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        _walk_json_for_forbidden_keys(data)
    elif path.suffix.lower() == ".md":
        text = path.read_text(encoding="utf-8")
        if _looks_like_free_text_in_markdown(text):
            raise ValueError(f"forbidden free-text content in markdown {path}")


def _looks_like_free_text(value: str) -> bool:
    v = value.strip()
    if not v:
        return False
    if v.isdigit():
        return False
    if len(v) <= 3 and v.replace(".", "").isdigit():
        return False
    # Allow UUID-like / numeric ids with punctuation common in exports.
    if re.fullmatch(r"[\dA-Za-z\-_./]+", v) and any(c.isalpha() for c in v):
        if " " not in v and len(v) < 40:
            return False
    return bool(re.search(r"[A-Za-z]{4,}", v)) and " " in v


def _looks_like_free_text_in_markdown(text: str) -> bool:
    """Free-text guard for markdown tables, except confidence-bar sweep data rows."""
    in_confidence_sweep = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Bar | Mode A text cov |"):
            in_confidence_sweep = True
            continue
        if stripped.startswith("| Bar | Mode B text cov |"):
            in_confidence_sweep = True
            continue
        if in_confidence_sweep and not stripped.startswith("|"):
            in_confidence_sweep = False
        if not line.startswith("|") or "|" not in line[1:]:
            continue
        if set(line.replace("|", "").replace("-", "").strip()) <= {":", " "}:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if any(
            c.lower() in ("suggester", "coverage", "precision", "stratum", "bar")
            for c in cells
        ):
            continue
        if (
            in_confidence_sweep
            and len(cells) >= 5
            and re.fullmatch(r"0\.\d+", (cells[0] or "").strip())
        ):
            continue
        for cell in cells:
            if cell and cell not in ("n/a", "0", "1") and len(cell.split()) >= 3:
                if _looks_like_free_text(cell):
                    return True
    return False


def _walk_json_for_forbidden_keys(node: Any, prefix: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if str(k).lower() in _FORBIDDEN_OUTPUT_COLUMNS:
                raise ValueError(f"forbidden JSON key {key!r}")
            _walk_json_for_forbidden_keys(v, key)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk_json_for_forbidden_keys(item, f"{prefix}[{i}]")


def write_report_and_outputs(
    *,
    out_dir: Path,
    stamp: str,
    scored: Sequence[ScoredTextBill],
    metrics: dict[str, Any],
    dry_info: Optional[dict[str, Any]],
    eval_since: date,
    eval_until: date,
    run_status: str = "complete",
    populations: Optional[dict[str, int]] = None,
    ladder_name: str = DEFAULT_BACKTEST_LADDER_NAME,
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    file_stamp = f"{ladder_name}_{stamp}"
    report_path = out_dir / f"u654a_report_{file_stamp}.md"
    csv_path = out_dir / f"u654a_predictions_{file_stamp}.csv"
    json_path = out_dir / f"u654a_summary_{file_stamp}.json"

    summary = {
        "model_version": MODEL_VERSION,
        "ladder": ladder_name,
        "evaluation_window": {"since": str(eval_since), "until": str(eval_until)},
        "run_status": run_status,
        "populations": populations or {},
        "sample_size": len(scored),
        "cap_tripped": metrics.get("cap_tripped", False),
        "scored_bill_count": metrics.get("scored_bill_count"),
        "unscored_bill_count": metrics.get("unscored_bill_count"),
        "metrics": metrics,
        "dry_run": dry_info,
    }
    json_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
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
            ]
        )
        for row in scored:
            for mode, hist, text in (
                ("A", row.history_a, row.text_a),
                ("B", row.history_b, row.text_b),
            ):
                for source, pred_scc, pred_proj, cascade in (
                    (
                        "history",
                        hist.predicted_scc_id,
                        hist.predicted_project_id,
                        None,
                    ),
                    (
                        "text",
                        text.predicted_scc_id if text else None,
                        text.predicted_project_id if text else None,
                        text.cascade if text else None,
                    ),
                ):
                    if pred_scc is None and source == "text" and text is None:
                        continue
                    correct_scc = ""
                    if pred_scc is not None:
                        correct_scc = "1" if pred_scc == row.bill.label_scc_id else "0"
                    correct_proj = ""
                    if mode == "B" and pred_proj is not None:
                        correct_proj = (
                            "1"
                            if pred_proj == row.bill.label_project_id
                            else "0"
                        )
                    raw_scc = ""
                    raw_proj = ""
                    raw_conf = ""
                    raw_correct_scc = ""
                    raw_correct_proj = ""
                    err_kind = ""
                    http_st = ""
                    if source == "text" and text is not None:
                        if text.raw_predicted_scc_id is not None:
                            raw_scc = str(text.raw_predicted_scc_id)
                            raw_correct_scc = (
                                "1"
                                if text.raw_predicted_scc_id == row.bill.label_scc_id
                                else "0"
                            )
                        if text.raw_predicted_project_id is not None:
                            raw_proj = str(text.raw_predicted_project_id)
                        if mode == "B" and text.raw_predicted_project_id is not None:
                            raw_correct_proj = (
                                "1"
                                if text.raw_predicted_project_id
                                == row.bill.label_project_id
                                else "0"
                            )
                        if text.raw_confidence is not None:
                            raw_conf = f"{text.raw_confidence:.4f}"
                        err_kind = text.error_kind or ""
                        if text.http_status is not None:
                            http_st = str(text.http_status)
                    w.writerow(
                        [
                            row.bill.bill_id,
                            row.bill.vendor_id,
                            mode,
                            source,
                            pred_scc if pred_scc is not None else "",
                            row.bill.label_scc_id,
                            pred_proj if pred_proj is not None else "",
                            row.bill.label_project_id
                            if row.bill.label_project_id is not None
                            else "",
                            correct_scc,
                            correct_proj,
                            row.stratum,
                            cascade.winning_rung.model
                            if cascade and cascade.winning_rung
                            else "",
                            cascade.winning_rung.provider
                            if cascade and cascade.winning_rung
                            else "",
                            "1"
                            if cascade and cascade.needs_human
                            else ("0" if cascade else ""),
                            raw_scc,
                            raw_proj,
                            raw_conf,
                            raw_correct_scc,
                            raw_correct_proj,
                            err_kind,
                            http_st,
                        ]
                    )

    def pct(v: Optional[Decimal]) -> str:
        return f"{v:.2%}" if v is not None else "n/a"

    lines = [
        f"# U-654a text backtest report ({stamp})",
        "",
        f"- ModelVersion: `{MODEL_VERSION}`",
        f"- Task: `{BILL_CODING_SUGGESTION_TASK.name}`",
        f"- Ladder: `{ladder_name}`",
        f"- Run status: {run_status}",
        f"- Evaluation window: {eval_since} .. {eval_until}",
        f"- Sample size: {len(scored)}",
    ]
    if populations:
        lines.append(
            f"- Populations (eval window): all Bills={populations.get('eval_window_bill_population', 'n/a')}; "
            f"text tier={populations.get('text_tier_bill_population', 'n/a')}"
        )
    if metrics.get("cap_tripped"):
        lines.append(
            f"- Cost cap: tripped (scored={metrics.get('scored_bill_count')}, "
            f"unscored={metrics.get('unscored_bill_count')})"
        )
    elif metrics.get("cost_overshoot_usd"):
        lines.append(
            f"- Cost cap: not tripped (all Bills scored); spend overshoot "
            f"${metrics['cost_overshoot_usd']:.4f} above cap"
        )
    if metrics.get("scored_bill_count") is not None:
        lines.append(f"- Bills scored (text cascade finished): {metrics['scored_bill_count']}")
    if metrics.get("call_timeout_count"):
        lines.append(
            f"- Cascade calls timed out (left unscored; resume with --resume-from): "
            f"{metrics['call_timeout_count']}"
        )
    if metrics.get("blob_unreadable_count"):
        lines.append(
            f"- Bills dropped (text blob unreadable after retries): {metrics['blob_unreadable_count']}"
        )
    if metrics.get("mode_a_n") is not None:
        lines.append(
            f"- Metric denominators: Mode A n={metrics['mode_a_n']}, Mode B n={metrics.get('mode_b_n')}"
        )
    if metrics.get("label_not_in_text_catalog_count") is not None:
        lines.append(
            f"- Label SCC not in text catalog: {metrics['label_not_in_text_catalog_count']} "
            f"(min catalog size {metrics.get('text_catalog_size_min_per_bill', 'n/a')}, "
            f"median {metrics.get('text_catalog_size_median_per_bill', 'n/a')})"
        )
    lines.append("")
    if dry_info:
        lines.append("## Dry-run composition (no model calls)")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(dry_info, indent=2))
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "## Mode A (project fixed to label project)",
            "",
            "Top-1 uses every Bill in the scored set (missing suggestion = miss). "
            f"Combined uses history when raw score ≥ {COMBINED_HISTORY_RAW_SCORE_THRESHOLD:.0%}, else text.",
            "",
            "| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |",
            "|-----------|----------|-----------|---------------|-----------|",
            f"| history | {pct(metrics['mode_a']['history']['coverage'])} | "
            f"{pct(metrics['mode_a']['history']['precision'])} | "
            f"{pct(metrics['mode_a']['history']['top1_bill'])} | "
            f"{pct(metrics['mode_a']['history']['top1_dollar'])} |",
            f"| text | {pct(metrics['mode_a']['text']['coverage'])} | "
            f"{pct(metrics['mode_a']['text']['precision'])} | "
            f"{pct(metrics['mode_a']['text']['top1_bill'])} | "
            f"{pct(metrics['mode_a']['text']['top1_dollar'])} |",
            f"| combined | {pct(metrics['mode_a']['combined']['coverage'])} | "
            f"{pct(metrics['mode_a']['combined']['precision'])} | "
            f"{pct(metrics['mode_a']['combined']['top1_bill'])} | "
            f"{pct(metrics['mode_a']['combined']['top1_dollar'])} |",
            "",
            "## Mode B (project predicted)",
            "",
            "| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |",
            "|-----------|----------|---------------|-----------|---------------|",
            f"| history | {pct(metrics['mode_b']['history']['coverage'])} | "
            f"{pct(metrics['mode_b']['history']['top1_bill'])} | "
            f"{pct(metrics['mode_b']['history']['top1_dollar'])} | "
            f"{pct(metrics['mode_b']['history']['project_top1'])} |",
            f"| text | {pct(metrics['mode_b']['text']['coverage'])} | "
            f"{pct(metrics['mode_b']['text']['top1_bill'])} | "
            f"{pct(metrics['mode_b']['text']['top1_dollar'])} | "
            f"{pct(metrics['mode_b']['text']['project_top1'])} |",
            "",
            "### Per-stratum Mode A top-1 (text vs history)",
            "",
            "| Stratum | n | History | Text |",
            "|---------|---|---------|------|",
        ]
    )
    for stratum, row in metrics["stratum_mode_a"].items():
        lines.append(
            f"| {stratum} | {row['n']} | {pct(row['history_top1'])} | {pct(row['text_top1'])} |"
        )

    lines.extend(
        [
            "",
            "### Rung usage (text cascade)",
            "",
            json.dumps(metrics["rung_usage"], indent=2),
            "",
            f"- needs_human count: {metrics['needs_human_count']}",
            f"- harness rate-limit retries: {metrics.get('throttle_retry_count', 0)}",
            f"- throttled final failures: {metrics.get('throttle_failure_count', 0)}",
            f"- harness retries by kind: {json.dumps(metrics.get('harness_retry_by_kind') or {})}",
            f"- cascade task errors: {metrics.get('cascade_error_count', 0)}",
            f"- duplicate SCC numbers excluded: {metrics.get('duplicate_scc_number_count', 0)}",
            f"- duplicate project abbrevs excluded: {metrics.get('duplicate_project_abbrev_count', 0)}",
            "",
            "### Mixed-code Bills (all sampled with mixed_code=true)",
            "",
            f"- n={metrics['mixed_code_all']['n']}",
            f"- history top-1: {pct(metrics['mixed_code_all']['history_top1'])}",
            f"- text top-1: {pct(metrics['mixed_code_all']['text_top1'])}",
            f"- combined top-1: {pct(metrics['mixed_code_all']['combined_top1'])}",
            "",
            f"- Total tokens in: {metrics['usage']['input_tokens']}",
            f"- Total tokens out: {metrics['usage']['output_tokens']}",
            f"- Total cost USD: {metrics['usage']['cost_usd']:.4f}",
            "",
            "### Call failures by kind (text cascade)",
            "",
            json.dumps(metrics.get("call_failures_by_kind") or {}, indent=2),
            "",
            "### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)",
            "",
        ]
    )
    sweep = metrics.get("confidence_bar_sweep") or {}
    best_bar = sweep.get("best_combined_top1_bar_mode_a")
    if best_bar is not None:
        lines.append(
            f"- Mode A combined top-1 maximized at confidence bar **{best_bar}** "
            "(history when raw score ≥ 60%, else text at bar)"
        )
        lines.append("")
    lines.append("| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |")
    lines.append("|-----|-----------------|------------------|-------------------|----------------------|-----------|")
    for bar in CONFIDENCE_BAR_SWEEP_BARS:
        row = (sweep.get("mode_a") or {}).get(str(bar)) or {}
        text_m = row.get("text") or {}
        delta = row.get("combined_delta_vs_history")
        lines.append(
            f"| {bar:.2f} | {pct(text_m.get('coverage'))} | {pct(text_m.get('precision'))} | "
            f"{pct(text_m.get('top1_bill'))} | {pct(row.get('combined_top1_bill'))} | "
            f"{pct(delta) if delta is not None else 'n/a'} |"
        )
    lines.extend(
        [
            "",
            "| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |",
            "|-----|-----------------|-------------------|----------------------|------------------------|",
        ]
    )
    for bar in CONFIDENCE_BAR_SWEEP_BARS:
        row = (sweep.get("mode_b") or {}).get(str(bar)) or {}
        text_m = row.get("text") or {}
        lines.append(
            f"| {bar:.2f} | {pct(text_m.get('coverage'))} | {pct(text_m.get('top1_bill'))} | "
            f"{pct(text_m.get('project_top1'))} | {pct(row.get('combined_top1_bill'))} |"
        )
    lines.append("")
    cat_cmp = metrics.get("combined_vs_history_catalog_eligible")
    if cat_cmp and cat_cmp.get("n", 0) > 0:
        lines.extend(
            [
                "### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)",
                "",
                f"- n={cat_cmp['n']}",
                f"- history top-1: {pct(cat_cmp.get('history_top1_bill'))}",
                f"- combined top-1: {pct(cat_cmp.get('combined_top1_bill'))}",
                f"- delta: {pct(cat_cmp.get('delta_top1_bill'))}",
                "",
                "(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)",
                "",
            ]
        )
    lines.extend(
        [
            f"**{metrics['verdict_m4']}**",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")

    assert_outputs_privacy_safe(csv_path)
    assert_outputs_privacy_safe(json_path)
    assert_outputs_privacy_safe(report_path)
    return report_path, csv_path, json_path


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(type(obj))


def _failed_cascade_result() -> CascadeResult:
    return CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[],
    )


# U-654d: one cascade (up to 3 rungs + retries) normally takes seconds; a call
# still open after this is a stalled connection, not a slow model.
CASCADE_CALL_TIMEOUT_SECONDS = 300.0


def _record_harness_retry(cost_state: dict[str, Any], kind: str) -> None:
    cost_state["throttle_retry_count"] = int(cost_state.get("throttle_retry_count", 0)) + 1
    by_kind: dict[str, int] = cost_state.setdefault("harness_retry_by_kind", {})
    by_kind[kind] = int(by_kind.get(kind, 0)) + 1


async def run_capped_cascade(
    call: Callable[[], Any],
    *,
    est: float,
    cost_state: dict[str, Any],
    max_cost_usd: float,
) -> CascadeResult | None:
    """One cascade with cap, timeout, and harness-level rate-limit re-calls. None = leave unscored."""
    if cost_state.get("stopped"):
        return None
    if cost_state["usd"] + est > max_cost_usd:
        cost_state["stopped"] = True
        return None
    cascade: CascadeResult = _failed_cascade_result()
    merged_attempts: list[Attempt] = []
    try:
        max_attempts = 1 + len(HARNESS_RATE_LIMIT_BACKOFF_SECONDS)
        for attempt_idx in range(max_attempts):
            if attempt_idx > 0:
                if cost_state["usd"] + est > max_cost_usd:
                    cost_state["stopped"] = True
                    return None
                prev_kind, _ = cascade_call_error_summary(cascade)
                if prev_kind not in ("rate_limit", "overloaded"):
                    break
                _record_harness_retry(cost_state, prev_kind)
                await asyncio.sleep(HARNESS_RATE_LIMIT_BACKOFF_SECONDS[attempt_idx - 1])
            try:
                cascade = await asyncio.wait_for(
                    call(),
                    timeout=CASCADE_CALL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                cost_state["call_timeout_count"] = (
                    int(cost_state.get("call_timeout_count", 0)) + 1
                )
                return None
            merged_attempts.extend(cascade.attempts)
            apply_cascade_spend(cascade, cost_state)
            if cascade.accepted:
                break
            if not cascade_only_rate_or_overloaded_failures(cascade):
                break
            if attempt_idx >= len(HARNESS_RATE_LIMIT_BACKOFF_SECONDS):
                cost_state["throttle_failure_count"] = (
                    int(cost_state.get("throttle_failure_count", 0)) + 1
                )
                break
    except Exception:
        cost_state["cascade_error_count"] = (
            int(cost_state.get("cascade_error_count", 0)) + 1
        )
        cascade = _failed_cascade_result()
    if merged_attempts:
        cascade.attempts = merged_attempts
    if cost_state["usd"] > max_cost_usd:
        cost_state["stopped"] = True
    return cascade


def _rung_from_prior_csv(
    model: str,
    provider: str,
    *,
    ladder_name: str | None,
) -> Rung | None:
    if not model:
        return None
    prov = (provider or "").strip()
    if prov:
        return Rung(prov, model)
    if ladder_name and ladder_name.startswith("claude"):
        return Rung("anthropic", model)
    if model in PRICING.get("anthropic", {}):
        return Rung("anthropic", model)
    return Rung("foundry", model)


def load_prior_text_results(
    csv_path: Path,
    *,
    ladder_name: str | None = None,
) -> dict[tuple[int, str], TextAttemptResult]:
    """Rebuild the finished (Bill, mode) text results from an earlier run's
    predictions CSV (U-654d resume). A text row exists only for a (Bill, mode)
    whose cascade ran to its own end, so these are exactly the scored ones."""
    out: dict[tuple[int, str], TextAttemptResult] = {}
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("suggester") != "text":
                continue
            needs_human = row.get("needs_human") == "1"
            model = (row.get("winning_rung") or "").strip()
            provider = (row.get("winning_rung_provider") or "").strip()
            rung = _rung_from_prior_csv(model, provider, ladder_name=ladder_name)
            resume_provider = rung.provider if rung else "foundry"
            cascade = CascadeResult(
                accepted=not needs_human and rung is not None,
                needs_human=needs_human,
                result=None,
                confidence=None,
                winning_rung=rung,
                # A placeholder attempt keeps a needs_human result counted in
                # aggregate_rung_usage (it counts only cascades that attempted).
                # The CSV cannot tell a cascade ERROR (no attempts, not counted in
                # rung usage originally) from an exhausted ladder, so a reload may
                # count the earlier leg's errors as needs_human (documented residual).
                attempts=[] if rung else [
                    Attempt(rung=Rung(resume_provider, "resumed"), accepted=False)
                ],
            )

            def _int(v: str) -> Optional[int]:
                return int(v) if (v or "").strip() else None

            out[(int(row["BillId"]), row["mode"])] = TextAttemptResult(
                mode=row["mode"],
                cascade=cascade,
                predicted_scc_id=_int(row.get("predicted_scc_id", "")),
                predicted_project_id=_int(row.get("predicted_project_id", "")),
            )
    return out


async def run_text_cascade_batch(
    work: Sequence[tuple[TextBillRow, str, str, dict[str, Any]]],
    *,
    transport_for: Callable[[str], Any],
    concurrency: int,
    max_cost_usd: float,
    cost_state: dict[str, Any],
    ladder: tuple[Rung, ...] | None = None,
    out: Optional[dict[tuple[int, str], TextAttemptResult]] = None,
) -> dict[tuple[int, str], TextAttemptResult]:
    resolved_ladder = ladder if ladder is not None else BILL_CODING_FOUNDRY_LADDER
    sem = asyncio.Semaphore(concurrency)
    results_out: dict[tuple[int, str], TextAttemptResult] = (
        out if out is not None else {}
    )

    async def one(item: tuple[TextBillRow, str, str, dict[str, Any]]) -> None:
        bill, mode, _stratum, payload = item
        async with sem:
            # Invariant (U-654a r3): a (Bill, mode) the cost cap stops or cuts off gets NO
            # result entry — it stays unscored, which makes the run INCOMPLETE. Only a
            # cascade that ran to its own end (accepted, or genuinely failed) is scored.
            if cost_state.get("stopped"):
                return
            est = estimate_ladder_first_rung_cost_usd(resolved_ladder)
            if cost_state["usd"] + est > max_cost_usd:
                cost_state["stopped"] = True
                return
            cascade = await run_capped_cascade(
                lambda: run_bill_coding_suggestion(
                    payload,
                    transport_for=transport_for,
                    ladder=resolved_ladder,
                ),
                est=est,
                cost_state=cost_state,
                max_cost_usd=max_cost_usd,
            )
            if cascade is None:
                return
            scc_id, proj_id = resolve_text_prediction(
                cascade,
                scc_number_to_id=cost_state["scc_number_to_id"],
                proj_abbrev_to_id=cost_state["proj_abbrev_to_id"],
            )
            raw_scc, raw_proj, raw_conf = resolve_raw_text_prediction(
                cascade,
                scc_number_to_id=cost_state["scc_number_to_id"],
                proj_abbrev_to_id=cost_state["proj_abbrev_to_id"],
            )
            err_kind, http_status = cascade_call_error_summary(cascade)
            results_out[(bill.bill_id, mode)] = TextAttemptResult(
                mode=mode,
                cascade=cascade,
                predicted_scc_id=scc_id,
                predicted_project_id=proj_id,
                raw_predicted_scc_id=raw_scc,
                raw_predicted_project_id=raw_proj,
                raw_confidence=raw_conf,
                error_kind=err_kind,
                http_status=http_status,
            )

    tasks = [one(w) for w in work]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    for res in gathered:
        if isinstance(res, Exception):
            cost_state["cascade_error_count"] = (
                int(cost_state.get("cascade_error_count", 0)) + 1
            )
    return results_out


from shared.text_blob_read import (
    _BLOB_READ_ATTEMPTS,
    blob_error_is_transient,
    make_text_blob_reader,
    read_text_blob_with_retry,
)

# A run that loses more than this share of its sample to unreadable blobs is
# INCOMPLETE: the verdict would rest on a smaller, possibly skewed sample.
MAX_UNREADABLE_SHARE = 0.02

# Backward-compatible alias for tests that patch u654a._blob_error_is_transient.
_blob_error_is_transient = blob_error_is_transient


def run_status_after_unreadable(run_status: str, *, unreadable: int, sampled: int) -> str:
    """A complete run that dropped more than MAX_UNREADABLE_SHARE of its sample
    to unreadable blobs is incomplete (no KEEP/DROP verdict)."""
    if run_status == "complete" and unreadable > MAX_UNREADABLE_SHARE * sampled:
        return "incomplete"
    return run_status


def execute_run(build_work: Callable[[], None], run_cascades: Callable[[], None]) -> str:
    """Build the work list, then run it. An interrupt while building makes NO model
    call (an operator's Ctrl-C stops spending, it never starts it)."""
    try:
        build_work()
    except KeyboardInterrupt:
        return "partial"
    try:
        run_cascades()
    except KeyboardInterrupt:
        return "partial"
    return "complete"


@dataclass
class RunState:
    run_status: str
    scored_bill_count: int
    unscored_bill_count: int
    cap_tripped: bool
    cost_overshoot_usd: Optional[float]


def finalize_run_state(
    scored: Sequence["ScoredTextBill"],
    results: dict[tuple[int, str], "TextAttemptResult"],
    *,
    cost_state: dict[str, Any],
    max_cost_usd: float,
    run_status: str,
) -> RunState:
    """One rule: the run is complete only if EVERY sampled (Bill, mode) has a result.
    Anything missing makes it incomplete (or keeps it partial after an interrupt);
    no KEEP/DROP is ever printed for it."""
    expected = {(r.bill.bill_id, mode) for r in scored for mode in ("A", "B")}
    missing = expected - set(results)
    unscored_bills = {bill_id for bill_id, _mode in missing}
    if run_status == "complete" and missing:
        run_status = "incomplete"
    usd = float(cost_state.get("usd", 0.0))
    return RunState(
        run_status=run_status,
        scored_bill_count=len(scored) - len(unscored_bills),
        unscored_bill_count=len(unscored_bills),
        cap_tripped=bool(cost_state.get("stopped")) and bool(missing),
        cost_overshoot_usd=(usd - max_cost_usd) if usd > max_cost_usd else None,
    )


def prepare_scored_rows(
    sample: Sequence[tuple[TextBillRow, str]],
    vendor_votes: dict[int, list[PriorBillVote]],
) -> list[ScoredTextBill]:
    rows: list[ScoredTextBill] = []
    for bill, stratum in sample:
        priors = priors_for_bill(vendor_votes, bill)
        rows.append(
            ScoredTextBill(
                bill=bill,
                stratum=stratum,
                history_a=predict_mode_a(bill, priors),
                history_b=predict_mode_b(bill, priors),
            )
        )
    return rows


def bills_with_text_mode_a(rows: Sequence[ScoredTextBill]) -> list[ScoredTextBill]:
    return [r for r in rows if r.text_a is not None]


def bills_with_text_mode_b(rows: Sequence[ScoredTextBill]) -> list[ScoredTextBill]:
    return [r for r in rows if r.text_b is not None]


def metric_rows_for_mode(
    rows: Sequence[ScoredTextBill],
    *,
    mode: str,
    restrict: bool,
) -> list[ScoredTextBill]:
    if not restrict:
        return list(rows)
    if mode == "A":
        return bills_with_text_mode_a(rows)
    if mode == "B":
        return bills_with_text_mode_b(rows)
    raise ValueError(f"unknown mode {mode!r}")


def build_metrics(
    scored: Sequence[ScoredTextBill],
    *,
    cascades: Sequence[CascadeResult],
    usage_totals: dict[str, Any],
    cap_tripped: bool,
    scored_bill_count: int,
    unscored_bill_count: int,
    throttle_retry_count: int,
    throttle_failure_count: int,
    cascade_error_count: int,
    duplicate_scc_number_count: int,
    duplicate_project_abbrev_count: int,
    catalog_diagnostics: Optional[dict[str, Any]] = None,
    cost_overshoot_usd: Optional[float] = None,
    run_status: str = "complete",
    harness_retry_by_kind: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    restrict = cap_tripped or run_status != "complete"
    rows_a = metric_rows_for_mode(scored, mode="A", restrict=restrict)
    rows_b = metric_rows_for_mode(scored, mode="B", restrict=restrict)
    mode_a_hist = compute_suggester_metrics(rows_a, mode="A", source="history")
    mode_a_text = compute_suggester_metrics(rows_a, mode="A", source="text")
    mode_a_combined = compute_suggester_metrics(rows_a, mode="A", source="combined")
    mode_b_hist = compute_suggester_metrics(rows_b, mode="B", source="history")
    mode_b_text = compute_suggester_metrics(rows_b, mode="B", source="text")
    mixed_rows = [r for r in rows_a if r.bill.mixed_code]
    if catalog_diagnostics is not None:
        exclude_ids = catalog_diagnostics.get("label_not_in_catalog_bill_ids") or set()
        # Same denominator as the Mode A tables: on a restricted run, only Bills scored in Mode A.
        cat_rows = [r for r in rows_a if r.bill.bill_id not in exclude_ids]
        m4_hist_cat = compute_suggester_metrics(cat_rows, mode="A", source="history")
        m4_combined_cat = compute_suggester_metrics(cat_rows, mode="A", source="combined")
        combined_vs_history_catalog_eligible = {
            "n": len(cat_rows),
            "history_top1_bill": m4_hist_cat.top1_bill,
            "combined_top1_bill": m4_combined_cat.top1_bill,
            "delta_top1_bill": (
                (m4_combined_cat.top1_bill - m4_hist_cat.top1_bill)
                if m4_combined_cat.top1_bill is not None and m4_hist_cat.top1_bill is not None
                else None
            ),
        }
    else:
        combined_vs_history_catalog_eligible = None
    mixed_hist = compute_suggester_metrics(mixed_rows, mode="A", source="history")
    mixed_text = compute_suggester_metrics(mixed_rows, mode="A", source="text")
    mixed_combined = compute_suggester_metrics(mixed_rows, mode="A", source="combined")
    needs_human = sum(1 for c in cascades if c.needs_human)
    catalog_public = (
        {
            k: v
            for k, v in catalog_diagnostics.items()
            if k != "label_not_in_catalog_bill_ids"
        }
        if catalog_diagnostics
        else {}
    )
    return {
        "cap_tripped": cap_tripped,
        "scored_bill_count": scored_bill_count,
        "unscored_bill_count": unscored_bill_count,
        "mode_a_n": len(rows_a),
        "mode_b_n": len(rows_b),
        "mode_a": {
            "history": _metrics_to_dict(mode_a_hist),
            "text": _metrics_to_dict(mode_a_text),
            "combined": _metrics_to_dict(mode_a_combined),
        },
        "mode_b": {
            "history": _metrics_to_dict(mode_b_hist),
            "text": _metrics_to_dict(mode_b_text),
        },
        "stratum_mode_a": {
            k: {
                "n": v["n"],
                "history_top1": v["history_top1"],
                "text_top1": v["text_top1"],
            }
            for k, v in stratum_table(rows_a, mode="A").items()
        },
        "mixed_code_all": {
            "n": len(mixed_rows),
            "history_top1": mixed_hist.top1_bill,
            "text_top1": mixed_text.top1_bill,
            "combined_top1": mixed_combined.top1_bill,
        },
        "rung_usage": aggregate_rung_usage(cascades),
        "needs_human_count": needs_human,
        "throttle_retry_count": throttle_retry_count,
        "throttle_failure_count": throttle_failure_count,
        "harness_retry_by_kind": dict(harness_retry_by_kind or {}),
        "cascade_error_count": cascade_error_count,
        "call_failures_by_kind": aggregate_call_failures_by_kind(
            t
            for row in scored
            for t in (row.text_a, row.text_b)
            if t is not None
        ),
        "confidence_bar_sweep": confidence_bar_sweep(scored),
        "duplicate_scc_number_count": duplicate_scc_number_count,
        "duplicate_project_abbrev_count": duplicate_project_abbrev_count,
        "usage": usage_totals,
        "cost_overshoot_usd": cost_overshoot_usd,
        "combined_vs_history_catalog_eligible": combined_vs_history_catalog_eligible,
        **catalog_public,
        "verdict_m4": m4_verdict(
            mode_a_hist,
            mode_a_combined,
            run_status=run_status,
            cap_tripped=cap_tripped,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="U-654a text-tier bill coding backtest.")
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
    ap.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out-dir", type=Path, default=Path("docs/design/u654a"))
    ap.add_argument(
        "--max-cost-usd",
        type=float,
        default=DEFAULT_MAX_COST_USD,
    )
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument(
        "--claude-concurrency",
        type=int,
        default=DEFAULT_CLAUDE_CONCURRENCY,
        help="Parallel in-flight calls for Claude ladders (default: 2)",
    )
    ap.add_argument(
        "--run",
        action="store_true",
        help="Call the cascade (default: dry-run only)",
    )
    ap.add_argument(
        "--ladder",
        choices=sorted(BACKTEST_LADDERS),
        default=DEFAULT_BACKTEST_LADDER_NAME,
        help="Cascade ladder for text suggester (default: foundry)",
    )
    ap.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help=(
            "An earlier run's u654a_predictions_*.csv (same seed/window/sample): its "
            "finished (Bill, mode) results are reused and only the rest are run; its "
            "summary JSON beside it supplies the spend already incurred."
        ),
    )
    args = ap.parse_args(argv)
    selected_ladder = ladder_by_name(args.ladder)

    validate_windows(
        date(2025, 10, 11),
        date(2026, 4, 10),
        args.eval_since,
        args.eval_until,
    )

    bills = load_bills_from_db()
    blob_map = load_blob_urls_from_db()
    text_bills = merge_text_bills(bills, blob_map)
    vendor_votes = build_vendor_priors(bills)
    populations = count_eval_populations(
        bills,
        text_bills,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
    )
    sample = stratified_sample(
        text_bills,
        vendor_votes,
        sample_size=args.sample_size,
        seed=args.seed,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
    )
    ref = load_reference_data()
    catalog_diag = catalog_diagnostics_for_sample(sample, ref)
    dry_info = {
        **dry_run_summary(
            sample,
            sample_size=args.sample_size,
            populations=populations,
            ladder_name=args.ladder,
            ladder=selected_ladder,
            ref=ref,
            vendor_votes=vendor_votes,
        ),
        **{
            k: v
            for k, v in catalog_diag.items()
            if k != "label_not_in_catalog_bill_ids"
        },
    }

    if not args.run:
        print(json.dumps(dry_info, indent=2, default=_json_default))
        return 0
    run_status = "complete"
    scored = prepare_scored_rows(sample, vendor_votes)
    work: list[tuple[TextBillRow, str, str, dict[str, Any]]] = []
    cost_state: dict[str, Any] = {
        "usd": 0.0,
        "stopped": False,
        "throttle_retry_count": 0,
        "throttle_failure_count": 0,
        "cascade_error_count": 0,
        "scc_number_to_id": ref.scc_number_to_id,
        "proj_abbrev_to_id": ref.proj_abbrev_to_id,
    }
    results: dict[tuple[int, str], TextAttemptResult] = {}
    prior_tokens = {"input_tokens": 0, "output_tokens": 0}
    if args.resume_from:
        prior_summary_path = args.resume_from.with_name(
            args.resume_from.name.replace("predictions", "summary")
        ).with_suffix(".json")
        prior_summary = json.loads(prior_summary_path.read_text(encoding="utf-8"))
        prior_ladder = prior_summary.get("ladder", DEFAULT_BACKTEST_LADDER_NAME)
        if prior_ladder != args.ladder:
            raise SystemExit(
                f"--resume-from ladder {prior_ladder!r} differs from --ladder {args.ladder!r}"
            )
        prior = load_prior_text_results(args.resume_from, ladder_name=prior_ladder)
        sample_ids = {b.bill_id for b, _ in sample}
        stray = {bid for bid, _ in prior} - sample_ids
        if stray:
            raise SystemExit(
                f"--resume-from has {len(stray)} Bill(s) outside this sample; "
                "seed, window or sample size differ from the earlier run"
            )
        results.update(prior)
        prior_usage = prior_summary["metrics"]["usage"]
        cost_state["usd"] += float(prior_usage.get("cost_usd") or 0.0)
        prior_tokens["input_tokens"] = int(prior_usage.get("input_tokens") or 0)
        prior_tokens["output_tokens"] = int(prior_usage.get("output_tokens") or 0)
        # Carry the earlier leg's counters so the merged report covers the whole sample.
        prior_metrics = prior_summary["metrics"]
        for key in ("throttle_retry_count", "throttle_failure_count", "cascade_error_count", "call_timeout_count"):
            cost_state[key] = int(cost_state.get(key, 0)) + int(prior_metrics.get(key) or 0)
        print(f"Resuming: {len(prior)} (Bill, mode) results reused; prior spend ${cost_state['usd']:.4f}")

    blob_storage: list[Any] = []
    read_blob = make_text_blob_reader(blob_storage)

    from intelligence.transport import registry as transport_registry

    cascade_concurrency = llm_backtest_cascade_concurrency(
        args.ladder,
        concurrency=args.concurrency,
        claude_concurrency=args.claude_concurrency,
    )

    def run_cascades_on_work() -> None:
        if not work:
            return
        asyncio.run(
            run_text_cascade_batch(
                work,
                transport_for=transport_registry.get_transport,
                concurrency=cascade_concurrency,
                max_cost_usd=args.max_cost_usd,
                cost_state=cost_state,
                ladder=selected_ladder,
                out=results,
            )
        )

    unreadable_bill_ids: set[int] = set()

    def build_work() -> None:
        from shared.storage import AzureBlobStorageError

        for row in scored:
            bill = row.bill
            todo_modes = [m for m in ("A", "B") if (bill.bill_id, m) not in results]
            if not todo_modes:
                continue  # resumed: both modes already finished, no blob read
            priors = priors_for_bill(vendor_votes, bill)
            try:
                bill_text = read_text_blob_with_retry(read_blob, bill.extracted_text_blob_url)
            except AzureBlobStorageError as exc:
                if not _blob_error_is_transient(exc):
                    raise  # auth / 404 / config: stop the run, never a silent drop
                # Still timing out after retries: drop the Bill rather than abort the
                # run. Counted and reported; it is never scored.
                unreadable_bill_ids.add(bill.bill_id)
                continue
            vname = ref.vendor_names.get(bill.vendor_id, "")
            candidate_sccs = build_candidate_sccs_for_bill(
                ref.scc_by_id,
                as_of=bill.created,
                ambiguous_numbers=ref.ambiguous_scc_numbers,
            )
            proj_candidates = build_candidate_projects_for_bill(
                projects_with_bills_before(bills, bill.created, ref.proj_by_id),
                ambiguous_abbrevs=ref.ambiguous_project_abbrevs,
            )
            payload_a = build_payload(
                bill,
                bill_text=bill_text,
                vendor_name=vname,
                mode="A",
                priors=priors,
                scc_by_id=ref.scc_by_id,
                candidate_sccs=candidate_sccs,
                candidate_projects=None,
            )
            payload_b = build_payload(
                bill,
                bill_text=bill_text,
                vendor_name=vname,
                mode="B",
                priors=priors,
                scc_by_id=ref.scc_by_id,
                candidate_sccs=candidate_sccs,
                candidate_projects=proj_candidates,
            )
            if "A" in todo_modes:
                work.append((bill, "A", row.stratum, payload_a))
            if "B" in todo_modes:
                work.append((bill, "B", row.stratum, payload_b))

    run_status = execute_run(build_work, run_cascades_on_work)
    if unreadable_bill_ids:
        scored = [row for row in scored if row.bill.bill_id not in unreadable_bill_ids]
        kept_sample = [(b, s) for b, s in sample if b.bill_id not in unreadable_bill_ids]
        catalog_diag = catalog_diagnostics_for_sample(kept_sample, ref)
        run_status = run_status_after_unreadable(
            run_status, unreadable=len(unreadable_bill_ids), sampled=len(sample)
        )

    for row in scored:
        row.text_a = results.get((row.bill.bill_id, "A"))
        row.text_b = results.get((row.bill.bill_id, "B"))

    state = finalize_run_state(
        scored,
        results,
        cost_state=cost_state,
        max_cost_usd=args.max_cost_usd,
        run_status=run_status,
    )
    run_status = state.run_status
    scored_bill_count = state.scored_bill_count
    unscored_bill_count = state.unscored_bill_count
    cap_tripped = state.cap_tripped
    cost_overshoot_usd = state.cost_overshoot_usd

    # Counted over the scored Bills only, so a Bill dropped on a resume (its blob
    # unreadable) cannot leave a reloaded half behind in the counts.
    cascades = [
        t.cascade for row in scored for t in (row.text_a, row.text_b) if t is not None
    ]
    usage = {
        "input_tokens": prior_tokens["input_tokens"],
        "output_tokens": prior_tokens["output_tokens"],
        "cost_usd": cost_state["usd"],
    }
    for c in cascades:
        for att in c.attempts:
            usage["input_tokens"] += att.usage.input_tokens
            usage["output_tokens"] += att.usage.output_tokens

    metrics = build_metrics(
        scored,
        cascades=cascades,
        usage_totals=usage,
        cap_tripped=cap_tripped,
        scored_bill_count=scored_bill_count,
        unscored_bill_count=unscored_bill_count,
        throttle_retry_count=int(cost_state.get("throttle_retry_count", 0)),
        throttle_failure_count=int(cost_state.get("throttle_failure_count", 0)),
        cascade_error_count=int(cost_state.get("cascade_error_count", 0)),
        duplicate_scc_number_count=ref.duplicate_scc_number_count,
        duplicate_project_abbrev_count=ref.duplicate_project_abbrev_count,
        catalog_diagnostics=catalog_diag,
        cost_overshoot_usd=cost_overshoot_usd,
        run_status=run_status,
        harness_retry_by_kind=dict(cost_state.get("harness_retry_by_kind") or {}),
    )
    metrics["blob_unreadable_count"] = len(unreadable_bill_ids)
    metrics["call_timeout_count"] = int(cost_state.get("call_timeout_count", 0))
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    write_report_and_outputs(
        out_dir=args.out_dir,
        stamp=stamp,
        scored=scored,
        metrics=metrics,
        dry_info=None,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        run_status=run_status,
        populations=populations,
        ladder_name=args.ladder,
    )
    print(
        f"U-654a run {run_status} ladder={args.ladder}; cost USD ~ {usage['cost_usd']:.4f} "
        f"(scored={scored_bill_count}, unscored={unscored_bill_count})"
    )
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
