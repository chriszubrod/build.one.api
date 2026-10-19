"""Background bill text-tier coding suggestion tick (U-654b)."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Optional

import config
from entities.attachment.business.extraction_service import extraction_payload_is_current
from entities.coding_suggestion.business.bill_suggestion_service import (
    _HISTORY_SOURCES,
    HISTORY_STRONG_RAW_SCORE,
    BillCodingSuggester,
)
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillTextCodingClaim,
    BillTextCodingSuggestionRepository,
)
from intelligence.cascade import claude_budget
from intelligence.cascade.bill_coding import (
    SUSPENSE_SCC_NUMBER,
    normalize_project_abbreviation,
    normalize_scc_number,
    run_bill_coding_suggestion,
    truncate_bill_text,
)
from intelligence.cascade.bill_coding_payload import (
    build_candidate_projects_for_bill,
    build_candidate_sccs_for_bill,
    history_evidence_from_scc_counts,
    resolve_text_prediction,
)
from shared.storage import AzureBlobStorage
from shared.text_blob_read import read_text_blob_with_retry

logger = logging.getLogger(__name__)

MODEL_VERSION = "u654b-text-v1"

_CATALOG_CACHE_TTL_SECONDS = 60.0
_catalog_lock = threading.Lock()
_catalog_cache: dict[datetime, tuple[float, CatalogSnapshot]] = {}
_catalog_now_fn: Callable[[], float] = time.monotonic
_catalog_loader_override: Optional[Callable[[datetime], CatalogSnapshot]] = None


def configure_catalog_cache_for_tests(
    *,
    now_fn: Optional[Callable[[], float]] = None,
    loader: Optional[Callable[[datetime], CatalogSnapshot]] = None,
) -> None:
    global _catalog_cache, _catalog_now_fn, _catalog_loader_override
    with _catalog_lock:
        _catalog_cache = {}
    _catalog_loader_override = loader
    if now_fn is not None:
        _catalog_now_fn = now_fn
    elif loader is None:
        _catalog_now_fn = time.monotonic


def _catalog_cache_key(as_of: datetime) -> datetime:
    return as_of.replace(second=0, microsecond=0)


def foundry_is_configured() -> bool:
    settings = config.Settings()
    return bool(
        (settings.foundry_api_key or "").strip()
        and (settings.foundry_endpoint or "").strip()
    )


@dataclass(frozen=True)
class CatalogSnapshot:
    scc_by_id: dict[int, Any]
    scc_number_to_id: dict[str, int]
    ambiguous_scc_numbers: frozenset[str]
    proj_abbrev_to_id: dict[str, int]
    ambiguous_project_abbrevs: frozenset[str]
    candidate_projects: list[dict[str, str]]
    vendor_name: str


def _unique_string_keys(rows: list[tuple[str, int]]) -> tuple[dict[str, int], frozenset[str]]:
    by_key: dict[str, list[int]] = {}
    for key, row_id in rows:
        by_key.setdefault(key, []).append(row_id)
    ambiguous = frozenset(k for k, ids in by_key.items() if len(ids) > 1)
    unique = {k: ids[0] for k, ids in by_key.items() if len(ids) == 1}
    return unique, ambiguous


def history_eligible_for_text(
    *,
    vendor_id: int,
    chosen_project_id: Optional[int],
    as_of: datetime,
    exclude_bill_id: int,
    suggester: Optional[BillCodingSuggester] = None,
    precomputed_suggestion: Optional[Any] = None,
) -> tuple[bool, Optional[str]]:
    """Return (eligible, skip_reason). eligible=True means run the cascade."""
    if precomputed_suggestion is not None:
        suggestion = precomputed_suggestion
    else:
        engine = suggester or BillCodingSuggester()
        suggestion = engine.suggest(
            vendor_id=vendor_id,
            project_id=chosen_project_id,
            memo=None,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )
    if suggestion.source not in _HISTORY_SOURCES:
        return True, None
    if chosen_project_id is None:
        return True, None
    raw = suggestion.raw_score
    if raw is not None and raw < HISTORY_STRONG_RAW_SCORE:
        return True, None
    return False, "history_strong"


def _catalog_as_of(claim: BillTextCodingClaim) -> datetime:
    """Backtest parity (U-654a): as-of is Bill.CreatedDatetime, not BillDate."""
    return claim.created_datetime


def decode_bill_text_from_blob_bytes(content_bytes: bytes) -> str:
    """Match u654a read_blob: whole download as utf-8; truncate in payload build."""
    return content_bytes.decode("utf-8", errors="replace")


def blob_has_current_redaction(blob_text: str) -> bool:
    """True only for an extraction blob stamped by the current TIN-masking writer.

    The whole blob goes to Foundry, so a blob written before U-535g (no stamp, or an
    older one), one carrying keys the current writer never writes (a stamped legacy
    blob still holding pages / paragraphs / tables), or anything that is not the
    extraction JSON envelope fails closed.
    """
    try:
        envelope = json.loads(blob_text)
    except ValueError:
        return False
    return extraction_payload_is_current(envelope)


def build_runtime_payload(
    *,
    bill_text: str,
    catalog: CatalogSnapshot,
    history_rows: Any,
    chosen_project_id: Optional[int],
    as_of: datetime,
) -> tuple[dict[str, Any], str]:
    mode = "A" if chosen_project_id is not None else "B"
    candidate_sccs = build_candidate_sccs_for_bill(
        catalog.scc_by_id,
        as_of=as_of,
        ambiguous_numbers=catalog.ambiguous_scc_numbers,
    )
    candidate_projects: Optional[list[dict[str, str]]]
    if mode == "A":
        candidate_projects = None
    else:
        candidate_projects = build_candidate_projects_for_bill(
            catalog.candidate_projects,
            ambiguous_abbrevs=catalog.ambiguous_project_abbrevs,
        )
    payload = {
        "vendor_name": catalog.vendor_name,
        "bill_text": truncate_bill_text(bill_text),
        "history_evidence": history_evidence_from_scc_counts(history_rows),
        "candidate_sccs": candidate_sccs,
        "candidate_projects": candidate_projects,
    }
    return payload, mode


STALE_REDACTION = "stale_redaction"


def _tick_result(**fields: Any) -> dict[str, Any]:
    base = {
        "status": "idle",
        "bill_id": None,
        "error": None,
        "failure_reason": None,
        "skipped_reason": None,
    }
    base.update(fields)
    return base


async def _process_one_claim(
    claim: BillTextCodingClaim,
    *,
    repository: BillTextCodingSuggestionRepository,
    storage: AzureBlobStorage,
    suggester: Optional[BillCodingSuggester],
    catalog_loader: Optional[Callable[[datetime], CatalogSnapshot]],
    transport_for: Optional[Callable[[str], Any]],
) -> dict[str, Any]:
    def _finalize_complete(*, record_call: bool = False, **kwargs: Any) -> bool:
        row = repository.complete(
            bill_id=claim.bill_id,
            attachment_id=claim.attachment_id,
            extracted_datetime=claim.extracted_datetime,
            model_version=MODEL_VERSION,
            record_call=record_call,
            **kwargs,
        )
        return row is not None

    def _finish_skipped(reason: Optional[str]) -> dict[str, Any]:
        if not _finalize_complete(status="skipped", failure_reason=reason):
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="complete_refused",
            )
        return _tick_result(status="skipped", bill_id=claim.bill_id, skipped_reason=reason)

    # Set once the model has been called, so every exit after that counts toward the cap.
    cascade_ran = False
    as_of = _catalog_as_of(claim)
    try:
        eligible, skip_reason = history_eligible_for_text(
            vendor_id=claim.vendor_id,
            chosen_project_id=claim.chosen_project_id,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
            suggester=suggester,
        )
        if not eligible:
            return _finish_skipped(skip_reason)

        def _read_blob(url: str) -> str:
            content_bytes, _meta = storage.download_file(url)
            return decode_bill_text_from_blob_bytes(content_bytes)

        try:
            bill_text = read_text_blob_with_retry(_read_blob, claim.extracted_text_blob_url)
        except Exception as error:
            logger.warning(
                "bill_text_coding_tick blob read failed bill_id=%s err=%s",
                claim.bill_id,
                type(error).__name__,
            )
            _finalize_complete(status="failed", failure_reason="blob_read")
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="blob_read",
                error=type(error).__name__,
            )

        if not blob_has_current_redaction(bill_text):
            # Never send text the current masking has not been applied to. A U-535h
            # re-mask keeps the same ExtractedDatetime, so these rows are not re-claimed
            # on their own; delete the row to retry once the blob is re-masked.
            return _finish_skipped(STALE_REDACTION)

        catalog = (catalog_loader or _load_catalog_from_db)(as_of)
        catalog = replace(
            catalog, vendor_name=_read_vendor_name(claim.vendor_id) or catalog.vendor_name
        )
        history_rows = repository.read_vendor_scc_counts(
            claim.vendor_id,
            project_id=claim.chosen_project_id,
            as_of=as_of,
            exclude_bill_id=claim.bill_id,
        )
        payload, mode = build_runtime_payload(
            bill_text=bill_text,
            catalog=catalog,
            history_rows=history_rows,
            chosen_project_id=claim.chosen_project_id,
            as_of=as_of,
        )

        cascade_ran = True
        cascade = await run_bill_coding_suggestion(payload, transport_for=transport_for)
        if cascade.stopped_reason == "daily_cap":
            return _tick_result(status="capped", bill_id=claim.bill_id)
        if not cascade.accepted or cascade.result is None:
            _finalize_complete(
                status="failed",
                failure_reason="not_accepted",
                record_call=True,
            )
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="not_accepted",
            )

        scc_id, proj_id = resolve_text_prediction(
            cascade,
            scc_number_to_id=catalog.scc_number_to_id,
            proj_abbrev_to_id=catalog.proj_abbrev_to_id,
        )
        if scc_id is None:
            _finalize_complete(
                status="failed",
                failure_reason="unresolved_scc",
                record_call=True,
            )
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="unresolved_scc",
            )

        # The cascade's coerced confidence (a float clamped to [0, 1]), never the raw
        # model value — that can be a bool or out of range and overflow DECIMAL(5,4).
        confidence = (
            Decimal(str(cascade.confidence)) if cascade.confidence is not None else None
        )
        winning_model = (
            cascade.winning_rung.model if cascade.winning_rung else None
        )
        out_project = proj_id if mode == "B" else None

        if not _finalize_complete(
            status="done",
            sub_cost_code_id=scc_id,
            project_id=out_project,
            confidence=confidence,
            mode=mode,
            winning_model=winning_model,
            record_call=True,
        ):
            return _tick_result(
                status="failed",
                bill_id=claim.bill_id,
                failure_reason="complete_refused",
            )
        return _tick_result(status="done", bill_id=claim.bill_id)
    except Exception as error:
        logger.warning(
            "bill_text_coding_tick failed bill_id=%s err=%s",
            claim.bill_id,
            type(error).__name__,
        )
        try:
            _finalize_complete(status="failed", failure_reason="error", record_call=cascade_ran)
        except Exception:
            logger.warning(
                "bill_text_coding_tick complete-after-error failed bill_id=%s",
                claim.bill_id,
            )
        return _tick_result(
            status="failed",
            bill_id=claim.bill_id,
            error=type(error).__name__,
            failure_reason="error",
        )


async def run_bill_text_coding_tick(
    *,
    limit: int = 1,
    repo: Optional[BillTextCodingSuggestionRepository] = None,
    storage: Optional[AzureBlobStorage] = None,
    suggester: Optional[BillCodingSuggester] = None,
    catalog_loader: Optional[Callable[[datetime], CatalogSnapshot]] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
) -> dict[str, Any]:
    settings = config.Settings()
    if not settings.bill_coding_text_enabled or not foundry_is_configured():
        return _tick_result(status="disabled")

    repository = repo or BillTextCodingSuggestionRepository()
    cap = int(settings.bill_coding_text_daily_call_cap)
    if cap <= 0:
        return _tick_result(status="cap_reached")

    blob_store = storage
    last = _tick_result(status="idle")
    for _ in range(max(1, int(limit))):
        if repository.count_calls_today() >= cap:
            return _tick_result(status="cap_reached", bill_id=last.get("bill_id"))
        if await claude_budget.is_claude_day_stopped_async():
            return _tick_result(status="capped", bill_id=last.get("bill_id"))
        claim = repository.claim_next()
        if claim is None:
            return last
        blob_store = blob_store or AzureBlobStorage()
        last = await _process_one_claim(
            claim,
            repository=repository,
            storage=blob_store,
            suggester=suggester,
            catalog_loader=catalog_loader,
            transport_for=transport_for,
        )
    return last


def _read_vendor_name(vendor_id: int) -> str:
    from shared.database import get_connection

    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT [Name] FROM dbo.[Vendor] WHERE [Id] = ?",
                vendor_id,
            )
            row = cur.fetchone()
            return str(row.Name) if row and row.Name else ""
        finally:
            conn.rollback()


def _load_catalog_from_db(as_of: datetime) -> CatalogSnapshot:
    from shared.database import get_connection

    suspense_norm = normalize_scc_number(SUSPENSE_SCC_NUMBER)
    scc_by_id: dict[int, Any] = {}
    scc_key_rows: list[tuple[str, int]] = []
    proj_key_rows: list[tuple[str, int]] = []
    candidate_projects: list[dict[str, str]] = []
    vendor_name = ""

    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT [Id], [Number], [Name], [CreatedDatetime]
                FROM dbo.[SubCostCode]
                WHERE [Number] IS NOT NULL
                  AND ISNULL([IsHoldingCode], 0) = 0
                ORDER BY [Number]
                """
            )
            for row in cur.fetchall():
                number = normalize_scc_number(str(row.Number))
                ref = SimpleNamespace(
                    scc_id=int(row.Id),
                    number=number,
                    name=str(row.Name or ""),
                    created=row.CreatedDatetime,
                )
                scc_by_id[ref.scc_id] = ref
                if number and number != suspense_norm:
                    scc_key_rows.append((number, ref.scc_id))

            cur.execute(
                """
                SELECT DISTINCT p.[Id], p.[Abbreviation], p.[Name]
                FROM dbo.[Project] p
                INNER JOIN dbo.[BillLineItem] bli ON bli.[ProjectId] = p.[Id]
                INNER JOIN dbo.[Bill] b ON b.[Id] = bli.[BillId]
                WHERE p.[Abbreviation] IS NOT NULL
                  AND b.[IsDraft] = 0
                  AND b.[CreatedDatetime] < ?
                ORDER BY p.[Id]
                """,
                as_of,
            )
            for row in cur.fetchall():
                abbrev = normalize_project_abbreviation(str(row.Abbreviation))
                if abbrev:
                    proj_key_rows.append((abbrev, int(row.Id)))
                    candidate_projects.append(
                        {"abbreviation": abbrev, "name": str(row.Name or "")}
                    )
        finally:
            conn.rollback()

    scc_number_to_id, amb_scc = _unique_string_keys(scc_key_rows)
    proj_abbrev_to_id, amb_proj = _unique_string_keys(proj_key_rows)

    return CatalogSnapshot(
        scc_by_id=scc_by_id,
        scc_number_to_id=scc_number_to_id,
        ambiguous_scc_numbers=amb_scc,
        proj_abbrev_to_id=proj_abbrev_to_id,
        ambiguous_project_abbrevs=amb_proj,
        candidate_projects=candidate_projects,
        vendor_name=vendor_name,
    )


