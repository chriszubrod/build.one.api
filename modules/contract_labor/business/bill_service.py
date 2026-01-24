# Python Standard Library Imports
import io
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from calendar import monthrange

# Third-party Imports
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

# Local Imports
from modules.contract_labor.business.service import ContractLaborService
from modules.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
from modules.contract_labor.persistence.repo import ContractLaborRepository
from modules.bill.business.service import BillService
from modules.bill_line_item.business.service import BillLineItemService
from modules.vendor.business.service import VendorService
from modules.project.business.service import ProjectService
from modules.sub_cost_code.business.service import SubCostCodeService
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


# Hardcoded vendor addresses
VENDOR_ADDRESSES = {
    'Denis Samuel Marcia Izaguirre': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
    },
    'Wilmer Diaz': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
    },
    'Elmer Cordova': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
    },
    'Emilson O. Cordova Tercero': {
        'address': '759 Huntington Parkway',
        'city_state_zip': 'Nashville, TN 37211',
    },
    'Selvin Humberto Cordova Tercero': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
    },
    'Michael Jacobson': {
        'address': '523 Fatherland St.',
        'city_state_zip': 'Nashville, TN 37206',
    },
}

# Hardcoded Bill To
BILL_TO = {
    'name': 'Rogers Build, Inc.',
    'address': 'PO Box 594',
    'city_state_zip': 'Brentwood, TN 37024',
}


