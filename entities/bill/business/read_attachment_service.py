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
from entities.attachment.persistence.repo import AttachmentLinkState, AttachmentRepository
from entities.coding_suggestion.business.bill_suggestion_service import (
    SIMILAR_BILLS_SUGGESTION_REASON,
    SUGGESTION_SOURCE_SIMILAR_BILLS,
    TEXT_LLM_SUGGESTION_REASON,
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
from shared.access import (
    EntityNotAccessibleError,
    assert_can_access_project,
    resolve_accessible_project_ids,
)
from shared.authz import current_company_id, current_user_id

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
) -> AttachmentLinkState:
    """One ReadAttachmentLinkState call: (is_linked, is_taxpayer, is_owned).

    Fails closed on any error: linked, taxpayer (mask everything), not owned.
    """
    try:
        return (repo or AttachmentRepository()).read_link_state_with_ownership(attachment_id)
    except Exception as error:
        logger.warning(
            "bill_read_attachment.link_state_failed err=%s",
            type(error).__name__,
        )
        return AttachmentLinkState(is_linked=True, is_taxpayer=True, is_owned=False)


async def gate_attachment_for_bill_read(
    attachment_public_id: str,
    *,
    attachment_service: Optional[AttachmentService] = None,
    attachment: Any = None,
    timing_ms: Optional[dict[str, int]] = None,
) -> tuple[Any, AttachmentLinkState]:
    """Resolve the attachment and read its link state ONCE; 404 unless the caller owns it.

    Ownership is the sproc's own rule (IsOwned: system admin, or creator in the caller's
    company). Linked-ness is returned, not enforced: the read endpoint refuses a linked
    attachment, the list-drop path first checks for the caller's own existing draft.
    """
    att_svc = attachment_service or AttachmentService()
    step = time.monotonic()
    if attachment is None:
        attachment = await asyncio.to_thread(att_svc.read_by_public_id, attachment_public_id)
    if timing_ms is not None:
        timing_ms["attachment"] = int((time.monotonic() - step) * 1000)
    if attachment is None or attachment.id is None:
        raise AttachmentNotReadable("not_found")
    step = time.monotonic()
    state = await asyncio.to_thread(read_attachment_link_state, int(attachment.id))
    if timing_ms is not None:
        timing_ms["link_state"] = int((time.monotonic() - step) * 1000)
    if not state.is_owned:
        raise AttachmentNotReadable("not_found")
    return attachment, state


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


_LADDER_LLM_SOURCES = frozenset({"primary", "escalate", "fallback"})
HISTORY_FOR_PROJECT_REASON = "Most prior bills from this vendor on this project used this code"
# Drop coding sub-deadline (U-701b4): coding must finish this many seconds before the
# outer read timeout, so a slow model yields "partial" WITH the header fields (and the
# intake row) instead of the outer timeout discarding everything.
CODING_DEADLINE_MARGIN_SECONDS = 3.0


