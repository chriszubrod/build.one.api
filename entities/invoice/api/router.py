# Python Standard Library Imports
import hashlib
import io
import logging
import uuid
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, status
from decimal import Decimal

# Local Imports
from entities.invoice.api.schemas import InvoiceCreate, InvoiceUpdate
from entities.invoice.business.service import InvoiceService
from shared.api.responses import list_response, item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "invoice"])


# ── TOC helpers ─────────────────────────────────────────────────────────────

def _toc_source_label(source_type: str) -> str:
    return {"BillLineItem": "Bill", "BillCreditLineItem": "Credit", "ExpenseLineItem": "Expense"}.get(source_type, "")


def _consolidate_basic_toc_rows(rows: list[dict]) -> list[dict]:
    """
    Consolidate line items from the same source bill/expense into one row.
    Groups by (source_type, parent_number, vendor_name, source_date).
    Single-item groups: keep original description and type label.
    Multi-item groups: description="Multiple", type_label="See Image", price=sum.
    Manual lines (no parent_number) are never consolidated — each stays its own row.
    """
    from itertools import groupby

    def _key(r):
        pn = r.get("parent_number") or ""
        if not pn:
            # Unique per-row key so Manual lines are never merged
            return ("__manual__", id(r), "", "")
        return (
            r.get("source_type", ""),
            pn,
            r.get("vendor_name", "") or "",
            r.get("source_date", "") or "",
        )

    consolidated = []
    for key, group_iter in groupby(rows, key=_key):
        group = list(group_iter)
        if len(group) == 1 or key[0] == "__manual__":
            for r in group:
                consolidated.append(dict(r, type_label=_toc_source_label(r.get("source_type", ""))))
        else:
            total_price = sum(float(r.get("price") or 0) for r in group)
            first = group[0]
            consolidated.append({
                "source_date": first.get("source_date", ""),
                "vendor_name": first.get("vendor_name", ""),
                "parent_number": first.get("parent_number", ""),
                "description": "Multiple See Image",
                "source_type": first.get("source_type", ""),
                "price": total_price,
                "type_label": _toc_source_label(first.get("source_type", "")),
            })
    return consolidated


def _build_toc_basic_pdf(rows: list[dict]) -> bytes:
    """
    Generate the basic Table of Contents PDF page.
    rows must be pre-sorted (Bill → Credit → Expense, then vendor name).
    """
    import html as _html
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT

    BLUE = colors.HexColor("#1F3864")

    wrap_style = ParagraphStyle("toc_wrap", fontName="Helvetica", fontSize=8, leading=10)
    wrap_hdr = ParagraphStyle("toc_wrap_hdr", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE)
    bold_right = ParagraphStyle("toc_bold_right", fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT)
    hdr_right = ParagraphStyle("toc_hdr_right", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE, alignment=TA_RIGHT)

    def W(text):
        """Wrapping Paragraph for Vendor / Description columns. HTML-escape so & < > are safe in ReportLab XML."""
        return Paragraph(_html.escape(str(text)) if text else "", wrap_style)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )

    # Columns: Date(65) Vendor(110) Invoice(80) Description(120) Type(52) Amount(77) = 504pt
    # Date/Invoice/Type/Amount sized to fit content on one line; Vendor/Description wrap.
    col_widths = [65, 110, 80, 120, 52, 77]
    headers = [
        Paragraph("Date", wrap_hdr), Paragraph("Vendor", wrap_hdr), Paragraph("Invoice", wrap_hdr),
        Paragraph("Description", wrap_hdr), Paragraph("Type", wrap_hdr), Paragraph("Amount", hdr_right),
    ]

    consolidated = _consolidate_basic_toc_rows(rows)

    table_data = [headers]
    for r in consolidated:
        price = r.get("price") if r.get("price") is not None else r.get("amount")
        try:
            amt_str = f"${float(price):,.2f}" if price is not None else "\u2014"
        except (TypeError, ValueError):
            amt_str = "\u2014"
        table_data.append([
            r.get("source_date", ""),
            W(r.get("vendor_name", "")),
            r.get("parent_number", ""),
            W(r.get("description", "") or ""),
            r.get("type_label", ""),
            amt_str,
        ])

    grand_total = sum(float(r.get("price") if r.get("price") is not None else r.get("amount") or 0) for r in consolidated)
    table_data.append(["", "", "", "", Paragraph("Total", bold_right), Paragraph(f"${grand_total:,.2f}", bold_right)])
    n = len(table_data)

    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        # Header row
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 8),
        ("TEXTCOLOR",    (0, 0), (-1, 0), BLUE),
        ("TOPPADDING",   (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 5),
        ("LINEBELOW",    (0, 0), (-1, 0), 0.75, BLUE),
        # Data rows
        ("FONTNAME",     (0, 1), (-1, n - 2), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, n - 2), 8),
        ("TOPPADDING",   (0, 1), (-1, n - 2), 3),
        ("BOTTOMPADDING",(0, 1), (-1, n - 2), 3),
        ("LINEBELOW",    (0, 1), (-1, n - 2), 0.25, colors.HexColor("#CCCCCC")),
        # Total row
        ("FONTNAME",     (0, n - 1), (-1, n - 1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, n - 1), (-1, n - 1), 8),
        ("TOPPADDING",   (0, n - 1), (-1, n - 1), 5),
        ("BOTTOMPADDING",(0, n - 1), (-1, n - 1), 4),
        ("LINEABOVE",    (0, n - 1), (-1, n - 1), 0.75, colors.black),
        # Amount col right-aligned (plain string cells)
        ("ALIGN",        (-1, 0), (-1, -1), "RIGHT"),
        # Padding
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        # Top-align so wrapped rows don't look odd
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))

    doc.build([
        Paragraph("Table Of Contents", ParagraphStyle(
            "TOCTitle", fontName="Helvetica-Bold", fontSize=12,
            textColor=BLUE, alignment=TA_CENTER, spaceAfter=8,
        )),
        table,
    ])
    return buf.getvalue()


