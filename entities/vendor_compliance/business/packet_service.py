# Python Standard Library Imports
import io
import re

# Third-party Imports

# Local Imports
from entities.attachment.business.service import AttachmentService
from entities.certificate_of_insurance.business.service import CertificateOfInsuranceService
from entities.vendor.business.service import VendorService
from entities.vendor_compliance.business.read_helpers import resolve_latest_w9_attachment
from entities.vendor_insurance_policy.business.service import VendorInsurancePolicyService
from shared.pdf_utils import merge_pdfs
from shared.storage import AzureBlobStorage


class VendorCompliancePacketService:
    """
    Builds vendor compliance packet PDFs (cover + licenses + COI + W-9)
    and resolves single-document downloads.
    """

    def _build_cover_pdf(self, vendor_name: str, doc_rows: list[dict], w9_present: bool) -> bytes:
        import html as _html
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER

        BLUE = colors.HexColor("#1F3864")
        RED = colors.red

        wrap_style = ParagraphStyle("cover_wrap", fontName="Helvetica", fontSize=8, leading=10)
        wrap_hdr = ParagraphStyle(
            "cover_wrap_hdr", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE
        )

        def W(text):
            return Paragraph(_html.escape(str(text)) if text else "", wrap_style)

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=letter,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.5 * inch,
        )

        col_widths = [180, 120, 100]
        headers = [
            Paragraph("Document", wrap_hdr),
            Paragraph("Number", wrap_hdr),
            Paragraph("Expiry", wrap_hdr),
        ]

        table_data = [headers]
        for row in doc_rows:
            table_data.append([
                W(row.get("type_label", "")),
                row.get("document_number") or "",
                row.get("expiry_date") or "",
            ])

        w9_text = "W-9: included" if w9_present else "W-9: MISSING"
        w9_style = ParagraphStyle(
            "cover_w9",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=BLUE if w9_present else RED,
        )
        table_data.append([Paragraph(w9_text, w9_style), "", ""])

        n = len(table_data)
        table = Table(table_data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
            ("TOPPADDING", (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, BLUE),
            ("FONTNAME", (0, 1), (-1, n - 2), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, n - 2), 8),
            ("TOPPADDING", (0, 1), (-1, n - 2), 3),
            ("BOTTOMPADDING", (0, 1), (-1, n - 2), 3),
            ("LINEBELOW", (0, 1), (-1, n - 2), 0.25, colors.HexColor("#CCCCCC")),
            ("FONTNAME", (0, n - 1), (-1, n - 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, n - 1), (-1, n - 1), 8),
            ("TOPPADDING", (0, n - 1), (-1, n - 1), 5),
            ("BOTTOMPADDING", (0, n - 1), (-1, n - 1), 4),
            ("LINEABOVE", (0, n - 1), (-1, n - 1), 0.75, colors.black),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))

        title = vendor_name or "Vendor"
        doc.build([
            Paragraph(
                f"Compliance Packet — {title}",
                ParagraphStyle(
                    "CoverTitle",
                    fontName="Helvetica-Bold",
                    fontSize=12,
                    textColor=BLUE,
                    alignment=TA_CENTER,
                    spaceAfter=8,
                ),
            ),
            table,
        ])
        return buf.getvalue()

    def _earliest_policy_expiry(self, certificate_of_insurance_id: int) -> str | None:
        policies = VendorInsurancePolicyService().read_by_certificate_of_insurance_id(
            certificate_of_insurance_id
        )
        expiry_dates = [p.expiry_date for p in policies if p.expiry_date]
        if not expiry_dates:
            return None
        return min(expiry_dates)

    def build_packet(self, vendor_public_id: str) -> tuple[bytes, str]:
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        certs = CertificateOfInsuranceService().read_by_vendor_id(vendor_id=int(vendor.id))
        cert = certs[0] if certs else None

        att_service = AttachmentService()
        blob_urls = []
        doc_summary_rows = []

        if cert and cert.attachment_id:
            attachment = att_service.read_by_id(int(cert.attachment_id))
            if attachment and attachment.blob_url:
                blob_urls.append(attachment.blob_url)
                doc_summary_rows.append({
                    "type_label": "Certificate of Insurance",
                    "document_number": None,
                    "expiry_date": (
                        self._earliest_policy_expiry(int(cert.id)) if cert.id else None
                    ),
                })

        w9_present = False
        w9_att = resolve_latest_w9_attachment(vendor)
        if w9_att and w9_att.blob_url:
            blob_urls.append(w9_att.blob_url)
            w9_present = True

        cover = self._build_cover_pdf(vendor.name or "", doc_summary_rows, w9_present)
        pdf_bytes = merge_pdfs(blob_urls, leading_pdf_bytes=[cover])

        base_name = vendor.abbreviation or vendor.name or "vendor"
        safe_stem = re.sub(r"[^A-Za-z0-9]+", "-", base_name).strip("-") or "vendor"
        filename = f"{safe_stem}-compliance-packet.pdf"
        return pdf_bytes, filename

    def resolve_single_doc(self, document_public_id: str) -> tuple[bytes, str]:
        doc = CertificateOfInsuranceService().read_by_public_id(public_id=document_public_id)
        if not doc:
            raise ValueError(
                f"Vendor compliance document with public_id '{document_public_id}' not found"
            )
        if not doc.attachment_id:
            raise ValueError("Vendor compliance document has no attachment")

        attachment = AttachmentService().read_by_id(int(doc.attachment_id))
        if not attachment or not attachment.blob_url:
            raise ValueError("Vendor compliance document has no attachment")

        content, _ = AzureBlobStorage().download_file(attachment.blob_url)
        filename = attachment.original_filename or f"{document_public_id}.pdf"
        return content, filename
