"""U-179 — invoice draw push orchestration (halt-on-each-step, no QBO push)."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Callable, Optional

from fastapi import HTTPException

from shared.env_flags import is_truthy

from entities.invoice.business.audit import InvoiceDrawAuditService
from entities.invoice.business.reconciliation import InvoiceReconciliationService
from entities.invoice.business.service import InvoiceService
from entities.invoice.persistence.repo import InvoiceRepository
from entities.invoice_line_item.business.service import InvoiceLineItemService

_PUSH_NOTE = (
    "Box/MS steps record enqueue (or MS direct Graph stamp) only — external "
    "drain verification (box.Outbox complete, KI-46 col-N values) is deferred to U3b"
)

_DETAILS_RERUN_NOTE = (
    "Source DETAILS inserts enqueued — re-run push-draw after MS/Box drains (~30–60s)"
)

_PARENT_KIND_BY_SOURCE_TYPE = {
    "BillLineItem": "Bill",
    "ExpenseLineItem": "Expense",
    "BillCreditLineItem": "BillCredit",
}


def build_details_insert_plan(
    invoice_line_items: list[dict],
    missing_source_keys: set[str],
) -> list[tuple[str, int]]:
    """
    Dedupe (parent entity kind, parent entity id) pairs for DETAILS row inserts.

    ``invoice_line_items`` must already carry ``source_type``, ``source_line_public_id``,
    and ``parent_entity_id`` (internal Bill / Expense / BillCredit id).
    """
    normalized_missing = {k.lower() for k in missing_source_keys if k}
    seen: set[tuple[str, int]] = set()
    plan: list[tuple[str, int]] = []
    for li in invoice_line_items:
        spid = (li.get("source_line_public_id") or "").lower()
        if not spid or spid not in normalized_missing:
            continue
        parent_kind = _PARENT_KIND_BY_SOURCE_TYPE.get(li.get("source_type") or "")
        parent_id = li.get("parent_entity_id")
        if not parent_kind or parent_id is None:
            continue
        key = (parent_kind, int(parent_id))
        if key in seen:
            continue
        seen.add(key)
        plan.append(key)
    return plan


def source_public_ids_missing_from_details(db_only: list[dict]) -> set[str]:
    """Source line public ids for reconcile ``db_only`` rows (deterministic via ``source_public_ids``)."""
    if not db_only:
        return set()
    return {
        pid.lower()
        for entry in db_only
        for pid in (entry.get("source_public_ids") or [])
        if pid
    }


def _missing_billcredit_source_public_ids(
    invoice_line_items: list[dict],
    missing_source_keys: set[str],
) -> list[str]:
    normalized_missing = {k.lower() for k in missing_source_keys if k}
    pids: list[str] = []
    for li in invoice_line_items:
        spid = (li.get("source_line_public_id") or "").lower()
        if not spid or spid not in normalized_missing:
            continue
        if li.get("source_type") == "BillCreditLineItem":
            pids.append(spid)
    return sorted(set(pids))


def _unresolved_missing_source_public_ids(
    invoice_line_items: list[dict],
    missing_source_keys: set[str],
    insert_plan: list[tuple[str, int]],
) -> list[str]:
    normalized_missing = {k.lower() for k in missing_source_keys if k}
    if not normalized_missing:
        return []
    planned_parents = set(insert_plan)
    unresolved: set[str] = set()
    resolved: set[str] = set()
    for li in invoice_line_items:
        spid = (li.get("source_line_public_id") or "").lower()
        if not spid or spid not in normalized_missing:
            continue
        parent_kind = _PARENT_KIND_BY_SOURCE_TYPE.get(li.get("source_type") or "")
        parent_id = li.get("parent_entity_id")
        if not parent_kind or parent_id is None:
            unresolved.add(spid)
            continue
        parent_key = (parent_kind, int(parent_id))
        if parent_key not in planned_parents:
            unresolved.add(spid)
        else:
            resolved.add(spid)
    for spid in normalized_missing:
        if spid not in resolved and spid not in unresolved:
            unresolved.add(spid)
    return sorted(unresolved)


def _filter_parent_lines_for_project(
    parent_kind: str,
    line_items: list,
    project_id: int,
) -> list:
    """Limit DETAILS sync to lines on the invoice's project (Bill writers do not self-filter)."""
    if parent_kind not in ("Bill", "Expense"):
        return line_items
    return [li for li in line_items if getattr(li, "project_id", None) == project_id]


