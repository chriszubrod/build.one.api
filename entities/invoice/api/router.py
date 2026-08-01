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
from entities.invoice.business.cover import _signed_line_amount
from entities.invoice.business.service import InvoiceService
from shared.api.responses import list_response, item_response, raise_not_found
from shared.pdf_utils import fit_page_to_letter
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "invoice"])


# ── TOC helpers ─────────────────────────────────────────────────────────────

def _toc_signed_amount(row: dict) -> Optional[float]:
    """
    Float view of the row's signed display amount, for the TOC (which renders and
    sums in float). Delegates to the pure Decimal `_signed_line_amount` in
    `entities.invoice.business.cover` so the TOC and the cover page share ONE
    source of truth for the billed_price→price→amount fallback and the credit
    sign rules — the packet's cover-vs-TOC subtotal reconciliation cannot drift
    because both surfaces are computed by the same code. See that function for the
    full billed_price / credit-negation rationale. Cast to float at the boundary.
    """
    v = _signed_line_amount(row)
    return float(v) if v is not None else None


def _toc_format_money(value: Optional[float]) -> str:
    """Format a number as accounting-style money: $1,234.56 or ($1,234.56) for negatives."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v < 0:
        return f"(${abs(v):,.2f})"
    return f"${v:,.2f}"


def _toc_source_label(source_type: str) -> str:
    return {
        "BillLineItem": "Bill",
        "BillCreditLineItem": "Credit",
        "ExpenseLineItem": "Expense",
        "EmployeeLaborLineItem": "EmpLabor",
    }.get(source_type, "")


def _resolve_builders_fee_rate(project_id: Optional[int]) -> Optional[Decimal]:
    """Builder's-fee rate for the cover page: the most-recent Contract on the
    project that carries a non-null BuildersFeeRate (highest Id wins). Returns
    None when the project has no contract or none sets a rate — the cover then
    omits the fee row (Total == Subtotal).

    NOTE: 'most-recent non-null' is a documented simplification pending the formal
    Contract design — the minimal Contract entity (U-188) allows multiple rows per
    project with no designated-active flag yet. Failure-isolated: any lookup error
    degrades to no-fee rather than failing the packet.
    """
    if not project_id:
        return None
    try:
        from entities.contract.business.service import ContractService
        contracts = ContractService().read_by_project_id(project_id)
    except Exception:
        logger.warning(f"Cover fee-rate lookup failed for project {project_id}", exc_info=True)
        return None
    rated = [c for c in contracts if c.builders_fee_rate is not None]
    if not rated:
        return None
    return max(rated, key=lambda c: c.id or 0).builders_fee_rate


def _consolidate_basic_toc_rows(rows: list[dict]) -> list[dict]:
    """
    Consolidate line items from the same source bill/expense into one row.
    Groups by (source_type, parent_number, vendor_name, source_date).
    Single-item groups: keep the item's sub cost code name and type label.
    Multi-item groups: sub_cost_code_name = the shared SCC name if uniform else
    "Multiple See Image", type_label from source, price=sum.
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
            total_price = sum((_toc_signed_amount(r) or 0) for r in group)
            first = group[0]
            # Consolidated SCC label: the shared sub cost code name if every
            # item in the group maps to the same one, else "Multiple See Image".
            scc_names = {(r.get("sub_cost_code_name") or "").strip() for r in group}
            scc_names.discard("")
            scc_label = next(iter(scc_names)) if len(scc_names) == 1 else "Multiple See Image"
            consolidated.append({
                "source_date": first.get("source_date", ""),
                "vendor_name": first.get("vendor_name", ""),
                "parent_number": first.get("parent_number", ""),
                "sub_cost_code_name": scc_label,
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
    # The "Description" column shows the sub cost code name (not the work paragraph).
    # Date/Invoice/Type/Amount sized to fit content on one line; Vendor/Description wrap.
    col_widths = [65, 110, 80, 120, 52, 77]
    headers = [
        Paragraph("Date", wrap_hdr), Paragraph("Vendor", wrap_hdr), Paragraph("Invoice", wrap_hdr),
        Paragraph("Description", wrap_hdr), Paragraph("Type", wrap_hdr), Paragraph("Amount", hdr_right),
    ]

    consolidated = _consolidate_basic_toc_rows(rows)

    table_data = [headers]
    for r in consolidated:
        amt_str = _toc_format_money(_toc_signed_amount(r))
        table_data.append([
            r.get("source_date", ""),
            W(r.get("vendor_name", "")),
            r.get("parent_number", ""),
            # Prefer the sub cost code name; fall back to the work-description
            # so the column is never blank when a source line has no SCC.
            W(r.get("sub_cost_code_name") or r.get("description") or ""),
            r.get("type_label", ""),
            amt_str,
        ])

    grand_total = sum((_toc_signed_amount(r) or 0) for r in consolidated)
    table_data.append(["", "", "", "", Paragraph("Total", bold_right), Paragraph(_toc_format_money(grand_total), bold_right)])
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
    # The "Description" column shows the sub cost code name (not the work paragraph).
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
            amt_str = _toc_format_money(_toc_signed_amount(r))
            table_data.append([
                cc,
                r.get("source_date", ""),
                W(r.get("vendor_name", "")),
                r.get("parent_number", ""),
                # Prefer the sub cost code name; fall back to the work-description
                # so the column is never blank when a source line has no SCC.
                W(r.get("sub_cost_code_name") or r.get("description") or ""),
                _toc_source_label(r.get("source_type", "")),
                amt_str,
            ])
        subtotal = sum((_toc_signed_amount(r) or 0) for r in group_items)
        table_data.append(["", "", "", "", "", Paragraph("Subtotal", bold_right), Paragraph(_toc_format_money(subtotal), bold_right)])
        subtotal_indices.append(len(table_data) - 1)
        # Blank spacer row between groups
        table_data.append(["", "", "", "", "", "", ""])
        spacer_indices.append(len(table_data) - 1)

    # Remove trailing spacer
    if spacer_indices and spacer_indices[-1] == len(table_data) - 1:
        table_data.pop()
        spacer_indices.pop()

    grand_total = sum((_toc_signed_amount(r) or 0) for r in rows)
    table_data.append(["", "", "", "", "", Paragraph("Total", bold_right), Paragraph(_toc_format_money(grand_total), bold_right)])
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

    # Manual lines (typed directly into the QBO invoice tray with no underlying transaction)
    # are excluded from the customer-facing TOC. They're internal accounting adjustments
    # (offsets, reversals, $0-net pairs) — the customer reads the TOC to verify each charge
    # against a source document, and a Manual line has no source. The lines remain on the
    # invoice in QBO and dbo for accounting integrity; they just don't surface here.
    toc_items = [r for r in enriched_items if r.get("source_type") != "Manual"]

    _type_order_map = {"BillLineItem": 0, "BillCreditLineItem": 1, "ExpenseLineItem": 2, "EmployeeLaborLineItem": 3}

    basic_toc_rows = sorted(toc_items, key=lambda r: (
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

    expanded_toc_rows = sorted(toc_items, key=_expanded_sort_key)
    expanded_toc_bytes = _build_toc_expanded_pdf(expanded_toc_rows)

    # Cover page (U-191): a cost-code rollup carrying the Builder's Fee line, page 1
    # of the packet. Built from the SAME `toc_items` the expanded TOC totals, with
    # the identical sign rules, so its subtotal reconciles to the expanded TOC by
    # construction. Fully failure-isolated — any cover error logs and the packet
    # still generates with just the two TOC pages.
    cover_bytes = None
    try:
        from entities.invoice.business.cover import build_cover_pdf, build_cover_rollup
        from entities.project.business.service import ProjectService

        cover_model = build_cover_rollup(toc_items, _resolve_builders_fee_rate(invoice.project_id))
        project = ProjectService().read_by_id(invoice.project_id) if invoice.project_id else None
        cover_bytes = build_cover_pdf(
            {
                "title": "Draw Summary",
                "project_name": project.name if project else "",
                "invoice_number": invoice.invoice_number or "",
                "invoice_date": invoice.invoice_date or "",
            },
            cover_model,
        )
    except Exception:
        logger.exception(f"Packet [{public_id}]: cover page generation failed — continuing without it")

    # Attachment pages follow the basic TOC's row order exactly: walk the
    # already-sorted TOC rows and take each line's attachment (first-seen wins —
    # many lines, e.g. contract-labor, share one document). Deriving the order
    # from basic_toc_rows instead of a separate per-parent query keeps the pages
    # in the same sequence the reader sees in the TOC, and includes every
    # distinct per-line document (a per-parent MIN(AttachmentId) grouping would
    # drop extra documents on bills whose lines carry different PDFs).
    seen_attachment_public_ids: set = set()
    ordered_attachment_public_ids = []
    for r in basic_toc_rows:
        apid = r.get("attachment_public_id") or ""
        if apid and apid not in seen_attachment_public_ids:
            seen_attachment_public_ids.add(apid)
            ordered_attachment_public_ids.append(apid)

    if not ordered_attachment_public_ids:
        raise HTTPException(status_code=400, detail="No PDF attachments found on line items")

    att_service = AttachmentService()
    attachments_sorted = []
    for apid in ordered_attachment_public_ids:
        att = att_service.read_by_public_id(public_id=apid)
        if att:
            attachments_sorted.append(att)
    if not attachments_sorted:
        raise HTTPException(status_code=400, detail="No attachment records found")

    storage = AzureBlobStorage()
    writer = PdfWriter()

    # Prepend the cover (page 1, when built) then both TOC pages before the images
    prepend_pdfs = ([cover_bytes] if cover_bytes else []) + [basic_toc_bytes, expanded_toc_bytes]
    for toc_bytes in prepend_pdfs:
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
                writer.add_page(fit_page_to_letter(page))
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

    # Box mirror — enqueue the freshly generated packet to the invoice's
    # mapped project Box folder. Additive + failure-isolated: never affects
    # packet generation.
    _enqueue_box_packet_upload(invoice=invoice, attachment=attachment, blob_url=blob_url, filename=filename)

    return item_response({
        "attachment_public_id": attachment.public_id,
        "filename": filename,
        "page_count": len(writer.pages),
        "skipped": skipped,
    })


def _enqueue_box_packet_upload(invoice, attachment, blob_url: str, filename: str) -> None:
    """
    Enqueue a Box upload for the invoice packet attachment.

    Uses the invoice's project for the Box folder mapping; unmapped (or
    missing) project → skip with an info log. Resolves (or idempotently
    creates) a per-invoice subfolder under the mapped 15-Draw Requests root,
    mirroring the SharePoint per-invoice subfolder at
    `entities/invoice/business/service.py::_upload_to_sharepoint` (lines
    1016-1030). Additive + failure-isolated — any exception is logged and
    swallowed so Box can never affect the packet flow.
    """
    import os as _os
    if _os.getenv("ALLOW_BOX_WRITES", "").strip().lower() != "true":
        return  # gate closed — skip the DB legwork, not just the enqueue
    try:
        from integrations.box.base.client import BoxHttpClient
        from integrations.box.folder.business.service import (
            BoxProjectFolderService,
            DOC_CLASS_DRAW_REQUESTS,
        )
        from integrations.box.outbox.business.service import BoxOutboxService

        if not invoice.project_id:
            logger.info(f"box.enqueue.skipped_unmapped_project project_id=None invoice={invoice.public_id}")
            return
        # Customer invoice packets file to the project's "15 - Draw Requests"
        # ('draw_requests') folder — distinct from vendor AP docs (14-Invoices).
        folder_service = BoxProjectFolderService()
        mapping = folder_service.read_mapping_by_project_id_and_class(
            invoice.project_id, DOC_CLASS_DRAW_REQUESTS
        )
        if mapping is None:
            logger.info(f"box.enqueue.skipped_unmapped_project project_id={invoice.project_id} doc_class=draw_requests")
            return

        # Resolve the per-invoice subfolder. On failure, SKIP (same as the
        # line PDFs) — no flat-root fallback: Box 409 recovery is per-folder,
        # so a root copy could never be re-versioned by a later subfolder
        # upload, leaving a permanently stale packet a reviewer might send to
        # the customer. POST /sync/invoice/{id}/box re-delivers once the
        # subfolder resolves (the outbox coalesce refreshes stale targets).
        subfolder_name = invoice.invoice_number or str(invoice.public_id)
        try:
            with BoxHttpClient() as client:
                subfolder = folder_service.read_or_create_child_folder(
                    client=client,
                    parent_box_folder_id=mapping["box_folder_id"],
                    folder_name=subfolder_name,
                )
            target_folder_id = subfolder["box_folder_id"]
        except Exception as sub_err:
            logger.warning(
                f"box.subfolder.create.failed invoice={invoice.public_id} "
                f"parent={mapping['box_folder_id']} name={subfolder_name!r}: {sub_err}. "
                f"Packet upload skipped — repair via POST /sync/invoice/{{id}}/box."
            )
            return None

        return BoxOutboxService().enqueue_box_upload(
            entity_type="invoice",
            entity_public_id=str(invoice.public_id),
            doc_kind="packet",
            blob_path=blob_url,
            filename=filename,
            content_type="application/pdf",
            box_folder_id=target_folder_id,
            attachment_id=attachment.id,
            project_id=invoice.project_id,
        )
    except Exception as e:
        logger.warning(f"box.enqueue.failed invoice={invoice.public_id}: {e}")
        return None


@router.get("/get/invoice/{public_id}/reconcile")
def reconcile_invoice_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES))):
    """
    Compare invoice line items against the project's Budget Tracker worksheet.

    Worksheet filtering:
      - Only rows with a DATE value (col I) and NO Draw Request Date (col H)
        are considered unbilled and included.

    Matching strategy (tiered):
      - Tier 0: column-Z source public_id (the reconciliation key written by the
        Bill/Expense Excel syncs) — deterministic; preferred whenever Z is populated.
      - Tier 1 (fallback): Source column M: "Bill" → Bill in our system; anything
        else → Expense. Bills match by INVOICE # (col K) against DB ParentNumber;
        Expenses match by Description + Billable amount.

    Direction B: rows whose DRAW REQUEST (col H) already carries THIS invoice's
    number but whose column-Z key is NOT one of this invoice's source lines are
    returned in `already_tagged` (surface-only — they reflect prior manual edits).
    """
    from entities.invoice.business.worksheet_reconcile import WorksheetReconcileService

    payload = WorksheetReconcileService().reconcile(public_id)
    return item_response(payload)


@router.get("/get/invoice/{public_id}/source-links")
def propose_invoice_source_links_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.INVOICES)),
):
    from entities.invoice.business.reconciliation import InvoiceReconciliationService

    try:
        payload = InvoiceReconciliationService().propose_links(public_id)
    except ValueError:
        raise_not_found("Invoice")
    return item_response(payload)


