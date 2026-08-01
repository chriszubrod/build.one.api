"""Box DETAILS draw column-N value read (KI-46) + draw_requests folder guard (HP2-11)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.service import InvoiceService
from entities.invoice.business.worksheet_reconcile import _tag_matches
from integrations.box.base.client import BoxHttpClient
from integrations.box.excel.persistence.repo import BoxProjectWorkbookRepository

logger = logging.getLogger(__name__)

_DEFAULT_TOLERANCE = Decimal("0.01")


def is_draw_requests_folder(name: Optional[str]) -> bool:
    """HP2-11: live Box folder name must look like a draw-requests destination."""
    return "draw request" in (name or "").lower()


def _cell_draw_tag(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _parse_money_cell(val: Any) -> Decimal:
    if val is None or val == "" or val == "—":
        return Decimal("0")
    try:
        cleaned = str(val).replace(",", "").replace("$", "").strip()
        if not cleaned:
            return Decimal("0")
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def sum_box_draw_column(
    rows: list[list],
    draw_number: str,
    *,
    h_idx: int = 7,
    n_idx: int = 13,
) -> dict:
    """
    Sum column N (billable amount) for rows whose column H draw tag matches
    `draw_number` (numeric-tolerant — Box/Excel may store H as str or number).
    """
    draw_key = str(draw_number or "").strip()
    total = Decimal("0")
    tagged = 0
    for row in rows:
        if h_idx >= len(row):
            continue
        tag = _cell_draw_tag(row[h_idx])
        if not _tag_matches(tag, draw_key):
            continue
        if n_idx < len(row):
            total += _parse_money_cell(row[n_idx])
        tagged += 1
    return {"draw_total": total, "tagged_row_count": tagged}


def compare_box_draw(
    box_total: Decimal,
    expected_total: Decimal,
    *,
    tolerance: Decimal = _DEFAULT_TOLERANCE,
) -> dict:
    diff = box_total - expected_total
    match = abs(diff) <= tolerance
    return {
        "match": match,
        "box_total": str(box_total),
        "expected_total": str(expected_total),
        "difference": str(diff),
    }


def _worksheet_rows_from_bytes(file_bytes: bytes, worksheet_name: str) -> list[list]:
    from integrations.box.excel.business.workbook_editor import (
        _load_workbook_resilient,
        _sanitize_workbook_bytes,
    )

    payload = _sanitize_workbook_bytes(file_bytes)
    wb = _load_workbook_resilient(payload, data_only=True, read_only=True)
    try:
        if worksheet_name not in wb.sheetnames:
            raise ValueError(f"worksheet_not_found:{worksheet_name}")
        ws = wb[worksheet_name]
        return [list(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _read_sharepoint_worksheet_rows(project_id: int) -> dict:
    """SharePoint Budget Tracker read — same path as WorksheetReconcileService."""
    from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
    from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import (
        DriveItemProjectExcelConnector,
    )
    from integrations.ms.sharepoint.external.client import get_excel_used_range_values

    linked_excel = DriveItemProjectExcelConnector().get_excel_for_project(project_id=project_id)
    if not linked_excel:
        return {"ok": False, "reason": "no_sharepoint_workbook"}

    worksheet_name = linked_excel.get("worksheet_name")
    item_id_graph = linked_excel.get("item_id")
    ms_drive_id = linked_excel.get("ms_drive_id")

    if not item_id_graph or not ms_drive_id:
        return {"ok": False, "reason": "no_sharepoint_workbook"}

    drive = MsDriveRepository().read_by_id(ms_drive_id)
    if not drive:
        return {"ok": False, "reason": "sharepoint_drive_not_found"}

    ws_result = get_excel_used_range_values(drive.drive_id, item_id_graph, worksheet_name)
    if ws_result.get("status_code") != 200:
        msg = ws_result.get("message") or "sharepoint_read_failed"
        return {"ok": False, "reason": str(msg)}

    ws_values = ws_result.get("range", {}).get("values") or []
    return {"ok": True, "rows": ws_values}


class BoxDrawVerifyService:
    """READ-ONLY Box vs SharePoint DETAILS col-N draw total (KI-46)."""

    def verify(self, invoice_public_id: str) -> dict:
        invoice = InvoiceService().read_by_public_id(public_id=invoice_public_id)
        if not invoice:
            raise ValueError("invoice_not_found")
        if not invoice.project_id:
            return {"verified": False, "reason": "no_project"}

        draw_number = invoice.invoice_number or str(invoice.public_id)

        try:
            sp_read = _read_sharepoint_worksheet_rows(invoice.project_id)
        except Exception as exc:
            logger.warning(
                "box.draw_verify.sharepoint_read_failed invoice=%s: %s",
                invoice_public_id,
                exc,
            )
            return {"verified": False, "reason": str(exc)}

        if not sp_read.get("ok"):
            return {"verified": False, "reason": sp_read.get("reason", "no_sharepoint_workbook")}

        sp_rows = sp_read["rows"]

        try:
            mapping = BoxProjectWorkbookRepository().read_by_project_id(invoice.project_id)
        except Exception as exc:
            logger.warning(
                "box.draw_verify.workbook_mapping_failed invoice=%s: %s",
                invoice_public_id,
                exc,
            )
            return {"verified": False, "reason": f"workbook_mapping: {exc}"}

        if not mapping or not mapping.get("box_file_id"):
            return {"verified": False, "reason": "no_box_workbook"}

        worksheet_name = mapping.get("worksheet_name") or "DETAILS"
        box_file_id = mapping["box_file_id"]

        try:
            with BoxHttpClient() as client:
                file_bytes = client.download_file(box_file_id)
            box_rows = _worksheet_rows_from_bytes(file_bytes, worksheet_name)
        except Exception as exc:
            logger.warning(
                "box.draw_verify.read_failed invoice=%s file=%s: %s",
                invoice_public_id,
                box_file_id,
                exc,
            )
            return {"verified": False, "reason": str(exc)}

        box = sum_box_draw_column(box_rows, draw_number)
        sp = sum_box_draw_column(sp_rows, draw_number)
        compared = compare_box_draw(box["draw_total"], sp["draw_total"])
        return {
            "verified": True,
            "box_draw_total": compared["box_total"],
            "sharepoint_draw_total": compared["expected_total"],
            "match": compared["match"],
            "difference": compared["difference"],
            "box_tagged_row_count": box["tagged_row_count"],
            "sp_tagged_row_count": sp["tagged_row_count"],
            "draw_number": draw_number,
            "worksheet_name": worksheet_name,
        }