def _build_toc_expanded_pdf(rows: list[dict]) -> bytes:
    """
    Generate the expanded Table of Contents PDF (grouped by cost code, subtotals per group).
    rows must be pre-sorted (cost_code_num, then type, then vendor).
    """
    import html as _html
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from itertools import groupby

    BLUE = colors.HexColor("#1F3864")

    wrap_style = ParagraphStyle("toc_ewrap", fontName="Helvetica", fontSize=8, leading=10)
    wrap_hdr = ParagraphStyle("toc_ewrap_hdr", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE)
    bold_right = ParagraphStyle("toc_ebold_right", fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT)
    hdr_right = ParagraphStyle("toc_ehdr_right", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE, alignment=TA_RIGHT)

    def W(text):
        """Wrapping Paragraph for Vendor / Description columns. HTML-escape so & < > are safe in ReportLab XML."""
        return Paragraph(_html.escape(str(text)) if text else "", wrap_style)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )

    # Columns: CostCode(45) Date(62) Vendor(95) Invoice(78) Description(100) Type(52) Amount(72) = 504pt
    # Date/Invoice/Type/Amount sized to fit content on one line; Vendor/Description wrap.
    col_widths = [45, 62, 95, 78, 100, 52, 72]
    headers = [
        Paragraph("Cost Code", wrap_hdr), Paragraph("Date", wrap_hdr), Paragraph("Vendor", wrap_hdr),
        Paragraph("Invoice", wrap_hdr), Paragraph("Description", wrap_hdr),
        Paragraph("Type", wrap_hdr), Paragraph("Amount", hdr_right),
    ]

    table_data = [headers]
    subtotal_indices: list[int] = []
    spacer_indices: list[int] = []

    for cc, group_iter in groupby(rows, key=lambda r: r.get("cost_code_number") or ""):
        group_items = list(group_iter)
        for r in group_items:
            price = r.get("price") if r.get("price") is not None else r.get("amount")
            try:
                amt_str = f"${float(price):,.2f}" if price is not None else "\u2014"
            except (TypeError, ValueError):
                amt_str = "\u2014"
            table_data.append([
                cc,
                r.get("source_date", ""),
                W(r.get("vendor_name", "")),
                r.get("parent_number", ""),
                W(r.get("description", "") or ""),
                _toc_source_label(r.get("source_type", "")),
                amt_str,
            ])
        subtotal = sum(float(r.get("price") if r.get("price") is not None else r.get("amount") or 0) for r in group_items)
        table_data.append(["", "", "", "", "", Paragraph("Subtotal", bold_right), Paragraph(f"${subtotal:,.2f}", bold_right)])
        subtotal_indices.append(len(table_data) - 1)
        # Blank spacer row between groups
        table_data.append(["", "", "", "", "", "", ""])
        spacer_indices.append(len(table_data) - 1)

    # Remove trailing spacer
    if spacer_indices and spacer_indices[-1] == len(table_data) - 1:
        table_data.pop()
        spacer_indices.pop()

    grand_total = sum(float(r.get("price") if r.get("price") is not None else r.get("amount") or 0) for r in rows)
    table_data.append(["", "", "", "", "", Paragraph("Total", bold_right), Paragraph(f"${grand_total:,.2f}", bold_right)])
    total_idx = len(table_data) - 1
    n = len(table_data)

    style_cmds = [
        # Header
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 8),
        ("TEXTCOLOR",    (0, 0), (-1, 0), BLUE),
        ("TOPPADDING",   (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 5),
        ("LINEBELOW",    (0, 0), (-1, 0), 0.75, BLUE),
        # All non-header rows default
        ("FONTNAME",     (0, 1), (-1, n - 1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, n - 1), 8),
        ("TOPPADDING",   (0, 1), (-1, n - 1), 3),
        ("BOTTOMPADDING",(0, 1), (-1, n - 1), 3),
        ("LINEBELOW",    (0, 1), (-1, n - 1), 0.25, colors.HexColor("#CCCCCC")),
        # Amount col right-aligned (plain string cells)
        ("ALIGN",        (-1, 0), (-1, -1), "RIGHT"),
        # Padding
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        # Top-align so wrapped rows don't look odd
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        # Total row
        ("FONTNAME",     (0, total_idx), (-1, total_idx), "Helvetica-Bold"),
        ("LINEABOVE",    (0, total_idx), (-1, total_idx), 0.75, colors.black),
        ("TOPPADDING",   (0, total_idx), (-1, total_idx), 5),
        ("BOTTOMPADDING",(0, total_idx), (-1, total_idx), 4),
    ]
    for idx in subtotal_indices:
        style_cmds.extend([
            ("FONTNAME",     (0, idx), (-1, idx), "Helvetica-Bold"),
            ("LINEABOVE",    (0, idx), (-1, idx), 0.5, colors.HexColor("#888888")),
            ("TOPPADDING",   (0, idx), (-1, idx), 4),
        ])
    for idx in spacer_indices:
        style_cmds.extend([
            ("TOPPADDING",   (0, idx), (-1, idx), 2),
            ("BOTTOMPADDING",(0, idx), (-1, idx), 2),
            ("LINEBELOW",    (0, idx), (-1, idx), 0, colors.white),
        ])

    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle(style_cmds))

    doc.build([
        Paragraph("Table Of Contents", ParagraphStyle(
            "TOCTitle", fontName="Helvetica-Bold", fontSize=12,
            textColor=BLUE, alignment=TA_CENTER, spaceAfter=8,
        )),
        table,
    ])
    return buf.getvalue()


