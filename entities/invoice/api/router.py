# Python Standard Library Imports
import hashlib
import io
import logging
import uuid

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.invoice.api.schemas import InvoiceCreate, InvoiceUpdate
from entities.invoice.business.service import InvoiceService
from entities.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "invoice"])


@router.post("/create/invoice")
def create_invoice_router(body: InvoiceCreate, current_user: dict = Depends(get_current_user_api)):
    try:
        invoice = InvoiceService().create(
            tenant_id=current_user.get("tenant_id", 1),
            project_public_id=body.project_public_id,
            payment_term_public_id=body.payment_term_public_id,
            invoice_date=body.invoice_date,
            due_date=body.due_date,
            invoice_number=body.invoice_number,
            total_amount=Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            memo=body.memo,
            is_draft=body.is_draft if body.is_draft is not None else True,
        )
        return invoice.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/get/invoices")
def get_invoices_router(current_user: dict = Depends(get_current_user_api)):
    invoices = InvoiceService().read_all()
    return [inv.to_dict() for inv in invoices]


@router.get("/get/invoice/billable-items/{project_public_id}")
def get_billable_items_router(
    project_public_id: str,
    invoice_public_id: str = None,
    current_user: dict = Depends(get_current_user_api),
):
    try:
        items = InvoiceService().get_billable_items_for_project(
            project_public_id=project_public_id,
            invoice_public_id=invoice_public_id,
        )
        return items
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/get/invoice/next-number/{project_public_id}")
def get_next_invoice_number_router(project_public_id: str, current_user: dict = Depends(get_current_user_api)):
    try:
        next_number = InvoiceService().get_next_invoice_number(project_public_id=project_public_id)
        return {"next_invoice_number": next_number}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/generate/invoice/{public_id}/packet")
