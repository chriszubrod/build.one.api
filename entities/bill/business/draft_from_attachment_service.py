"""U-676p: list-page PDF drop → draft bill (read, dedup, create)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional
from uuid import UUID
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

import config
from entities.attachment.business.service import AttachmentService
from entities.bill.business.read_attachment_service import (
    AttachmentNotReadable,
    gate_attachment_for_bill_read,
    read_attachment_link_state,
    run_bill_read_attachment_with_timeout,
)
from entities.bill.business.bill_memo import compose_bill_memo, entered_by_display_name
from entities.bill.business.service import BillService, DuplicateBillError
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.sub_cost_code.business.service import SubCostCodeService
from shared.access import EntityNotAccessibleError

logger = logging.getLogger(__name__)

_hash_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _lock_for_content_hash(file_hash: str) -> asyncio.Lock:
    """In-process serialization per PDF hash (residual cross-worker race remains)."""
    lock = _hash_locks.get(file_hash)
    if lock is None:
        lock = asyncio.Lock()
        _hash_locks[file_hash] = lock
    return lock


def business_today_iso() -> str:
    """Calendar date in BUSINESS_TIMEZONE (default America/Chicago) for empty-read drafts."""
    settings = config.Settings()
    tz_name = (getattr(settings, "business_timezone", None) or "America/Chicago").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("America/Chicago")
    return datetime.now(tz).date().isoformat()


def _format_bill_public_id(value: Any) -> str:
    return str(UUID(str(value)))


def _field_value(fields: dict, key: str) -> Optional[str]:
    block = fields.get(key) or {}
    val = block.get("value")
    if val is None or val == "":
        return None
    return str(val)


def _vendor_public_id(fields: dict) -> Optional[str]:
    vendor = (fields.get("vendor") or {}).get("value")
    if not isinstance(vendor, dict):
        return None
    pid = vendor.get("public_id")
    return str(pid) if pid else None


def _parse_total_amount(fields: dict) -> Optional[Decimal]:
    raw = _field_value(fields, "total_amount")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _resolve_sub_cost_code_id(coding: Optional[dict]) -> Optional[int]:
    if not coding:
        return None
    scc_public = coding.get("sub_cost_code_public_id")
    if not scc_public:
        return None
    scc = SubCostCodeService().read_by_public_id(public_id=str(scc_public))
    if scc is None or scc.id is None:
        return None
    return int(scc.id)


def _response(
    *,
    bill_public_id: str,
    existing: bool,
    existing_reason: Optional[str],
    read_status: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "bill_public_id": _format_bill_public_id(bill_public_id),
        "existing": existing,
        "existing_reason": existing_reason,
        "read_status": read_status,
    }


# Same gate as the web's applyReadAttachment: no memo unless the read produced fields.
_MEMO_READ_STATUSES = frozenset({"ok", "partial"})
_DEFAULT_PAYMENT_TERM_NAME = "due on receipt"


async def _resolve_default_payment_term_public_id() -> Optional[str]:
    """Match New Bill page default: payment term named 'Due on receipt' (case-insensitive)."""
    try:
        from entities.payment_term.business.service import PaymentTermService

        def _lookup() -> Optional[str]:
            # Same predicate as BillCreate.tsx: first term whose lower-cased name matches.
            for term in PaymentTermService().read_all():
                if (term.name or "").lower() == _DEFAULT_PAYMENT_TERM_NAME and term.public_id:
                    return str(term.public_id)
            return None

        return await asyncio.to_thread(_lookup)
    except Exception:
        logger.warning(
            "draft_from_attachment.default_payment_term_lookup_failed",
            exc_info=True,
        )
        return None


async def _resolve_entered_by_for_memo(user_id: Optional[int]) -> str:
    """The dropping user's first + last name, else their username; "" on any failure."""
    if user_id is None:
        return ""
    try:
        from entities.user.business.service import UserService

        user = await asyncio.to_thread(UserService().read_by_id, str(user_id))
    except Exception:
        logger.debug("draft_from_attachment.entered_by_lookup_failed user_id=%s", user_id, exc_info=True)
        return ""
    if user is None:
        return ""
    name = entered_by_display_name(user.firstname, user.lastname, None)
    if name:
        return name
    try:
        from entities.auth.business.service import AuthService

        auth = await asyncio.to_thread(AuthService().read_by_user_id, user_id=int(user_id))
    except Exception:
        logger.debug("draft_from_attachment.entered_by_username_lookup_failed user_id=%s", user_id, exc_info=True)
        return ""
    return entered_by_display_name(None, None, auth.username if auth is not None else None)


