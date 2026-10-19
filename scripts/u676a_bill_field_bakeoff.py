"""
U-676a: offline bake-off for bill HEADER field pre-fill (rules mapper vs LLM).

Compares Arm A (BillExtractionMapper on DI blob) vs Arm B (structured LLM on masked text)
against completed Bill ground truth. Dry-run by default; --run calls Foundry.

Usage (repo root):
  ./.venv/bin/python -m scripts.u676a_bill_field_bakeoff
  ./.venv/bin/python -m scripts.u676a_bill_field_bakeoff --run
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
from contextlib import contextmanager
from dataclasses import InitVar, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional, Sequence

from entities.attachment.business.extraction_service import extraction_payload_is_current
from entities.bill.business.extraction_mapper import BillExtractionMapper, BillExtractionResult
from intelligence.cascade.bill_header_extraction import (
    BILL_HEADER_EXTRACTION_TASK,
    BILL_HEADER_TEXT_CHAR_BUDGET,
    run_bill_header_extraction,
)
from intelligence.cascade.bill_coding import (
    BILL_CODING_FOUNDRY_LADDER,
    BACKTEST_LADDERS,
    ladder_by_name,
)
from intelligence.cascade.core import CascadeResult, Rung
from scripts.sync_helper import assert_cli_system_admin
from scripts.u653b_backtest import (
    DEFAULT_EVAL_SINCE,
    DEFAULT_EVAL_UNTIL,
    _date_only,
    _parse_dt,
    validate_windows,
)
from scripts.u654a_text_backtest import (
    MAX_UNREADABLE_SHARE,
    RunState,
    _ESTIMATE_OUTPUT_TOKENS_PER_CALL,
    _failed_cascade_result,
    _json_default,
    assert_outputs_privacy_safe,
    bill_cascade_spend_usd,
    cascade_call_error_summary,
    estimate_ladder_first_rung_cost_usd,
    estimate_payload_input_tokens,
    execute_run,
    run_capped_cascade,
    run_status_after_unreadable,
)
from shared.database import get_connection
from shared.text_blob_read import (
    blob_error_is_transient,
    make_text_blob_reader,
    read_text_blob_with_retry,
)

MODEL_VERSION = "u676a-bill-header-bakeoff-v1"
DEFAULT_SAMPLE_SIZE = 200
DEFAULT_SEED = 676
DEFAULT_MAX_COST_USD = 10.0
DEFAULT_CONCURRENCY = 4
DEFAULT_CLAUDE_CONCURRENCY = 2
DEFAULT_ARMS = "A,B"
HEADER_CONFIDENCE_BAR_SWEEP_BARS: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80)
DEFAULT_CLAUDE_LADDER = "claude-haiku"
ACCURACY_BAR = Decimal("0.80")
AMOUNT_TOLERANCE = Decimal("0.01")
# Fixed matcher input for LLM arms B/C (never use model self-reported confidence).
LLM_VENDOR_MATCH_CONFIDENCE = 0.9
ARM_A_MEASUREMENT_NOTE = (
    "Arm A measured on stored text + key/value pairs only — no DI tables "
    "(U-535a blob shape)."
)

_INTAKE_STRATA = ("manual", "agent", "other")

_LOAD_HEADER_BILLS_SQL = """
WITH CodedBills AS (
    SELECT b.[Id] AS [BillId]
    FROM dbo.[Bill] b
    WHERE b.[IsDraft] = 0
),
ContractLaborBills AS (
    SELECT DISTINCT bli.[BillId]
    FROM dbo.[ContractLaborLineItem] clli
    INNER JOIN dbo.[BillLineItem] bli ON bli.[Id] = clli.[BillLineItemId]
),
AllBillAttachments AS (
    SELECT
        bli.[BillId],
        COUNT(DISTINCT blia.[AttachmentId]) AS [AttachmentCount]
    FROM dbo.[BillLineItem] bli
    INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
    GROUP BY bli.[BillId]
),
AttachmentLinks AS (
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
),
SingleAttachment AS (
    SELECT al.[BillId], al.[ExtractedTextBlobUrl]
    FROM AttachmentLinks al
    INNER JOIN AllBillAttachments aba ON aba.[BillId] = al.[BillId]
    WHERE al.[Rn] = 1 AND aba.[AttachmentCount] = 1
),
FirstLineDescription AS (
    SELECT
        bli.[BillId],
        bli.[Description],
        ROW_NUMBER() OVER (
            PARTITION BY bli.[BillId]
            ORDER BY bli.[Id] ASC
        ) AS [Rn]
    FROM dbo.[BillLineItem] bli
)
SELECT
    b.[Id] AS [BillId],
    b.[VendorId],
    v.[PublicId] AS [VendorPublicId],
    b.[BillNumber],
    b.[BillDate],
    b.[TotalAmount],
    b.[CreatedDatetime],
    b.[IntakeSource],
    sa.[ExtractedTextBlobUrl],
    fld.[Description] AS [FirstLineDescription]
FROM dbo.[Bill] b
INNER JOIN CodedBills cb ON cb.[BillId] = b.[Id]
INNER JOIN SingleAttachment sa ON sa.[BillId] = b.[Id]
INNER JOIN dbo.[Vendor] v ON v.[Id] = b.[VendorId]
LEFT JOIN FirstLineDescription fld ON fld.[BillId] = b.[Id] AND fld.[Rn] = 1
WHERE b.[IsDraft] = 0
  AND NOT EXISTS (
      SELECT 1 FROM ContractLaborBills clb WHERE clb.[BillId] = b.[Id]
  )
