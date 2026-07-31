"""Budget Tracker worksheet reconcile (U-180) — SharePoint DETAILS compare."""

from __future__ import annotations

from collections import defaultdict
from fastapi import HTTPException

_KNOWN_HEADERS = {
    "DRAW REQUEST DATE": "draw_request_date",
    "DATE": "date",
    "PAYABLE TO": "payable_to",
    "INVOICE #": "invoice_num",
    "DESCRIPTION": "description",
    "SOURCE": "source",
    "CK": "source",
    "BILLABLE": "billable",
    "SUB COST CODE": "sub_cost_code",
}

_SOURCE_LABEL = {
    "BillLineItem": "Bill",
    "ExpenseLineItem": "Expense",
    "BillCreditLineItem": "Expense",
}


def _cell(row, col_map, key):
    idx = col_map.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _parse_amt(val):
    if val is None or val == "" or val == "—":
        return 0.0
    try:
        return float(str(val).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return 0.0


def _cell_str(row, col_map, key):
    v = _cell(row, col_map, key)
    return str(v).strip() if v is not None and v != "" else ""


def _has_value(row, col_map, key):
    v = _cell(row, col_map, key)
    return v is not None and str(v).strip() != ""


def _tag_matches(tag: str, num: str) -> bool:
    # Column H is written as a string but Graph coerces numeric-looking
    # values ('004' → 4, '22' → 22.0), so exact compare alone would drop
    # already-tagged rows for numeric invoice numbers.
    if not tag or not num:
        return False
    if tag == num:
        return True
    try:
        return float(tag) == float(num)
    except (ValueError, TypeError):
        return False


def _detect_header_col_map(ws_values: list[list]) -> tuple[int, dict]:
    col_map = {}
    header_row_idx = None
    for ri, row in enumerate(ws_values):
        candidate = [str(c).strip().upper() if c else "" for c in row]
        hits = sum(1 for c in candidate if c in _KNOWN_HEADERS)
        if hits >= 3:
            header_row_idx = ri
            for idx, name in enumerate(candidate):
                key = _KNOWN_HEADERS.get(name)
                if key and key not in col_map:
                    col_map[key] = idx
            break

    if header_row_idx is None:
        raise HTTPException(status_code=400, detail="Could not locate header row in worksheet")

    return header_row_idx, col_map


def detect_header_and_columns(ws_values: list[list]) -> tuple[int, dict, list]:
    """Validate worksheet structure and return header index, column map, and data rows."""
    if len(ws_values) < 2:
        raise HTTPException(status_code=400, detail="Worksheet has no data rows")

    header_row_idx, col_map = _detect_header_col_map(ws_values)
    data_rows = ws_values[header_row_idx + 1 :]

    required = ["date", "draw_request_date", "billable", "description"]
    missing = [k for k in required if k not in col_map]
    if missing:
        raise HTTPException(status_code=400, detail=f"Worksheet missing columns: {', '.join(missing)}")

    return header_row_idx, col_map, data_rows


def _db_amt(li) -> float:
    # DB-side mirror of the worksheet's column-N rule: Price, falling back
    # to Amount when Price is NULL (QBO-pulled account-based lines carry
    # Amount only — the sheet shows Amount for them, so the comparison
    # must too or every such line reports a false mismatch).
    val = li.get("price")
    if val is None:
        val = li.get("amount") or 0
    return round(float(val), 2)


def match_worksheet_rows(
    header_row_idx: int,
    col_map: dict,
    data_rows: list,
    invoice_number: str,
    enriched_line_items: list[dict],
) -> dict:
    """
    Pure worksheet parse + tiered matching (no Graph / DB I/O).

    Caller must run ``detect_header_and_columns`` first.

    Returns matched / mismatched / db_only (Direction A) / ws_only /
    already_tagged (Direction B) and related counters.
    """
    inv_num = (invoice_number or "").strip()
    ws_bills = []
    ws_expenses = []
    ws_tagged = []
    for row_idx, row in enumerate(data_rows, start=header_row_idx + 2):
        if not _has_value(row, col_map, "date"):
            continue

        z_pid = ""
        if len(row) > 25 and row[25] is not None:
            z_val = str(row[25]).strip()
            if len(z_val) == 36:
                z_pid = z_val.lower()

        source = _cell_str(row, col_map, "source").lower()
        draw_tag = _cell_str(row, col_map, "draw_request_date")
        entry = {
            "row": row_idx,
            "invoice_num": _cell_str(row, col_map, "invoice_num"),
            "billable": _parse_amt(_cell(row, col_map, "billable")),
            "payable_to": _cell_str(row, col_map, "payable_to"),
            "description": _cell_str(row, col_map, "description"),
            "date": _cell_str(row, col_map, "date"),
            "source": "Bill" if source == "bill" else "Expense",
            "sub_cost_code": _cell_str(row, col_map, "sub_cost_code"),
            "z": z_pid,
        }

        if draw_tag:
            if _tag_matches(draw_tag, inv_num):
                ws_tagged.append(entry)
            continue

        if source == "bill":
            ws_bills.append(entry)
        else:
            ws_expenses.append(entry)

    db_bills = [li for li in enriched_line_items if li.get("source_type") == "BillLineItem"]
    db_expenses = [
        li
        for li in enriched_line_items
        if li.get("source_type") in ("ExpenseLineItem", "BillCreditLineItem")
    ]

    matched = []
    mismatched = []
    db_only = []
    ws_only = []

    # ── Tier 0: column-Z source public_id (deterministic) ──
    db_by_source_pid_all = defaultdict(list)
    for li in db_bills + db_expenses:
        spid = (li.get("source_line_public_id") or "").lower()
        if spid:
            db_by_source_pid_all[spid].append(li)

    duplicate_source_lines = []
    db_by_source_pid = {}
    for spid, lis in db_by_source_pid_all.items():
        if len(lis) == 1:
            db_by_source_pid[spid] = lis[0]
        else:
            duplicate_source_lines.append(
                {
                    "source_public_id": spid,
                    "count": len(lis),
                    "descriptions": [x.get("description") or "" for x in lis],
                    "amounts": [_db_amt(x) for x in lis],
                }
            )

    def _tier0_entry(li, r):
        db_price = _db_amt(li)
        ws_amt = round(r["billable"], 2)
        return db_price, ws_amt, {
            "ref": li.get("parent_number") or r["invoice_num"] or "—",
            "source": _SOURCE_LABEL.get(li.get("source_type"), "Expense"),
            "date": li.get("source_date") or r.get("date", ""),
            "vendor": li.get("vendor_name") or r.get("payable_to", ""),
            "description": li.get("description") or r.get("description", ""),
            "cost_code": li.get("sub_cost_code_number") or r.get("sub_cost_code", ""),
            "db_total": db_price,
            "ws_total": ws_amt,
            "difference": round(db_price - ws_amt, 2),
            "match_key": "public_id",
        }

    z_matched_pids = set()
    for pool in (ws_bills, ws_expenses):
        remaining = []
        for r in pool:
            li = db_by_source_pid.get(r["z"]) if r["z"] else None
            if li is None or r["z"] in z_matched_pids:
                remaining.append(r)
                continue
            z_matched_pids.add(r["z"])
            db_price, ws_amt, entry = _tier0_entry(li, r)
            (matched if abs(db_price - ws_amt) < 0.01 else mismatched).append(entry)
        pool[:] = remaining

    # ── Tagged rows (H already carries this invoice's number) ──
    already_tagged = []
    tagged_ok_count = 0
    for r in ws_tagged:
        li = db_by_source_pid.get(r["z"]) if r["z"] else None
        if li is not None and r["z"] not in z_matched_pids:
            z_matched_pids.add(r["z"])
            tagged_ok_count += 1
            db_price, ws_amt, entry = _tier0_entry(li, r)
            entry["tagged"] = True
            (matched if abs(db_price - ws_amt) < 0.01 else mismatched).append(entry)
        else:
            already_tagged.append(
                {
                    "row": r["row"],
                    "ref": r["invoice_num"] or r["description"] or "—",
                    "source": r["source"],
                    "date": r.get("date", ""),
                    "vendor": r.get("payable_to", ""),
                    "description": r.get("description", ""),
                    "ws_total": round(r["billable"], 2),
                }
            )

    if z_matched_pids:
        db_bills = [
            li
            for li in db_bills
            if (li.get("source_line_public_id") or "").lower() not in z_matched_pids
        ]
        db_expenses = [
            li
            for li in db_expenses
            if (li.get("source_line_public_id") or "").lower() not in z_matched_pids
        ]

    # ── Tier 1 fallback — Bills: match by INVOICE # ──
    ws_bills_by_ref = defaultdict(list)
    for r in ws_bills:
        if r["invoice_num"]:
            ws_bills_by_ref[r["invoice_num"]].append(r)

    db_bills_by_ref = defaultdict(list)
    for li in db_bills:
        ref = (li.get("parent_number") or "").strip()
        if ref:
            db_bills_by_ref[ref].append(li)

    all_bill_refs = set(ws_bills_by_ref.keys()) | set(db_bills_by_ref.keys())
    for ref in sorted(all_bill_refs):
        in_db = db_bills_by_ref.get(ref)
        in_ws = ws_bills_by_ref.get(ref)

        db_total = round(sum(_db_amt(li) for li in in_db), 2) if in_db else 0.0
        ws_total = round(sum(r["billable"] for r in in_ws), 2) if in_ws else 0.0

        first_db = in_db[0] if in_db else {}
        first_ws = in_ws[0] if in_ws else {}
        entry = {
            "ref": ref,
            "source": "Bill",
            "date": first_db.get("source_date") or first_ws.get("date", ""),
            "vendor": first_db.get("vendor_name") or first_ws.get("payable_to", ""),
            "description": first_db.get("description") or first_ws.get("description", ""),
            "cost_code": first_db.get("sub_cost_code_number") or first_ws.get("sub_cost_code", ""),
            "db_total": db_total,
            "ws_total": ws_total,
            "difference": round(db_total - ws_total, 2),
        }

        if in_db and in_ws:
            (matched if abs(db_total - ws_total) < 0.01 else mismatched).append(entry)
        elif in_db:
            db_only.append(entry)
        else:
            ws_only.append(entry)

    # ── Expenses: match by Description + Billable amount ──
    ws_exp_unmatched = list(ws_expenses)
    db_exp_unmatched = []

    for li in db_expenses:
        db_desc = (li.get("description") or "").strip().lower()
        db_price = _db_amt(li)

        found = None
        for i, ws_row in enumerate(ws_exp_unmatched):
            ws_desc = ws_row["description"].lower()
            ws_amt = round(ws_row["billable"], 2)
            if db_desc == ws_desc and abs(db_price - ws_amt) < 0.01:
                found = i
                break

        ref_label = li.get("parent_number") or li.get("description") or "—"
        if found is not None:
            ws_row = ws_exp_unmatched.pop(found)
            matched.append(
                {
                    "ref": ref_label,
                    "source": "Expense",
                    "date": li.get("source_date") or ws_row.get("date", ""),
                    "vendor": li.get("vendor_name") or ws_row.get("payable_to", ""),
                    "description": li.get("description") or ws_row.get("description", ""),
                    "cost_code": li.get("sub_cost_code_number") or ws_row.get("sub_cost_code", ""),
                    "db_total": db_price,
                    "ws_total": round(ws_row["billable"], 2),
                    "difference": 0.0,
                }
            )
        else:
            db_exp_unmatched.append(li)

    for li in db_exp_unmatched:
        ref_label = li.get("parent_number") or li.get("description") or "—"
        db_price = _db_amt(li)
        db_only.append(
            {
                "ref": ref_label,
                "source": "Expense",
                "date": li.get("source_date", ""),
                "vendor": li.get("vendor_name", ""),
                "description": li.get("description", ""),
                "cost_code": li.get("sub_cost_code_number", ""),
                "db_total": db_price,
                "ws_total": 0.0,
                "difference": db_price,
            }
        )

    for ws_row in ws_exp_unmatched:
        ws_only.append(
            {
                "ref": ws_row["invoice_num"] or ws_row["description"] or "—",
                "source": "Expense",
                "date": ws_row.get("date", ""),
                "vendor": ws_row.get("payable_to", ""),
                "description": ws_row.get("description", ""),
                "cost_code": ws_row.get("sub_cost_code", ""),
                "db_total": 0.0,
                "ws_total": round(ws_row["billable"], 2),
                "difference": round(-ws_row["billable"], 2),
            }
        )

    return {
        "matched": matched,
        "mismatched": mismatched,
        "db_only": db_only,
        "ws_only": ws_only,
        "z_matched_count": len(z_matched_pids),
        "tagged_ok_count": tagged_ok_count,
        "already_tagged": already_tagged,
        "duplicate_source_lines": duplicate_source_lines,
    }


class WorksheetReconcileService:
    def reconcile(self, invoice_public_id: str) -> dict:
        from entities.invoice_line_item.business.service import InvoiceLineItemService
        from entities.invoice.business.enrichment import enrich_line_items
        from entities.invoice.business.service import InvoiceService
        from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import (
            DriveItemProjectExcelConnector,
        )
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        from integrations.ms.sharepoint.external.client import get_excel_used_range_values

        service = InvoiceService()
        invoice = service.read_by_public_id(public_id=invoice_public_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if not invoice.project_id:
            raise HTTPException(status_code=400, detail="Invoice has no project assigned")

        excel_connector = DriveItemProjectExcelConnector()
        linked_excel = excel_connector.get_excel_for_project(project_id=invoice.project_id)
        if not linked_excel:
            raise HTTPException(status_code=400, detail="No Budget Tracker workbook linked to this project")

        worksheet_name = linked_excel.get("worksheet_name")
        item_id_graph = linked_excel.get("item_id")
        ms_drive_id = linked_excel.get("ms_drive_id")

        if not item_id_graph or not ms_drive_id:
            raise HTTPException(status_code=400, detail="Linked workbook is missing drive/item info")

        drive = MsDriveRepository().read_by_id(ms_drive_id)
        if not drive:
            raise HTTPException(status_code=400, detail="Drive not found for linked workbook")

        ws_result = get_excel_used_range_values(drive.drive_id, item_id_graph, worksheet_name)
        if ws_result.get("status_code") != 200:
            raise HTTPException(
                status_code=ws_result.get("status_code", 500),
                detail=ws_result.get("message", "Failed to read worksheet"),
            )

        ws_values = ws_result.get("range", {}).get("values", [])

        header_row_idx, col_map, data_rows = detect_header_and_columns(ws_values)

        line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
        enriched = enrich_line_items(line_items)

        inv_num = (invoice.invoice_number or "").strip()
        match_result = match_worksheet_rows(
            header_row_idx,
            col_map,
            data_rows,
            inv_num,
            enriched,
        )

        matched = match_result["matched"]
        mismatched = match_result["mismatched"]
        db_only = match_result["db_only"]
        ws_only = match_result["ws_only"]

        return {
            "db_total": round(sum(e["db_total"] for e in matched + mismatched + db_only), 2),
            "ws_total": round(sum(e["ws_total"] for e in matched + mismatched + ws_only), 2),
            "matched": matched,
            "mismatched": mismatched,
            "db_only": db_only,
            "ws_only": ws_only,
            "z_matched_count": match_result["z_matched_count"],
            "tagged_ok_count": match_result["tagged_ok_count"],
            "already_tagged": match_result["already_tagged"],
            "duplicate_source_lines": match_result["duplicate_source_lines"],
        }
