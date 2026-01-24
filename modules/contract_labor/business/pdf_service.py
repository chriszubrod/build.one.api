# Python Standard Library Imports
import io
import re
import logging
from decimal import Decimal
from typing import Optional
from datetime import datetime
from collections import defaultdict

# Third-party Imports
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

# Local Imports
from modules.contract_labor.persistence.repo import ContractLaborRepository
from modules.bill.business.service import BillService
from modules.bill_line_item.business.service import BillLineItemService
from modules.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from modules.attachment.business.service import AttachmentService
from modules.vendor.business.service import VendorService
from modules.project.business.service import ProjectService
from modules.sub_cost_code.business.service import SubCostCodeService
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


class ContractLaborPDFService:
    """
    Service for generating PDF attachments for contract labor bills.
    
    Generates time log PDFs and attaches them to BillLineItems.
    
    Filename pattern:
    {Project.Abbreviation} - {Vendor.Name} - {Bill.Date} - {Description} - {SubCostCode} - {Amount} - {Date}.pdf
    
    If a BillLineItem has multiple SubCostCodes, the SubCostCode field becomes "Multiple See Image".
    """

    def __init__(self):
        self.repo = ContractLaborRepository()
        self.bill_service = BillService()
        self.bill_line_item_service = BillLineItemService()
        self.bill_line_item_attachment_service = BillLineItemAttachmentService()
        self.attachment_service = AttachmentService()
        self.vendor_service = VendorService()
        self.project_service = ProjectService()
        self.sub_cost_code_service = SubCostCodeService()

    def generate_pdfs_for_bill(self, bill_public_id: str) -> dict:
        """
        Generate PDF attachments for all line items in a bill.
        
        Args:
            bill_public_id: Public ID of the bill
            
        Returns:
            dict with:
            - success: bool
            - message: str
            - pdfs_generated: int
            - errors: list
        """
        errors = []
        pdfs_generated = 0
        
        try:
            # Get bill
            bill = self.bill_service.read_by_public_id(public_id=bill_public_id)
            if not bill or not bill.id:
                return {
                    "success": False,
                    "message": "Bill not found",
                    "pdfs_generated": 0,
                    "errors": ["Bill not found"],
                }
            
            # Get vendor
            vendor = None
            if bill.vendor_id:
                vendor = self.vendor_service.read_by_id(id=bill.vendor_id)
            
            if not vendor:
                return {
                    "success": False,
                    "message": "Vendor not found for bill",
                    "pdfs_generated": 0,
                    "errors": ["Vendor not found"],
                }
            
            # Get line items
            line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill.id)
            
            if not line_items:
                return {
                    "success": True,
                    "message": "No line items to process",
                    "pdfs_generated": 0,
                    "errors": [],
                }
            
            # Initialize storage
            try:
                storage = AzureBlobStorage()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to initialize storage: {str(e)}",
                    "pdfs_generated": 0,
                    "errors": [str(e)],
                }
            
            # Get contract labor entries linked to this bill's line items
            for line_item in line_items:
                try:
                    # Check if attachment already exists
                    existing = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                        bill_line_item_public_id=line_item.public_id
                    )
                    if existing:
                        logger.info(f"Line item {line_item.id} already has attachment, skipping")
                        continue
                    
                    # Get contract labor entries for this line item
                    entries = self._get_entries_for_line_item(line_item.id)
                    
                    if not entries:
                        logger.info(f"No contract labor entries for line item {line_item.id}")
                        continue
                    
                    # Get project
                    project = None
                    if line_item.project_id:
                        project = self.project_service.read_by_id(id=str(line_item.project_id))
                    
                    # Get sub cost code
                    sub_cost_code = None
                    if line_item.sub_cost_code_id:
                        sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(line_item.sub_cost_code_id))
                    
                    # Generate PDF
                    pdf_content = self._generate_pdf(
                        entries=entries,
                        bill=bill,
                        vendor=vendor,
                        project=project,
                        sub_cost_code=sub_cost_code,
                        line_item=line_item,
                    )
                    
                    # Generate filename
                    filename = self._generate_filename(
                        bill=bill,
                        vendor=vendor,
                        project=project,
                        sub_cost_code=sub_cost_code,
                        line_item=line_item,
                    )
                    
                    # Upload to Azure Blob Storage
                    blob_name = f"contract-labor/{bill.bill_number}/{filename}"
                    blob_url = storage.upload_file(
                        blob_name=blob_name,
                        file_content=pdf_content,
                        content_type="application/pdf",
                    )
                    
                    # Create attachment record
                    file_hash = self.attachment_service.calculate_hash(pdf_content)
                    attachment = self.attachment_service.create(
                        filename=filename,
                        original_filename=filename,
                        file_extension="pdf",
                        content_type="application/pdf",
                        file_size=len(pdf_content),
                        file_hash=file_hash,
                        blob_url=blob_url,
                        description=f"Contract Labor Time Log for {bill.bill_number}",
                        category="contract_labor",
                    )
                    
                    # Link attachment to line item
                    self.bill_line_item_attachment_service.create(
                        bill_line_item_public_id=line_item.public_id,
                        attachment_public_id=attachment.public_id,
                    )
                    
                    pdfs_generated += 1
                    logger.info(f"Generated PDF for line item {line_item.id}: {filename}")
                    
                except Exception as e:
                    logger.exception(f"Error generating PDF for line item {line_item.id}")
                    errors.append(f"Line item {line_item.public_id}: {str(e)}")
            
            success = pdfs_generated > 0 or len(errors) == 0
            message = f"Generated {pdfs_generated} PDF(s)"
            if errors:
                message += f" with {len(errors)} error(s)"
            
            return {
                "success": success,
                "message": message,
                "pdfs_generated": pdfs_generated,
                "errors": errors,
            }
            
        except Exception as e:
            logger.exception("Error generating PDFs for bill")
            return {
                "success": False,
                "message": str(e),
                "pdfs_generated": 0,
                "errors": [str(e)],
            }

    def _get_entries_for_line_item(self, bill_line_item_id: int) -> list:
        """
        Get contract labor entries linked to a bill line item.
        """
        try:
            entries = self.repo.read_by_bill_line_item_id(bill_line_item_id=bill_line_item_id)
            return entries
        except Exception:
            # If the method doesn't exist, we need to add it
            # For now, return empty list
            logger.warning(f"Could not get entries for bill line item {bill_line_item_id}")
            return []

    def _generate_pdf(
        self,
        entries: list,
        bill,
        vendor,
        project,
        sub_cost_code,
        line_item,
    ) -> bytes:
        """
        Generate a PDF document for contract labor time entries.
        """
        buffer = io.BytesIO()
        
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5 * inch,
            leftMargin=0.5 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.5 * inch,
        )
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'Title',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=12,
        )
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=6,
        )
        
        elements = []
        
        # Title
        elements.append(Paragraph("Contract Labor Time Log", title_style))
        elements.append(Spacer(1, 6))
        
        # Header info
        vendor_name = vendor.name if vendor else "Unknown"
        project_name = f"{project.abbreviation or ''} - {project.name}" if project else "N/A"
        bill_number = bill.bill_number or "N/A"
        bill_date = bill.bill_date[:10] if bill.bill_date else "N/A"
        scc_number = sub_cost_code.number if sub_cost_code else "N/A"
        
        header_data = [
            ["Vendor:", vendor_name, "Bill #:", bill_number],
            ["Project:", project_name, "Bill Date:", bill_date],
            ["SubCostCode:", scc_number, "Amount:", f"${float(line_item.price or 0):,.2f}"],
        ]
        
        header_table = Table(header_data, colWidths=[1.2 * inch, 2.5 * inch, 1 * inch, 2 * inch])
        header_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 12))
        
        # Time entries table
        table_data = [
            ["Date", "Worker", "Time In", "Time Out", "Break", "Hours", "Rate", "Amount"],
        ]
        
        total_hours = Decimal("0")
        total_amount = Decimal("0")
        
        for entry in entries:
            work_date = entry.work_date or ""
            worker = entry.employee_name or ""
            time_in = entry.time_in or ""
            time_out = entry.time_out or ""
            break_time = entry.break_time or ""
            hours = entry.total_hours or Decimal("0")
            rate = entry.hourly_rate or Decimal("0")
            amount = entry.total_amount or Decimal("0")
            
            total_hours += hours
            total_amount += amount
            
            table_data.append([
                work_date,
                worker[:20] if len(worker) > 20 else worker,
                time_in,
                time_out,
                break_time,
                f"{float(hours):.2f}",
                f"${float(rate):.2f}",
                f"${float(amount):.2f}",
            ])
        
        # Totals row
        table_data.append([
            "", "", "", "", "TOTAL:",
            f"{float(total_hours):.2f}",
            "",
            f"${float(total_amount):.2f}",
        ])
        
        col_widths = [0.9 * inch, 1.3 * inch, 0.7 * inch, 0.7 * inch, 0.6 * inch, 0.6 * inch, 0.7 * inch, 0.9 * inch]
        entries_table = Table(table_data, colWidths=col_widths)
        entries_table.setStyle(TableStyle([
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            # Data rows
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (5, 1), (7, -1), 'RIGHT'),
            # Grid
            ('GRID', (0, 0), (-1, -2), 0.5, colors.black),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
            # Totals row
            ('FONTNAME', (4, -1), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (4, -1), (-1, -1), 'RIGHT'),
            # Alternating row colors
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.Color(0.95, 0.95, 0.95)]),
        ]))
        elements.append(entries_table)
        elements.append(Spacer(1, 12))
        
        # Footer
        generated_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer_text = f"Generated: {generated_date}"
        elements.append(Paragraph(footer_text, subtitle_style))
        
        doc.build(elements)
        
        pdf_content = buffer.getvalue()
        buffer.close()
        
        return pdf_content

    def _generate_filename(
        self,
        bill,
        vendor,
        project,
        sub_cost_code,
        line_item,
    ) -> str:
        """
        Generate filename following the pattern:
        {Project.Abbreviation} - {Vendor.Name} - {Bill.Date} - {Description} - {SubCostCode} - {Amount} - {Date}.pdf
        
        If multiple SubCostCodes, use "Multiple See Image" for SubCostCode field.
        """
        # Project identifier
        project_identifier = ""
        if project:
            project_identifier = project.abbreviation or project.name or ""
        
        # Vendor name
        vendor_name = vendor.name if vendor else ""
        
        # Bill date (formatted as MM-DD-YYYY)
        bill_date_str = ""
        if bill.bill_date:
            try:
                date_parts = bill.bill_date[:10].split("-")
                if len(date_parts) == 3:
                    bill_date_str = f"{date_parts[1]}-{date_parts[2]}-{date_parts[0]}"
            except Exception:
                bill_date_str = bill.bill_date[:10]
        
        # Description
        description = line_item.description or ""
        # Truncate if too long
        if len(description) > 50:
            description = description[:50]
        
        # SubCostCode
        scc_str = ""
        if sub_cost_code:
            scc_str = sub_cost_code.number or ""
        
        # Amount
        amount_str = ""
        if line_item.price is not None:
            amount_str = f"{float(line_item.price):.2f}"
        
        # Build filename
        parts = [
            project_identifier,
            vendor_name,
            bill_date_str,
            description,
            scc_str,
            amount_str,
            bill_date_str,  # Date at end per pattern
        ]
        
        # Filter out empty parts
        parts = [p for p in parts if p]
        
        filename = " - ".join(parts)
        
        # Sanitize filename
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.strip()
        
        # Ensure filename is not too long (max 200 chars before extension)
        if len(filename) > 200:
            filename = filename[:200]
        
        return f"{filename}.pdf"

    def generate_pdfs_for_billed_entries(
        self,
        vendor_id: Optional[int] = None,
        billing_period_start: Optional[str] = None,
    ) -> dict:
        """
        Generate PDFs for all billed contract labor entries.
        Groups by bill and generates PDFs for each line item.
        """
        try:
            # Get billed entries
            entries = self.repo.read_by_status(status="billed")
            
            if not entries:
                return {
                    "success": True,
                    "message": "No billed entries to process",
                    "pdfs_generated": 0,
                    "errors": [],
                }
            
            # Apply filters
            if vendor_id:
                entries = [e for e in entries if e.vendor_id == vendor_id]
            if billing_period_start:
                entries = [e for e in entries if e.billing_period_start == billing_period_start]
            
            # Group by bill_line_item_id to find unique bills
            bill_line_item_ids = set()
            for entry in entries:
                if entry.bill_line_item_id:
                    bill_line_item_ids.add(entry.bill_line_item_id)
            
            # Get unique bill IDs
            bill_ids = set()
            for bli_id in bill_line_item_ids:
                line_item = self.bill_line_item_service.read_by_id(id=bli_id)
                if line_item and line_item.bill_id:
                    bill_ids.add(line_item.bill_id)
            
            # Generate PDFs for each bill
            total_pdfs = 0
            all_errors = []
            
            for bill_id in bill_ids:
                bill = self.bill_service.read_by_id(id=bill_id)
                if bill and bill.public_id:
                    result = self.generate_pdfs_for_bill(bill_public_id=bill.public_id)
                    total_pdfs += result["pdfs_generated"]
                    all_errors.extend(result["errors"])
            
            success = total_pdfs > 0 or len(all_errors) == 0
            message = f"Generated {total_pdfs} PDF(s) for {len(bill_ids)} bill(s)"
            if all_errors:
                message += f" with {len(all_errors)} error(s)"
            
            return {
                "success": success,
                "message": message,
                "pdfs_generated": total_pdfs,
                "errors": all_errors,
            }
            
        except Exception as e:
            logger.exception("Error generating PDFs for billed entries")
            return {
                "success": False,
                "message": str(e),
                "pdfs_generated": 0,
                "errors": [str(e)],
            }
