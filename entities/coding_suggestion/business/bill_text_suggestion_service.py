"""Background bill text-tier coding suggestion tick (U-654b)."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Optional

import config
from entities.attachment.business.extraction_service import (
    EXTRACTION_BLOB_KEYS,
    REDACTION_VERSION,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    _HISTORY_SOURCES,
    HISTORY_STRONG_RAW_SCORE,
    BillCodingSuggester,
)
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillTextCodingClaim,
    BillTextCodingSuggestionRepository,
)
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
) -> tuple[bool, Optional[str]]:
    """Return (eligible, skip_reason). eligible=True means run the cascade."""
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
    return (
        isinstance(envelope, dict)
        and envelope.get("redaction_version") == REDACTION_VERSION
        and set(envelope) <= EXTRACTION_BLOB_KEYS
    )


def build_runtime_payload(
    *,
    claim: BillTextCodingClaim,
    bill_text: str,
    catalog: CatalogSnapshot,
    history_rows: Any,
) -> tuple[dict[str, Any], str]:
    mode = "A" if claim.chosen_project_id is not None else "B"
    as_of = _catalog_as_of(claim)
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
            # on their own; delete the row to retry once the blob is re-masked (legacy
            # pages/paragraphs/tables blobs pass only after the U-672 re-mask fix).
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
            claim=claim,
            bill_text=bill_text,
            catalog=catalog,
            history_rows=history_rows,
        )

        cascade_ran = True
        cascade = await run_bill_coding_suggestion(payload, transport_for=transport_for)
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


def run_bill_text_coding_tick_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_bill_text_coding_tick(**kwargs))