async def _create_with_duplicate_handling(
    bill_svc: BillService,
    *,
    create_kwargs: dict[str, Any],
    read_status: str,
    read_produced_bill_date: bool,
    retry_memo: Optional[str] = None,
) -> dict[str, Any]:
    """Create via BillService; dedup is create-driven (no pre-check oracle)."""
    try:
        bill = await asyncio.to_thread(bill_svc.create, **create_kwargs)
    except DuplicateBillError as error:
        dup_id = error.existing_bill_public_id
        existing_bill = None
        if read_produced_bill_date and dup_id:
            try:
                existing_bill = await asyncio.to_thread(
                    bill_svc.read_by_public_id, dup_id
                )
            except EntityNotAccessibleError:
                existing_bill = None
        if existing_bill is not None:
            return _response(
                bill_public_id=str(existing_bill.public_id),
                existing=True,
                existing_reason="same_vendor_number_date",
                read_status=read_status,
            )
        # The memo's DOC# segment is relative to the bill number actually used.
        retry_kwargs = {**create_kwargs, "bill_number": None, "memo": retry_memo}
        bill = await asyncio.to_thread(bill_svc.create, **retry_kwargs)

    return _response(
        bill_public_id=str(bill.public_id),
        existing=False,
        existing_reason=None,
        read_status=read_status,
    )