def generate_invoice_packet_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Merge all line-item PDF attachments into a single PDF packet,
    store it as an Attachment, and link it via InvoiceAttachment.
    Returns the attachment public_id so the UI can open it.
    """
    from pypdf import PdfReader, PdfWriter
    from entities.invoice_line_item.business.service import InvoiceLineItemService
    from entities.attachment.business.service import AttachmentService
    from entities.invoice_attachment.business.service import InvoiceAttachmentService
    from shared.storage import AzureBlobStorage, AzureBlobStorageError
    from shared.database import get_connection

    service = InvoiceService()
    invoice = service.read_by_public_id(public_id=public_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
    if not line_items:
        raise HTTPException(status_code=400, detail="Invoice has no line items")

    bill_ids = []
    expense_ids = []
    credit_ids = []
    for li in line_items:
        if li.source_type == "BillLineItem" and li.bill_line_item_id:
            bill_ids.append(li.bill_line_item_id)
        elif li.source_type == "ExpenseLineItem" and li.expense_line_item_id:
            expense_ids.append(li.expense_line_item_id)
        elif li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id:
            credit_ids.append(li.bill_credit_line_item_id)

    attachment_ids = []
    with get_connection() as conn:
        cursor = conn.cursor()
        if bill_ids:
            ph = ",".join("?" * len(bill_ids))
            cursor.execute(f"""
                SELECT blia.AttachmentId
                FROM dbo.BillLineItemAttachment blia
                WHERE blia.BillLineItemId IN ({ph})
                ORDER BY blia.BillLineItemId, blia.AttachmentId
            """, bill_ids)
            attachment_ids.extend(row.AttachmentId for row in cursor.fetchall())
        if expense_ids:
            ph = ",".join("?" * len(expense_ids))
            cursor.execute(f"""
                SELECT elia.AttachmentId
                FROM dbo.ExpenseLineItemAttachment elia
                WHERE elia.ExpenseLineItemId IN ({ph})
                ORDER BY elia.ExpenseLineItemId, elia.AttachmentId
            """, expense_ids)
            attachment_ids.extend(row.AttachmentId for row in cursor.fetchall())
        if credit_ids:
            ph = ",".join("?" * len(credit_ids))
            cursor.execute(f"""
                SELECT bclia.AttachmentId
                FROM dbo.BillCreditLineItemAttachment bclia
                WHERE bclia.BillCreditLineItemId IN ({ph})
                ORDER BY bclia.BillCreditLineItemId, bclia.AttachmentId
            """, credit_ids)
            attachment_ids.extend(row.AttachmentId for row in cursor.fetchall())
        cursor.close()

    if not attachment_ids:
        raise HTTPException(status_code=400, detail="No PDF attachments found on line items")

    att_service = AttachmentService()
    attachments = att_service.read_by_ids(attachment_ids)
    if not attachments:
        raise HTTPException(status_code=400, detail="No attachment records found")

    storage = AzureBlobStorage()
    writer = PdfWriter()
    skipped = 0
    for att in attachments:
        if not att.blob_url:
            skipped += 1
            continue
        try:
            content, _ = storage.download_file(att.blob_url)
            reader = PdfReader(io.BytesIO(content))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:
            logger.warning(f"Skipping attachment {att.public_id}: {e}")
            skipped += 1

    if len(writer.pages) == 0:
        raise HTTPException(status_code=400, detail="Could not read any PDF pages from attachments")

    merged_buf = io.BytesIO()
    writer.write(merged_buf)
    merged_bytes = merged_buf.getvalue()

    file_hash = hashlib.sha256(merged_bytes).hexdigest()
    new_public_id = str(uuid.uuid4())
    blob_name = f"{new_public_id}.pdf"
    filename = f"Invoice-{invoice.invoice_number or public_id}-Packet.pdf"

    try:
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=merged_bytes,
            content_type="application/pdf",
        )
    except AzureBlobStorageError as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload merged PDF: {e}")

    inv_att_service = InvoiceAttachmentService()
    existing_links = inv_att_service.read_by_invoice_id(invoice_id=invoice.id)
    for link in existing_links:
        if link.attachment_id:
            old_att = att_service.read_by_id(link.attachment_id)
            if old_att and old_att.category == "invoice_packet":
                if old_att.blob_url:
                    try:
                        storage.delete_file(old_att.blob_url)
                    except Exception:
                        logger.warning(f"Failed to delete old packet blob: {old_att.blob_url}")
                inv_att_service.delete_by_public_id(link.public_id)
                att_service.delete_by_public_id(old_att.public_id)

    attachment = att_service.create(
        filename=filename,
        original_filename=filename,
        file_extension="pdf",
        content_type="application/pdf",
        file_size=len(merged_bytes),
        file_hash=file_hash,
        blob_url=blob_url,
        description=f"Invoice packet for {invoice.invoice_number or public_id}",
        category="invoice_packet",
    )

    inv_att_service.create(invoice_id=invoice.id, attachment_id=attachment.id)

    return {
        "attachment_public_id": attachment.public_id,
        "filename": filename,
        "page_count": len(writer.pages),
        "skipped": skipped,
    }


@router.get("/get/invoice/{public_id}/reconcile")
def reconcile_invoice_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Compare invoice line items against the project's Budget Tracker worksheet.

    Worksheet filtering:
      - Only rows with a DATE value (col I) and NO Draw Request Date (col H)
        are considered unbilled and included.

    Matching strategy:
      - Source column M: "Bill" → Bill in our system; anything else → Expense.
      - Bills: match by INVOICE # (col K) against DB ParentNumber (BillNumber).
      - Expenses: INVOICE # won't reliably match; match by Description + Billable amount.
    """
    from collections import defaultdict
    from entities.invoice_line_item.business.service import InvoiceLineItemService
    from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import DriveItemProjectExcelConnector
    from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
    from integrations.ms.sharepoint.external.client import get_excel_used_range_values

    service = InvoiceService()
    invoice = service.read_by_public_id(public_id=public_id)
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
    if len(ws_values) < 2:
        raise HTTPException(status_code=400, detail="Worksheet has no data rows")

    known = {
        "DRAW REQUEST DATE": "draw_request_date",
        "DATE": "date",
        "PAYABLE TO": "payable_to",
        "INVOICE #": "invoice_num",
        "DESCRIPTION": "description",
        "SOURCE": "source", "CK": "source",
        "BILLABLE": "billable",
        "SUB COST CODE": "sub_cost_code",
    }

    # Find the header row (may not be row 0 if there's a title row)
    col_map = {}
    header_row_idx = None
    for ri, row in enumerate(ws_values):
        candidate = [str(c).strip().upper() if c else "" for c in row]
        hits = sum(1 for c in candidate if c in known)
        if hits >= 3:
            header_row_idx = ri
            for idx, name in enumerate(candidate):
                key = known.get(name)
                if key and key not in col_map:
                    col_map[key] = idx
            break

    if header_row_idx is None:
        raise HTTPException(status_code=400, detail="Could not locate header row in worksheet")

    data_rows = ws_values[header_row_idx + 1:]

    required = ["date", "draw_request_date", "billable", "description"]
    missing = [k for k in required if k not in col_map]
    if missing:
        raise HTTPException(status_code=400, detail=f"Worksheet missing columns: {', '.join(missing)}")

    def cell(row, key):
        idx = col_map.get(key)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    def parse_amt(val):
        if val is None or val == "" or val == "—":
            return 0.0
        try:
            return float(str(val).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            return 0.0

    def cell_str(row, key):
        v = cell(row, key)
        return str(v).strip() if v is not None and v != "" else ""

    def has_value(row, key):
        v = cell(row, key)
        return v is not None and str(v).strip() != ""

    # Parse worksheet rows: only unbilled (has DATE, no DRAW REQUEST DATE)
    ws_bills = []
    ws_expenses = []
    for row_idx, row in enumerate(data_rows, start=header_row_idx + 2):
        if not has_value(row, "date"):
            continue
        if has_value(row, "draw_request_date"):
            continue

        source = cell_str(row, "source").lower()
        entry = {
            "row": row_idx,
            "invoice_num": cell_str(row, "invoice_num"),
            "billable": parse_amt(cell(row, "billable")),
            "payable_to": cell_str(row, "payable_to"),
            "description": cell_str(row, "description"),
            "date": cell_str(row, "date"),
            "source": "Bill" if source == "bill" else "Expense",
            "sub_cost_code": cell_str(row, "sub_cost_code"),
        }

        if source == "bill":
            ws_bills.append(entry)
        else:
            ws_expenses.append(entry)

    # Load and enrich DB line items
    line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
    from entities.invoice.web.controller import _enrich_line_items
    enriched = _enrich_line_items(line_items)

    db_bills = [li for li in enriched if li.get("source_type") == "BillLineItem"]
    db_expenses = [li for li in enriched if li.get("source_type") in ("ExpenseLineItem", "BillCreditLineItem")]

    matched = []
    mismatched = []
    db_only = []
    ws_only = []

    # ── Bills: match by INVOICE # ──
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

        db_total = round(sum(float(li.get("price") or 0) for li in in_db), 2) if in_db else 0.0
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
        db_price = round(float(li.get("price") or 0), 2)

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
            matched.append({
                "ref": ref_label,
                "source": "Expense",
                "date": li.get("source_date") or ws_row.get("date", ""),
                "vendor": li.get("vendor_name") or ws_row.get("payable_to", ""),
                "description": li.get("description") or ws_row.get("description", ""),
                "cost_code": li.get("sub_cost_code_number") or ws_row.get("sub_cost_code", ""),
                "db_total": db_price,
                "ws_total": round(ws_row["billable"], 2),
                "difference": 0.0,
            })
        else:
            db_exp_unmatched.append(li)

    for li in db_exp_unmatched:
        ref_label = li.get("parent_number") or li.get("description") or "—"
        db_price = round(float(li.get("price") or 0), 2)
        db_only.append({
            "ref": ref_label,
            "source": "Expense",
            "date": li.get("source_date", ""),
            "vendor": li.get("vendor_name", ""),
            "description": li.get("description", ""),
            "cost_code": li.get("sub_cost_code_number", ""),
            "db_total": db_price,
            "ws_total": 0.0,
            "difference": db_price,
        })

    for ws_row in ws_exp_unmatched:
        ws_only.append({
            "ref": ws_row["invoice_num"] or ws_row["description"] or "—",
            "source": "Expense",
            "date": ws_row.get("date", ""),
            "vendor": ws_row.get("payable_to", ""),
            "description": ws_row.get("description", ""),
            "cost_code": ws_row.get("sub_cost_code", ""),
            "db_total": 0.0,
            "ws_total": round(ws_row["billable"], 2),
            "difference": round(-ws_row["billable"], 2),
        })

    return {
        "db_total": round(sum(e["db_total"] for e in matched + mismatched + db_only), 2),
        "ws_total": round(sum(e["ws_total"] for e in matched + mismatched + ws_only), 2),
        "matched": matched,
        "mismatched": mismatched,
        "db_only": db_only,
        "ws_only": ws_only,
    }


@router.get("/get/invoice/{public_id}")
def get_invoice_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    invoice = InvoiceService().read_by_public_id(public_id=public_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice.to_dict()


@router.put("/update/invoice/{public_id}")
def update_invoice_by_public_id_router(public_id: str, body: InvoiceUpdate, current_user: dict = Depends(get_current_user_api)):
    try:
        invoice = InvoiceService().update_by_public_id(
            public_id=public_id,
            row_version=body.row_version,
            project_public_id=body.project_public_id,
            payment_term_public_id=body.payment_term_public_id,
            invoice_date=body.invoice_date,
            due_date=body.due_date,
            invoice_number=body.invoice_number,
            total_amount=float(body.total_amount) if body.total_amount else None,
            memo=body.memo,
            is_draft=body.is_draft,
        )
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        return invoice.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/delete/invoice/{public_id}")
def delete_invoice_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    try:
        invoice = InvoiceService().delete_by_public_id(public_id=public_id)
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        return invoice.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting invoice {public_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete invoice: {str(e)}")


@router.post("/complete/invoice/{public_id}")
def complete_invoice_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    service = InvoiceService()
    invoice = service.read_by_public_id(public_id=public_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not getattr(invoice, "is_draft", True):
        raise HTTPException(status_code=400, detail="Invoice is already completed")
    result = service.complete_invoice(public_id=public_id)
    return result