def _attach_parent_entity_ids(enriched_line_items: list[dict]) -> list[dict]:
    from entities.bill_credit_line_item.business.service import BillCreditLineItemService
    from entities.bill_line_item.business.service import BillLineItemService
    from entities.expense_line_item.business.service import ExpenseLineItemService

    bli_svc = BillLineItemService()
    eli_svc = ExpenseLineItemService()
    bcli_svc = BillCreditLineItemService()

    resolved: list[dict] = []
    for li in enriched_line_items:
        row = dict(li)
        st = row.get("source_type")
        if st == "BillLineItem" and row.get("bill_line_item_id"):
            bli = bli_svc.read_by_id(id=int(row["bill_line_item_id"]))
            if bli:
                row["parent_entity_id"] = bli.bill_id
        elif st == "ExpenseLineItem" and row.get("expense_line_item_id"):
            eli = eli_svc.read_by_id(id=int(row["expense_line_item_id"]))
            if eli:
                row["parent_entity_id"] = eli.expense_id
        elif st == "BillCreditLineItem" and row.get("bill_credit_line_item_id"):
            bcli = bcli_svc.read_by_id(id=int(row["bill_credit_line_item_id"]))
            if bcli:
                row["parent_entity_id"] = bcli.bill_credit_id
        resolved.append(row)
    return resolved


def _ki16_ensure_price_on_parent_lines(parent_kind: str, parent_line_items: list) -> None:
    """KI-16: NULL Price → Amount before single-entity Excel enqueue (Bill / Expense)."""
    if parent_kind == "Bill":
        from entities.bill_line_item.business.service import BillLineItemService

        svc = BillLineItemService()
    elif parent_kind == "Expense":
        from entities.expense_line_item.business.service import ExpenseLineItemService

        svc = ExpenseLineItemService()
    else:
        return

    for li in parent_line_items:
        if li.price is not None or li.amount is None:
            continue
        svc.update_by_public_id(
            public_id=str(li.public_id),
            row_version=li.row_version,
            price=float(li.amount),
        )