async def draft_bill_from_attachment(
    *,
    attachment_public_id: str,
    user_id: Optional[int] = None,
    tenant_id: int = 1,
    attachment_service: Optional[AttachmentService] = None,
    blia_service: Optional[BillLineItemAttachmentService] = None,
    bill_service: Optional[BillService] = None,
    read_fn: Callable[..., Any] = run_bill_read_attachment_with_timeout,
) -> dict[str, Any]:
    """
    Resolve attachment → same-PDF dedup → read → create (vendor/number/date dedup via create).

    Same-PDF dedup matches only Status='draft' bills (not submitted/in_review/completed).
    When the read produces no bill_date, the draft is dated today but duplicate detection
    still runs only inside BillService.create — an inaccessible duplicate on that today
    date is retried with bill_number cleared (no existence leak).
    """
    att_svc = attachment_service or AttachmentService()
    blia_svc = blia_service or BillLineItemAttachmentService()
    bill_svc = bill_service or BillService()

    # Ownership first, before the lock or any hash lookup: an attachment the caller does
    # not own gets the same 404 as a missing one and never touches another user's lock.
    attachment, _state = await gate_attachment_for_bill_read(
        attachment_public_id, attachment_service=att_svc
    )
    attachment_id = int(attachment.id)
    file_hash = getattr(attachment, "file_hash", None) or ""
    lock = _lock_for_content_hash(file_hash or f"attachment:{attachment_id}")

    async with lock:
        # The caller's own draft carrying the same PDF bytes (incl. this very attachment
        # on a retry) wins over a new draft.
        if file_hash:
            existing_pid = await asyncio.to_thread(
                blia_svc.read_draft_bill_public_id_by_attachment_hash,
                file_hash=file_hash,
            )
            if existing_pid:
                return _response(
                    bill_public_id=existing_pid,
                    existing=True,
                    existing_reason="same_pdf",
                )

        # Re-read link state under the lock: an attachment linked since the gate (by
        # another request or flow) must never be attached to a second bill.
        state = await asyncio.to_thread(read_attachment_link_state, attachment_id)
        if state.is_linked or not state.is_owned:
            raise AttachmentNotReadable("not_found")
        is_taxpayer = state.is_taxpayer

        read_result = await read_fn(
            attachment_public_id=attachment_public_id,
            intake_source="list_drop",
            pre_gated=(attachment, is_taxpayer),
        )
        read_status = str(read_result.get("status") or "unknown")
        fields = read_result.get("fields") or {}
        coding = read_result.get("coding")

        vendor_public_id = _vendor_public_id(fields)
        bill_number = _field_value(fields, "bill_number")
        memo = retry_memo = None
        if read_status in _MEMO_READ_STATUSES:
            memo_args = dict(
                entered_by=await _resolve_entered_by_for_memo(user_id),
                method="PDF drop",
                long_description=_field_value(fields, "long_description"),
                reference=_field_value(fields, "reference"),
                read_bill_number=bill_number,
            )
            memo = compose_bill_memo(**memo_args, current_bill_number=bill_number)
            retry_memo = compose_bill_memo(**memo_args, current_bill_number=None)
        predicted_bill_date = _field_value(fields, "bill_date")
        bill_date = predicted_bill_date or business_today_iso()
        due_date = bill_date

        total = _parse_total_amount(fields)
        description = _field_value(fields, "description")
        line_project_public_id = (
            str(coding.get("project_public_id"))
            if coding and coding.get("project_public_id")
            else None
        )
        line_sub_cost_code_id = await asyncio.to_thread(_resolve_sub_cost_code_id, coding)
        has_coding = line_sub_cost_code_id is not None or line_project_public_id is not None
        line_coding_prefill_state = "prefilled" if has_coding else None

        line_candidates = {
            "line_description": description,
            "line_quantity": Decimal("1") if total is not None else None,
            "line_rate": total,
            "line_amount": total,
            "line_price": total,
            "line_sub_cost_code_id": line_sub_cost_code_id,
            "line_project_public_id": line_project_public_id,
            "line_coding_prefill_state": line_coding_prefill_state,
        }
        line_kwargs = {k: v for k, v in line_candidates.items() if v is not None}

        payment_term_public_id = await _resolve_default_payment_term_public_id()

        create_kwargs = dict(
            tenant_id=tenant_id,
            user_id=user_id,
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total,
            memo=memo,
            is_draft=True,
            intake_source="list_drop",
            attachment_public_id=attachment_public_id,
            submit_for_review=False,
            **line_kwargs,
        )
        if payment_term_public_id:
            create_kwargs["payment_term_public_id"] = payment_term_public_id

        created = await _create_with_duplicate_handling(
            bill_svc,
            create_kwargs=create_kwargs,
            read_status=read_status,
            read_produced_bill_date=predicted_bill_date is not None,
            retry_memo=retry_memo,
        )
        bill_public_id = created.get("bill_public_id")
        if bill_public_id and not created.get("existing"):
            try:
                from entities.bill.business.duplicate_check import effective_mode_for_path
                from entities.bill.business.duplicate_gate import (
                    apply_post_create_duplicate_audit,
                    run_bill_possible_duplicate_gate_for_bill,
                )

                # U-723: record-only advisory; BILL_DUPLICATE_WARN=off skips every read.
                list_drop_dup_mode = effective_mode_for_path(
                    path="record_path", explicit="record"
                )
                if list_drop_dup_mode != "off":

                    def _record_possible_duplicates():
                        bill = bill_svc.read_by_public_id(bill_public_id)
                        if not bill:
                            return None
                        dup_result, dup_mode = run_bill_possible_duplicate_gate_for_bill(
                            bill=bill,
                            stage="create",
                            mode=list_drop_dup_mode,
                            confirm_not_duplicate=False,
                            confirmed_duplicate_public_ids=[],
                            username=None,
                        )
                        return apply_post_create_duplicate_audit(
                            bill_id=int(bill.id),
                            result=dup_result,
                            mode=dup_mode,
                            confirm_not_duplicate=False,
                            actor_user_id=user_id,
                        )

                    possible = await asyncio.to_thread(_record_possible_duplicates)
                    if possible:
                        created["possible_duplicates"] = possible
            except Exception as error:
                logger.warning(
                    "bill_duplicate.record_after_list_drop_failed err=%s",
                    type(error).__name__,
                )
        return created
