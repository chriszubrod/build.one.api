"""U-716a: expense list receipt drop — read fields + match candidates + attach."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

import config
from entities.attachment.business.extraction_service import AttachmentExtractionService
from entities.attachment.business.service import AttachmentService
from entities.bill.business.read_attachment_service import (
    AttachmentNotReadable,
    MSG_AI_PAUSED,
    MSG_COULDNT_READ,
    MSG_COULDNT_READ_IN_TIME,
    extract_with_inflight_wait,
    gate_attachment_for_bill_read,
    header_fields_from_cascade,
)
from entities.expense.business.service import ExpenseService
from entities.expense.persistence.receipt_match_repo import ExpenseReceiptMatchRepository
from entities.vendor.business.service import VendorService
from entities.vendor.business.vendor_name_resolver import VendorResolveMatch
from intelligence.cascade import claude_budget
from intelligence.cascade.bill_header_extraction import run_bill_header_extraction
from shared.authz import current_user_id
from shared.lifecycle.terminal_lock import is_terminal

logger = logging.getLogger(__name__)


class ExpenseReceiptConflictError(Exception):
    """409 attach/read conflict with a machine detail code."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class ExpenseReceiptNotFoundError(LookupError):
    """404 for expense or attachment (caller maps message)."""

    def __init__(self, entity: str):
        self.entity = entity
        super().__init__(entity)


def _empty_fields() -> dict[str, Any]:
    return {
        "vendor_public_id": None,
        "vendor_name": None,
        "expense_date": None,
        "total_amount": None,
        "reference_number": None,
    }


def _result_shell(
    status: str,
    *,
    message: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    candidates: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "fields": fields if fields is not None else _empty_fields(),
        "candidates": candidates if candidates is not None else [],
    }


def _allowed_content_type(content_type: Optional[str]) -> bool:
    if content_type is None:
        return True
    ct = content_type.split(";", 1)[0].strip().lower()
    if ct == "application/pdf":
        return True
    return ct.startswith("image/")


def _parse_expense_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _abs_total_decimal(raw: Optional[str]) -> Optional[Decimal]:
    if raw is None or raw == "":
        return None
    try:
        amount = Decimal(str(raw))
        return abs(amount.quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return None


def _expense_fields_from_bill_header(
    bill_fields: dict[str, Any],
    vendor_match: Optional[VendorResolveMatch],
) -> dict[str, Any]:
    out = _empty_fields()
    vendor_block = bill_fields.get("vendor") or {}
    vendor_val = vendor_block.get("value")
    if isinstance(vendor_val, dict):
        out["vendor_public_id"] = vendor_val.get("public_id")
        out["vendor_name"] = vendor_val.get("name")
    extracted = vendor_block.get("extracted_name")
    if out["vendor_name"] is None and isinstance(extracted, str):
        out["vendor_name"] = extracted

    bill_date = (bill_fields.get("bill_date") or {}).get("value")
    parsed = _parse_expense_date(bill_date)
    if parsed:
        out["expense_date"] = parsed.isoformat()

    total_val = (bill_fields.get("total_amount") or {}).get("value")
    if total_val is not None:
        try:
            out["total_amount"] = f"{abs(Decimal(str(total_val))):.2f}"
        except (InvalidOperation, ValueError):
            pass

    ref = (bill_fields.get("bill_number") or {}).get("value")
    if ref is not None and str(ref).strip():
        out["reference_number"] = str(ref).strip()
    return out


def _format_candidate(row: dict[str, Any]) -> dict[str, Any]:
    total = row.get("total_amount")
    if total is not None:
        try:
            total_str = f"{abs(total):.2f}"
        except (InvalidOperation, ValueError):
            total_str = str(total)
    else:
        total_str = None
    status = row.get("status")
    is_draft = row.get("is_draft")
    return {
        "public_id": row["public_id"],
        "vendor_name": row.get("vendor_name"),
        "expense_date": row.get("expense_date"),
        "total_amount": total_str,
        "reference_number": row.get("reference_number"),
        "is_draft": bool(is_draft),
        "status": status,
        "is_completed": is_terminal(status, is_draft=is_draft),
        "vendor_matches": bool(row.get("vendor_matches")),
        "days_apart": int(row.get("days_apart") or 0),
    }


async def run_expense_read_receipt(
    *,
    attachment_public_id: str,
    extraction_service: Optional[AttachmentExtractionService] = None,
    attachment_service: Optional[AttachmentService] = None,
    match_repo: Optional[ExpenseReceiptMatchRepository] = None,
    settings: Optional[Any] = None,
) -> dict[str, Any]:
    cfg = settings or config.Settings()
    if not cfg.bill_drop_prefill_enabled:
        return _result_shell("disabled")

    if await claude_budget.is_claude_day_stopped_async():
        return _result_shell("ai_unavailable", message=MSG_AI_PAUSED)

    att_svc = attachment_service or AttachmentService()
    attachment, state = await gate_attachment_for_bill_read(
        attachment_public_id, attachment_service=att_svc
    )
    if state.is_linked:
        raise ExpenseReceiptConflictError("attachment_linked")

    content_type = getattr(attachment, "content_type", None)
    if not _allowed_content_type(content_type):
        return _result_shell("unreadable", message=MSG_COULDNT_READ)

    ext_svc = extraction_service or AttachmentExtractionService()
    deadline = time.monotonic() + max(1, int(cfg.bill_drop_prefill_timeout_seconds))
    extract_out = await extract_with_inflight_wait(
        ext_svc=ext_svc,
        att_svc=att_svc,
        attachment=attachment,
        is_taxpayer=state.is_taxpayer,
        deadline=deadline,
    )
    if extract_out.get("status") == "timeout":
        return _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME)
    if extract_out.get("status") != "completed":
        return _result_shell("unreadable", message=MSG_COULDNT_READ)

    masked_text = extract_out.get("masked_text") or ""
    if not masked_text.strip():
        return _result_shell("unreadable", message=MSG_COULDNT_READ)

    try:
        header_cascade = await run_bill_header_extraction({"bill_text": masked_text})
    except Exception as error:
        logger.warning(
            "expense_read_receipt.header_failed err=%s",
            type(error).__name__,
        )
        return _result_shell("unreadable", message=MSG_COULDNT_READ)

    if header_cascade.stopped_reason == "daily_cap":
        return _result_shell("ai_unavailable", message=MSG_AI_PAUSED)

    bill_fields, _header_ok, _model_id, vendor_match = await asyncio.to_thread(
        header_fields_from_cascade,
        header_cascade,
    )
    fields = _expense_fields_from_bill_header(bill_fields, vendor_match)

    total_for_match = _abs_total_decimal(fields.get("total_amount"))
    if total_for_match is None:
        return _result_shell("unreadable", message=MSG_COULDNT_READ, fields=fields)

    vendor_id: Optional[int] = None
    if vendor_match is not None:
        vendor_id = vendor_match.vendor_id
        if vendor_id is None:
            vendor = await asyncio.to_thread(
                VendorService().read_by_public_id, vendor_match.public_id
            )
            vendor_id = (
                int(vendor.id) if vendor is not None and vendor.id is not None else None
            )

    expense_date = _parse_expense_date(fields.get("expense_date"))
    repo = match_repo or ExpenseReceiptMatchRepository()
    raw_candidates = await asyncio.to_thread(
        repo.read_candidates,
        total_amount=total_for_match,
        expense_date=expense_date,
        vendor_id=vendor_id,
    )
    candidates = [_format_candidate(row) for row in raw_candidates]
    status = "matched" if candidates else "no_match"
    return _result_shell(status, fields=fields, candidates=candidates)


