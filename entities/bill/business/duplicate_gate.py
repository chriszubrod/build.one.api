"""HTTP-layer duplicate gates for bill Create / Submit / Complete (U-723)."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional, Sequence

from entities.attachment.persistence.text_fingerprint_repo import AttachmentTextFingerprintRepository
from entities.bill.business.duplicate_check import (
    DuplicateCheckMode,
    DuplicateCheckResult,
    DuplicateStage,
    PossibleDuplicateRefused,
    assert_duplicate_check_passes_or_raise,
    effective_duplicate_amount,
    is_agent_username,
    record_duplicate_audit,
    run_possible_duplicate_check,
)
from entities.bill.business.service import BillService, DuplicateBillError
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from shared.api.responses import raise_workflow_error

logger = logging.getLogger(__name__)


def _resolve_vendor_id(vendor_public_id: Optional[str]) -> Optional[int]:
    if not vendor_public_id:
        return None
    vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
    return int(vendor.id) if vendor and vendor.id is not None else None


def _resolve_project_id(project_public_id: Optional[str]) -> Optional[int]:
    if not project_public_id:
        return None
    project = ProjectService().read_by_public_id(public_id=project_public_id)
    return int(project.id) if project and project.id is not None else None


def fingerprints_for_attachment_public_id(attachment_public_id: Optional[str]) -> list[str]:
    if not attachment_public_id:
        return []
    from entities.attachment.business.service import AttachmentService

    attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
    if not attachment or attachment.id is None:
        return []
    row = AttachmentTextFingerprintRepository().read_by_attachment_id(int(attachment.id))
    fp = getattr(row, "Fingerprint", None) if row else None
    return [str(fp)] if fp else []


def _fingerprints_from_line_items(line_items: Sequence[Any]) -> list[str]:
    fp_repo = AttachmentTextFingerprintRepository()
    blia_repo = BillLineItemAttachmentRepository()
    out: list[str] = []
    for line in line_items:
        line_id = getattr(line, "id", None)
        if line_id is None:
            continue
        link = blia_repo.read_by_bill_line_item_id(int(line_id))
        att_id = getattr(link, "attachment_id", None) if link else None
        if att_id is None:
            continue
        row = fp_repo.read_by_attachment_id(int(att_id))
        fp = getattr(row, "Fingerprint", None) if row else None
        if fp:
            out.append(str(fp))
    return list(dict.fromkeys(out))


def fingerprints_for_bill_id(
    bill_id: int,
    *,
    line_items: Optional[Sequence[Any]] = None,
) -> list[str]:
    if line_items is None:
        line_items = BillLineItemService().read_by_bill_id(bill_id=bill_id)
    return _fingerprints_from_line_items(line_items)


def assert_hard_duplicate_before_create(
    bill_service: BillService,
    *,
    vendor_public_id: Optional[str],
    bill_number: Optional[str],
    bill_date: str,
    source_email_message_public_id: Optional[str] = None,
) -> None:
    try:
        bill_service.assert_no_hard_duplicate_on_create(
            vendor_public_id=vendor_public_id,
            bill_number=bill_number,
            bill_date=bill_date,
            source_email_message_public_id=source_email_message_public_id,
        )
    except DuplicateBillError as err:
        raise_workflow_error(str(err), "Failed to create bill")


def run_create_possible_duplicate_gate(
    *,
    vendor_public_id: Optional[str],
    bill_date: str,
    total_amount: Optional[Decimal],
    line_amount: Optional[Decimal],
    bill_number: Optional[str],
    attachment_public_id: Optional[str],
    line_project_public_id: Optional[str],
    confirm_not_duplicate: bool,
    confirmed_duplicate_public_ids: Sequence[str],
    username: Optional[str],
    mode: DuplicateCheckMode,
) -> tuple[DuplicateCheckResult, DuplicateCheckMode]:
    if mode == "off":
        return DuplicateCheckResult(), mode

    vendor_id = _resolve_vendor_id(vendor_public_id)
    project_id = _resolve_project_id(line_project_public_id)
    amount = effective_duplicate_amount(
        header_total=total_amount,
        line_amounts=[line_amount],
    )
    fingerprints = fingerprints_for_attachment_public_id(attachment_public_id)
    result = run_possible_duplicate_check(
        vendor_id=vendor_id,
        bill_date=bill_date,
        total_amount=amount,
        bill_number=bill_number,
        text_fingerprints=fingerprints,
        exclude_bill_id=None,
        project_ids=[project_id] if project_id is not None else [],
    )
    assert_duplicate_check_passes_or_raise(
        mode=mode,
        result=result,
        confirm_not_duplicate=confirm_not_duplicate,
        confirmed_duplicate_public_ids=confirmed_duplicate_public_ids,
        agent_caller=is_agent_username(username),
    )
    return result, mode


def run_create_possible_duplicate_gate_advisory(
    *,
    vendor_public_id: Optional[str],
    bill_date: str,
    total_amount: Optional[Decimal],
    line_amount: Optional[Decimal],
    bill_number: Optional[str],
    attachment_public_id: Optional[str],
    line_project_public_id: Optional[str],
    confirm_not_duplicate: bool,
    confirmed_duplicate_public_ids: Sequence[str],
    username: Optional[str],
    mode: DuplicateCheckMode,
) -> tuple[DuplicateCheckResult, DuplicateCheckMode]:
    """Fail-open on infrastructure errors; warn-mode refusals still raise."""
    try:
        return run_create_possible_duplicate_gate(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            total_amount=total_amount,
            line_amount=line_amount,
            bill_number=bill_number,
            attachment_public_id=attachment_public_id,
            line_project_public_id=line_project_public_id,
            confirm_not_duplicate=confirm_not_duplicate,
            confirmed_duplicate_public_ids=confirmed_duplicate_public_ids,
            username=username,
            mode=mode,
        )
    except PossibleDuplicateRefused:
        raise
    except Exception as exc:
        logger.warning(
            "bill_duplicate.create_gate_failed err=%s",
            type(exc).__name__,
        )
        return DuplicateCheckResult(), "off"


def gate_existing_bill_fail_open(
    *,
    bill,
    stage: DuplicateStage,
    mode: DuplicateCheckMode,
    confirm_not_duplicate: bool,
    confirmed_duplicate_public_ids: Sequence[str],
    username: Optional[str],
    actor_user_id_resolver,
) -> None:
    """Submit/Complete gate + audit for an EXISTING bill (U-723).

    Advisory: a warn-mode refusal (PossibleDuplicateRefused) still raises, but any
    other failure - a DB error, the sproc not yet applied - is logged (ids + error
    type only) and the action proceeds. The duplicate check must never block a
    Submit or Complete on its own failure. `actor_user_id_resolver` is called only
    when the check ran, so `off` mode stays free of extra work.
    """
    try:
        result, resolved_mode = run_bill_possible_duplicate_gate_for_bill(
            bill=bill,
            stage=stage,
            mode=mode,
            confirm_not_duplicate=confirm_not_duplicate,
            confirmed_duplicate_public_ids=confirmed_duplicate_public_ids,
            username=username,
        )
    except PossibleDuplicateRefused:
        raise
    except Exception as exc:
        logger.warning(
            "bill_duplicate.%s_gate_failed bill_id=%s err=%s",
            stage,
            getattr(bill, "id", None),
            type(exc).__name__,
        )
        return
    if resolved_mode == "off":
        return
    try:
        record_duplicate_audit(
            bill_id=int(bill.id),
            result=result,
            mode=resolved_mode,
            confirm_not_duplicate=confirm_not_duplicate,
            stage=stage,
            actor_user_id=actor_user_id_resolver(),
        )
    except Exception as exc:
        logger.warning(
            "bill_duplicate.%s_audit_failed bill_id=%s err=%s",
            stage,
            getattr(bill, "id", None),
            type(exc).__name__,
        )


def run_bill_possible_duplicate_gate_for_bill(
    *,
    bill,
    stage: DuplicateStage,
    mode: DuplicateCheckMode,
    confirm_not_duplicate: bool,
    confirmed_duplicate_public_ids: Sequence[str],
    username: Optional[str],
) -> tuple[DuplicateCheckResult, DuplicateCheckMode]:
    if mode == "off":
        return DuplicateCheckResult(), mode

    line_items = BillLineItemService().read_by_bill_id(bill_id=bill.id)
    project_ids = [
        int(li.project_id)
        for li in line_items
        if getattr(li, "project_id", None) is not None
    ]
    amount = effective_duplicate_amount(
        header_total=getattr(bill, "total_amount", None),
        line_amounts=[getattr(li, "amount", None) for li in line_items],
    )
    result = run_possible_duplicate_check(
        vendor_id=getattr(bill, "vendor_id", None),
        bill_date=str(getattr(bill, "bill_date", ""))[:10] if bill.bill_date else None,
        total_amount=amount,
        bill_number=getattr(bill, "bill_number", None),
        text_fingerprints=fingerprints_for_bill_id(int(bill.id), line_items=line_items),
        exclude_bill_id=int(bill.id),
        project_ids=project_ids,
    )
    assert_duplicate_check_passes_or_raise(
        mode=mode,
        result=result,
        confirm_not_duplicate=confirm_not_duplicate,
        confirmed_duplicate_public_ids=confirmed_duplicate_public_ids,
        agent_caller=is_agent_username(username),
    )
    return result, mode


def apply_post_create_duplicate_audit(
    *,
    bill_id: int,
    result: DuplicateCheckResult,
    mode: DuplicateCheckMode,
    confirm_not_duplicate: bool,
    actor_user_id: Optional[int],
) -> Optional[list[dict[str, Any]]]:
    return record_duplicate_audit(
        bill_id=bill_id,
        result=result,
        mode=mode,
        confirm_not_duplicate=confirm_not_duplicate,
        stage="create",
        actor_user_id=actor_user_id,
    )


def apply_post_create_duplicate_audit_fail_open(
    *,
    bill_id: int,
    result: DuplicateCheckResult,
    mode: DuplicateCheckMode,
    confirm_not_duplicate: bool,
    actor_user_id: Optional[int],
) -> Optional[list[dict[str, Any]]]:
    try:
        return apply_post_create_duplicate_audit(
            bill_id=bill_id,
            result=result,
            mode=mode,
            confirm_not_duplicate=confirm_not_duplicate,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        logger.warning(
            "bill_duplicate.post_create_audit_failed bill_id=%s err=%s",
            bill_id,
            type(exc).__name__,
        )
        return None
