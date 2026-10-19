"""Synchronous bill-create attachment read pipeline (U-676b)."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from functools import partial
from typing import Any, Callable, Optional
from uuid import UUID

import config
from entities.attachment.business.extraction_service import AttachmentExtractionService
from entities.attachment.business.service import AttachmentService
from entities.attachment.persistence.repo import AttachmentRepository
from entities.coding_suggestion.business.bill_suggestion_service import (
    BillCodingSuggester,
    BillSuggestion,
    history_blocks_text_tier,
    resolve_accessible_text_project_id,
)
from entities.coding_suggestion.business.bill_text_suggestion_service import (
    CatalogSnapshot,
    history_eligible_for_text,
    load_catalog_from_db,
    predict_text_coding,
)
from entities.vendor.business.vendor_name_resolver import VendorResolveMatch
from entities.coding_suggestion.business.presenters import suggestion_to_api_payload
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from entities.vendor.business.vendor_name_resolver import resolve_vendor_match_from_name
from intelligence.cascade import claude_budget
from entities.bill.business.description_format import format_short_description
from intelligence.cascade.bill_header_extraction import (
    run_bill_header_extraction,
    validate_bill_header_result,
)
from entities.bill_intake_prediction.business.service import (
    BillIntakePredictionService,
    INTAKE_SOURCES,
)
from intelligence.cascade.core import CascadeResult
from shared.access import EntityNotAccessibleError, assert_can_access_project

logger = logging.getLogger(__name__)

# Background record_read (U-676m): its own small pool so a slow DB cannot starve the
# default executor the read path itself uses; strong refs keep tasks alive until done;
# a cap drops recording (telemetry only) rather than queueing without bound.
_RECORD_READ_MAX_PENDING = 50
_record_read_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="intake-record")
# Executor futures (not asyncio ones): their done-callback fires in the worker thread even
# if the scheduling event loop has closed, so an entry can never leak and silently fill
# the cap. Guarded by a lock because callbacks run off the loop.
_record_read_tasks: set[Future] = set()
_record_read_lock = threading.Lock()


def _record_read_done(future: Future) -> None:
    with _record_read_lock:
        _record_read_tasks.discard(future)
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.warning("bill_read_attachment.record_read_failed err=%s", type(exc).__name__)


def _schedule_record_read(intake_svc: BillIntakePredictionService, **kwargs: Any) -> None:
    try:
        with _record_read_lock:
            if len(_record_read_tasks) >= _RECORD_READ_MAX_PENDING:
                logger.warning("bill_read_attachment.record_read_skipped reason=backlog")
                return
            # The executor does not copy contextvars — carry the request's context
            # (current_user_id → CreatedByUserId) explicitly.
            ctx = contextvars.copy_context()
            future = _record_read_executor.submit(ctx.run, partial(intake_svc.record_read, **kwargs))
            _record_read_tasks.add(future)
        future.add_done_callback(_record_read_done)
    except Exception as error:  # e.g. executor shut down at interpreter exit
        logger.warning("bill_read_attachment.record_read_not_scheduled err=%s", type(error).__name__)


LLM_VENDOR_MATCH_CONFIDENCE = 0.9

MSG_COULDNT_READ = "Couldn't read this file"
MSG_COULDNT_READ_IN_TIME = "Couldn't read this file in time"
MSG_AI_PAUSED = "AI suggestions are paused for today"


class AttachmentNotReadable(LookupError):
    """Attachment missing, linked, or not owned by the caller."""


def _empty_fields() -> dict[str, Any]:
    return {
        "vendor": {"value": None, "extracted_name": None, "confidence": None},
        "bill_number": {"value": None, "confidence": None},
        "bill_date": {"value": None, "confidence": None},
        "total_amount": {"value": None, "confidence": None},
        "description": {"value": None, "confidence": None},
        "long_description": {"value": None, "confidence": None},
        "reference": {"value": None, "confidence": None},
    }


def _format_total_amount(value: Any) -> str:
    try:
        amount = Decimal(str(value))
        quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{quantized:.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def _conf_str(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0 or f > 1:
        return None
    return f"{f:.4f}"


def _result_shell(
    status: str,
    *,
    message: Optional[str] = None,
    fields: Optional[dict] = None,
    coding: Optional[dict] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "fields": fields if fields is not None else _empty_fields(),
        "coding": coding,
        "model": model,
    }


def read_attachment_link_state(
    attachment_id: int,
    *,
    repo: Optional[AttachmentRepository] = None,
) -> tuple[bool, bool]:
    """Return (is_linked, is_taxpayer). Fail closed on DB error (linked + taxpayer)."""
    try:
        return (repo or AttachmentRepository()).read_link_state(attachment_id)
    except Exception as error:
        logger.warning(
            "bill_read_attachment.link_state_failed err=%s",
            type(error).__name__,
        )
        return True, True


async def _extract_with_inflight_wait(
    *,
    ext_svc: AttachmentExtractionService,
    att_svc: AttachmentService,
    attachment: Any,
    is_taxpayer: bool,
    deadline: float,
) -> dict:
    """Poll while background extraction holds ``processing`` (1 s, off the event loop)."""
    while True:
        outcome = await asyncio.to_thread(
            ext_svc.extract_for_bill_drop_prefill,
            attachment,
            is_taxpayer_document=is_taxpayer,
        )
        if outcome.get("reason") != "processing_in_flight":
            return outcome
        if time.monotonic() >= deadline:
            return {"status": "timeout", "reason": "processing_in_flight"}
        await asyncio.sleep(1.0)
        if attachment.id is not None:
            refreshed = await asyncio.to_thread(att_svc.read_by_id, int(attachment.id))
            if refreshed is not None:
                attachment = refreshed


def _header_from_cascade(
    cascade: CascadeResult,
) -> tuple[dict[str, Any], bool, Optional[str], Optional[VendorResolveMatch]]:
    fields = _empty_fields()
    model_id: Optional[str] = None
    if cascade.winning_rung is not None:
        model_id = cascade.winning_rung.model

    raw = cascade.result if isinstance(cascade.result, dict) else None
    if raw is None and cascade.attempts:
        for attempt in reversed(cascade.attempts):
            if attempt.validated and isinstance(attempt.result, dict):
                raw = attempt.result
                break
        if raw is None:
            for attempt in reversed(cascade.attempts):
                if isinstance(attempt.result, dict):
                    raw = attempt.result
                    break
    vendor_match: Optional[VendorResolveMatch] = None
    if raw is None:
        return fields, False, model_id, vendor_match

    header = dict(raw)
    ok, _reason = validate_bill_header_result(header)
    if not ok:
        return fields, False, model_id, vendor_match

    overall = float(header.get("confidence") or 0.0)
    vendor_name = (
        header.get("vendor_name") if isinstance(header.get("vendor_name"), str) else None
    )
    fields["vendor"]["extracted_name"] = vendor_name

    match = resolve_vendor_match_from_name(
        vendor_name,
        confidence=LLM_VENDOR_MATCH_CONFIDENCE,
        cascade_confidence=overall,
    )
    vendor_match = match
    if match is not None:
        fields["vendor"]["value"] = {"public_id": match.public_id, "name": match.name}
        fields["vendor"]["confidence"] = _conf_str(match.confidence)
    elif vendor_name:
        fields["vendor"]["confidence"] = _conf_str(overall)

    short_desc = header.get("short_description")
    if not isinstance(short_desc, str):
        short_desc = None
    long_desc = header.get("long_description")
    if not isinstance(long_desc, str):
        legacy = header.get("description")
        long_desc = legacy if isinstance(legacy, str) else None

    line_description = format_short_description(short_desc)
    if line_description is None and long_desc:
        line_description = format_short_description(long_desc)
    if line_description is not None:
        fields["description"]["value"] = line_description
        fields["description"]["confidence"] = _conf_str(overall)
    if long_desc:
        fields["long_description"]["value"] = long_desc
        fields["long_description"]["confidence"] = _conf_str(overall)

    reference = header.get("reference")
    if isinstance(reference, str) and reference.strip():
        fields["reference"]["value"] = reference.strip()
        fields["reference"]["confidence"] = _conf_str(overall)

    for key, out_key in (
        ("bill_number", "bill_number"),
        ("bill_date", "bill_date"),
        ("total_amount", "total_amount"),
    ):
        val = header.get(key)
        if val is not None and val != "":
            if out_key == "total_amount":
                fields[out_key]["value"] = _format_total_amount(val)
            else:
                fields[out_key]["value"] = str(val)
            fields[out_key]["confidence"] = _conf_str(overall)

    header_ok = any(
        fields[k]["value"] is not None
        for k in (
            "bill_number",
            "bill_date",
            "total_amount",
            "description",
            "long_description",
            "reference",
        )
    ) or fields["vendor"]["extracted_name"] is not None
    return fields, header_ok, model_id, vendor_match


async def _resolve_chosen_project_id(
    project_public_id: Optional[UUID],
) -> Optional[int]:
    if project_public_id is None:
        return None
    project = await asyncio.to_thread(
        ProjectService().read_by_public_id, str(project_public_id)
    )
    if project is None or project.id is None:
        raise AttachmentNotReadable("not_found")
    try:
        await asyncio.to_thread(assert_can_access_project, int(project.id))
    except EntityNotAccessibleError:
        raise AttachmentNotReadable("not_found") from None
    return int(project.id)


async def _await_or_cancel_tasks(*tasks: Optional[asyncio.Task]) -> None:
    active = [t for t in tasks if t is not None]
    for task in active:
        if not task.done():
            task.cancel()
    if not active:
        return
    try:
        await asyncio.gather(*active, return_exceptions=True)
    except asyncio.CancelledError:
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            raise


def _log_read_attachment_timing(
    *,
    attachment_id: int,
    timing_ms: dict[str, int],
    status: str,
) -> None:
    logger.info(
        "bill_read_attachment.timing attachment_id=%s attachment=%d link_state=%d "
        "extract=%d header_llm=%d vendor_match=%d history=%d catalog=%d coding_llm=%d "
        "payload=%d total=%d status=%s",
        attachment_id,
        timing_ms.get("attachment", 0),
        timing_ms.get("link_state", 0),
        timing_ms.get("extract", 0),
        timing_ms.get("header_llm", 0),
        timing_ms.get("vendor_match", 0),
        timing_ms.get("history", 0),
        timing_ms.get("catalog", 0),
        timing_ms.get("coding_llm", 0),
        timing_ms.get("payload", 0),
        timing_ms.get("total", 0),
        status,
    )


def _suggestion_from_history(
    suggestion: BillSuggestion,
    *,
    project_service: ProjectService,
) -> BillSuggestion:
    out_project = resolve_accessible_text_project_id(
        suggestion.project_id,
        project_service=project_service,
    )
    if out_project != suggestion.project_id:
        return replace(suggestion, project_id=out_project)
    return suggestion


def _history_strong_for_project(
    history_suggestion: BillSuggestion,
    *,
    project_id: int,
    project_service: ProjectService,
) -> BillSuggestion:
    """Strong vendor history SCC on project P; project field is P (access-checked)."""
    resolved = _suggestion_from_history(
        history_suggestion,
        project_service=project_service,
    )
    accessible = resolve_accessible_text_project_id(
        project_id,
        project_service=project_service,
    )
    return replace(resolved, project_id=accessible)


def _drop_coding_after_text_model(
    *,
    text_suggestion: Optional[BillSuggestion],
    engine: BillCodingSuggester,
    vendor_id: int,
    as_of: datetime,
    project_service: ProjectService,
    model_unavailable: bool,
) -> Optional[BillSuggestion]:
    """U-676k: Mode B project first; history keyed on true project P, else no-project fallback."""
    if model_unavailable:
        history_nop = engine.suggest(
            vendor_id=vendor_id,
            project_id=None,
            memo=None,
            as_of=as_of,
            exclude_bill_id=None,
        )
        if history_blocks_text_tier(history_nop):
            return _suggestion_from_history(
                history_nop,
                project_service=project_service,
            )
        return None

    text_project: Optional[int] = None
    if text_suggestion is not None:
        text_project = resolve_accessible_text_project_id(
            text_suggestion.project_id,
            project_service=project_service,
        )

    if text_project is not None:
        history_for_p = engine.suggest(
            vendor_id=vendor_id,
            project_id=text_project,
            memo=None,
            as_of=as_of,
            exclude_bill_id=None,
        )
        if history_blocks_text_tier(history_for_p):
            return _history_strong_for_project(
                history_for_p,
                project_id=text_project,
                project_service=project_service,
            )
        if text_suggestion is not None and text_suggestion.sub_cost_code_id is not None:
            return replace(text_suggestion, project_id=text_project)
        return None

    history_nop = engine.suggest(
        vendor_id=vendor_id,
        project_id=None,
        memo=None,
        as_of=as_of,
        exclude_bill_id=None,
    )
    if history_blocks_text_tier(history_nop):
        return _suggestion_from_history(
            history_nop,
            project_service=project_service,
        )
    if text_suggestion is not None and text_suggestion.sub_cost_code_id is not None:
        return replace(text_suggestion, project_id=None)
    return None


def _resolve_status(
    *,
    header_ok: bool,
    coding_error: bool,
    coding_payload: Optional[dict],
) -> str:
    if not header_ok:
        return "partial" if coding_payload is not None else "unreadable"
    if coding_error:
        return "partial"
    return "ok"


async def run_bill_read_attachment(
    *,
    attachment_public_id: str,
    project_public_id: Optional[UUID] = None,
    intake_source: str = "pdf_drop",
    extraction_service: Optional[AttachmentExtractionService] = None,
    attachment_service: Optional[AttachmentService] = None,
    suggester: Optional[BillCodingSuggester] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
    settings: Optional[Any] = None,
    intake_prediction_service: Optional[BillIntakePredictionService] = None,
) -> dict[str, Any]:
    if intake_source not in INTAKE_SOURCES:
        raise ValueError("invalid intake_source")

    cfg = settings or config.Settings()
    if not cfg.bill_drop_prefill_enabled:
        return _result_shell("disabled")

    if await claude_budget.is_claude_day_stopped_async():
        return _result_shell("ai_unavailable", message=MSG_AI_PAUSED)

    request_start = time.monotonic()
    timing_ms: dict[str, int] = {}
    attachment_id_for_log = 0
    persist_enabled = False
    header_cascade: Optional[CascadeResult] = None
    coding_cascade: Optional[CascadeResult] = None
    coding_suggestion: Optional[BillSuggestion] = None
    header_model_id: Optional[str] = None
    vendor_id_for_persist: Optional[int] = None
    vendor_extracted_name: Optional[str] = None
    chosen_project_id_for_persist: Optional[int] = None
    fields_for_persist: dict[str, Any] = _empty_fields()
    intake_svc = intake_prediction_service or BillIntakePredictionService()

    async def _finish(
        result: dict[str, Any],
        *,
        attachment_id: int = 0,
    ) -> dict[str, Any]:
        timing_ms["total"] = int((time.monotonic() - request_start) * 1000)
        if persist_enabled and attachment_id:
            status = str(result.get("status", "unknown"))
            if status != "disabled":
                _schedule_record_read(
                    intake_svc,
                    attachment_id=attachment_id,
                    intake_source=intake_source,
                    status=status,
                    timing_ms=dict(timing_ms),
                    fields=result.get("fields") or fields_for_persist,
                    suggestion=coding_suggestion,
                    chosen_project_id=chosen_project_id_for_persist,
                    header_cascade=header_cascade,
                    coding_cascade=coding_cascade,
                    header_model=header_model_id,
                    vendor_id=vendor_id_for_persist,
                    vendor_extracted_name=vendor_extracted_name,
                )
        if attachment_id:
            _log_read_attachment_timing(
                attachment_id=attachment_id,
                timing_ms=timing_ms,
                status=str(result.get("status", "unknown")),
            )
        return result

    att_svc = attachment_service or AttachmentService()
    step = time.monotonic()
    attachment = await asyncio.to_thread(
        att_svc.read_by_public_id, attachment_public_id
    )
    timing_ms["attachment"] = int((time.monotonic() - step) * 1000)
    if attachment is None or attachment.id is None:
        raise AttachmentNotReadable("not_found")
    attachment_id_for_log = int(attachment.id)

    step = time.monotonic()
    is_linked, is_taxpayer = await asyncio.to_thread(
        read_attachment_link_state, attachment_id_for_log
    )
    timing_ms["link_state"] = int((time.monotonic() - step) * 1000)
    if is_linked:
        raise AttachmentNotReadable("not_found")

    persist_enabled = True

    # Naive UTC, like the DB timestamps the tick compares against.
    as_of = datetime.now(timezone.utc).replace(tzinfo=None)
    catalog_task: Optional[asyncio.Task[CatalogSnapshot]] = asyncio.create_task(
        asyncio.to_thread(load_catalog_from_db, as_of)
    )
    project_task: Optional[asyncio.Task[Optional[int]]] = None
    if project_public_id is not None:
        project_task = asyncio.create_task(
            _resolve_chosen_project_id(project_public_id)
        )

    try:
        ext_svc = extraction_service or AttachmentExtractionService()
        deadline = time.monotonic() + max(1, int(cfg.bill_drop_prefill_timeout_seconds))
        step = time.monotonic()
        extract_out = await _extract_with_inflight_wait(
            ext_svc=ext_svc,
            att_svc=att_svc,
            attachment=attachment,
            is_taxpayer=is_taxpayer,
            deadline=deadline,
        )
        timing_ms["extract"] = int((time.monotonic() - step) * 1000)
        if extract_out.get("status") == "timeout":
            return await _finish(
                _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME),
                attachment_id=attachment_id_for_log,
            )
        if extract_out.get("status") != "completed":
            return await _finish(
                _result_shell("unreadable"),
                attachment_id=attachment_id_for_log,
            )

        masked_text = extract_out.get("masked_text") or ""
        if not masked_text.strip():
            return await _finish(
                _result_shell("unreadable"),
                attachment_id=attachment_id_for_log,
            )

        chosen_project_id: Optional[int] = None
        if project_task is not None:
            try:
                chosen_project_id = await project_task
            except AttachmentNotReadable:
                raise
            project_task = None

        chosen_project_id_for_persist = chosen_project_id

        step = time.monotonic()
        try:
            header_cascade = await run_bill_header_extraction(
                {"bill_text": masked_text},
                transport_for=transport_for,
            )
        except Exception as error:
            logger.warning(
                "bill_read_attachment.header_failed err=%s",
                type(error).__name__,
            )
            return await _finish(
                _result_shell("unreadable", message=MSG_COULDNT_READ, fields=_empty_fields()),
                attachment_id=attachment_id_for_log,
            )
        timing_ms["header_llm"] = int((time.monotonic() - step) * 1000)

        if header_cascade.stopped_reason == "daily_cap":
            return await _finish(
                _result_shell("ai_unavailable", message=MSG_AI_PAUSED),
                attachment_id=attachment_id_for_log,
            )

        step = time.monotonic()
        fields, header_ok, model_id, vendor_match = await asyncio.to_thread(
            _header_from_cascade,
            header_cascade,
        )
        fields_for_persist = fields
        header_model_id = model_id
        timing_ms["vendor_match"] = int((time.monotonic() - step) * 1000)
        coding_payload: Optional[dict] = None
        coding_error = False

        vendor_id: Optional[int] = None
        vendor_name_for_catalog: Optional[str] = None
        vendor_extracted_name = fields["vendor"].get("extracted_name")
        if vendor_match is not None:
            vendor_id = vendor_match.vendor_id
            vendor_name_for_catalog = vendor_match.name or None
            if vendor_id is None and fields["vendor"]["value"]:
                vendor_public_id = fields["vendor"]["value"]["public_id"]
                vendor = await asyncio.to_thread(
                    VendorService().read_by_public_id, vendor_public_id
                )
                if vendor is not None and vendor.id is not None:
                    vendor_id = int(vendor.id)
                    if not vendor_name_for_catalog:
                        vendor_name_for_catalog = vendor.name or None
            vendor_id_for_persist = vendor_id

        if vendor_id is not None:
            engine = suggester or BillCodingSuggester()
            project_service = engine.project_service

            suggestion: Optional[BillSuggestion] = None

            if chosen_project_id is not None:
                step = time.monotonic()
                history_suggestion = await asyncio.to_thread(
                    lambda: engine.suggest(
                        vendor_id=int(vendor_id),
                        project_id=chosen_project_id,
                        memo=None,
                        as_of=as_of,
                        exclude_bill_id=None,
                    )
                )
                timing_ms["history"] = int((time.monotonic() - step) * 1000)

                eligible, skip_reason = await asyncio.to_thread(
                    history_eligible_for_text,
                    vendor_id=int(vendor_id),
                    chosen_project_id=chosen_project_id,
                    as_of=as_of,
                    exclude_bill_id=None,
                    suggester=engine,
                    precomputed_suggestion=history_suggestion,
                )

                if not eligible and skip_reason == "history_strong":
                    if catalog_task is not None and not catalog_task.done():
                        catalog_task.cancel()
                    catalog_task = None
                    timing_ms.setdefault("catalog", 0)
                    suggestion = await asyncio.to_thread(
                        _suggestion_from_history,
                        history_suggestion,
                        project_service=project_service,
                    )
                elif eligible:
                    step = time.monotonic()
                    prefetched_catalog: Optional[CatalogSnapshot] = None
                    if catalog_task is not None:
                        try:
                            prefetched_catalog = await catalog_task
                        except Exception:
                            prefetched_catalog = None
                        catalog_task = None
                    timing_ms["catalog"] = int((time.monotonic() - step) * 1000)
                    step = time.monotonic()
                    try:
                        text_suggestion, stopped, coding_cascade_result = await predict_text_coding(
                            bill_text=masked_text,
                            vendor_id=int(vendor_id),
                            chosen_project_id=chosen_project_id,
                            as_of=as_of,
                            suggester=engine,
                            transport_for=transport_for,
                            catalog=prefetched_catalog,
                            vendor_name=vendor_name_for_catalog,
                        )
                        if coding_cascade_result is not None:
                            coding_cascade = coding_cascade_result
                        if stopped == "daily_cap":
                            coding_error = True
                        else:
                            suggestion = text_suggestion
                    except Exception as error:
                        logger.warning(
                            "bill_read_attachment.coding_failed err=%s",
                            type(error).__name__,
                        )
                        cascade_from_error = getattr(error, "coding_cascade", None)
                        if cascade_from_error is not None:
                            coding_cascade = cascade_from_error
                        coding_error = True
                    timing_ms["coding_llm"] = int((time.monotonic() - step) * 1000)
            else:
                step = time.monotonic()
                prefetched_catalog = None
                if catalog_task is not None:
                    try:
                        prefetched_catalog = await catalog_task
                    except Exception:
                        prefetched_catalog = None
                    catalog_task = None
                timing_ms["catalog"] = int((time.monotonic() - step) * 1000)

                step = time.monotonic()
                text_suggestion: Optional[BillSuggestion] = None
                model_unavailable = False
                try:
                    text_suggestion, stopped, coding_cascade_result = await predict_text_coding(
                        bill_text=masked_text,
                        vendor_id=int(vendor_id),
                        chosen_project_id=None,
                        as_of=as_of,
                        suggester=engine,
                        transport_for=transport_for,
                        catalog=prefetched_catalog,
                        vendor_name=vendor_name_for_catalog,
                        retain_accessible_project=True,
                    )
                    if coding_cascade_result is not None:
                        coding_cascade = coding_cascade_result
                    if stopped == "daily_cap":
                        model_unavailable = True
                        text_suggestion = None
                except Exception as error:
                    logger.warning(
                        "bill_read_attachment.coding_failed err=%s",
                        type(error).__name__,
                    )
                    cascade_from_error = getattr(error, "coding_cascade", None)
                    if cascade_from_error is not None:
                        coding_cascade = cascade_from_error
                    coding_error = True
                    model_unavailable = True
                    text_suggestion = None
                timing_ms["coding_llm"] = int((time.monotonic() - step) * 1000)
                if model_unavailable:
                    coding_error = True

                step = time.monotonic()
                suggestion = await asyncio.to_thread(
                    _drop_coding_after_text_model,
                    text_suggestion=text_suggestion,
                    engine=engine,
                    vendor_id=int(vendor_id),
                    as_of=as_of,
                    project_service=project_service,
                    model_unavailable=model_unavailable,
                )
                timing_ms["history"] = int((time.monotonic() - step) * 1000)

            coding_suggestion = suggestion
            if suggestion is not None and suggestion.sub_cost_code_id is not None:
                suggestion.text_status = "ready"
                step = time.monotonic()
                coding_payload = await asyncio.to_thread(
                    suggestion_to_api_payload, suggestion
                )
                timing_ms["payload"] = int((time.monotonic() - step) * 1000)

        status = _resolve_status(
            header_ok=header_ok,
            coding_error=coding_error,
            coding_payload=coding_payload,
        )
        if status == "unreadable":
            return await _finish(
                _result_shell(
                    "unreadable",
                    message=MSG_COULDNT_READ,
                    fields=fields,
                    coding=coding_payload,
                    model=model_id,
                ),
                attachment_id=attachment_id_for_log,
            )

        return await _finish(
            _result_shell(
                status,
                fields=fields,
                coding=coding_payload,
                model=model_id,
            ),
            attachment_id=attachment_id_for_log,
        )
    finally:
        await _await_or_cancel_tasks(catalog_task, project_task)


async def run_bill_read_attachment_with_timeout(
    *,
    attachment_public_id: str,
    project_public_id: Optional[UUID] = None,
    intake_source: str = "pdf_drop",
    settings: Optional[Any] = None,
) -> dict[str, Any]:
    cfg = settings or config.Settings()
    timeout = max(1, int(cfg.bill_drop_prefill_timeout_seconds))
    try:
        return await asyncio.wait_for(
            run_bill_read_attachment(
                attachment_public_id=attachment_public_id,
                project_public_id=project_public_id,
                intake_source=intake_source,
                settings=cfg,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME)