ORDER BY b.[CreatedDatetime], b.[Id]
"""


@dataclass(frozen=True)
class HeaderBillRow:
    bill_id: int
    vendor_id: int
    vendor_public_id: str
    bill_number: Optional[str]
    bill_date: Optional[date]
    total_amount: Optional[Decimal]
    created: datetime
    intake_source: Optional[str]
    extracted_text_blob_url: str
    first_line_description: Optional[str]


@dataclass(frozen=True)
class HeaderTruth:
    vendor_id: int
    vendor_public_id: str
    bill_number: Optional[str]
    bill_date: Optional[date]
    total_amount: Optional[Decimal]
    description: Optional[str]


@dataclass
class ArmPrediction:
    vendor_public_id: Optional[str] = None
    bill_number: Optional[str] = None
    bill_date: Optional[str] = None
    total_amount: Optional[Decimal] = None
    description: Optional[str] = None
    latency_ms: int = 0
    arm_error: bool = False
    cascade: Optional[CascadeResult] = None
    spend_usd: float = 0.0
    raw_confidence: Optional[float] = None
    error_kind: str = "none"
    http_status: Optional[int] = None
    raw_vendor_correct: Optional[bool] = None
    raw_bill_number_correct: Optional[bool] = None
    raw_bill_date_correct: Optional[bool] = None
    raw_amount_correct: Optional[bool] = None
    raw_description_nonempty: Optional[bool] = None
    raw_vendor_resolve_failed: bool = False
    accepted_vendor_resolve_failed: bool = False


@dataclass
class ScoredHeaderBill:
    bill: HeaderBillRow
    stratum: str
    truth: HeaderTruth
    extraction_method: Optional[str] = None
    preds: dict[str, ArmPrediction] = field(default_factory=dict)
    blob_skip_reason: Optional[str] = None  # unreadable | unparseable | stale
    llm_arms_scored: frozenset[str] = field(default_factory=frozenset)
    init_arm_a: InitVar[ArmPrediction | None] = None
    init_arm_b: InitVar[ArmPrediction | None] = None
    init_arm_c: InitVar[ArmPrediction | None] = None

    def __post_init__(
        self,
        init_arm_a: ArmPrediction | None,
        init_arm_b: ArmPrediction | None,
        init_arm_c: ArmPrediction | None,
    ) -> None:
        if init_arm_a is not None:
            self.preds["A"] = init_arm_a
        elif "A" not in self.preds:
            self.preds["A"] = ArmPrediction()
        if init_arm_b is not None:
            self.preds["B"] = init_arm_b
        if init_arm_c is not None:
            self.preds["C"] = init_arm_c

    @property
    def arm_a(self) -> ArmPrediction:
        return self.preds.setdefault("A", ArmPrediction())

    @arm_a.setter
    def arm_a(self, value: ArmPrediction) -> None:
        self.preds["A"] = value

    @property
    def arm_b(self) -> Optional[ArmPrediction]:
        return self.preds.get("B")

    @arm_b.setter
    def arm_b(self, value: Optional[ArmPrediction]) -> None:
        if value is None:
            self.preds.pop("B", None)
        else:
            self.preds["B"] = value

    @property
    def arm_c(self) -> Optional[ArmPrediction]:
        return self.preds.get("C")

    @arm_c.setter
    def arm_c(self, value: Optional[ArmPrediction]) -> None:
        if value is None:
            self.preds.pop("C", None)
        else:
            self.preds["C"] = value

def intake_stratum(intake_source: Optional[str], *, creator_is_agent: bool = False) -> str:
    """Stratify: manual / agent / other (QBO-pulled, NULL intake, email, etc.)."""
    if intake_source and str(intake_source).strip().lower() == "manual":
        return "manual"
    if intake_source and str(intake_source).strip().lower() == "agent":
        return "agent"
    if creator_is_agent:
        return "agent"
    return "other"


def normalize_bill_number(value: Optional[str]) -> str:
    if not value:
        return ""
    s = re.sub(r"\s+", "", str(value).strip().upper())
    return s.replace("#", "")


def bill_number_is_scorable(truth_bill_number: Optional[str]) -> bool:
    if not truth_bill_number or not str(truth_bill_number).strip():
        return False
    if re.match(r"^QBO-", str(truth_bill_number).strip(), re.I):
        return False
    return True


def score_bill_number(pred: Optional[str], truth: Optional[str]) -> bool:
    if not bill_number_is_scorable(truth):
        return False
    return normalize_bill_number(pred) == normalize_bill_number(truth)


def score_bill_date(pred: Optional[str], truth: Optional[date]) -> bool:
    if truth is None:
        return pred is None
    if not pred:
        return False
    try:
        return date.fromisoformat(pred.strip()) == truth
    except ValueError:
        return False


def amount_is_scorable(truth_amount: Optional[Decimal]) -> bool:
    return truth_amount is not None


def score_amount(pred: Optional[Decimal], truth: Optional[Decimal]) -> bool:
    if not amount_is_scorable(truth):
        return False
    if pred is None:
        return False
    return abs(pred - truth) <= AMOUNT_TOLERANCE


def coerce_bill_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return None


def score_vendor(pred_public_id: Optional[str], truth_public_id: str) -> bool:
    if not pred_public_id:
        return False
    return pred_public_id.strip().lower() == truth_public_id.strip().lower()


def description_from_mapper_result(result: BillExtractionResult) -> Optional[str]:
    if result.line_items:
        desc = (result.line_items[0].description or "").strip()
        if desc:
            return desc
    if result.memo:
        return (result.memo or "").strip() or None
    return None


def build_extraction_namespace(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        content=payload.get("content") or "",
        key_value_pairs=payload.get("key_value_pairs") or [],
        tables=payload.get("tables") or [],
        paragraphs=payload.get("paragraphs") or [],
    )


def truth_from_row(row: HeaderBillRow) -> HeaderTruth:
    return HeaderTruth(
        vendor_id=row.vendor_id,
        vendor_public_id=row.vendor_public_id,
        bill_number=row.bill_number,
        bill_date=row.bill_date,
        total_amount=row.total_amount,
        description=(row.first_line_description or "").strip() or None,
    )


def load_header_bills_from_db() -> list[HeaderBillRow]:
    out: list[HeaderBillRow] = []
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_LOAD_HEADER_BILLS_SQL)
            for row in cur.fetchall():
                bill_date = coerce_bill_date(row.BillDate)
                amt = row.TotalAmount
                total = Decimal(str(amt)) if amt is not None else None
                out.append(
                    HeaderBillRow(
                        bill_id=int(row.BillId),
                        vendor_id=int(row.VendorId),
                        vendor_public_id=str(row.VendorPublicId),
                        bill_number=str(row.BillNumber) if row.BillNumber is not None else None,
                        bill_date=bill_date,
                        total_amount=total,
                        created=_parse_dt(row.CreatedDatetime),
                        intake_source=(
                            str(row.IntakeSource) if row.IntakeSource is not None else None
                        ),
                        extracted_text_blob_url=str(row.ExtractedTextBlobUrl),
                        first_line_description=(
                            str(row.FirstLineDescription)
                            if row.FirstLineDescription is not None
                            else None
                        ),
                    )
                )
        finally:
            conn.rollback()
    return out


def stratified_sample_by_intake(
    bills: Sequence[HeaderBillRow],
    *,
    sample_size: int,
    seed: int,
    eval_since: date,
    eval_until: date,
) -> list[tuple[HeaderBillRow, str]]:
    eval_bills = [
        b for b in bills if eval_since <= _date_only(b.created) <= eval_until
    ]
    keyed: dict[str, list[HeaderBillRow]] = defaultdict(list)
    for b in eval_bills:
        keyed[intake_stratum(b.intake_source)].append(b)

    non_empty = [s for s in _INTAKE_STRATA if keyed.get(s)]
    if not non_empty:
        return []

    rng = random.Random(seed)
    per = max(1, sample_size // len(non_empty))
    chosen_ids: set[int] = set()
    picked: list[tuple[HeaderBillRow, str]] = []

    def take_from(pool: list[HeaderBillRow], stratum: str, n: int) -> None:
        pool = [b for b in pool if b.bill_id not in chosen_ids]
        if not pool:
            return
        rng.shuffle(pool)
        for b in pool[:n]:
            if b.bill_id in chosen_ids:
                continue
            chosen_ids.add(b.bill_id)
            picked.append((b, stratum))

    for s in _INTAKE_STRATA:
        if keyed.get(s):
            take_from(keyed[s], s, per)

    remainder = sample_size - len(picked)
    if remainder > 0:
        rest = [b for b in eval_bills if b.bill_id not in chosen_ids]
        rng.shuffle(rest)
        for b in rest[:remainder]:
            picked.append((b, intake_stratum(b.intake_source)))
            chosen_ids.add(b.bill_id)

    picked.sort(key=lambda x: (x[0].created, x[0].bill_id))
    return picked[:sample_size]


def count_populations(
    all_bills: Sequence[HeaderBillRow],
    *,
    eval_since: date,
    eval_until: date,
) -> dict[str, Any]:
    in_window = [b for b in all_bills if eval_since <= _date_only(b.created) <= eval_until]
    by_intake: dict[str, int] = defaultdict(int)
    for b in in_window:
        by_intake[intake_stratum(b.intake_source)] += 1
    return {
        "eval_window_bill_population": len(in_window),
        "intake_stratum_counts": dict(by_intake),
    }


def parse_arms(raw: str) -> frozenset[str]:
    parts = [p.strip().upper() for p in (raw or "").split(",") if p.strip()]
    allowed = frozenset({"A", "B", "C"})
    if not parts:
        raise ValueError("arms must list at least one of A, B, C")
    bad = set(parts) - allowed
    if bad:
        raise ValueError(f"unknown arm(s) {sorted(bad)}; expected subset of A,B,C")
    return frozenset(parts)


def arms_signature(arms: frozenset[str]) -> str:
    order = ("A", "B", "C")
    return ",".join(a for a in order if a in arms)


def _dry_run_input_tokens_per_llm_call(
    sample: Sequence[tuple[HeaderBillRow, str]],
) -> int:
    """Upper-bound tokens per LLM call: system prompt + JSON user message at char budget."""
    del sample  # dry-run does not read blobs; budget is uniform per doc
    bill_text_ub = "x" * BILL_HEADER_TEXT_CHAR_BUDGET
    user_msg = json.dumps({"bill_text": bill_text_ub}, ensure_ascii=False, indent=2)
    user_tokens = max(1, len(user_msg) // 4)
    system_tokens = max(1, len(BILL_HEADER_EXTRACTION_TASK.system_prompt) // 4)
    return user_tokens + system_tokens


def llm_arm_concurrency(
    arm_key: str,
    *,
    concurrency: int,
    claude_concurrency: int,
) -> int:
    if arm_key == "C":
        return claude_concurrency
    return concurrency


def dry_run_summary(
    sample: Sequence[tuple[HeaderBillRow, str]],
    *,
    sample_size: int,
    populations: dict[str, Any],
    arms: frozenset[str],
    claude_ladder_name: str,
) -> dict[str, Any]:
    by_stratum: dict[str, int] = defaultdict(int)
    for _, s in sample:
        by_stratum[s] += 1
    calls = len(sample)
    input_per_call = _dry_run_input_tokens_per_llm_call(sample)
    llm_arm_ladders = llm_arm_ladder_map(claude_ladder_name)
    out: dict[str, Any] = {
        **populations,
        "arms": arms_signature(arms),
        "sample_requested": sample_size,
        "sample_selected": len(sample),
        "stratum_counts": dict(by_stratum),
        "intake_strata": "manual / agent / other (includes QBO-pulled and NULL intake)",
        "arm_a_note": ARM_A_MEASUREMENT_NOTE,
        "extraction_method_share": "requires --run (read from blob JSON)",
        "note": (
            "Dry-run: Arm A is local rules only; LLM arm costs assume first rung stop. "
            "estimated_input_tokens_per_call is an upper bound (system prompt + bill_text "
            f"at {BILL_HEADER_TEXT_CHAR_BUDGET} chars, no blob download)."
        ),
        "estimated_input_tokens_per_call": input_per_call,
        "estimated_input_tokens_per_call_is_upper_bound": True,
    }
    if "C" in arms:
        out["claude_ladder"] = claude_ladder_name
    for arm_key in ("B", "C"):
        if arm_key not in arms:
            continue
        ladder = llm_arm_ladders[arm_key]
        tokens = input_per_call
        cost = calls * estimate_ladder_first_rung_cost_usd(ladder, input_tokens=tokens)
        est_tokens = calls * (tokens + _ESTIMATE_OUTPUT_TOKENS_PER_CALL)
        out[f"estimated_cascade_calls_arm_{arm_key.lower()}"] = calls
        out[f"estimated_tokens_arm_{arm_key.lower()}"] = est_tokens
        out[f"estimated_cost_usd_arm_{arm_key.lower()}_first_rung_only"] = round(cost, 6)
        if arm_key == "B":
            out["estimated_cost_usd_first_rung_only"] = out[
                "estimated_cost_usd_arm_b_first_rung_only"
            ]
    return out


def llm_arm_ladder_map(claude_ladder_name: str) -> dict[str, tuple[Rung, ...]]:
    out: dict[str, tuple[Rung, ...]] = {"B": BILL_CODING_FOUNDRY_LADDER}
    out["C"] = ladder_by_name(claude_ladder_name)
    return out


@contextmanager
def vendor_service_read_cache():
    """Avoid N× VendorService.read_all(); one shared mapper for all LLM vendor resolves."""
    from entities.vendor.business import service as vendor_service_mod

    cache: dict[str, Any] = {
        "vendors": None,
        "by_name": {},
        "mapper": BillExtractionMapper(),
    }

    orig_read_all = vendor_service_mod.VendorService.read_all
    orig_read_by_name = vendor_service_mod.VendorService.read_by_name

    def cached_read_all(self):
        if cache["vendors"] is None:
            cache["vendors"] = orig_read_all(self)
        return cache["vendors"]

    def cached_read_by_name(self, name: str):
        key = (name or "").strip().lower()
        if key not in cache["by_name"]:
            cache["by_name"][key] = orig_read_by_name(self, name)
        return cache["by_name"][key]

    vendor_service_mod.VendorService.read_all = cached_read_all  # type: ignore[method-assign]
    vendor_service_mod.VendorService.read_by_name = cached_read_by_name  # type: ignore[method-assign]
    try:
        yield cache
    finally:
        vendor_service_mod.VendorService.read_all = orig_read_all  # type: ignore[method-assign]
        vendor_service_mod.VendorService.read_by_name = orig_read_by_name  # type: ignore[method-assign]


def run_arm_a_on_blob(payload: dict[str, Any], mapper: BillExtractionMapper) -> ArmPrediction:
    t0 = time.perf_counter()
    pred = ArmPrediction()
    try:
        ns = build_extraction_namespace(payload)
        result = mapper.map(extraction=ns)
        result = mapper.resolve_entities(result)
        if result.vendor_match:
            pred.vendor_public_id = result.vendor_match.public_id
        pred.bill_number = result.bill_number
        pred.bill_date = result.bill_date
        pred.total_amount = result.total_amount
        pred.description = description_from_mapper_result(result)
    except Exception:
        pred.arm_error = True
    pred.latency_ms = int((time.perf_counter() - t0) * 1000)
    return pred


def resolve_vendor_from_name(
    vendor_name: Optional[str],
    mapper: BillExtractionMapper,
    *,
    confidence: float = 0.9,
) -> Optional[str]:
    """Map LLM vendor_name through the same matcher as Arm A (never trust model ids)."""
    from entities.vendor.business.vendor_name_resolver import (
        resolve_vendor_public_id_from_name,
    )

    return resolve_vendor_public_id_from_name(
        vendor_name, mapper, confidence=confidence
    )


def _raw_scalar_fields_from_result(
    r: dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[Decimal], bool]:
    bn = r.get("bill_number") if isinstance(r.get("bill_number"), str) else None
    bd = r.get("bill_date") if isinstance(r.get("bill_date"), str) else None
    amt: Optional[Decimal] = None
    raw_amt = r.get("total_amount")
    if raw_amt is not None and str(raw_amt).strip():
        try:
            amt = Decimal(str(raw_amt).strip())
        except Exception:
            amt = None
    desc = r.get("long_description")
    if not isinstance(desc, str) or not desc.strip():
        legacy = r.get("description")
        desc = legacy if isinstance(legacy, str) else None
    desc_ok = isinstance(desc, str) and bool(desc.strip())
    return bn, bd, amt, desc_ok


def _resolve_cascade_vendor(
    cascade: CascadeResult,
    mapper: BillExtractionMapper,
) -> tuple[Optional[str], bool]:
    """Return (vendor_public_id, resolve_failed)."""
    if not cascade.result or not isinstance(cascade.result, dict):
        return None, False
    r = cascade.result
    vendor_name = (
        r.get("vendor_name") if isinstance(r.get("vendor_name"), str) else None
    )
    if not vendor_name or not vendor_name.strip():
        return None, False
    try:
        vid = resolve_vendor_from_name(
            vendor_name,
            mapper,
            confidence=LLM_VENDOR_MATCH_CONFIDENCE,
        )
        return vid, False
    except Exception:
        return None, True


def _raw_result_field_nonempty(r: dict[str, Any], field: str) -> bool:
    """Non-empty check aligned with _raw_scalar_fields_from_result / vendor string gate."""
    if field == "vendor":
        vn = r.get("vendor_name")
        return isinstance(vn, str) and bool(vn.strip())
    if field == "bill_number":
        bn = r.get("bill_number")
        return isinstance(bn, str) and bool(bn.strip())
    if field == "bill_date":
        bd = r.get("bill_date")
        return isinstance(bd, str) and bool(bd.strip())
    if field == "amount":
        raw = r.get("total_amount")
        return raw is not None and str(raw).strip() != ""
    return False


def populate_raw_field_correctness(
    pred: ArmPrediction,
    cascade: CascadeResult,
    truth: HeaderTruth,
    mapper: BillExtractionMapper,
) -> Optional[str]:
    """Resolve raw vendor once and store per-field raw correctness on the prediction."""
    if not cascade.result or not isinstance(cascade.result, dict):
        return None
    vendor, resolve_failed = _resolve_cascade_vendor(cascade, mapper)
    pred.raw_vendor_resolve_failed = resolve_failed
    bn, bd, amt, desc_ok = _raw_scalar_fields_from_result(cascade.result)
    pred.raw_vendor_correct = (
        False
        if pred.raw_vendor_resolve_failed
        else score_vendor(vendor, truth.vendor_public_id)
        if vendor
        else False
    )
    if bill_number_is_scorable(truth.bill_number):
        pred.raw_bill_number_correct = (
            score_bill_number(bn, truth.bill_number) if bn else False
        )
    pred.raw_bill_date_correct = (
        score_bill_date(bd, truth.bill_date) if bd else False
    )
    if amount_is_scorable(truth.total_amount):
        pred.raw_amount_correct = (
            score_amount(amt, truth.total_amount) if amt is not None else False
        )
    pred.raw_description_nonempty = desc_ok
    return vendor


def arm_prediction_from_cascade(
    cascade: CascadeResult,
    mapper: BillExtractionMapper,
    *,
    truth: Optional[HeaderTruth] = None,
) -> ArmPrediction:
    pred = ArmPrediction()
    pred.latency_ms = sum(a.latency_ms for a in cascade.attempts)
    pred.spend_usd = bill_cascade_spend_usd(cascade)
    pred.cascade = cascade
    pred.raw_confidence = cascade.confidence
    err_kind, http_status = cascade_call_error_summary(cascade)
    pred.error_kind = err_kind
    pred.http_status = http_status
    resolved_vendor: Optional[str] = None
    if cascade.result and isinstance(cascade.result, dict):
        if truth is not None:
            resolved_vendor = populate_raw_field_correctness(pred, cascade, truth, mapper)
        if cascade.accepted:
            if truth is None:
                resolved_vendor, failed = _resolve_cascade_vendor(cascade, mapper)
                pred.accepted_vendor_resolve_failed = failed
            elif pred.raw_vendor_resolve_failed:
                resolved_vendor = None
                pred.accepted_vendor_resolve_failed = True
    if not cascade.accepted or not cascade.result:
        pred.arm_error = True
        return pred
    r = cascade.result
    pred.vendor_public_id = resolved_vendor
    if isinstance(r, dict):
        pred.bill_number = r.get("bill_number")
        pred.bill_date = r.get("bill_date")
        amt = r.get("total_amount")
        if amt is not None and str(amt).strip():
            try:
                pred.total_amount = Decimal(str(amt).strip())
            except Exception:
                pred.arm_error = True
        desc = r.get("long_description")
        if not isinstance(desc, str) or not desc.strip():
            legacy = r.get("description")
            desc = legacy if isinstance(legacy, str) else None
        if isinstance(desc, str) and desc.strip():
            pred.description = desc.strip()
    return pred


# Backward-compatible alias for tests.
arm_b_from_cascade = arm_prediction_from_cascade


@dataclass
class FieldMetrics:
    accuracy: Optional[Decimal]
    coverage: Decimal
    n_scored: int


def _arm_prediction(row: ScoredHeaderBill, arm: str) -> Optional[ArmPrediction]:
    if arm == "A":
        return row.arm_a
    return row.preds.get(arm)


def _llm_arm_scored(row: ScoredHeaderBill, arm: str) -> bool:
    if arm == "A":
        return not row.blob_skip_reason
    return arm in row.llm_arms_scored


def _field_accuracy_coverage(
    rows: Sequence[ScoredHeaderBill],
    arm: str,
    field: str,
) -> FieldMetrics:
    correct = 0
    denom = 0
    covered = 0
    for row in rows:
        if row.blob_skip_reason:
            continue
        truth = row.truth
        if field == "bill_number" and not bill_number_is_scorable(truth.bill_number):
            continue
        if field == "amount" and not amount_is_scorable(truth.total_amount):
            continue
        if not _llm_arm_scored(row, arm):
            continue
        pred = _arm_prediction(row, arm)
        if pred is None:
            continue
        denom += 1
        if field == "vendor":
            if pred.vendor_public_id:
                covered += 1
            if score_vendor(pred.vendor_public_id, truth.vendor_public_id):
                correct += 1
        elif field == "bill_number":
            if pred.bill_number:
                covered += 1
            if score_bill_number(pred.bill_number, truth.bill_number):
                correct += 1
        elif field == "bill_date":
            if pred.bill_date:
                covered += 1
            if score_bill_date(pred.bill_date, truth.bill_date):
                correct += 1
        elif field == "amount":
            if pred.total_amount is not None:
                covered += 1
            if score_amount(pred.total_amount, truth.total_amount):
                correct += 1
    acc = Decimal(correct) / Decimal(denom) if denom else None
    cov = Decimal(covered) / Decimal(denom) if denom else Decimal("0")
    return FieldMetrics(accuracy=acc, coverage=cov, n_scored=denom)


def description_nonempty_share(rows: Sequence[ScoredHeaderBill], arm: str) -> Optional[Decimal]:
    n = 0
    nonempty = 0
    for row in rows:
        if row.blob_skip_reason:
            continue
        if not _llm_arm_scored(row, arm):
            continue
        pred = _arm_prediction(row, arm)
        if pred is None:
            continue
        n += 1
        if pred.description and str(pred.description).strip():
            nonempty += 1
    if not n:
        return None
    return Decimal(nonempty) / Decimal(n)


def stratum_field_table(
    rows: Sequence[ScoredHeaderBill],
    field: str,
) -> dict[str, dict[str, Any]]:
    by_stratum: dict[str, list[ScoredHeaderBill]] = defaultdict(list)
    for r in rows:
        if not r.blob_skip_reason:
            by_stratum[r.stratum].append(r)
    out: dict[str, dict[str, Any]] = {}
    for key, chunk in sorted(by_stratum.items()):
        out[key] = {
            "n": len(chunk),
            "arm_a_accuracy": _field_accuracy_coverage(chunk, "A", field).accuracy,
            "arm_b_accuracy": _field_accuracy_coverage(chunk, "B", field).accuracy,
            "arm_c_accuracy": _field_accuracy_coverage(chunk, "C", field).accuracy,
        }
    return out


def extraction_method_shares(rows: Sequence[ScoredHeaderBill]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    n = 0
    for r in rows:
        if r.blob_skip_reason or not r.extraction_method:
            continue
        n += 1
        counts[r.extraction_method] += 1
    if not n:
        return {"n_with_method": 0, "shares": {}}
    shares = {k: round(v / n, 4) for k, v in counts.items()}
    text_layer = counts.get("text_layer", 0)
    return {
        "n_with_method": n,
        "text_layer_share": round(text_layer / n, 4),
        "document_intelligence_share": round(
            (n - text_layer) / n, 4
        ),
        "shares": shares,
    }


def _percentile(values: Sequence[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def latency_summary(rows: Sequence[ScoredHeaderBill]) -> dict[str, Any]:
    a_ms = [r.arm_a.latency_ms for r in rows if not r.blob_skip_reason]
    b_ms = [r.arm_b.latency_ms for r in rows if r.arm_b and not r.blob_skip_reason]
    c_ms = [r.arm_c.latency_ms for r in rows if r.arm_c and not r.blob_skip_reason]
    b_spend = [r.arm_b.spend_usd for r in rows if r.arm_b and not r.blob_skip_reason]
    c_spend = [r.arm_c.spend_usd for r in rows if r.arm_c and not r.blob_skip_reason]
    llm_spend = b_spend + c_spend
    return {
        "arm_a_ms_p50": _percentile(a_ms, 50),
        "arm_a_ms_p90": _percentile(a_ms, 90),
        "arm_b_latency_ms_p50": _percentile(b_ms, 50),
        "arm_b_latency_ms_p90": _percentile(b_ms, 90),
        "arm_b_latency_ms_max": max(b_ms) if b_ms else 0,
        "arm_c_latency_ms_p50": _percentile(c_ms, 50),
        "arm_c_latency_ms_p90": _percentile(c_ms, 90),
        "arm_c_latency_ms_max": max(c_ms) if c_ms else 0,
        "arm_b_cost_per_doc_mean_usd": round(sum(b_spend) / len(b_spend), 6) if b_spend else 0.0,
        "arm_b_cost_total_usd": round(sum(b_spend), 6),
        "arm_c_cost_per_doc_mean_usd": round(sum(c_spend) / len(c_spend), 6) if c_spend else 0.0,
        "arm_c_cost_total_usd": round(sum(c_spend), 6),
        "llm_arms_cost_total_usd": round(sum(llm_spend), 6),
    }


def field_verdict(
    rows: Sequence[ScoredHeaderBill],
    *,
    run_status: str,
    arms: frozenset[str],
) -> dict[str, Any]:
    if run_status != "complete":
        return {"status": "withheld", "reason": f"run_status={run_status}"}
    candidates = [a for a in ("A", "B", "C") if a in arms]
    fields = ("vendor", "bill_number", "bill_date", "amount")
    verdicts: dict[str, Any] = {}
    for f in fields:
        acc_by_arm: dict[str, Optional[Decimal]] = {
            arm: _field_accuracy_coverage(rows, arm, f).accuracy for arm in candidates
        }
        ranked = [
            (arm, acc)
            for arm, acc in acc_by_arm.items()
            if acc is not None
        ]
        if not ranked:
            winner = "none"
            best = None
        else:
            winner, best = max(ranked, key=lambda x: x[1])
        below_bar = best is not None and best < ACCURACY_BAR
        verdicts[f] = {
            "winner": winner,
            "arm_a_accuracy": acc_by_arm.get("A"),
            "arm_b_accuracy": acc_by_arm.get("B"),
            "arm_c_accuracy": acc_by_arm.get("C"),
            "below_bar": below_bar,
        }
    return {"status": "complete", "fields": verdicts}


def build_metrics(
    rows: Sequence[ScoredHeaderBill],
    *,
    run_status: str,
    cap_tripped: bool,
    scored_bill_count: int,
    unscored_bill_count: int,
    blob_skip_unreadable: int,
    blob_skip_unparseable: int,
    blob_skip_stale: int,
    arm_a_errors: int,
    arms: frozenset[str],
) -> dict[str, Any]:
    scored_rows = [r for r in rows if not r.blob_skip_reason]
    fields = {}
    for f in ("vendor", "bill_number", "bill_date", "amount"):
        entry: dict[str, Any] = {
            "arm_a": _field_accuracy_coverage(scored_rows, "A", f),
            "by_stratum": stratum_field_table(scored_rows, f),
        }
        if "B" in arms:
            entry["arm_b"] = _field_accuracy_coverage(scored_rows, "B", f)
        if "C" in arms:
            entry["arm_c"] = _field_accuracy_coverage(scored_rows, "C", f)
        fields[f] = entry

    def _field_block(v: dict[str, Any]) -> dict[str, Any]:
        block: dict[str, Any] = {
            "arm_a": {
                "accuracy": v["arm_a"].accuracy,
                "coverage": v["arm_a"].coverage,
                "n_scored": v["arm_a"].n_scored,
            },
            "by_stratum": v["by_stratum"],
        }
        if "arm_b" in v:
            block["arm_b"] = {
                "accuracy": v["arm_b"].accuracy,
                "coverage": v["arm_b"].coverage,
                "n_scored": v["arm_b"].n_scored,
            }
        if "arm_c" in v:
            block["arm_c"] = {
                "accuracy": v["arm_c"].accuracy,
                "coverage": v["arm_c"].coverage,
                "n_scored": v["arm_c"].n_scored,
            }
        return block

    desc_share: dict[str, Any] = {"arm_a": description_nonempty_share(scored_rows, "A")}
    if "B" in arms:
        desc_share["arm_b"] = description_nonempty_share(scored_rows, "B")
    if "C" in arms:
        desc_share["arm_c"] = description_nonempty_share(scored_rows, "C")

    vendor_resolve = aggregate_vendor_resolve_failures(scored_rows, arms=arms)

    return {
        "arms": arms_signature(arms),
        "cap_tripped": cap_tripped,
        "scored_bill_count": scored_bill_count,
        "unscored_bill_count": unscored_bill_count,
        "blob_skip_unreadable": blob_skip_unreadable,
        "blob_skip_unparseable": blob_skip_unparseable,
        "blob_skip_stale": blob_skip_stale,
        "blob_skip_total": blob_skip_unreadable + blob_skip_unparseable + blob_skip_stale,
        "arm_a_error_count": arm_a_errors,
        "arm_a_note": ARM_A_MEASUREMENT_NOTE,
        "fields": {k: _field_block(v) for k, v in fields.items()},
        "description_nonempty_share": desc_share,
        "extraction_method": extraction_method_shares(scored_rows),
        "latency": latency_summary(scored_rows),
        "verdict": field_verdict(scored_rows, run_status=run_status, arms=arms),
        "call_failures_by_arm": aggregate_arm_call_failures(scored_rows, arms=arms),
        "confidence_bar_sweep": header_confidence_bar_sweep(scored_rows, arms=arms),
        **vendor_resolve,
    }


def _flag(value: bool | None) -> str:
    if value is None:
        return ""
    return "1" if value else "0"


def _raw_extraction_values(
    cascade: CascadeResult,
    mapper: BillExtractionMapper,
) -> tuple[
    Optional[str],
    Optional[str],
    Optional[str],
    Optional[Decimal],
    bool,
]:
    if not cascade.result or not isinstance(cascade.result, dict):
        return None, None, None, None, False
    vendor, _failed = _resolve_cascade_vendor(cascade, mapper)
    bn, bd, amt, desc_ok = _raw_scalar_fields_from_result(cascade.result)
    return vendor, bn, bd, amt, desc_ok


def aggregate_vendor_resolve_failures(
    rows: Sequence[ScoredHeaderBill],
    *,
    arms: frozenset[str],
) -> dict[str, int]:
    raw_failed = 0
    accepted_failed = 0
    for row in rows:
        if row.blob_skip_reason:
            continue
        for arm in ("B", "C"):
            if arm not in arms:
                continue
            pred = _arm_prediction(row, arm)
            if pred is None or not _llm_arm_scored(row, arm):
                continue
            if pred.raw_vendor_resolve_failed:
                raw_failed += 1
            if pred.accepted_vendor_resolve_failed:
                accepted_failed += 1
    return {
        "raw_vendor_resolve_failed": raw_failed,
        "accepted_vendor_resolve_failed": accepted_failed,
    }


def _raw_field_correct(
    row: ScoredHeaderBill,
    arm: str,
    field: str,
    *,
    bar: float | None = None,
) -> str:
    pred = _arm_prediction(row, arm)
    if pred is None or pred.cascade is None or not _llm_arm_scored(row, arm):
        return ""
    if bar is not None:
        if pred.raw_confidence is None or pred.raw_confidence < bar:
            return ""
    truth = row.truth
    if field == "vendor":
        if pred.raw_vendor_correct is None:
            return ""
        return _flag(pred.raw_vendor_correct)
    if field == "bill_number":
        if not bill_number_is_scorable(truth.bill_number):
            return ""
        if pred.raw_bill_number_correct is None:
            return ""
        return _flag(pred.raw_bill_number_correct)
    if field == "bill_date":
        if pred.raw_bill_date_correct is None:
            return ""
        return _flag(pred.raw_bill_date_correct)
    if field == "amount":
        if not amount_is_scorable(truth.total_amount):
            return ""
        if pred.raw_amount_correct is None:
            return ""
        return _flag(pred.raw_amount_correct)
    if field == "description":
        if pred.raw_description_nonempty is None:
            return ""
        return _flag(pred.raw_description_nonempty)
    return ""


def _raw_prediction_nonempty(pred: ArmPrediction, field: str) -> bool:
    if not pred.cascade or not isinstance(pred.cascade.result, dict):
        return False
    r = pred.cascade.result
    if field == "description":
        return pred.raw_description_nonempty is True
    return _raw_result_field_nonempty(r, field)


def _raw_field_accuracy_at_bar(
    rows: Sequence[ScoredHeaderBill],
    arm: str,
    field: str,
    bar: float,
) -> tuple[Optional[Decimal], Decimal, Optional[Decimal]]:
    correct = 0
    denom = 0
    covered = 0
    for row in rows:
        if row.blob_skip_reason or not _llm_arm_scored(row, arm):
            continue
        if field == "bill_number" and not bill_number_is_scorable(row.truth.bill_number):
            continue
        if field == "amount" and not amount_is_scorable(row.truth.total_amount):
            continue
        pred = _arm_prediction(row, arm)
        if pred is None:
            continue
        denom += 1
        at_bar = pred.raw_confidence is not None and pred.raw_confidence >= bar
        if at_bar and _raw_prediction_nonempty(pred, field):
            covered += 1
            flag = _raw_field_correct(row, arm, field, bar=bar)
            if flag == "1":
                correct += 1
    acc = Decimal(correct) / Decimal(denom) if denom else None
    cov = Decimal(covered) / Decimal(denom) if denom else Decimal("0")
    prec = Decimal(correct) / Decimal(covered) if covered else None
    return acc, cov, prec


def header_confidence_bar_sweep(
    rows: Sequence[ScoredHeaderBill],
    *,
    arms: frozenset[str],
) -> dict[str, Any]:
    scored_rows = [r for r in rows if not r.blob_skip_reason]
    fields = ("vendor", "bill_number", "bill_date", "amount")
    out: dict[str, Any] = {"bars": list(HEADER_CONFIDENCE_BAR_SWEEP_BARS), "arms": {}}
    for arm in ("B", "C"):
        if arm not in arms:
            continue
        arm_rows: dict[str, Any] = {}
        for bar in HEADER_CONFIDENCE_BAR_SWEEP_BARS:
            per_field: dict[str, Any] = {}
            for fname in fields:
                acc, cov, prec = _raw_field_accuracy_at_bar(
                    scored_rows, arm, fname, bar
                )
                per_field[fname] = {
                    "accuracy": acc,
                    "coverage": cov,
                    "precision": prec,
                }
            arm_rows[str(bar)] = per_field
        out["arms"][arm] = arm_rows
    return out


def aggregate_arm_call_failures(
    rows: Sequence[ScoredHeaderBill],
    *,
    arms: frozenset[str],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if row.blob_skip_reason:
            continue
        for arm in ("B", "C"):
            if arm not in arms:
                continue
            pred = _arm_prediction(row, arm)
            if pred is None or not _llm_arm_scored(row, arm):
                continue
            counts[arm][pred.error_kind or "none"] += 1
    return {arm: dict(kind_counts) for arm, kind_counts in counts.items()}


def _per_doc_csv_fieldnames(arms: frozenset[str]) -> list[str]:
    base = [
        "BillId",
        "stratum",
        "extraction_method",
        "blob_skip_reason",
        "arm_a_scored",
        "arm_a_error",
        "arm_a_ms",
    ]
    for arm in ("B", "C"):
        if arm not in arms:
            continue
        low = arm.lower()
        base.extend(
            [
                f"arm_{low}_scored",
                f"arm_{low}_error",
                f"arm_{low}_latency_ms",
                f"arm_{low}_spend_usd",
                f"arm_{low}_winning_rung",
                f"arm_{low}_needs_human",
                f"arm_{low}_error_kind",
                f"arm_{low}_http_status",
                f"arm_{low}_raw_confidence",
            ]
        )
    base.append("bill_number_scorable")
    for arm in ("A", "B", "C"):
        if arm != "A" and arm not in arms:
            continue
        low = arm.lower()
        base.extend(
            [
                f"vendor_{low}_correct",
                f"bill_number_{low}_correct",
                f"bill_date_{low}_correct",
                f"amount_{low}_correct",
                f"description_{low}_nonempty",
            ]
        )
        if low in ("b", "c"):
            base.extend(
                [
                    f"raw_vendor_{low}_correct",
                    f"raw_bill_number_{low}_correct",
                    f"raw_bill_date_{low}_correct",
                    f"raw_amount_{low}_correct",
                    f"raw_description_{low}_nonempty",
                ]
            )
    return base


def _csv_correctness_for_arm(row: ScoredHeaderBill, arm: str) -> dict[str, str]:
    suffix = arm.lower()
    empty = {
        f"vendor_{suffix}_correct": "",
        f"bill_number_{suffix}_correct": "",
        f"bill_date_{suffix}_correct": "",
        f"amount_{suffix}_correct": "",
        f"description_{suffix}_nonempty": "",
    }
    if arm != "A" and not _llm_arm_scored(row, arm):
        return empty
    if arm == "A" and row.blob_skip_reason:
        return empty
    t = row.truth
    pred = _arm_prediction(row, arm) or ArmPrediction()
    bn = bill_number_is_scorable(t.bill_number)
    return {
        f"vendor_{suffix}_correct": _flag(score_vendor(pred.vendor_public_id, t.vendor_public_id)),
        f"bill_number_{suffix}_correct": (
            _flag(score_bill_number(pred.bill_number, t.bill_number))
            if bn
            else ""
        ),
        f"bill_date_{suffix}_correct": _flag(score_bill_date(pred.bill_date, t.bill_date)),
        f"amount_{suffix}_correct": (
            _flag(score_amount(pred.total_amount, t.total_amount))
            if amount_is_scorable(t.total_amount)
            else ""
        ),
        f"description_{suffix}_nonempty": _flag(bool(pred.description and pred.description.strip())),
    }


def scored_row_to_csv_dict(row: ScoredHeaderBill, *, arms: frozenset[str]) -> dict[str, str]:
    out: dict[str, str] = {
        "BillId": str(row.bill.bill_id),
        "stratum": row.stratum,
        "extraction_method": row.extraction_method or "",
        "blob_skip_reason": row.blob_skip_reason or "",
        "arm_a_scored": "0" if row.blob_skip_reason else "1",
        "arm_a_error": _flag(row.arm_a.arm_error) if not row.blob_skip_reason else "",
        "arm_a_ms": str(row.arm_a.latency_ms) if not row.blob_skip_reason else "",
        "bill_number_scorable": _flag(bill_number_is_scorable(row.truth.bill_number)),
    }
    for arm in ("B", "C"):
        if arm not in arms:
            continue
        low = arm.lower()
        pred = row.preds.get(arm)
        if row.blob_skip_reason:
            scored_flag = "0"
        elif _llm_arm_scored(row, arm) and pred is not None:
            scored_flag = "1"
        else:
            scored_flag = "0"
        out[f"arm_{low}_scored"] = scored_flag
        if scored_flag != "1":
            out.update(
                {
                    f"arm_{low}_error": "",
                    f"arm_{low}_latency_ms": "",
                    f"arm_{low}_spend_usd": "",
                    f"arm_{low}_winning_rung": "",
                    f"arm_{low}_needs_human": "",
                    f"arm_{low}_error_kind": "",
                    f"arm_{low}_http_status": "",
                    f"arm_{low}_raw_confidence": "",
                }
            )
        elif pred:
            out[f"arm_{low}_error"] = _flag(pred.arm_error)
            out[f"arm_{low}_latency_ms"] = str(pred.latency_ms)
            out[f"arm_{low}_spend_usd"] = f"{pred.spend_usd:.6f}" if pred.spend_usd else "0"
            out[f"arm_{low}_winning_rung"] = (
                pred.cascade.winning_rung.model
                if pred.cascade and pred.cascade.winning_rung
                else ""
            )
            out[f"arm_{low}_needs_human"] = (
                "1"
                if pred.cascade and pred.cascade.needs_human
                else ("0" if pred.cascade else "")
            )
            out[f"arm_{low}_error_kind"] = pred.error_kind or ""
            out[f"arm_{low}_http_status"] = (
                str(pred.http_status) if pred.http_status is not None else ""
            )
            out[f"arm_{low}_raw_confidence"] = (
                f"{pred.raw_confidence:.4f}" if pred.raw_confidence is not None else ""
            )
            for fname in (
                "vendor",
                "bill_number",
                "bill_date",
                "amount",
                "description",
            ):
                key = fname if fname != "description" else "description"
                col = (
                    f"raw_{key}_{low}_correct"
                    if fname != "description"
                    else f"raw_description_{low}_nonempty"
                )
                out[col] = _raw_field_correct(row, arm, fname)
        out.update(_csv_correctness_for_arm(row, arm))
    out.update(_csv_correctness_for_arm(row, "A"))
    return out


def write_report_and_outputs(
    *,
    out_dir: Path,
    stamp: str,
    scored: Sequence[ScoredHeaderBill],
    metrics: dict[str, Any],
    dry_info: Optional[dict[str, Any]],
    eval_since: date,
    eval_until: date,
    run_status: str = "complete",
    populations: Optional[dict[str, Any]] = None,
    arms: frozenset[str] | None = None,
    claude_ladder_name: str | None = None,
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"u676a_report_{stamp}.md"
    csv_path = out_dir / f"u676a_per_doc_{stamp}.csv"
    json_path = out_dir / f"u676a_summary_{stamp}.json"

    arms_sig = arms_signature(arms) if arms else metrics.get("arms")
    summary = {
        "model_version": MODEL_VERSION,
        "arms": arms_sig,
        "evaluation_window": {"since": str(eval_since), "until": str(eval_until)},
        "run_status": run_status,
        "populations": populations or {},
        "sample_size": len(scored),
        "metrics": metrics,
        "dry_run": dry_info,
    }
    if claude_ladder_name and arms and "C" in arms:
        summary["claude_ladder"] = claude_ladder_name
    json_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")

    arms_set = arms if arms is not None else frozenset({"A", "B"})
    fieldnames = _per_doc_csv_fieldnames(arms_set)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, restval="")
        w.writeheader()
        for row in scored:
            w.writerow(scored_row_to_csv_dict(row, arms=arms_set))

    def pct(v: Optional[Decimal]) -> str:
        return f"{v:.2%}" if v is not None else "n/a"

    lines = [
        f"# U-676a bill header bake-off ({stamp})",
        "",
        f"- ModelVersion: `{MODEL_VERSION}`",
        f"- Arms: `{arms_sig}`",
        f"- Run status: {run_status}",
        f"- Evaluation window: {eval_since} .. {eval_until}",
        f"- Sample rows: {len(scored)}",
    ]
    if populations:
        lines.append(
            f"- Eval population: {populations.get('eval_window_bill_population', 'n/a')}"
        )
    verdict = metrics.get("verdict") or {}
    lines.append("")
    lines.append(f"- {ARM_A_MEASUREMENT_NOTE}")
    if verdict.get("status") == "complete":
        lines.append("")
        lines.append("## Field verdicts")
        for fname, v in (verdict.get("fields") or {}).items():
            flag = " BELOW BAR" if v.get("below_bar") else ""
            parts = [f"A={pct(v.get('arm_a_accuracy'))} (stored text+KV only)"]
            if v.get("arm_b_accuracy") is not None:
                parts.append(f"B={pct(v.get('arm_b_accuracy'))}")
            if v.get("arm_c_accuracy") is not None:
                parts.append(f"C={pct(v.get('arm_c_accuracy'))}")
            lines.append(
                f"- {fname}: winner arm {v.get('winner')}{flag} "
                f"({', '.join(parts)})"
            )
    else:
        lines.append("")
        lines.append(f"## Verdict withheld ({verdict.get('reason', 'incomplete')})")

    lines.append("")
    lines.append("## Latency / cost")
    lat = metrics.get("latency") or {}
    for k, v in lat.items():
        lines.append(f"- {k}: {v}")

    failures = metrics.get("call_failures_by_arm") or {}
    if failures:
        lines.append("")
        lines.append("## Call failures by kind")
        lines.append("")
        for arm_key in sorted(failures):
            lines.append(f"- Arm {arm_key}: `{json.dumps(failures[arm_key])}`")

    raw_v_fail = metrics.get("raw_vendor_resolve_failed")
    acc_v_fail = metrics.get("accepted_vendor_resolve_failed")
    if raw_v_fail or acc_v_fail:
        lines.append("")
        lines.append("## Vendor resolution")
        lines.append("")
        lines.append(f"- vendor lookups failed: {raw_v_fail or 0}")
        if acc_v_fail:
            lines.append(f"- accepted vendor lookups failed: {acc_v_fail}")

    sweep = metrics.get("confidence_bar_sweep") or {}
    if sweep.get("arms"):
        lines.append("")
        lines.append("## Confidence-bar sweep (raw LLM predictions)")
        lines.append("")
        for arm_key, bars in (sweep.get("arms") or {}).items():
            lines.append(f"### Arm {arm_key}")
            lines.append("")
            lines.append("| Bar | vendor acc | vendor cov | bill# acc | amount acc |")
            lines.append("|-----|------------|------------|-----------|------------|")
            for bar in HEADER_CONFIDENCE_BAR_SWEEP_BARS:
                row = bars.get(str(bar)) or {}
                v = row.get("vendor") or {}
                bn = row.get("bill_number") or {}
                amt = row.get("amount") or {}

                def _pct_dec(vv: Any) -> str:
                    if vv is None:
                        return "n/a"
                    if isinstance(vv, Decimal):
                        return f"{vv:.2%}"
                    return f"{float(vv):.2%}"

                lines.append(
                    f"| {bar:.2f} | {_pct_dec(v.get('accuracy'))} | {_pct_dec(v.get('coverage'))} | "
                    f"{_pct_dec(bn.get('accuracy'))} | {_pct_dec(amt.get('accuracy'))} |"
                )
            lines.append("")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert_outputs_privacy_safe(csv_path)
    assert_outputs_privacy_safe(json_path)
    assert_outputs_privacy_safe(report_path)
    return report_path, csv_path, json_path


async def run_llm_arm_batch(
    work: Sequence[tuple[HeaderBillRow, dict[str, Any]]],
    *,
    ladder: tuple[Rung, ...],
    transport_for: Callable[[str], Any],
    concurrency: int,
    max_cost_usd: float,
    cost_state: dict[str, Any],
    mapper: BillExtractionMapper,
    out: dict[int, ArmPrediction],
) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(item: tuple[HeaderBillRow, dict[str, Any]]) -> None:
        bill, payload = item
        async with sem:
            est = estimate_ladder_first_rung_cost_usd(ladder)
            cascade = await run_capped_cascade(
                lambda: run_bill_header_extraction(
                    payload,
                    transport_for=transport_for,
                    ladder=ladder,
                ),
                est=est,
                cost_state=cost_state,
                max_cost_usd=max_cost_usd,
            )
            if cascade is None:
                return
            out[bill.bill_id] = arm_prediction_from_cascade(
                cascade, mapper, truth=truth_from_row(bill)
            )

    await asyncio.gather(*[one(w) for w in work], return_exceptions=True)


def finalize_header_run_state(
    scored: Sequence[ScoredHeaderBill],
    llm_results: dict[str, dict[int, ArmPrediction]],
    *,
    arms: frozenset[str],
    cost_state: dict[str, Any],
    max_cost_usd: float,
    run_status: str,
) -> Any:
    """Complete only if every non-stale sampled bill has each requested LLM arm."""
    expected = {r.bill.bill_id for r in scored if not r.blob_skip_reason}
    missing: set[int] = set()
    for arm_key in ("B", "C"):
        if arm_key not in arms:
            continue
        missing |= expected - set(llm_results.get(arm_key, {}).keys())
    if run_status == "complete" and missing:
        run_status = "incomplete"
    cap_tripped = bool(cost_state.get("stopped"))
    scored_count = len(expected) - len(missing)
    unscored = len(missing)
    overshoot = None
    if cost_state["usd"] > max_cost_usd:
        overshoot = cost_state["usd"] - max_cost_usd
    return RunState(
        run_status=run_status,
        scored_bill_count=scored_count,
        unscored_bill_count=unscored,
        cap_tripped=cap_tripped,
        cost_overshoot_usd=overshoot,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="U-676a bill header field bake-off.")
    ap.add_argument("--eval-since", type=date.fromisoformat, default=DEFAULT_EVAL_SINCE)
    ap.add_argument("--eval-until", type=date.fromisoformat, default=DEFAULT_EVAL_UNTIL)
    ap.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out-dir", type=Path, default=Path("docs/design/u676a"))
    ap.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument(
        "--claude-concurrency",
        type=int,
        default=DEFAULT_CLAUDE_CONCURRENCY,
        help="Parallel in-flight calls for arm C (Claude ladder; default: 2)",
    )
    ap.add_argument("--run", action="store_true", help="Execute arms (default: dry-run)")
    ap.add_argument(
        "--arms",
        default=DEFAULT_ARMS,
        help="Comma list of arms to run: A (rules), B (Foundry LLM), C (Claude LLM)",
    )
    ap.add_argument(
        "--claude-ladder",
        choices=sorted(k for k in BACKTEST_LADDERS if k != "foundry"),
        default=DEFAULT_CLAUDE_LADDER,
        help="Claude ladder for arm C (default: claude-haiku)",
    )
    args = ap.parse_args(argv)

    try:
        selected_arms = parse_arms(args.arms)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    llm_ladders = llm_arm_ladder_map(args.claude_ladder)

    validate_windows(
        date(2025, 10, 11),
        date(2026, 4, 10),
        args.eval_since,
        args.eval_until,
    )

    all_bills = load_header_bills_from_db()
    populations = count_populations(
        all_bills, eval_since=args.eval_since, eval_until=args.eval_until
    )
    sample = stratified_sample_by_intake(
        all_bills,
        sample_size=args.sample_size,
        seed=args.seed,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
    )
    dry_info = dry_run_summary(
        sample,
        sample_size=args.sample_size,
        populations=populations,
        arms=selected_arms,
        claude_ladder_name=args.claude_ladder,
    )

    if not args.run:
        print(json.dumps(dry_info, indent=2, default=_json_default))
        return 0

    scored: list[ScoredHeaderBill] = []
    for bill, stratum in sample:
        scored.append(
            ScoredHeaderBill(
                bill=bill,
                stratum=stratum,
                truth=truth_from_row(bill),
            )
        )

    blob_storage: list[Any] = []
    read_blob = make_text_blob_reader(blob_storage)
    llm_work: dict[str, list[tuple[HeaderBillRow, dict[str, Any]]]] = {
        "B": [],
        "C": [],
    }
    llm_results: dict[str, dict[int, ArmPrediction]] = {"B": {}, "C": {}}
    cost_state: dict[str, Any] = {
        "usd": 0.0,
        "stopped": False,
        "throttle_retry_count": 0,
        "throttle_failure_count": 0,
        "cascade_error_count": 0,
        "call_timeout_count": 0,
    }
    skip_unreadable = 0
    skip_unparseable = 0
    skip_stale = 0
    arm_a_errors = 0
    shared_mapper: BillExtractionMapper | None = None

    def build_work() -> None:
        nonlocal skip_unreadable, skip_unparseable, skip_stale, arm_a_errors, shared_mapper
        from shared.storage import AzureBlobStorageError

        with vendor_service_read_cache() as cache:
            shared_mapper = cache["mapper"]
            for row in scored:
                try:
                    raw = read_text_blob_with_retry(
                        read_blob, row.bill.extracted_text_blob_url
                    )
                except AzureBlobStorageError as exc:
                    if not blob_error_is_transient(exc):
                        raise
                    row.blob_skip_reason = "unreadable"
                    skip_unreadable += 1
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    row.blob_skip_reason = "unparseable"
                    skip_unparseable += 1
                    continue
                if not extraction_payload_is_current(payload):
                    row.blob_skip_reason = "stale"
                    skip_stale += 1
                    continue
                row.extraction_method = str(payload.get("method") or "unknown")
                row.arm_a = run_arm_a_on_blob(payload, cache["mapper"])
                if row.arm_a.arm_error:
                    arm_a_errors += 1
                masked = {"bill_text": payload.get("content") or ""}
                for arm_key in ("B", "C"):
                    if arm_key not in selected_arms:
                        continue
                    llm_work[arm_key].append((row.bill, masked))

    def run_llm_arms() -> None:
        from intelligence.transport import registry as transport_registry

        if shared_mapper is None:
            shared_mapper_local = BillExtractionMapper()
        else:
            shared_mapper_local = shared_mapper
        transport = transport_registry.get_transport
        for arm_key in ("B", "C"):
            if arm_key not in selected_arms:
                continue
            work = llm_work[arm_key]
            if not work:
                continue
            arm_concurrency = llm_arm_concurrency(
                arm_key,
                concurrency=args.concurrency,
                claude_concurrency=args.claude_concurrency,
            )
            asyncio.run(
                run_llm_arm_batch(
                    work,
                    ladder=llm_ladders[arm_key],
                    transport_for=transport,
                    concurrency=arm_concurrency,
                    max_cost_usd=args.max_cost_usd,
                    cost_state=cost_state,
                    mapper=shared_mapper_local,
                    out=llm_results[arm_key],
                )
            )

    run_status = execute_run(build_work, run_llm_arms)
    blob_skipped_total = skip_unreadable + skip_unparseable + skip_stale
    run_status = run_status_after_unreadable(
        run_status,
        unreadable=blob_skipped_total,
        sampled=len(sample),
    )

    for row in scored:
        if row.blob_skip_reason:
            continue
        bid = row.bill.bill_id
        scored_arms: set[str] = set()
        for arm_key in ("B", "C"):
            if arm_key not in selected_arms:
                continue
            if bid in llm_results[arm_key]:
                row.preds[arm_key] = llm_results[arm_key][bid]
                scored_arms.add(arm_key)
        row.llm_arms_scored = frozenset(scored_arms)

    state = finalize_header_run_state(
        scored,
        llm_results,
        arms=selected_arms,
        cost_state=cost_state,
        max_cost_usd=args.max_cost_usd,
        run_status=run_status,
    )
    run_status = state.run_status

    metrics = build_metrics(
        scored,
        run_status=run_status,
        cap_tripped=state.cap_tripped,
        scored_bill_count=state.scored_bill_count,
        unscored_bill_count=state.unscored_bill_count,
        blob_skip_unreadable=skip_unreadable,
        blob_skip_unparseable=skip_unparseable,
        blob_skip_stale=skip_stale,
        arm_a_errors=arm_a_errors,
        arms=selected_arms,
    )
    metrics["usage"] = {
        "cost_usd": cost_state["usd"],
        "throttle_retry_count": cost_state.get("throttle_retry_count", 0),
        "throttle_failure_count": cost_state.get("throttle_failure_count", 0),
        "harness_retry_by_kind": dict(cost_state.get("harness_retry_by_kind") or {}),
        "cascade_error_count": cost_state.get("cascade_error_count", 0),
        "call_timeout_count": cost_state.get("call_timeout_count", 0),
    }
    if "C" in selected_arms:
        metrics["claude_ladder"] = args.claude_ladder

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
        arms=selected_arms,
        claude_ladder_name=args.claude_ladder,
    )
    print(
        f"U-676a run {run_status} arms={arms_signature(selected_arms)}; "
        f"LLM cost USD ~ {cost_state['usd']:.4f} "
        f"(scored={state.scored_bill_count}, unscored={state.unscored_bill_count}, "
        f"blob_skipped={blob_skipped_total} "
        f"[unreadable={skip_unreadable}, unparseable={skip_unparseable}, stale={skip_stale}])"
    )
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
