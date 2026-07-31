"""U-179 — invoice draw push orchestration (halt-on-each-step, no QBO push)."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Callable, Optional

from entities.invoice.business.audit import InvoiceDrawAuditService
from entities.invoice.business.reconciliation import InvoiceReconciliationService
from entities.invoice.business.service import InvoiceService
from entities.invoice.persistence.repo import InvoiceRepository
from entities.invoice_line_item.business.service import InvoiceLineItemService

_PUSH_NOTE = (
    "Box/MS steps record enqueue (or MS direct Graph stamp) only — external "
    "drain verification (box.Outbox complete, KI-46 col-N values) is deferred to U3b"
)


def _env_writes_enabled(value: Optional[str]) -> bool:
    return (value or "").strip().lower() == "true"


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

    def push_draw(self, invoice_public_id: str, *, force: bool = False) -> dict:
        steps: list[dict] = []
        audit_verdict: Optional[str] = None
        packet_data: dict[str, Any] = {}

        def _halt(reason: str, **extra: Any) -> dict:
            body: dict[str, Any] = {
                "status": "halt",
                "reason": reason,
                "steps": steps,
                "matrix": extra.get("matrix"),
                "audit_verdict": audit_verdict,
                "note": _PUSH_NOTE,
            }
            body.update({k: v for k, v in extra.items() if k != "matrix"})
            return body

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

        # g. col-H DRAW stamp (MS sync + Box enqueue)
        if project_id:
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
        else:
            steps.append({"step": "excel_draw_stamp", "status": "skipped", "reason": "no_project_id"})

        # h. uploads
        sp_result = self.invoice_service._upload_to_sharepoint(invoice=invoice, line_items=line_items)
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
            steps.append({"step": "box_line_pdfs", "status": "halt", "result": box_line_result})
            return _halt("box_line_pdfs_failed")
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
            "note": _PUSH_NOTE,
        }
