"""Synchronous bill-create attachment read pipeline (U-676b)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Callable, Optional
from uuid import UUID

import config
from entities.attachment.business.extraction_service import AttachmentExtractionService
from entities.attachment.business.service import AttachmentService
from entities.attachment.persistence.repo import AttachmentRepository
from entities.coding_suggestion.business.bill_suggestion_service import (
    BillCodingSuggester,
    BillSuggestion,
    resolve_accessible_text_project_id,
)
from entities.coding_suggestion.business.bill_text_suggestion_service import (
    history_eligible_for_text,
    predict_text_coding,
)
from entities.coding_suggestion.business.presenters import suggestion_to_api_payload
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from entities.vendor.business.vendor_name_resolver import resolve_vendor_match_from_name
from intelligence.cascade import claude_budget
from intelligence.cascade.bill_header_extraction import (
    run_bill_header_extraction,
    validate_bill_header_result,
)
from intelligence.cascade.core import CascadeResult
from shared.access import EntityNotAccessibleError, assert_can_access_project

logger = logging.getLogger(__name__)

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


def _header_from_cascade(cascade: CascadeResult) -> tuple[dict[str, Any], bool, Optional[str]]:
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
    if raw is None:
        return fields, False, model_id

    ok, _reason = validate_bill_header_result(dict(raw))
    if not ok:
        return fields, False, model_id

    overall = float(raw.get("confidence") or 0.0)
    vendor_name = raw.get("vendor_name") if isinstance(raw.get("vendor_name"), str) else None
    fields["vendor"]["extracted_name"] = vendor_name

    match = resolve_vendor_match_from_name(
        vendor_name,
        confidence=LLM_VENDOR_MATCH_CONFIDENCE,
        cascade_confidence=overall,
    )
    if match is not None:
        fields["vendor"]["value"] = {"public_id": match.public_id, "name": match.name}
        fields["vendor"]["confidence"] = _conf_str(match.confidence)
    elif vendor_name:
        fields["vendor"]["confidence"] = _conf_str(overall)

    for key, out_key in (
        ("bill_number", "bill_number"),
        ("bill_date", "bill_date"),
        ("total_amount", "total_amount"),
        ("description", "description"),
    ):
        val = raw.get(key)
        if val is not None and val != "":
            if out_key == "description":
                fields[out_key]["value"] = str(val)[:120]
            elif out_key == "total_amount":
                fields[out_key]["value"] = _format_total_amount(val)
            else:
                fields[out_key]["value"] = str(val)
            fields[out_key]["confidence"] = _conf_str(overall)

    header_ok = any(
        fields[k]["value"] is not None
        for k in ("bill_number", "bill_date", "total_amount", "description")
    ) or fields["vendor"]["extracted_name"] is not None
    return fields, header_ok, model_id


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
    extraction_service: Optional[AttachmentExtractionService] = None,
    attachment_service: Optional[AttachmentService] = None,
    suggester: Optional[BillCodingSuggester] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
    settings: Optional[Any] = None,
) -> dict[str, Any]:
    cfg = settings or config.Settings()
    if not cfg.bill_drop_prefill_enabled:
        return _result_shell("disabled")

    if await claude_budget.is_claude_day_stopped_async():
        return _result_shell("ai_unavailable", message=MSG_AI_PAUSED)

    att_svc = attachment_service or AttachmentService()
    attachment = await asyncio.to_thread(
        att_svc.read_by_public_id, attachment_public_id
    )
    if attachment is None or attachment.id is None:
        raise AttachmentNotReadable("not_found")

    is_linked, is_taxpayer = await asyncio.to_thread(
        read_attachment_link_state, int(attachment.id)
    )
    if is_linked:
        raise AttachmentNotReadable("not_found")

    ext_svc = extraction_service or AttachmentExtractionService()
    deadline = time.monotonic() + max(1, int(cfg.bill_drop_prefill_timeout_seconds))
    extract_out = await _extract_with_inflight_wait(
        ext_svc=ext_svc,
        att_svc=att_svc,
        attachment=attachment,
        is_taxpayer=is_taxpayer,
        deadline=deadline,
    )
    if extract_out.get("status") == "timeout":
        return _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME)
    if extract_out.get("status") != "completed":
        return _result_shell("unreadable")

    masked_text = extract_out.get("masked_text") or ""
    if not masked_text.strip():
        return _result_shell("unreadable")

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
        return _result_shell("unreadable", message=MSG_COULDNT_READ, fields=_empty_fields())

    if header_cascade.stopped_reason == "daily_cap":
        return _result_shell("ai_unavailable", message=MSG_AI_PAUSED)

    fields, header_ok, model_id = await asyncio.to_thread(
        _header_from_cascade, header_cascade
    )
    coding_payload: Optional[dict] = None
    coding_error = False

    vendor_public_id = (
        fields["vendor"]["value"]["public_id"] if fields["vendor"]["value"] else None
    )
    if vendor_public_id:
        vendor_svc = VendorService()
        vendor = await asyncio.to_thread(
            vendor_svc.read_by_public_id, vendor_public_id
        )
        if vendor is not None and vendor.id is not None:
            chosen_project_id: Optional[int] = None
            if project_public_id is not None:
                project = await asyncio.to_thread(
                    ProjectService().read_by_public_id, str(project_public_id)
                )
                if project is None or project.id is None:
                    raise AttachmentNotReadable("not_found")
                try:
                    await asyncio.to_thread(assert_can_access_project, int(project.id))
                except EntityNotAccessibleError:
                    raise AttachmentNotReadable("not_found") from None
                chosen_project_id = int(project.id)

            as_of = datetime.now(timezone.utc)
            engine = suggester or BillCodingSuggester()
            project_service = engine.project_service

            history_suggestion = await asyncio.to_thread(
                lambda: engine.suggest(
                    vendor_id=int(vendor.id),
                    project_id=chosen_project_id,
                    memo=None,
                    as_of=as_of,
                    exclude_bill_id=None,
                )
            )

            eligible, skip_reason = await asyncio.to_thread(
                history_eligible_for_text,
                vendor_id=int(vendor.id),
                chosen_project_id=chosen_project_id,
                as_of=as_of,
                exclude_bill_id=None,
                suggester=engine,
            )

            suggestion: Optional[BillSuggestion] = None
            if not eligible and skip_reason == "history_strong":
                suggestion = await asyncio.to_thread(
                    _suggestion_from_history,
                    history_suggestion,
                    project_service=project_service,
                )
            elif eligible:
                try:
                    suggestion, stopped = await predict_text_coding(
                        bill_text=masked_text,
                        vendor_id=int(vendor.id),
                        chosen_project_id=chosen_project_id,
                        as_of=as_of,
                        suggester=engine,
                        transport_for=transport_for,
                    )
                    if stopped == "daily_cap":
                        return _result_shell(
                            "ai_unavailable",
                            message=MSG_AI_PAUSED,
                            fields=fields,
                            model=model_id,
                        )
                except Exception as error:
                    logger.warning(
                        "bill_read_attachment.coding_failed err=%s",
                        type(error).__name__,
                    )
                    coding_error = True

            if suggestion is not None and suggestion.sub_cost_code_id is not None:
                suggestion.text_status = "ready"
                coding_payload = await asyncio.to_thread(
                    suggestion_to_api_payload, suggestion
                )

    status = _resolve_status(
        header_ok=header_ok,
        coding_error=coding_error,
        coding_payload=coding_payload,
    )
    if status == "unreadable":
        return _result_shell(
            "unreadable",
            message=MSG_COULDNT_READ,
            fields=fields,
            coding=coding_payload,
            model=model_id,
        )

    return _result_shell(
        status,
        fields=fields,
        coding=coding_payload,
        model=model_id,
    )


async def run_bill_read_attachment_with_timeout(
    *,
    attachment_public_id: str,
    project_public_id: Optional[UUID] = None,
    settings: Optional[Any] = None,
) -> dict[str, Any]:
    cfg = settings or config.Settings()
    timeout = max(1, int(cfg.bill_drop_prefill_timeout_seconds))
    try:
        return await asyncio.wait_for(
            run_bill_read_attachment(
                attachment_public_id=attachment_public_id,
                project_public_id=project_public_id,
                settings=cfg,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME)