@router.get("/get/invoice/{public_id}/draw-audit")
def draw_audit_invoice_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.INVOICES)),
):
    from entities.invoice.business.audit import InvoiceDrawAuditService

    try:
        payload = InvoiceDrawAuditService().audit(public_id)
    except ValueError:
        raise_not_found("Invoice")
    return item_response(payload)


@router.get("/get/invoice/{public_id}/box-draw-verify")
def box_draw_verify_invoice_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.INVOICES)),
):
    """KI-46: READ-ONLY Box vs SharePoint DETAILS col-N draw total compare."""
    from entities.invoice.business.box_verify import BoxDrawVerifyService

    try:
        payload = BoxDrawVerifyService().verify(public_id)
    except ValueError:
        raise_not_found("Invoice")
    return item_response(payload)


@router.post("/reconcile/invoice/{public_id}/link")
def apply_invoice_source_links_router(
    public_id: str,
    only_line_ids: Optional[list[int]] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete")),
):
    from entities.invoice.business.reconciliation import InvoiceReconciliationService

    try:
        payload = InvoiceReconciliationService().apply_links(
            public_id,
            only_line_ids=only_line_ids,
        )
    except ValueError:
        raise_not_found("Invoice")
    return item_response(payload)


@router.post("/reconcile/invoice/{public_id}/push-draw")
def push_draw_invoice_router(
    public_id: str,
    force: bool = Query(default=False),
    current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete")),
):
    from entities.invoice.business.push import InvoiceDrawPushService

    try:
        payload = InvoiceDrawPushService().push_draw(public_id, force=force)
    except ValueError:
        raise_not_found("Invoice")
    return item_response(payload)


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

    from entities.completion_job.business.service import CompletionJobService

    job_service = CompletionJobService()
    job = job_service.enqueue("Invoice", public_id)
    try:
        result = service.complete_invoice(public_id=public_id)
        if job.public_id:
            job_service.mark_success(job.public_id)
    except Exception as e:
        if job.public_id:
            job_service.mark_failure(job.public_id, str(e))
        raise

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