def _drop_amount(fields: dict[str, Any]) -> Optional[Decimal]:
    """The read's total, absolute (credit memos read negative); None when absent/unparseable."""
    total = fields.get("total_amount") if isinstance(fields, dict) else None
    value = total.get("value") if isinstance(total, dict) else None
    if value is None or value == "":
        return None
    try:
        amount = abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _suggestion_from_decision(
    decision: Any,
    *,
    chosen_project_id: Optional[int],
    engine: BillCodingSuggester,
) -> Optional[BillSuggestion]:
    """U-701b4: the drop's prefill from a ladder V2 decision (None = no coding).

    history -> the history suggestion (its own source values); primary/escalate/fallback ->
    "llm"; similar_vote -> "similar_bills"; none -> no coding. No confidence floor: a
    low-confidence Haiku answer IS the prefill. A holding (Suspense) code is never returned.
    """
    from entities.coding_suggestion.business.bill_text_suggestion_service import (
        MODEL_VERSION_V2,
    )

    source = getattr(decision, "source", None)
    project_service = engine.project_service
    history_label: Optional[BillSuggestion] = None
    if source == "history":
        # The decision's own code / project / confidence (Mode B history-for-P keys on
        # the model's P, not the vendor-wide history); decision.history only labels it,
        # and only when it is that same code.
        history = getattr(decision, "history", None)
        if history is not None and history.sub_cost_code_id == getattr(
            decision, "sub_cost_code_id", None
        ):
            history_label = history
        out_source = history_label.source if history_label else "vendor_project_history"
        reason = history_label.reason if history_label else HISTORY_FOR_PROJECT_REASON
    elif source in _LADDER_LLM_SOURCES:
        out_source, reason = "llm", TEXT_LLM_SUGGESTION_REASON
    elif source == "similar_vote":
        out_source, reason = SUGGESTION_SOURCE_SIMILAR_BILLS, SIMILAR_BILLS_SUGGESTION_REASON
    else:
        return None
    is_history = source == "history"

    scc_id = getattr(decision, "sub_cost_code_id", None)
    if scc_id is None:
        return None
    scc_svc = engine.sub_cost_code_service
    scc = scc_svc.read_by_id(int(scc_id))
    if scc is None or scc_svc.is_holding_code(int(scc_id)):
        return None

    mode = getattr(decision, "mode", None) or ("A" if chosen_project_id is not None else "B")
    if chosen_project_id is not None:
        out_project = resolve_accessible_text_project_id(
            chosen_project_id, project_service=project_service
        )
        predicted = False
    else:
        out_project = resolve_accessible_text_project_id(
            getattr(decision, "project_id", None), project_service=project_service
        )
        predicted = out_project is not None
    confidence = getattr(decision, "confidence", None)
    if confidence is not None and not isinstance(confidence, Decimal):
        confidence = Decimal(str(confidence))
    return BillSuggestion(
        project_id=out_project,
        sub_cost_code_id=int(scc_id),
        sub_cost_code_public_id=str(scc.public_id) if scc.public_id else None,
        sub_cost_code_number=scc.number,
        sub_cost_code_name=scc.name,
        source=out_source,
        reason=reason,
        confidence=confidence,
        raw_score=confidence if is_history else None,
        top_count=history_label.top_count if history_label else None,
        second_count=history_label.second_count if history_label else None,
        total_count=history_label.total_count if history_label else None,
        model_version=history_label.model_version if history_label else MODEL_VERSION_V2,
        predicted_project=predicted,
        text_used=out_source == "llm",
        text_mode=None if is_history else mode,
        text_model=None if is_history else getattr(decision, "model", None),
        project_confidence=confidence if predicted else None,
        decision_source=source,
    )


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
    pre_gated: Optional[tuple[Any, bool]] = None,
    suggester: Optional[BillCodingSuggester] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
    settings: Optional[Any] = None,
    intake_prediction_service: Optional[BillIntakePredictionService] = None,
    exclude_bill_id: Optional[int] = None,
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
    shadow_extraction_content: Optional[str] = None
    # U-701b4: ladder V2 coding (flag default OFF; `is True` so a mocked Settings stays OFF).
    ladder_v2 = getattr(cfg, "bill_coding_ladder_v2_enabled", False) is True
    coding_decision: Any = None

    async def _finish(
        result: dict[str, Any],
        *,
        attachment_id: int = 0,
    ) -> dict[str, Any]:
        timing_ms["total"] = int((time.monotonic() - request_start) * 1000)
        if persist_enabled and attachment_id:
            status = str(result.get("status", "unknown"))
            if status != "disabled":
                total_field = (result.get("fields") or fields_for_persist).get("total_amount")
                shadow_amount = None
                if isinstance(total_field, dict):
                    shadow_amount = total_field.get("value")
                extra_record_kwargs: dict[str, Any] = {}
                if coding_decision is not None:
                    extra_record_kwargs["coding_decision"] = coding_decision
                _schedule_record_read(
                    intake_svc,
                    **extra_record_kwargs,
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
                    shadow_extraction_content=shadow_extraction_content,
                    shadow_vendor_id=vendor_id_for_persist,
                    shadow_amount=shadow_amount,
                    shadow_as_of=as_of,
                )
        if attachment_id:
            _log_read_attachment_timing(
                attachment_id=attachment_id,
                timing_ms=timing_ms,
                status=str(result.get("status", "unknown")),
            )
        return result

    att_svc = attachment_service or AttachmentService()
    if pre_gated is not None:
        # Callers: list-drop (gate already ran; linked attachments refused) and U-704 reread
        # (server-resolved attachment on the caller's own draft bill). pre_gated is only ever
        # built from a server-resolved attachment, never from request input.
        attachment, is_taxpayer = pre_gated
        timing_ms["attachment"] = 0
        timing_ms["link_state"] = 0
    else:
        attachment, state = await gate_attachment_for_bill_read(
            attachment_public_id, attachment_service=att_svc, timing_ms=timing_ms
        )
        if state.is_linked:
            raise AttachmentNotReadable("not_found")
        is_taxpayer = state.is_taxpayer
    attachment_id_for_log = int(attachment.id)

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
        shadow_extraction_content = masked_text
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

        async def _run_ladder_v2(
            engine: BillCodingSuggester,
            chosen: Optional[int],
            history_suggestion: Optional[BillSuggestion] = None,
        ) -> tuple[Optional[BillSuggestion], bool]:
            """ONE drop-stage ladder call (Haiku + Foundry fallback); returns (prefill, error)."""
            nonlocal coding_decision, coding_cascade
            from entities.coding_suggestion.business.bill_text_suggestion_service import (
                run_bill_coding_decision,
            )

            nonlocal catalog_task
            prefetched = catalog_task
            catalog_task = None
            step_l = time.monotonic()

            async def _decide() -> Any:
                catalog: Optional[CatalogSnapshot] = None
                if prefetched is not None:
                    try:
                        catalog = await prefetched
                    except Exception:
                        catalog = None
                accessible = await asyncio.to_thread(resolve_accessible_project_ids)
                return await run_bill_coding_decision(
                    stage="drop",
                    bill_text=masked_text,
                    vendor_id=int(vendor_id),
                    vendor_name=vendor_name_for_catalog or "",
                    chosen_project_id=chosen,
                    amount=_drop_amount(fields),
                    as_of=as_of,
                    exclude_bill_id=exclude_bill_id,
                    company_id=current_company_id.get(),
                    actor_user_id=current_user_id.get(),
                    accessible_project_ids=accessible,
                    transport_for=transport_for,
                    settings=cfg,
                    catalog=catalog,
                    history_suggestion=history_suggestion,
                )

            coding_budget = (
                request_start
                + max(1, int(cfg.bill_drop_prefill_timeout_seconds))
                - CODING_DEADLINE_MARGIN_SECONDS
                - time.monotonic()
            )
            try:
                if coding_budget <= 0:
                    raise asyncio.TimeoutError
                decision = await asyncio.wait_for(_decide(), timeout=coding_budget)
            except asyncio.TimeoutError:
                if prefetched is not None and not prefetched.done():
                    prefetched.cancel()
                logger.warning("bill_read_attachment.coding_timeout")
                timing_ms["coding_llm"] = int((time.monotonic() - step_l) * 1000)
                return None, True
            except Exception as error:
                logger.warning(
                    "bill_read_attachment.coding_failed err=%s",
                    type(error).__name__,
                )
                cascade_from_error = getattr(error, "coding_cascade", None)
                if cascade_from_error is not None:
                    coding_cascade = cascade_from_error
                timing_ms["coding_llm"] = int((time.monotonic() - step_l) * 1000)
                return None, True
            timing_ms["coding_llm"] = int((time.monotonic() - step_l) * 1000)
            coding_decision = decision
            cascades = getattr(decision, "cascades", None) or ()
            if cascades:
                coding_cascade = cascades[-1]
            try:
                out = await asyncio.to_thread(
                    _suggestion_from_decision,
                    decision,
                    chosen_project_id=chosen,
                    engine=engine,
                )
            except Exception as error:
                # The decision is still recorded; the user just gets no prefill.
                logger.warning(
                    "bill_read_attachment.coding_present_failed err=%s",
                    type(error).__name__,
                )
                return None, True
            # Claude capped/unavailable and nothing (Foundry, vote) answered: partial, as today.
            errored = out is None and getattr(decision, "stopped_reason", None) == "daily_cap"
            return out, errored

        if vendor_id is not None:
            engine = suggester or BillCodingSuggester()
            project_service = engine.project_service

            suggestion: Optional[BillSuggestion] = None

            if chosen_project_id is not None:
                step = time.monotonic()
                # Flag ON: the same exclude_bill_id as the ladder (a reread's own bill).
                history_exclude = exclude_bill_id if ladder_v2 else None
                history_suggestion = await asyncio.to_thread(
                    lambda: engine.suggest(
                        vendor_id=int(vendor_id),
                        project_id=chosen_project_id,
                        memo=None,
                        as_of=as_of,
                        exclude_bill_id=history_exclude,
                    )
                )
                timing_ms["history"] = int((time.monotonic() - step) * 1000)

                eligible, skip_reason = await asyncio.to_thread(
                    history_eligible_for_text,
                    vendor_id=int(vendor_id),
                    chosen_project_id=chosen_project_id,
                    as_of=as_of,
                    exclude_bill_id=history_exclude,
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
                elif eligible and ladder_v2:
                    suggestion, coding_error = await _run_ladder_v2(
                        engine, chosen_project_id, history_suggestion
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
            elif ladder_v2:
                # Mode B: the ladder owns history (project predicted from the decision).
                suggestion, coding_error = await _run_ladder_v2(engine, None)
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
    pre_gated: Optional[tuple[Any, bool]] = None,
    exclude_bill_id: Optional[int] = None,
) -> dict[str, Any]:
    cfg = settings or config.Settings()
    timeout = max(1, int(cfg.bill_drop_prefill_timeout_seconds))
    # Only forwarded when set (a reread's own bill), so existing call shapes are unchanged.
    extra: dict[str, Any] = {}
    if exclude_bill_id is not None:
        extra["exclude_bill_id"] = int(exclude_bill_id)
    try:
        return await asyncio.wait_for(
            run_bill_read_attachment(
                attachment_public_id=attachment_public_id,
                project_public_id=project_public_id,
                intake_source=intake_source,
                settings=cfg,
                pre_gated=pre_gated,
                **extra,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME)


# U-716a: shared with expense receipt read (aliases keep bill call sites unchanged).
# Bound at import time — expense-path tests must patch receipt_match_service names, not these.
extract_with_inflight_wait = _extract_with_inflight_wait
header_fields_from_cascade = _header_from_cascade