class ContractLaborBillService:
    """
    Service for generating bills from contract labor entries.
    """

    def __init__(self):
        self.cl_service = ContractLaborService()
        self.cl_repo = ContractLaborRepository()
        self.line_item_repo = ContractLaborLineItemRepository()
        self.bill_service = BillService()
        self.bill_line_item_service = BillLineItemService()
        self.vendor_service = VendorService()
        self.project_service = ProjectService()
        self.scc_service = SubCostCodeService()

    def get_due_date(self, billing_period: str) -> str:
        """
        Calculate due date as end of the billing period's month.
        Example: billing_period 2026-01-15 -> due_date 2026-01-31
        """
        try:
            bp_date = datetime.strptime(billing_period, "%Y-%m-%d")
            # Get last day of the month
            last_day = monthrange(bp_date.year, bp_date.month)[1]
            due_date = bp_date.replace(day=last_day)
            return due_date.strftime("%Y-%m-%d")
        except Exception:
            # Fallback: add 15 days
            bp_date = datetime.strptime(billing_period, "%Y-%m-%d")
            return (bp_date + timedelta(days=15)).strftime("%Y-%m-%d")

    def generate_invoice_number(self, billing_period: str, project_abbreviation: str) -> str:
        """
        Generate invoice number in format: YYYY.MM.DD.{ProjectAbbreviation}
        """
        try:
            bp_date = datetime.strptime(billing_period, "%Y-%m-%d")
            return f"{bp_date.year}.{bp_date.month:02d}.{bp_date.day:02d}.{project_abbreviation}"
        except Exception:
            return f"{billing_period.replace('-', '.')}.{project_abbreviation}"

    def generate_bills_for_vendor(self, vendor_id: int) -> dict:
        """
        Generate bills for all ready entries for a vendor.
        Groups by project and creates one bill per project.
        
        Returns dict with:
        - bills_created: number of bills created
        - line_items_created: number of bill line items created
        - entries_billed: number of contract labor entries marked as billed
        - pdf_urls: list of PDF URLs
        - errors: list of error messages
        """
        result = {
            "bills_created": 0,
            "line_items_created": 0,
            "entries_billed": 0,
            "pdf_urls": [],
            "errors": [],
        }

        # Get vendor info
        vendors = self.vendor_service.read_all()
        vendor = next((v for v in vendors if v.id == vendor_id), None)
        if not vendor:
            result["errors"].append(f"Vendor with ID {vendor_id} not found")
            return result

        # Get all ready entries for this vendor
        ready_entries = self.cl_service.read_by_status(status="ready")
        vendor_entries = [e for e in ready_entries if e.bill_vendor_id == vendor_id]

        if not vendor_entries:
            result["errors"].append("No ready entries found for this vendor")
            return result

        # Get lookups
        projects = self.project_service.read_all()
        project_map = {p.id: p for p in projects}
        sub_cost_codes = self.scc_service.read_all()
        scc_map = {s.id: s for s in sub_cost_codes}

        # Group line items by project
        project_groups = {}
        entry_ids_by_project = {}

        for entry in vendor_entries:
            line_items = self.line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
            
            for li in line_items:
                project_id = li.project_id
                if not project_id:
                    continue  # Skip line items without a project
                
                if project_id not in project_groups:
                    project_groups[project_id] = []
                    entry_ids_by_project[project_id] = set()
                
                project_groups[project_id].append({
                    "line_item": li,
                    "entry": entry,
                    "project": project_map.get(project_id),
                    "scc": scc_map.get(li.sub_cost_code_id),
                })
                entry_ids_by_project[project_id].add(entry.id)

        # Generate a bill for each project
        for project_id, items in project_groups.items():
            try:
                project = project_map.get(project_id)
                if not project:
                    result["errors"].append(f"Project ID {project_id} not found")
                    continue

                # Determine billing period from first entry
                first_entry = items[0]["entry"]
                billing_period = first_entry.billing_period_start or first_entry.work_date
                if not billing_period:
                    billing_period = datetime.now().strftime("%Y-%m-%d")

                # Calculate totals
                total_amount = sum(Decimal(str(item["line_item"].price or 0)) for item in items)
                
                # Generate invoice number and due date
                invoice_number = self.generate_invoice_number(
                    billing_period, 
                    project.abbreviation or project.name[:10]
                )
                due_date = self.get_due_date(billing_period)

                # TEMPORARILY DISABLED: Create Bill
                # bill = self.bill_service.create(
                #     vendor_public_id=vendor.public_id,
                #     bill_date=billing_period,
                #     due_date=due_date,
                #     bill_number=invoice_number,
                #     total_amount=total_amount,
                #     memo=f"Contract Labor - {project.abbreviation or project.name}",
                #     is_draft=False,
                # )
                # result["bills_created"] += 1

                # TEMPORARILY DISABLED: Create BillLineItems
                # for item in items:
                #     li = item["line_item"]
                #     self.bill_line_item_service.create(
                #         bill_public_id=bill.public_id,
                #         sub_cost_code_id=li.sub_cost_code_id,
                #         project_public_id=project.public_id,
                #         description=li.description,
                #         quantity=1,
                #         rate=li.rate,
                #         amount=li.price,
                #         is_billable=li.is_billable,
                #         is_billed=True,
                #         markup=li.markup,
                #         price=li.price,
                #         is_draft=False,
                #     )
                #     result["line_items_created"] += 1

                # Generate PDF
                pdf_bytes = self._generate_pdf(
                    vendor_name=vendor.name,
                    project=project,
                    invoice_number=invoice_number,
                    bill_date=billing_period,
                    due_date=due_date,
                    total_amount=float(total_amount),
                    line_items=items,
                )

                # Upload PDF to Azure
                try:
                    storage = AzureBlobStorage()
                    blob_name = f"invoices/{invoice_number.replace('.', '-')}.pdf"
                    pdf_url = storage.upload_file(
                        blob_name=blob_name,
                        file_content=pdf_bytes,
                        content_type="application/pdf",
                    )
                    result["pdf_urls"].append(pdf_url)
                except Exception as e:
                    logger.error(f"Failed to upload PDF: {e}")
                    result["errors"].append(f"Failed to upload PDF for {invoice_number}: {str(e)}")

                # TEMPORARILY DISABLED: Update ContractLabor entries to billed status
                # for entry_id in entry_ids_by_project[project_id]:
                #     entry = self.cl_repo.read_by_id(id=entry_id)
                #     if entry:
                #         self.cl_repo.update_by_id(
                #             id=entry_id,
                #             row_version=entry.row_version_bytes,
                #             status="billed",
                #         )
                #         result["entries_billed"] += 1

            except Exception as e:
                logger.exception(f"Error generating bill for project {project_id}")
                result["errors"].append(f"Error generating bill for project {project_id}: {str(e)}")

        return result

    def preview_pdf_for_vendor(self, vendor_id: int, project_id: Optional[int] = None) -> dict:
        """
        Generate a preview PDF for a vendor (doesn't save to database or Azure).
        Returns the PDF bytes directly for viewing.
        
        If project_id is provided, generates PDF only for that project.
        Otherwise, generates a combined PDF for all projects.
        """
        # Get vendor info
        vendors = self.vendor_service.read_all()
        vendor = next((v for v in vendors if v.id == vendor_id), None)
        if not vendor:
            return {"error": f"Vendor with ID {vendor_id} not found"}

        # Get all ready entries for this vendor
        ready_entries = self.cl_service.read_by_status(status="ready")
        vendor_entries = [e for e in ready_entries if e.bill_vendor_id == vendor_id]

        if not vendor_entries:
            return {"error": "No ready entries found for this vendor"}

        # Get lookups
        projects = self.project_service.read_all()
        project_map = {p.id: p for p in projects}
        sub_cost_codes = self.scc_service.read_all()
        scc_map = {s.id: s for s in sub_cost_codes}

        # Group line items by project
        project_groups = {}

        for entry in vendor_entries:
            line_items = self.line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
            
            for li in line_items:
                pid = li.project_id
                if not pid:
                    continue
                
                # If project_id filter is set, skip other projects
                if project_id is not None and pid != project_id:
                    continue
                
                if pid not in project_groups:
                    project_groups[pid] = []
                
                project_groups[pid].append({
                    "line_item": li,
                    "entry": entry,
                    "project": project_map.get(pid),
                    "scc": scc_map.get(li.sub_cost_code_id),
                })

        if not project_groups:
            return {"error": "No line items found for preview"}

        # Generate combined PDF for all projects
        pdf_bytes = self._generate_combined_pdf(
            vendor_name=vendor.name,
            project_groups=project_groups,
            project_map=project_map,
        )

        # Use vendor name for filename since it's a combined PDF
        filename = f"{vendor.name.replace(' ', '-')}-invoices.pdf"
        
        # Calculate grand total
        grand_total = sum(
            float(item["line_item"].price or 0)
            for items in project_groups.values()
            for item in items
        )
        
        return {
            "pdf_bytes": pdf_bytes,
            "filename": filename,
            "invoice_count": len(project_groups),
            "total_amount": grand_total,
        }

    def _generate_combined_pdf(
        self,
        vendor_name: str,
        project_groups: dict,
        project_map: dict,
    ) -> bytes:
        """
        Generate a combined PDF with all projects, one invoice per page.
        """
        from reportlab.platypus import PageBreak
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.5*inch,
            bottomMargin=0.5*inch,
        )

        all_elements = []
        first_page = True

        for project_id, items in project_groups.items():
            project = project_map.get(project_id)
            if not project or not items:
                continue

            # Add page break before each invoice (except the first)
            if not first_page:
                all_elements.append(PageBreak())
            first_page = False

            # Determine billing period from first entry
            first_entry = items[0]["entry"]
            billing_period = first_entry.billing_period_start or first_entry.work_date
            if not billing_period:
                billing_period = datetime.now().strftime("%Y-%m-%d")

            # Calculate totals
            total_amount = sum(Decimal(str(item["line_item"].price or 0)) for item in items)
            
            # Generate invoice number and due date
            invoice_number = self.generate_invoice_number(
                billing_period, 
                project.abbreviation or project.name[:10]
            )
            due_date = self.get_due_date(billing_period)

            # Generate PDF elements for this invoice
            elements = self._generate_pdf_elements(
                vendor_name=vendor_name,
                project=project,
                invoice_number=invoice_number,
                bill_date=billing_period,
                due_date=due_date,
                total_amount=float(total_amount),
                line_items=items,
            )
            all_elements.extend(elements)

        # Build PDF
        doc.build(all_elements)
        buffer.seek(0)
        return buffer.read()

    def _generate_pdf(
        self,
        vendor_name: str,
        project,
        invoice_number: str,
        bill_date: str,
        due_date: str,
        total_amount: float,
        line_items: list,
    ) -> bytes:
        """
        Generate invoice PDF matching the provided format.
        Returns PDF as bytes.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.5*inch,
            bottomMargin=0.5*inch,
        )

        elements = self._generate_pdf_elements(
            vendor_name=vendor_name,
            project=project,
            invoice_number=invoice_number,
            bill_date=bill_date,
            due_date=due_date,
            total_amount=total_amount,
            line_items=line_items,
        )

        doc.build(elements)
        buffer.seek(0)
        return buffer.read()

    def _generate_pdf_elements(
        self,
        vendor_name: str,
        project,
        invoice_number: str,
        bill_date: str,
        due_date: str,
        total_amount: float,
        line_items: list,
    ) -> list:
        """
        Generate PDF elements for a single invoice.
        Returns list of reportlab elements.
        """
        styles = getSampleStyleSheet()
        elements = []

        # Title
        title_style = ParagraphStyle(
            'Title',
            parent=styles['Heading1'],
            fontSize=24,
            alignment=2,  # Right align
            spaceAfter=20,
        )
        elements.append(Paragraph("Invoice", title_style))
        elements.append(Spacer(1, 10))

        # Vendor info and Bill To
        vendor_address = VENDOR_ADDRESSES.get(vendor_name, {
            'address': '',
            'city_state_zip': '',
        })

        header_data = [
            [
                Paragraph(f"<b>{vendor_name}</b><br/>{vendor_address['address']}<br/>{vendor_address['city_state_zip']}", styles['Normal']),
                Paragraph(f"<b>BILL TO</b><br/>{BILL_TO['name']}<br/>{BILL_TO['address']}<br/>{BILL_TO['city_state_zip']}", styles['Normal']),
            ]
        ]
        header_table = Table(header_data, colWidths=[3.5*inch, 3.5*inch])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOX', (1, 0), (1, 0), 1, colors.black),
            ('LEFTPADDING', (1, 0), (1, 0), 10),
            ('RIGHTPADDING', (1, 0), (1, 0), 10),
            ('TOPPADDING', (1, 0), (1, 0), 5),
            ('BOTTOMPADDING', (1, 0), (1, 0), 5),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 20))

        # Invoice details row
        # Format dates for display
        try:
            bill_date_display = datetime.strptime(bill_date, "%Y-%m-%d").strftime("%m/%d/%Y")
            due_date_display = datetime.strptime(due_date, "%Y-%m-%d").strftime("%m/%d/%Y")
        except Exception:
            bill_date_display = bill_date
            due_date_display = due_date

        project_display = project.name

        # Style for wrapping text in details cells
        details_style = ParagraphStyle(
            'Details',
            parent=styles['Normal'],
            fontSize=9,
            leading=11,
        )

        details_header = ['INVOICE #', 'DATE', 'TOTAL DUE', 'DUE DATE', 'TERMS', 'ENCLOSED']
        details_data = [
            Paragraph(invoice_number, details_style),
            bill_date_display,
            f"${total_amount:,.2f}",
            due_date_display,
            'Net 15',
            Paragraph(project_display, details_style),
        ]
        
        details_table = Table(
            [details_header, details_data],
            colWidths=[1.2*inch, 1*inch, 1*inch, 1*inch, 0.8*inch, 2*inch],
        )
        details_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#B8D4E8')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1F4E79')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('FONTSIZE', (0, 1), (-1, 1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(details_table)
        elements.append(Spacer(1, 20))

        # Line items table
        line_items_header = ['DATE', 'SERVICE', 'DESCRIPTION', 'AMOUNT']
        line_items_data = [line_items_header]

        # Style for wrapping description text
        desc_style = ParagraphStyle(
            'Description',
            parent=styles['Normal'],
            fontSize=9,
            leading=11,
        )

        for item in line_items:
            li = item["line_item"]
            scc = item["scc"]
            
            # Format date
            item_date = li.line_date or item["entry"].work_date or ""
            try:
                item_date_display = datetime.strptime(item_date, "%Y-%m-%d").strftime("%m/%d/%Y")
            except Exception:
                item_date_display = item_date

            service = f"{scc.number}" if scc else ""
            description = Paragraph(li.description or "", desc_style)
            amount = f"${float(li.price or 0):,.2f}"

            line_items_data.append([item_date_display, service, description, amount])

        # Add empty rows to match the template (about 15 total rows)
        while len(line_items_data) < 16:
            line_items_data.append(['', '', Paragraph('', desc_style), ''])

        # Add Balance Due row
        line_items_data.append(['', '', Paragraph('<b>Balance Due</b>', desc_style), f"${total_amount:,.2f}"])

        line_items_table = Table(
            line_items_data,
            colWidths=[1*inch, 0.8*inch, 4.2*inch, 1*inch],
        )
        
        # Style for line items
        table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#B8D4E8')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1F4E79')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),  # Amount column right-aligned
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]
        
        # Bold the Balance Due row
        balance_row = len(line_items_data) - 1
        table_style.append(('FONTNAME', (2, balance_row), (-1, balance_row), 'Helvetica-Bold'))
        table_style.append(('FONTSIZE', (2, balance_row), (-1, balance_row), 11))
        
        line_items_table.setStyle(TableStyle(table_style))
        elements.append(line_items_table)

        return elements