@router.post("/sync/invoice/{public_id}/box")
def sync_invoice_box_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete"))):
    """
    Re-run the Box mirror for a completed invoice: enqueue per-line-item
    supporting PDFs into `15-Draw Requests/<invoice#>/` and re-enqueue the
    packet PDF into the same subfolder.

    Symmetric to `POST /sync/invoice/{public_id}/sharepoint`. Useful when:
      * `ALLOW_BOX_WRITES` was off at completion time
      * a project's Box `draw_requests` folder was not mapped yet
      * a prior run predated per-invoice-subfolder support and files landed
        flat in the root of "15 - Draw Requests"

    Additive + idempotent (subfolder is read-or-created; file uploads
    409-recover via the `[box].[File]` ownership registry).
    """
    from entities.invoice_line_item.business.service import InvoiceLineItemService

    service = InvoiceService()
    invoice = service.read_by_public_id(public_id=public_id)
    if not invoice:
        raise_not_found("Invoice")
    if invoice.is_draft:
        # The draw-requests tree is customer-facing; a draft's number/prices
        # are in flux and would strand a stale (or GUID-named) subfolder with
        # wrong-priced PDFs and no cleanup path (Box mirror is forward-only).
        raise HTTPException(
            status_code=400,
            detail="Invoice is a draft — complete it before pushing to Box.",
        )

    line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)

    # 1. Line-item PDFs. Failure-isolated inside the helper; the summary is
    # surfaced so a structural failure is distinguishable from success.
    line_result = service._enqueue_box_line_pdfs(invoice=invoice, line_items=line_items)

    # 2. Packet PDF. Look up the current packet attachment on this invoice
    # (category='invoice_packet'); if none exists, the packet has never been
    # generated + the caller should hit `/generate/invoice/{public_id}/packet`
    # first (or `complete_invoice`, which regenerates).
    from entities.invoice_attachment.business.service import InvoiceAttachmentService
    from entities.attachment.business.service import AttachmentService
    packet_att = None
    try:
        links = InvoiceAttachmentService().read_by_invoice_id(invoice_id=invoice.id)
        att_svc = AttachmentService()
        for link in links:
            if not link.attachment_id:
                continue
            candidate = att_svc.read_by_id(link.attachment_id)
            if candidate and candidate.category == "invoice_packet" and candidate.blob_url:
                packet_att = candidate
                break
    except Exception as e:
        logger.warning(f"sync_invoice_box: packet lookup failed for {public_id}: {e}")

    packet_row = None
    if packet_att:
        packet_row = _enqueue_box_packet_upload(
            invoice=invoice,
            attachment=packet_att,
            blob_url=packet_att.blob_url,
            filename=packet_att.filename or f"Invoice-{invoice.invoice_number or public_id}-Packet.pdf",
        )
    else:
        logger.info(
            f"sync_invoice_box: no packet attachment found for invoice {public_id}; "
            f"line PDFs enqueued but packet upload skipped. Call /generate/invoice/{public_id}/packet first."
        )

    if not line_result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=f"Box line-PDF enqueue failed: {line_result.get('reason')}",
        )

    return item_response({
        "status_code": 200,
        "message": "Box re-sync enqueued",
        "line_pdfs_enqueued": line_result.get("enqueued", 0),
        "line_pdfs_skipped": line_result.get("skipped", 0),
        "line_pdfs_reason": line_result.get("reason"),
        "packet_enqueued": packet_row is not None,
    })


@router.post("/sync/invoice/{public_id}/qbo")
def sync_invoice_to_qbo_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete"))):
    """
    Invoice QBO sync is disabled. Invoices are created manually in QBO.
    """
    logger.info(f"Invoice QBO push sync disabled; skipping for invoice {public_id}")
    return item_response({"status_code": 200, "message": "Invoice QBO sync is disabled. Manage invoices manually in QBO."})