def _enqueue_parent_details_sync(
    parent_kind: str,
    parent_entity_id: int,
    project_id: int,
) -> dict:
    """MS DETAILS insert + Box mirror for one parent entity (Step 7b writers).

    Deferred (P2): BillCredit writer needs column-Z idempotency before auto-insert;
    rapid re-run before ms.Outbox drain should check pending outbox (bounded today by
    col-Z on Bill/Expense + ``_DETAILS_RERUN_NOTE``).
    """
    if parent_kind == "Bill":
        from entities.bill.business.service import BillService
        from entities.bill_line_item.business.service import BillLineItemService

        entity_svc = BillService()
        li_svc = BillLineItemService()
        entity = entity_svc.read_by_id(id=parent_entity_id)
        if not entity:
            raise ValueError(f"bill_not_found:{parent_entity_id}")
        all_lines = li_svc.read_by_bill_id(bill_id=parent_entity_id)
        line_items = _filter_parent_lines_for_project(parent_kind, all_lines, project_id)
        _ki16_ensure_price_on_parent_lines(parent_kind, line_items)
        all_lines = li_svc.read_by_bill_id(bill_id=parent_entity_id)
        line_items = _filter_parent_lines_for_project(parent_kind, all_lines, project_id)
        excel_result = entity_svc.sync_to_excel_workbook(
            bill=entity,
            line_items=line_items,
            project_id=project_id,
        )
        entity_svc._enqueue_box_excel(bill=entity, project_id=project_id)
        return excel_result

    if parent_kind == "Expense":
        from entities.expense.business.service import ExpenseService
        from entities.expense_line_item.business.service import ExpenseLineItemService

        entity_svc = ExpenseService()
        li_svc = ExpenseLineItemService()
        entity = entity_svc.read_by_id(id=parent_entity_id)
        if not entity:
            raise ValueError(f"expense_not_found:{parent_entity_id}")
        all_lines = li_svc.read_by_expense_id(expense_id=parent_entity_id)
        line_items = _filter_parent_lines_for_project(parent_kind, all_lines, project_id)
        _ki16_ensure_price_on_parent_lines(parent_kind, line_items)
        all_lines = li_svc.read_by_expense_id(expense_id=parent_entity_id)
        line_items = _filter_parent_lines_for_project(parent_kind, all_lines, project_id)
        excel_result = entity_svc.sync_to_excel_workbook(
            expense=entity,
            line_items=line_items,
            project_id=project_id,
        )
        entity_svc._enqueue_box_excel(expense=entity, project_id=project_id)
        return excel_result

    if parent_kind == "BillCredit":
        from entities.bill_credit.business.complete_service import BillCreditCompleteService
        from entities.bill_credit.business.service import BillCreditService
        from entities.bill_credit_line_item.business.service import BillCreditLineItemService

        entity_svc = BillCreditCompleteService()
        read_svc = BillCreditService()
        li_svc = BillCreditLineItemService()
        entity = read_svc.read_by_id(id=parent_entity_id)
        if not entity:
            raise ValueError(f"bill_credit_not_found:{parent_entity_id}")
        line_items = li_svc.read_by_bill_credit_id(bill_credit_id=parent_entity_id)
        excel_result = entity_svc.sync_to_excel_workbook(
            bill_credit=entity,
            line_items=line_items,
            project_id=project_id,
        )
        entity_svc._enqueue_box_excel(bill_credit=entity, project_id=project_id)
        return excel_result

    raise ValueError(f"unsupported_parent_kind:{parent_kind}")


def _env_writes_enabled(value: Optional[str]) -> bool:
    return is_truthy(value)


def writes_enabled(ms_value: Optional[str], box_value: Optional[str]) -> bool:
    return _env_writes_enabled(ms_value) and _env_writes_enabled(box_value)


def evaluate_gates(
    ms_writes: Optional[str],
    box_writes: Optional[str],
    audit_verdict: str,
    force: bool,
) -> str:
    """
    Pure gate evaluation for draw push.

    Returns 'proceed', 'writes_disabled', or 'audit_not_clear'.
    """
    if not (_env_writes_enabled(ms_writes) and _env_writes_enabled(box_writes)):
        return "writes_disabled"
    if audit_verdict != "clear" and not force:
        return "audit_not_clear"
    return "proceed"


