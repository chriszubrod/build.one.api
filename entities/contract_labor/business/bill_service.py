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
from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
from entities.contract_labor.persistence.repo import ContractLaborRepository
from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


# Hardcoded vendor config (address, rate, markup)
VENDOR_CONFIG = {
    'Denis Samuel Marcia Izaguirre': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
        'rate': Decimal('240.00'),
        'markup': Decimal('0.50'),
    },
    'Wilmer Diaz': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
        'rate': Decimal('260.00'),
        'markup': Decimal('0.50'),
    },
    'Elmer Cordova': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
        'rate': Decimal('260.00'),
        'markup': Decimal('0.50'),
    },
    'Emilson O. Cordova Tercero': {
        'address': '759 Huntington Parkway',
        'city_state_zip': 'Nashville, TN 37211',
        'rate': Decimal('370.00'),
        'markup': Decimal('0.50'),
    },
    'Selvin Humberto Cordova Tercero': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
        'rate': Decimal('500.00'),
        'markup': Decimal('0.35'),
    },
    'Michael Jacobson': {
        'address': '523 Fatherland St.',
        'city_state_zip': 'Nashville, TN 37206',
        'rate': Decimal('240.00'),
        'markup': Decimal('0.50'),
    },
    'Brayan Rafael Marcia Salina': {
        'address': '212 Delvin Ct.',
        'city_state_zip': 'Antioch, TN 37013',
        'rate': Decimal('240.00'),
        'markup': Decimal('0.50'),
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
        Due date from bill date (billing period):
        - Bill date = 15th → Due date = last day of same month.
        - Bill date = end of month → Due date = 15th of next month.
        """
        try:
            bp_date = datetime.strptime(billing_period, "%Y-%m-%d")
            last_day = monthrange(bp_date.year, bp_date.month)[1]
            if bp_date.day == 15:
                due_date = bp_date.replace(day=last_day)
            elif bp_date.day == last_day:
                if bp_date.month == 12:
                    due_date = bp_date.replace(year=bp_date.year + 1, month=1, day=15)
                else:
                    due_date = bp_date.replace(month=bp_date.month + 1, day=15)
            else:
                due_date = bp_date.replace(day=last_day)
            return due_date.strftime("%Y-%m-%d")
        except Exception:
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

    def generate_bills_for_vendor(self, vendor_id: int, billing_period_start: Optional[str] = None) -> dict:
        """
        Generate bills for all ready entries for a vendor.
        Groups by project and creates one bill per project.
        
        Returns dict with:
        - bills_created: number of new bills created
        - bills_updated: number of existing bills updated
        - line_items_created: number of bill line items created
        - entries_billed: number of contract labor entries marked as billed
        - pdf_urls: list of PDF URLs
        - errors: list of error messages
        """
        result = {
            "bills_created": 0,
            "bills_updated": 0,
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

        # Get ready entries for this vendor, filtered by billing period if provided
        ready_entries = self.cl_service.read_by_status(status="ready", billing_period_start=billing_period_start)
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

        all_entry_ids = {entry.id for entry in vendor_entries}
        for entry in vendor_entries:
            line_items = self.line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
            entry_added = False
            for li in line_items:
                project_id = li.project_id
                if not project_id and not li.is_overhead:
                    continue  # Skip line items without a project (unless overhead)

                group_key = project_id  # None for overhead items

                if group_key not in project_groups:
                    project_groups[group_key] = []
                    entry_ids_by_project[group_key] = set()

                project_groups[group_key].append({
                    "line_item": li,
                    "entry": entry,
                    "project": project_map.get(project_id) if project_id else None,
                    "scc": scc_map.get(li.sub_cost_code_id),
                })
                entry_ids_by_project[group_key].add(entry.id)
                entry_added = True

            if not entry_added:
                result["errors"].append(
                    f"Entry ID {entry.id} ({entry.employee_name}, {entry.work_date}) skipped: "
                    f"no line items with a project assigned"
                )

        # Generate a bill for each project (project_id=None means overhead)
        for project_id, items in project_groups.items():
            try:
                if project_id is not None:
                    project = project_map.get(project_id)
                    if not project:
                        result["errors"].append(f"Project ID {project_id} not found")
                        continue
                    project_abbr = project.abbreviation or project.name[:10]
                    memo = f"Contract Labor - {project.abbreviation or project.name}"
                else:
                    project = None
                    project_abbr = "OVERHEAD"
                    memo = "Contract Labor - Overhead"

                first_entry = items[0]["entry"]
                billing_period = first_entry.billing_period_start or first_entry.work_date
                if not billing_period:
                    billing_period = datetime.now().strftime("%Y-%m-%d")

                total_amount = sum(
                    Decimal(str(item["line_item"].price or 0)) for item in items
                    if item["line_item"].is_billable is not False
                )
                invoice_number = self.generate_invoice_number(billing_period, project_abbr)
                due_date = self.get_due_date(billing_period)

                # Check for existing bill (edit path)
                existing_bill = self.bill_service.repo.read_by_bill_number_and_vendor_id(
                    bill_number=invoice_number, vendor_id=vendor.id
                )
                bill = existing_bill
                is_edit = bill is not None

                if bill is None:
                    bill = self.bill_service.create(
                        vendor_public_id=vendor.public_id,
                        bill_date=billing_period,
                        due_date=due_date,
                        bill_number=invoice_number,
                        total_amount=total_amount,
                        memo=memo,
                        is_draft=True,
                    )
                    result["bills_created"] += 1
                else:
                    result["bills_updated"] += 1
                    bill.bill_date = billing_period
                    bill.due_date = due_date
                    bill.total_amount = total_amount
                    bill.memo = memo
                    bill = self.bill_service.repo.update_by_id(bill)
                    if not bill:
                        result["errors"].append(f"Row version conflict updating bill {invoice_number}")
                        continue
                    # Clean up existing BillLineItems and their attachment links
                    existing_blis = self.bill_line_item_service.read_by_bill_id(bill.id)
                    blia_service = BillLineItemAttachmentService()
                    orphan_attachment_ids = set()
                    for bli in existing_blis:
                        if bli.public_id:
                            link = blia_service.read_by_bill_line_item_id(bill_line_item_public_id=bli.public_id)
                            if link:
                                if link.attachment_id:
                                    orphan_attachment_ids.add(link.attachment_id)
                                blia_service.delete_by_public_id(link.public_id)
                        # Nullify ContractLabor.BillLineItemId before deleting to satisfy FK constraint
                        cl_refs = self.cl_repo.read_by_bill_line_item_id(bli.id)
                        for cl_entry in cl_refs:
                            cl_entry.bill_line_item_id = None
                            self.cl_repo.update_by_id(cl_entry)
                        self.bill_line_item_service.repo.delete_by_id(bli.id)
                    for att_id in orphan_attachment_ids:
                        try:
                            att = AttachmentService().read_by_id(id=att_id)
                            if att:
                                AttachmentService().delete_by_public_id(public_id=att.public_id)
                        except Exception:
                            logger.warning(f"Could not delete orphan attachment {att_id}")

                entry_id_to_first_bli_id = {}
                created_blis_this_project = []
                line_items_created_this_project = 0
                # Group by SubCostCode so we can consolidate into one BillLineItem per SubCostCode
                by_scc = {}
                for item in items:
                    li = item["line_item"]
                    scc_id = li.sub_cost_code_id
                    key = (scc_id,)  # tuple so None is valid key
                    if key not in by_scc:
                        by_scc[key] = []
                    by_scc[key].append(item)

                try:
                    for (scc_id,), group in by_scc.items():
                        first_item = group[0]
                        li_first = first_item["line_item"]
                        scc = first_item["scc"]
                        scc_amount = Decimal("0")
                        scc_price = Decimal("0")
                        any_billable = False
                        for item in group:
                            li = item["line_item"]
                            if li.is_billable is not False:
                                markup_val = Decimal(str(li.markup or 0))
                                price = Decimal(str(li.price or 0))
                                amount = price / (Decimal("1") + markup_val) if markup_val else price
                                scc_amount += amount
                                scc_price += price
                                any_billable = True

                        description = (scc.description if scc else None) or li_first.description or ""

                        # Skip creating a BillLineItem if all items in this group are non-billable
                        if not any_billable:
                            continue

                        if scc_amount:
                            effective_markup = (scc_price - scc_amount) / scc_amount
                        else:
                            effective_markup = Decimal("0")

                        bli = self.bill_line_item_service.create(
                            bill_public_id=bill.public_id,
                            sub_cost_code_id=scc_id,
                            project_public_id=project.public_id if project else None,
                            description=description,
                            quantity=1,
                            rate=scc_amount,
                            amount=scc_amount,
                            is_billable=any_billable,
                            is_billed=False,
                            markup=effective_markup,
                            price=scc_price,
                            is_draft=True,
                        )
                        line_items_created_this_project += 1
                        result["line_items_created"] += 1
                        created_blis_this_project.append(bli)

                        for item in group:
                            li = item["line_item"]
                            self.line_item_repo.update_by_id(
                                id=li.id,
                                row_version=li.row_version_bytes,
                                line_date=li.line_date,
                                project_id=li.project_id,
                                sub_cost_code_id=li.sub_cost_code_id,
                                description=li.description,
                                hours=li.hours,
                                rate=li.rate,
                                markup=li.markup,
                                price=li.price,
                                is_billable=li.is_billable if li.is_billable is not None else True,
                                bill_line_item_id=bli.id,
                            )
                            entry_id = item["entry"].id
                            if entry_id not in entry_id_to_first_bli_id:
                                entry_id_to_first_bli_id[entry_id] = bli.id
                except Exception as e:
                    if not is_edit:
                        try:
                            self.bill_service.delete_by_public_id(bill.public_id)
                        except Exception as cleanup_e:
                            logger.warning(f"Cleanup delete bill failed: {cleanup_e}")
                        result["bills_created"] = max(0, result["bills_created"] - 1)
                    raise e

                pdf_bytes = self._generate_pdf(
                    vendor_name=vendor.name,
                    project=project,
                    invoice_number=invoice_number,
                    bill_date=billing_period,
                    due_date=due_date,
                    total_amount=float(total_amount),
                    line_items=items,
                )
                try:
                    storage = AzureBlobStorage()
                    pdf_filename = f"{bill.public_id}.pdf"
                    pdf_url = storage.upload_file(
                        blob_name=pdf_filename,
                        file_content=pdf_bytes,
                        content_type="application/pdf",
                    )
                    result["pdf_urls"].append(pdf_url)

                    attachment = AttachmentService().create(
                        filename=pdf_filename,
                        original_filename=pdf_filename,
                        file_extension="pdf",
                        content_type="application/pdf",
                        file_size=len(pdf_bytes),
                        file_hash=None,
                        blob_url=pdf_url,
                        description=f"Contract Labor invoice - {(project.abbreviation or project.name) if project else 'Overhead'}",
                        category="invoice",
                    )
                    for bli in created_blis_this_project:
                        BillLineItemAttachmentService().create(
                            bill_line_item_public_id=bli.public_id,
                            attachment_public_id=attachment.public_id,
                        )
                except Exception as e:
                    logger.error(f"Failed to upload PDF or create attachment: {e}")
                    result["errors"].append(f"Failed to upload PDF for {invoice_number}: {str(e)}")

                for entry_id in entry_ids_by_project[project_id]:
                    entry = self.cl_repo.read_by_id(id=entry_id)
                    if not entry:
                        continue
                    entry.status = "billed"
                    entry.bill_line_item_id = entry_id_to_first_bli_id.get(entry_id)
                    updated = self.cl_repo.update_by_id(entry)
                    if updated:
                        result["entries_billed"] += 1

            except Exception as e:
                logger.exception(f"Error generating bill for project {project_id}")
                result["errors"].append(f"Error generating bill for project {project_id}: {str(e)}")

        return result

    def regenerate_pdf_for_entries(self, public_ids: list[str]) -> dict:
        """
        Regenerate invoice PDF for selected billed ContractLabor entries.
        Traces entries → BillLineItem → Bill, groups by bill, and generates
        a combined PDF returned as bytes for viewing.
        """
        from entities.bill_line_item.business.service import BillLineItemService

        cl_service = self.cl_service
        bill_line_item_service = BillLineItemService()

        # Resolve entries and group by bill
        bills = {}  # bill_id -> { bill, vendor, project_groups }
        projects = self.project_service.read_all()
        project_map = {p.id: p for p in projects}
        sub_cost_codes = self.scc_service.read_all()
        scc_map = {s.id: s for s in sub_cost_codes}
        vendors = self.vendor_service.read_all()
        vendor_map = {v.id: v for v in vendors}

        for pub_id in public_ids:
            entry = cl_service.read_by_public_id(public_id=pub_id)
            if not entry:
                continue

            # Find the bill: try BillLineItemId first, fall back to bill_number + bill_vendor_id
            bill = None
            if entry.bill_line_item_id:
                bli = bill_line_item_service.read_by_id(id=entry.bill_line_item_id)
                if bli and bli.bill_id:
                    bill = self.bill_service.read_by_id(id=bli.bill_id)

            if not bill and entry.bill_number and entry.bill_vendor_id:
                bill = self.bill_service.repo.read_by_bill_number_and_vendor_id(
                    bill_number=entry.bill_number, vendor_id=entry.bill_vendor_id,
                )

            if not bill:
                continue

            bill_id = bill.id
            if bill_id not in bills:
                vendor = vendor_map.get(bill.vendor_id)
                bills[bill_id] = {
                    "bill": bill,
                    "vendor": vendor,
                    "project_groups": {},
                }

            # Get line items for this entry and add to project groups
            line_items = self.line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
            for li in line_items:
                project_id = li.project_id
                group_key = project_id  # None for overhead

                if group_key not in bills[bill_id]["project_groups"]:
                    bills[bill_id]["project_groups"][group_key] = []

                bills[bill_id]["project_groups"][group_key].append({
                    "line_item": li,
                    "entry": entry,
                    "project": project_map.get(project_id) if project_id else None,
                    "scc": scc_map.get(li.sub_cost_code_id),
                })

        if not bills:
            return {"error": "No billed entries with linked bills found for the selected records"}

        # Generate combined PDF across all bills
        from reportlab.platypus import PageBreak

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5 * inch,
            leftMargin=0.5 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.5 * inch,
        )

        all_elements = []
        first_page = True

        for bill_id, bill_data in bills.items():
            bill = bill_data["bill"]
            vendor = bill_data["vendor"]
            vendor_name = vendor.name if vendor else "Unknown"

            for project_id, items in bill_data["project_groups"].items():
                if not items:
                    continue

                if not first_page:
                    all_elements.append(PageBreak())
                first_page = False

                project = project_map.get(project_id) if project_id else None
                total_amount = sum(
                    Decimal(str(item["line_item"].price or 0)) for item in items
                    if item["line_item"].is_billable is not False
                )

                elements = self._generate_pdf_elements(
                    vendor_name=vendor_name,
                    project=project,
                    invoice_number=bill.bill_number or "",
                    bill_date=bill.bill_date or "",
                    due_date=bill.due_date or "",
                    total_amount=float(total_amount),
                    line_items=items,
                )
                all_elements.extend(elements)

        doc.build(all_elements)
        buffer.seek(0)
        pdf_bytes = buffer.read()

        vendor_names = set(
            b["vendor"].name for b in bills.values() if b["vendor"]
        )
        filename = "-".join(vendor_names).replace(" ", "-") + "-invoices.pdf"

        return {
            "pdf_bytes": pdf_bytes,
            "filename": filename,
            "bill_count": len(bills),
        }

    def preview_pdf_for_vendor(self, vendor_id: int, project_id: Optional[int] = None, billing_period_start: Optional[str] = None) -> dict:
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

        # Get ready entries for this vendor, filtered by billing period if provided
        ready_entries = self.cl_service.read_by_status(status="ready", billing_period_start=billing_period_start)
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
                if not pid and not li.is_overhead:
                    continue

                group_key = pid  # None for overhead items

                # If project_id filter is set, skip non-matching projects (overhead always included)
                if project_id is not None and pid != project_id and not li.is_overhead:
                    continue

                if group_key not in project_groups:
                    project_groups[group_key] = []

                project_groups[group_key].append({
                    "line_item": li,
                    "entry": entry,
                    "project": project_map.get(pid) if pid else None,
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
            project = project_map.get(project_id) if project_id else None
            if not items:
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

            # Calculate totals (exclude non-billable items, matching generate_bills logic)
            total_amount = sum(
                Decimal(str(item["line_item"].price or 0)) for item in items
                if item["line_item"].is_billable is not False
            )

            # Generate invoice number and due date
            project_abbr = (project.abbreviation or project.name[:10]) if project else "OVERHEAD"
            invoice_number = self.generate_invoice_number(billing_period, project_abbr)
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
        vendor_address = VENDOR_CONFIG.get(vendor_name, {
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

        project_display = project.name if project else "Overhead"

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
            amount = "$0.00" if li.is_billable is False else f"${float(li.price or 0):,.2f}"

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