async def run_expense_read_receipt_with_timeout(
    *,
    attachment_public_id: str,
    settings: Optional[Any] = None,
) -> dict[str, Any]:
    cfg = settings or config.Settings()
    timeout = max(1, int(cfg.bill_drop_prefill_timeout_seconds))
    try:
        return await asyncio.wait_for(
            run_expense_read_receipt(
                attachment_public_id=attachment_public_id,
                settings=cfg,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return _result_shell("timeout", message=MSG_COULDNT_READ_IN_TIME)


def _parse_expense_public_id(raw: str) -> UUID:
    try:
        return UUID(str(raw))
    except ValueError as error:
        raise ExpenseReceiptNotFoundError("Expense") from error


async def attach_receipt_to_expense(
    *,
    expense_public_id: str,
    attachment_public_id: str,
    attachment_service: Optional[AttachmentService] = None,
    match_repo: Optional[ExpenseReceiptMatchRepository] = None,
    expense_service: Optional[ExpenseService] = None,
) -> dict[str, str]:
    expense_uuid = _parse_expense_public_id(expense_public_id)

    att_svc = attachment_service or AttachmentService()
    try:
        attachment, state = await gate_attachment_for_bill_read(
            attachment_public_id, attachment_service=att_svc
        )
    except AttachmentNotReadable as error:
        raise ExpenseReceiptNotFoundError("Attachment") from error

    if state.is_linked:
        raise ExpenseReceiptConflictError("attachment_linked")

    attachment_id = int(attachment.id)
    att_public = str(attachment.public_id)

    repo = match_repo or ExpenseReceiptMatchRepository()
    expense_svc = expense_service or ExpenseService()

    expense_before = await asyncio.to_thread(
        expense_svc.read_by_public_id, str(expense_uuid)
    )
    parent_terminal = (
        expense_before is not None
        and is_terminal(expense_before.status, is_draft=expense_before.is_draft)
    )

    outcome = await asyncio.to_thread(
        repo.attach_first_receipt,
        expense_public_id=expense_uuid,
        attachment_id=attachment_id,
        created_by_user_id=current_user_id.get(),
    )
    code = str(outcome.get("result_code") or "not_found")
    if code == "not_found":
        raise ExpenseReceiptNotFoundError("Expense")
    if code in ("attachment_linked", "expense_has_receipt", "expense_has_no_lines"):
        raise ExpenseReceiptConflictError(code)
    if code != "ok":
        raise RuntimeError(f"unexpected_attach_result:{code}")

    if parent_terminal and expense_before is not None and expense_before.id is not None:
        logger.info(
            "expense.attach_receipt.terminal_add expense_id=%s attachment_id=%s user_id=%s",
            expense_before.id,
            attachment_id,
            current_user_id.get(),
        )

    return {
        "expense_public_id": str(outcome.get("expense_public_id") or expense_uuid),
        "expense_line_item_public_id": str(outcome["expense_line_item_public_id"]),
        "attachment_public_id": str(outcome.get("attachment_public_id") or att_public),
    }