def assemble_draw_matrix(
    counts: dict[str, Any],
    packet_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the DB-side invariant matrix (Step 10 subset) with exact Decimal money checks.
    """
    qbo_lines = int(counts.get("qbo_line_count") or 0)
    dbo_lines = int(counts.get("dbo_line_count") or 0)
    sourced = int(counts.get("sourced_line_count") or 0)
    billed = int(counts.get("billed_source_count") or 0)

    qbo_total = counts.get("qbo_total_amt")
    dbo_total = counts.get("dbo_total_amount")
    dbo_sum = counts.get("dbo_line_sum")

    qbo_total_d = Decimal(str(qbo_total)) if qbo_total is not None else None
    dbo_total_d = Decimal(str(dbo_total)) if dbo_total is not None else None
    dbo_sum_d = Decimal(str(dbo_sum)) if dbo_sum is not None else None

    skipped = int(packet_result.get("skipped") or 0)
    page_count = int(packet_result.get("page_count") or 0)

    def _row(check: str, expect: Any, got: Any, passed: bool) -> dict:
        return {"check": check, "expect": expect, "got": got, "pass": passed}

    rows = [
        _row(
            "QBO lines == dbo ILIs",
            dbo_lines,
            qbo_lines,
            qbo_lines == dbo_lines,
        ),
        _row(
            "QBO TotalAmt == dbo TotalAmount",
            dbo_total_d,
            qbo_total_d,
            qbo_total_d is not None
            and dbo_total_d is not None
            and qbo_total_d == dbo_total_d,
        ),
        _row(
            "dbo TotalAmount == SUM(ILI.Amount)",
            dbo_total_d,
            dbo_sum_d,
            dbo_total_d is not None
            and dbo_sum_d is not None
            and dbo_total_d == dbo_sum_d,
        ),
        _row(
            "Sourced lines == IsBilled sources",
            sourced,
            billed,
            sourced == billed,
        ),
        _row("Packet skipped == 0", 0, skipped, skipped == 0),
        _row("Packet page_count >= 1", ">= 1", page_count, page_count >= 1),
    ]

    return {"rows": rows, "all_pass": all(r["pass"] for r in rows)}


class InvoiceDrawPushService:
    """Orchestrate draw push Steps 5 / 7d / 8 / 9 with halt-on-first-failure."""

    def __init__(
        self,
        invoice_service: Optional[InvoiceService] = None,
        invoice_repo: Optional[InvoiceRepository] = None,
        line_item_service: Optional[InvoiceLineItemService] = None,
        reconciliation_service: Optional[InvoiceReconciliationService] = None,
        audit_service: Optional[InvoiceDrawAuditService] = None,
        generate_packet_fn: Optional[Callable[[str], dict]] = None,
        worksheet_reconcile_service: Optional[Any] = None,
        excel_connector: Optional[Any] = None,
    ):
        self.invoice_service = invoice_service or InvoiceService()
        self.invoice_repo = invoice_repo or InvoiceRepository()
        self.line_item_service = line_item_service or InvoiceLineItemService()
        self.reconciliation_service = reconciliation_service or InvoiceReconciliationService(
            invoice_repo=self.invoice_repo,
            invoice_service=self.invoice_service,
        )
        self.audit_service = audit_service or InvoiceDrawAuditService(
            invoice_service=self.invoice_service,
            invoice_repo=self.invoice_repo,
            reconciliation_service=self.reconciliation_service,
        )
        self._generate_packet_fn = generate_packet_fn
        self._worksheet_reconcile_service = worksheet_reconcile_service
        self._excel_connector = excel_connector

    def _get_excel_connector(self):
        if self._excel_connector is None:
            from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import (
                DriveItemProjectExcelConnector,
            )

            self._excel_connector = DriveItemProjectExcelConnector()
        return self._excel_connector

    def push_draw(self, invoice_public_id: str, *, force: bool = False) -> dict:
        steps: list[dict] = []
        audit_verdict: Optional[str] = None
        packet_data: dict[str, Any] = {}
        already_tagged: list[dict] = []
        local_only = False
        skipped_external: list[str] = []

        def _halt(reason: str, **extra: Any) -> dict:
            body: dict[str, Any] = {
                "status": "halt",
                "reason": reason,
                "steps": steps,
                "matrix": extra.get("matrix"),
                "audit_verdict": audit_verdict,
                "already_tagged": already_tagged,
                "local_only": local_only,
                "skipped_external": list(skipped_external),
                "note": _PUSH_NOTE,
            }
            body.update({k: v for k, v in extra.items() if k != "matrix"})
            return body

        def _record_box_line_pdfs_step(box_line_result: dict) -> None:
            if not box_line_result.get("success"):
                steps.append({"step": "box_line_pdfs", "status": "halt", "result": box_line_result})
                return
            enqueued = int(box_line_result.get("enqueued") or 0)
            box_line_step = {
                "step": "box_line_pdfs",
                "enqueued": enqueued,
                "skipped": int(box_line_result.get("skipped") or 0),
                "reason": box_line_result.get("reason"),
            }
            if enqueued > 0:
                box_line_step["status"] = "ok"
            else:
                box_line_step["status"] = "skipped"
            steps.append(box_line_step)

        # a. Read invoice
        invoice = self.invoice_service.read_by_public_id(public_id=invoice_public_id)
        if not invoice:
            raise ValueError("invoice_not_found")
        if getattr(invoice, "is_draft", False):
            steps.append(
                {"step": "read_invoice", "status": "halt", "is_draft": True},
            )
            return _halt("invoice_is_draft")
        steps.append({"step": "read_invoice", "status": "ok", "invoice_id": invoice.id})

        # b. Write gates (zero writes if disabled)
        if not writes_enabled(os.getenv("ALLOW_MS_WRITES"), os.getenv("ALLOW_BOX_WRITES")):
            steps.append({"step": "write_gates", "status": "halt", "gate": "writes_disabled"})
            return _halt("writes_disabled")

        # c. Audit gate
        audit_report = self.audit_service.audit(invoice_public_id)
        audit_verdict = audit_report.get("verdict")
        gate = evaluate_gates(
            os.getenv("ALLOW_MS_WRITES"),
            os.getenv("ALLOW_BOX_WRITES"),
            audit_verdict=audit_verdict or "halt",
            force=force,
        )
        steps.append({"step": "audit", "status": "ok", "verdict": audit_verdict})
        if gate == "audit_not_clear":
            steps.append({"step": "audit_gate", "status": "halt"})
            return _halt("audit_not_clear", audit=audit_report)

        project_id = invoice.project_id

        # d. apply_links
        try:
            link_result = self.reconciliation_service.apply_links(invoice_public_id)
            steps.append(
                {
                    "step": "apply_links",
                    "status": "ok",
                    "applied_count": link_result.get("summary", {}).get("applied_count"),
                }
            )
        except Exception as exc:
            steps.append({"step": "apply_links", "status": "halt", "error": str(exc)})
            return _halt("apply_links_failed")

        line_items = self.line_item_service.read_by_invoice_id(invoice_id=invoice.id)

        # e. mark IsBilled
        try:
            for li in line_items:
                self.invoice_service._mark_source_as_billed(li)
            steps.append({"step": "mark_is_billed", "status": "ok", "line_count": len(line_items)})
        except Exception as exc:
            steps.append({"step": "mark_is_billed", "status": "halt", "error": str(exc)})
            return _halt("mark_is_billed_failed")

        draw_counts = self.invoice_repo.compute_invoice_draw_matrix(invoice_id=invoice.id)

        # f. generate packet (pre-check: readable attachment blobs on source lines)
        missing_blob_lines = self.invoice_repo.read_source_lines_missing_readable_blob(
            invoice_id=invoice.id,
        )
        if missing_blob_lines:
            steps.append(
                {
                    "step": "missing_attachment_blob",
                    "status": "halt",
                    "line_count": len(missing_blob_lines),
                }
            )
            return _halt("missing_attachment_blob", lines=missing_blob_lines)
        # Residual: non-NULL BlobUrl that 404s at fetch time is only caught post-gen below.

        try:
            if self._generate_packet_fn is not None:
                packet_resp = self._generate_packet_fn(invoice_public_id)
            else:
                from entities.invoice.api.router import _generate_invoice_packet

                packet_resp = _generate_invoice_packet(invoice_public_id)
            packet_data = packet_resp.get("data") or {}
            skipped = int(packet_data.get("skipped") or 0)
            page_count = int(packet_data.get("page_count") or 0)
            if skipped > 0 or page_count < 1:
                steps.append(
                    {
                        "step": "generate_packet",
                        "status": "halt",
                        "skipped": skipped,
                        "page_count": page_count,
                    }
                )
                return _halt("packet_incomplete", packet=packet_data)
            steps.append(
                {
                    "step": "generate_packet",
                    "status": "ok",
                    "page_count": page_count,
                    "skipped": skipped,
                }
            )
        except Exception as exc:
            steps.append({"step": "generate_packet", "status": "halt", "error": str(exc)})
            return _halt("generate_packet_failed")

        # g. DETAILS completeness (U-182) then col-H DRAW stamp (MS sync + Box enqueue)
        if project_id:
            linked = self._get_excel_connector().get_excel_for_project(project_id=project_id)
            if not linked:
                local_only = True
                skipped_external.extend(
                    ["sharepoint_details_stamp", "sharepoint_upload"],
                )
                steps.append(
                    {
                        "step": "local_only",
                        "status": "ok",
                        "reason": "no_sharepoint_excel_mapping",
                    }
                )

        if project_id and not local_only:
            from entities.invoice.business.enrichment import enrich_line_items

            reconcile_svc = self._worksheet_reconcile_service
            if reconcile_svc is None:
                from entities.invoice.business.worksheet_reconcile import WorksheetReconcileService

                reconcile_svc = WorksheetReconcileService()

            try:
                reconcile_result = reconcile_svc.reconcile(invoice_public_id)
                already_tagged = list(reconcile_result.get("already_tagged") or [])
                steps.append(
                    {
                        "step": "worksheet_reconcile",
                        "status": "ok",
                        "db_only_count": len(reconcile_result.get("db_only") or []),
                        "already_tagged_count": len(already_tagged),
                    }
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                steps.append(
                    {
                        "step": "worksheet_reconcile",
                        "status": "halt",
                        "error": detail,
                    }
                )
                if exc.status_code == 400 and "budget tracker" in detail.lower():
                    return _halt("project_not_mapped_for_details", reason=detail)
                return _halt("worksheet_reconcile_failed")
            except Exception as exc:
                steps.append(
                    {"step": "worksheet_reconcile", "status": "halt", "error": str(exc)},
                )
                return _halt("worksheet_reconcile_failed")

            enriched = enrich_line_items(line_items)
            plan_lines = _attach_parent_entity_ids(enriched)
            missing_keys = source_public_ids_missing_from_details(
                reconcile_result.get("db_only") or [],
            )

            billcredit_pids = _missing_billcredit_source_public_ids(plan_lines, missing_keys)
            if billcredit_pids:
                steps.append(
                    {
                        "step": "details_source_inserts",
                        "status": "halt",
                        "reason": "billcredit_manual_insert_required",
                        "source_public_ids": billcredit_pids,
                    }
                )
                return _halt(
                    "billcredit_manual_insert_required",
                    source_public_ids=billcredit_pids,
                )

            insert_plan = build_details_insert_plan(plan_lines, missing_keys)
            unresolved_pids = _unresolved_missing_source_public_ids(
                plan_lines,
                missing_keys,
                insert_plan,
            )
            if unresolved_pids:
                steps.append(
                    {
                        "step": "details_source_inserts",
                        "status": "halt",
                        "reason": "details_unresolved_missing",
                        "unresolved_source_public_ids": unresolved_pids,
                    }
                )
                return _halt(
                    "details_unresolved_missing",
                    unresolved_source_public_ids=unresolved_pids,
                )

            if insert_plan:
                enqueued_parents: list[dict] = []
                try:
                    for parent_kind, parent_id in insert_plan:
                        excel_result = _enqueue_parent_details_sync(
                            parent_kind,
                            parent_id,
                            project_id,
                        )
                        enqueued_parents.append(
                            {
                                "source_type": parent_kind,
                                "parent_entity_id": parent_id,
                                "excel_sync": excel_result,
                            }
                        )
                except Exception as exc:
                    steps.append(
                        {
                            "step": "details_source_inserts",
                            "status": "halt",
                            "error": str(exc),
                            "enqueued": enqueued_parents,
                        }
                    )
                    return _halt("details_inserts_failed", enqueued=enqueued_parents)

                steps.append(
                    {
                        "step": "details_source_inserts",
                        "status": "enqueued",
                        "enqueued": enqueued_parents,
                    }
                )
                return _halt(
                    "details_inserts_enqueued",
                    enqueued=enqueued_parents,
                    rerun_note=_DETAILS_RERUN_NOTE,
                )

            sourced_count = int(draw_counts.get("sourced_line_count") or 0)
            excel_result = self.invoice_service.sync_to_excel_workbook(
                invoice=invoice,
                line_items=line_items,
                project_id=project_id,
            )
            synced_count = int(excel_result.get("synced_count") or 0)
            stamp_step = {
                "step": "excel_draw_stamp",
                "synced_count": synced_count,
                "sourced_count": sourced_count,
            }
            if not excel_result.get("success"):
                msg = str(excel_result.get("message") or "")
                stamp_step["status"] = "halt"
                stamp_step["result"] = excel_result
                steps.append(stamp_step)
                if "not linked" in msg.lower():
                    return _halt(
                        "project_not_mapped_for_details",
                        reason=msg,
                    )
                return _halt("excel_draw_stamp_failed")
            if synced_count == 0 and sourced_count > 0:
                stamp_step["status"] = "halt"
                steps.append(stamp_step)
                return _halt("details_stamp_incomplete")
            stamp_step["status"] = "ok"
            steps.append(stamp_step)
            try:
                self.invoice_service._enqueue_box_excel(invoice=invoice, project_id=project_id)
                steps.append(
                    {
                        "step": "box_excel_stamp_enqueue",
                        "status": "enqueued",
                        "note": "failure_isolated_result_not_surfaced",
                    }
                )
            except Exception as exc:
                steps.append({"step": "box_excel_stamp_enqueue", "status": "halt", "error": str(exc)})
                return _halt("box_excel_stamp_failed")
        elif not project_id:
            steps.append({"step": "excel_draw_stamp", "status": "skipped", "reason": "no_project_id"})

        # h. uploads (SharePoint skipped in local-only draw mode)
        if not local_only:
            sp_result = self.invoice_service._upload_to_sharepoint(
                invoice=invoice, line_items=line_items
            )
            if not sp_result.get("success") or sp_result.get("errors"):
                steps.append({"step": "sharepoint_upload", "status": "halt", "result": sp_result})
                return _halt("sharepoint_upload_failed")
            steps.append(
                {
                    "step": "sharepoint_upload",
                    "status": "ok",
                    "synced_count": sp_result.get("synced_count"),
                }
            )

        box_line_result = self.invoice_service._enqueue_box_line_pdfs(
            invoice=invoice, line_items=line_items
        )
        if not box_line_result.get("success"):
            _record_box_line_pdfs_step(box_line_result)
            return _halt("box_line_pdfs_failed")
        _record_box_line_pdfs_step(box_line_result)
        if local_only and int(box_line_result.get("enqueued") or 0) == 0:
            if box_line_result.get("reason") == "unmapped_project":
                skipped_external.append("box")

        # i. DB-side invariant matrix
        matrix = assemble_draw_matrix(draw_counts, packet_data)
        steps.append({"step": "draw_matrix", "status": "ok", "all_pass": matrix["all_pass"]})

        if not matrix["all_pass"]:
            return _halt("matrix_invariant_failed", matrix=matrix)

        return {
            "status": "pushed",
            "steps": steps,
            "matrix": matrix,
            "audit_verdict": audit_verdict,
            "already_tagged": already_tagged,
            "local_only": local_only,
            "skipped_external": list(skipped_external),
            "note": _PUSH_NOTE,
        }