@router.post("/create/invoice")
def create_invoice_router(body: InvoiceCreate, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_create"))):
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
        return item_response(invoice.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/get/invoices")
def get_invoices_router(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    is_draft: Optional[bool] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.INVOICES)),
):
    """
    Read invoices with pagination + filters.

    Mirrors `GET /get/bills` so agent tooling can search consistently.
    Service layer (`read_paginated` + `count`) was already in place;
    this route just wires the filters through. Backwards-compatible —
    bare GET still works.
    """
    service = InvoiceService()
    invoices = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        project_id=project_id,
        is_draft=is_draft,
    )
    total = service.count(
        search_term=search,
        project_id=project_id,
        is_draft=is_draft,
    )
    return {
        "data": [inv.to_dict() for inv in invoices],
        "count": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/get/invoice/billable-items/{project_public_id}")
def get_billable_items_router(
    project_public_id: str,
    invoice_public_id: str = None,
    current_user: dict = Depends(require_module_api(Modules.INVOICES)),
):
    try:
        items = InvoiceService().get_billable_items_for_project(
            project_public_id=project_public_id,
            invoice_public_id=invoice_public_id,
        )
        return list_response(items)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/get/invoice/next-number/{project_public_id}")
def get_next_invoice_number_router(project_public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES))):
    try:
        next_number = InvoiceService().get_next_invoice_number(project_public_id=project_public_id)
        return item_response({"next_invoice_number": next_number})
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/generate/invoice/{public_id}/packet")
def generate_invoice_packet_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES))):
    """
    Merge all line-item PDF attachments into a single PDF packet,
    store it as an Attachment, and link it via InvoiceAttachment.
    Returns the attachment public_id so the UI can open it.
    """
    try:
        return _generate_invoice_packet(public_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Unhandled error generating packet for invoice {public_id}")
        raise HTTPException(status_code=500, detail="Failed to generate invoice packet — check server logs for details")


def _generate_invoice_packet(public_id: str):
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

    # Build TOC pages from enriched data (all line items, including those without attachments)
    from entities.invoice.business.enrichment import enrich_line_items
    logger.info(f"Packet [{public_id}]: enriching {len(line_items)} line items")
    enriched_items = enrich_line_items(line_items)

    _type_order_map = {"BillLineItem": 0, "BillCreditLineItem": 1, "ExpenseLineItem": 2}

    basic_toc_rows = sorted(enriched_items, key=lambda r: (
        _type_order_map.get(r.get("source_type", ""), 9),
        (r.get("vendor_name") or "").lower(),
        (r.get("parent_number") or "").lower(),
    ))
    basic_toc_bytes = _build_toc_basic_pdf(basic_toc_rows)

    def _expanded_sort_key(r):
        cc = (r.get("cost_code_number") or "").strip()
        try:
            cc_num = float(cc)
        except (ValueError, TypeError):
            cc_num = float("inf")
        return (cc_num, cc.lower(), _type_order_map.get(r.get("source_type", ""), 9), (r.get("vendor_name") or "").lower())

    expanded_toc_rows = sorted(enriched_items, key=_expanded_sort_key)
    expanded_toc_bytes = _build_toc_expanded_pdf(expanded_toc_rows)

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

    # Collect (type_order, vendor_name_lower, attachment_id) for deterministic ordering:
    # Bill (0) → BillCredit (1) → Expense (2), then vendor name ascending within each type.
    ordered_entries = []  # list of (type_order, vendor_name_lower, attachment_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        if bill_ids:
            ph = ",".join("?" * len(bill_ids))
            cursor.execute(f"""
                SELECT MIN(blia.AttachmentId) AS AttachmentId, LOWER(ISNULL(v.Name, '')) AS VendorNameLower
                FROM dbo.BillLineItemAttachment blia
                JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
                JOIN dbo.Bill b ON b.Id = bli.BillId
                LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
                WHERE blia.BillLineItemId IN ({ph})
                GROUP BY b.Id, LOWER(ISNULL(v.Name, ''))
            """, bill_ids)
            for row in cursor.fetchall():
                ordered_entries.append((0, row.VendorNameLower, row.AttachmentId))
        if credit_ids:
            ph = ",".join("?" * len(credit_ids))
            cursor.execute(f"""
                SELECT MIN(bclia.AttachmentId) AS AttachmentId, LOWER(ISNULL(v.Name, '')) AS VendorNameLower
                FROM dbo.BillCreditLineItemAttachment bclia
                JOIN dbo.BillCreditLineItem bcli ON bcli.Id = bclia.BillCreditLineItemId
                JOIN dbo.BillCredit bc ON bc.Id = bcli.BillCreditId
                LEFT JOIN dbo.Vendor v ON v.Id = bc.VendorId
                WHERE bclia.BillCreditLineItemId IN ({ph})
                GROUP BY bc.Id, LOWER(ISNULL(v.Name, ''))
            """, credit_ids)
            for row in cursor.fetchall():
                ordered_entries.append((1, row.VendorNameLower, row.AttachmentId))
        if expense_ids:
            ph = ",".join("?" * len(expense_ids))
            cursor.execute(f"""
                SELECT MIN(elia.AttachmentId) AS AttachmentId, LOWER(ISNULL(v.Name, '')) AS VendorNameLower
                FROM dbo.ExpenseLineItemAttachment elia
                JOIN dbo.ExpenseLineItem eli ON eli.Id = elia.ExpenseLineItemId
                JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                LEFT JOIN dbo.Vendor v ON v.Id = e.VendorId
                WHERE elia.ExpenseLineItemId IN ({ph})
                GROUP BY e.Id, LOWER(ISNULL(v.Name, ''))
            """, expense_ids)
            for row in cursor.fetchall():
                ordered_entries.append((2, row.VendorNameLower, row.AttachmentId))
        cursor.close()

    if not ordered_entries:
        raise HTTPException(status_code=400, detail="No PDF attachments found on line items")

    ordered_entries.sort(key=lambda x: (x[0], x[1]))
    seen_attachment_ids: set = set()
    deduped_entries = []
    for entry in ordered_entries:
        if entry[2] not in seen_attachment_ids:
            seen_attachment_ids.add(entry[2])
            deduped_entries.append(entry)
    attachment_ids = [x[2] for x in deduped_entries]

    att_service = AttachmentService()
    att_list = att_service.read_by_ids(attachment_ids)
    if not att_list:
        raise HTTPException(status_code=400, detail="No attachment records found")

    att_map = {a.id: a for a in att_list}
    attachments_sorted = [att_map[aid] for aid in attachment_ids if aid in att_map]

    storage = AzureBlobStorage()
    writer = PdfWriter()

    # Prepend both TOC pages before the attachment images
    for toc_bytes in [basic_toc_bytes, expanded_toc_bytes]:
        toc_reader = PdfReader(io.BytesIO(toc_bytes))
        for page in toc_reader.pages:
            writer.add_page(page)

    skipped = 0
    for att in attachments_sorted:
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

    # Write the merged PDF first, then compress with pikepdf
    merged_buf = io.BytesIO()
    writer.write(merged_buf)
    uncompressed_bytes = merged_buf.getvalue()

    try:
        import pikepdf
        compressed_buf = io.BytesIO()
        with pikepdf.open(io.BytesIO(uncompressed_bytes)) as pdf:
            pdf.save(
                compressed_buf,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                recompress_flate=True,
                normalize_content=True,
            )
        merged_bytes = compressed_buf.getvalue()
        logger.info(f"Packet [{public_id}]: compressed {len(uncompressed_bytes):,} → {len(merged_bytes):,} bytes")
    except Exception as e:
        logger.warning(f"Packet [{public_id}]: pikepdf compression failed, using uncompressed: {e}")
        merged_bytes = uncompressed_bytes

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

    return item_response({
        "attachment_public_id": attachment.public_id,
        "filename": filename,
        "page_count": len(writer.pages),
        "skipped": skipped,
    })


@router.get("/get/invoice/{public_id}/reconcile")
def reconcile_invoice_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES))):
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
    from entities.invoice.business.enrichment import enrich_line_items
    enriched = enrich_line_items(line_items)

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

    return item_response({
        "db_total": round(sum(e["db_total"] for e in matched + mismatched + db_only), 2),
        "ws_total": round(sum(e["ws_total"] for e in matched + mismatched + ws_only), 2),
        "matched": matched,
        "mismatched": mismatched,
        "db_only": db_only,
        "ws_only": ws_only,
    })