def load_catalog_from_db(as_of: datetime) -> CatalogSnapshot:
    key = _catalog_cache_key(as_of)
    now = _catalog_now_fn()
    with _catalog_lock:
        hit = _catalog_cache.get(key)
        if hit is not None and (now - hit[0]) < _CATALOG_CACHE_TTL_SECONDS:
            return hit[1]
    loader = _catalog_loader_override or _load_catalog_from_db
    snap = loader(as_of)
    with _catalog_lock:
        _catalog_cache.clear()
        _catalog_cache[key] = (now, snap)
    return snap


async def predict_text_coding(
    *,
    bill_text: str,
    vendor_id: int,
    chosen_project_id: Optional[int],
    as_of: datetime,
    exclude_bill_id: Optional[int] = None,
    text_repo: Optional[BillTextCodingSuggestionRepository] = None,
    suggester: Optional[BillCodingSuggester] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
    catalog: Optional[CatalogSnapshot] = None,
    vendor_name: Optional[str] = None,
    retain_accessible_project: bool = False,
) -> tuple[Optional[Any], Optional[str]]:
    """Run the text-tier cascade; return (BillSuggestion|None, stopped_reason)."""
    from entities.coding_suggestion.business.bill_suggestion_service import (
        BillSuggestion,
        bill_suggestion_from_text_cascade,
    )

    settings = config.Settings()
    cap = int(settings.bill_coding_text_daily_call_cap)
    if cap <= 0:
        return None, None
    repository = text_repo or BillTextCodingSuggestionRepository()
    # Every DB read below runs off the event loop (U-676l): this coroutine serves live
    # requests, and a sync pyodbc call here would stall every request on the worker.
    if await asyncio.to_thread(repository.count_calls_today) >= cap:
        return None, "daily_cap"

    if catalog is None:
        catalog = await asyncio.to_thread(load_catalog_from_db, as_of)
    if vendor_name:
        catalog = replace(catalog, vendor_name=vendor_name)
    elif not catalog.vendor_name:
        loaded_name = await asyncio.to_thread(_read_vendor_name, vendor_id)
        if loaded_name:
            catalog = replace(catalog, vendor_name=loaded_name)
    history_rows = await asyncio.to_thread(
        repository.read_vendor_scc_counts,
        vendor_id,
        project_id=chosen_project_id,
        as_of=as_of,
        exclude_bill_id=exclude_bill_id,
    )
    payload, mode = build_runtime_payload(
        bill_text=bill_text,
        catalog=catalog,
        history_rows=history_rows,
        chosen_project_id=chosen_project_id,
        as_of=as_of,
    )
    cascade = await run_bill_coding_suggestion(payload, transport_for=transport_for)
    if cascade.stopped_reason == "daily_cap":
        return None, "daily_cap"
    if not cascade.accepted or cascade.result is None:
        return None, None
    engine = suggester or BillCodingSuggester()
    suggestion = await asyncio.to_thread(
        bill_suggestion_from_text_cascade,
        cascade,
        catalog=catalog,
        mode=mode,
        chosen_project_id=chosen_project_id,
        project_service=engine.project_service,
        sub_cost_code_service=engine.sub_cost_code_service,
    )
    if (
        retain_accessible_project
        and suggestion is None
        and mode == "B"
        and cascade.accepted
        and cascade.result is not None
    ):
        from entities.coding_suggestion.business.bill_suggestion_service import (
            BillSuggestion,
            TEXT_LLM_SUGGESTION_REASON,
            resolve_accessible_text_project_id,
        )

        _scc_id, proj_id = resolve_text_prediction(
            cascade,
            scc_number_to_id=catalog.scc_number_to_id,
            proj_abbrev_to_id=catalog.proj_abbrev_to_id,
        )
        out_project = await asyncio.to_thread(
            resolve_accessible_text_project_id,
            proj_id,
            project_service=engine.project_service,
        )
        if out_project is not None:
            confidence = (
                Decimal(str(cascade.confidence))
                if cascade.confidence is not None
                else None
            )
            winning_model = (
                cascade.winning_rung.model if cascade.winning_rung else None
            )
            suggestion = BillSuggestion(
                project_id=out_project,
                sub_cost_code_id=None,
                sub_cost_code_public_id=None,
                sub_cost_code_number=None,
                sub_cost_code_name=None,
                source="llm",
                reason=TEXT_LLM_SUGGESTION_REASON,
                confidence=confidence,
                raw_score=None,
                top_count=None,
                second_count=None,
                total_count=None,
                model_version=MODEL_VERSION,
                predicted_project=True,
                text_used=True,
                text_mode=mode,
                text_model=winning_model,
            )
    return suggestion, None


def run_bill_text_coding_tick_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_bill_text_coding_tick(**kwargs))