@router.get("/get/invoice/{public_id}")
def get_invoice_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES))):
    invoice = InvoiceService().read_by_public_id(public_id=public_id)
    if not invoice:
        raise_not_found("Invoice")
    return item_response(invoice.to_dict())


@router.put("/update/invoice/{public_id}")
def update_invoice_by_public_id_router(public_id: str, body: InvoiceUpdate, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_update"))):
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
            raise_not_found("Invoice")
        return item_response(invoice.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/delete/invoice/{public_id}")
def delete_invoice_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_delete"))):
    try:
        invoice = InvoiceService().delete_by_public_id(public_id=public_id)
        if not invoice:
            raise_not_found("Invoice")
        return item_response(invoice.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting invoice {public_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete invoice: {str(e)}")


@router.post("/complete/invoice/{public_id}")
def complete_invoice_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete"))):
    service = InvoiceService()
    invoice = service.read_by_public_id(public_id=public_id)
    if not invoice:
        raise_not_found("Invoice")
    result = service.complete_invoice(public_id=public_id)
    return item_response(result)


@router.post("/sync/invoice/{public_id}/sharepoint")
def sync_invoice_sharepoint_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete"))):
    """
    Re-run the SharePoint upload step for a completed invoice.
    Useful when the Invoices module folder was not configured at completion time.
    """
    from entities.invoice_line_item.business.service import InvoiceLineItemService

    service = InvoiceService()
    invoice = service.read_by_public_id(public_id=public_id)
    if not invoice:
        raise_not_found("Invoice")

    line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
    result = service._upload_to_sharepoint(invoice=invoice, line_items=line_items)
    if not result.get("success"):
        logger.warning(f"SharePoint re-sync failed for invoice {public_id}: {result.get('message')}")
        raise HTTPException(status_code=500, detail=result.get("message", "SharePoint upload failed"))

    logger.info(f"SharePoint re-sync complete for invoice {public_id}: {result.get('message')}")
    return item_response(result)


@router.post("/sync/invoice/{public_id}/qbo")
def sync_invoice_to_qbo_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete"))):
    """
    Invoice QBO sync is disabled. Invoices are created manually in QBO.
    """
    logger.info(f"Invoice QBO push sync disabled; skipping for invoice {public_id}")
    return item_response({"status_code": 200, "message": "Invoice QBO sync is disabled. Manage invoices manually in QBO."})
