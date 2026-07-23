# Python Standard Library Imports
import html
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


def _pdf_text(value) -> str:
    """Escape a plain-text value for safe inclusion in a reportlab Paragraph, whose mini-XML parser requires & < > escaped. None-safe."""
    return html.escape('' if value is None else str(value))


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

    def _billed_feeder_blocks_rebuild(
        self,
        existing_bli_ids: set,
        billed_cls: list,
        line_items_cache: dict,
    ) -> bool:
        """True when a billed CL feeder still references this bill's BLIs."""
        if any(cl.bill_line_item_id in existing_bli_ids for cl in billed_cls):
            return True
        for cl in billed_cls:
            if cl.id not in line_items_cache:
                line_items_cache[cl.id] = (
                    self.line_item_repo.read_by_contract_labor_id(
                        contract_labor_id=cl.id
                    )
                )
            if any(
                li.bill_line_item_id in existing_bli_ids
                for li in line_items_cache[cl.id]
            ):
                return True
        return False

    def generate_invoice_number(self, billing_period: str, project_abbreviation: str) -> str:
        """
        Generate invoice number in format: YYYY.MM.DD.{ProjectAbbreviation}
        """
        try:
            bp_date = datetime.strptime(billing_period, "%Y-%m-%d")
            return f"{bp_date.year}.{bp_date.month:02d}.{bp_date.day:02d}.{project_abbreviation}"
        except Exception:
            return f"{billing_period.replace('-', '.')}.{project_abbreviation}"

    def generate_bills_for_vendor(
        self,
        vendor_id: int,
        billing_period_start: Optional[str] = None,
        project_id_filter: Optional[int] = None,
    ) -> dict:
        """
        Generate bills for a vendor's ready entries. Groups by project and
        creates one Bill per project (or one Bill scoped to a single project
        when `project_id_filter` is supplied — a multi-project CL then only
        contributes its lines matching that project; its other lines stay
        `ready` for a later run).

        Args:
            vendor_id: the CL's real Vendor FK.
            billing_period_start: optional YYYY-MM-DD; passes through to the
                by-status sproc.
            project_id_filter: optional Project FK to restrict billing to a
                single project. When set, only line items whose ProjectId
                matches are billed; a CL is only transitioned to `billed`
                if EVERY one of its lines is now linked to a BillLineItem.
                Multi-project CLs with lines on other projects remain
                `ready` for a later pass. Named `_filter` to avoid
                colliding with the `project_id` iterator variable in the
                per-project loop below.

        Returns dict with counts + `pdf_urls` + `errors`.
        """
        result = {
            "bills_created": 0,
            "bills_updated": 0,
            "bills_refused": 0,
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

        # Get ready entries for this vendor, filtered by billing period if
        # provided. Match on `vendor_id` (the CL's real vendor FK, set by the
        # aggregator or the vendor-backfill sweep) — the legacy
        # `bill_vendor_id` override is NULL for almost every row now.
        ready_entries = self.cl_service.read_by_status(status="ready", billing_period_start=billing_period_start)
        vendor_entries = [e for e in ready_entries if e.vendor_id == vendor_id]

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

        # Compute the period key for a given date. Bi-monthly periods:
        # day 1-15  → period ending on the 15th of that month
        # day 16-end → period ending on the last day of that month
        # Returns YYYY-MM-DD of the period END; this doubles as bill_date
        # and drives the YYYY.MM.DD.<project> invoice_number.
        def _period_end_for(date_str: Optional[str]) -> Optional[str]:
            if not date_str:
                return None
            try:
                d = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if d.day <= 15:
                    return d.replace(day=15).strftime("%Y-%m-%d")
                last = monthrange(d.year, d.month)[1]
                return d.replace(day=last).strftime("%Y-%m-%d")
            except Exception:
                return None

        for entry in vendor_entries:
            line_items = self.line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
            entry_added = False
            for li in line_items:
                li_project_id = li.project_id
                if not li_project_id and not li.is_overhead:
                    continue  # Skip line items without a project (unless overhead)

                # Project scope filter — when the caller passes
                # project_id_filter, skip every line item on a different
                # project. Overhead (li_project_id=None) is included only
                # if the caller didn't supply a filter.
                if project_id_filter is not None and li_project_id != project_id_filter:
                    continue

                # Group by (project, billing_period_end). The period is
                # derived from li.line_date (fallback to entry.work_date)
                # per Chris' rule — 1st-15th = first half, 16th-eom = second
                # half. Prevents multiple periods collapsing into one bill,
                # which is what happened before this fix. Note: overhead
                # lines (project=None) still group per-period.
                period_end = _period_end_for(
                    str(li.line_date) if li.line_date else (str(entry.work_date) if entry.work_date else None)
                )
                group_key = (li_project_id, period_end)  # None-safe on either

                if group_key not in project_groups:
                    project_groups[group_key] = []
                    entry_ids_by_project[group_key] = set()

                project_groups[group_key].append({
                    "line_item": li,
                    "entry": entry,
                    "project": project_map.get(li_project_id) if li_project_id else None,
                    "scc": scc_map.get(li.sub_cost_code_id),
                    "period_end": period_end,
                })
                entry_ids_by_project[group_key].add(entry.id)
                entry_added = True

            if not entry_added:
                # Only report as an error when we WEREN'T project-filtering
                # — a filtered run legitimately skips entries with no
                # matching line, that's expected, not an error.
                if project_id_filter is None:
                    result["errors"].append(
                        f"Entry ID {entry.id} ({entry.employee_name}, {entry.work_date}) skipped: "
                        f"no line items with a project assigned"
                    )

        # Count how many bill-groups each entry participates in. A CL that
        # lands in multiple (project × period) groups is "split" across
        # bills — storing a single BLI id on the parent's
        # `bill_line_item_id` would silently overwrite whichever
        # group's linkage came last. For those, leave the parent field
        # NULL and rely on the per-line `ContractLaborLineItem.BillLineItemId`
        # FK (set below at update_by_id) as the authoritative linkage.
        entry_project_count: dict[int, int] = {}
        for eid_set in entry_ids_by_project.values():
            for eid in eid_set:
                entry_project_count[eid] = entry_project_count.get(eid, 0) + 1

        expected_period_end = (
            _period_end_for(billing_period_start) if billing_period_start else None
        )
        billed_cls_cache = None
        billed_cl_line_items_cache: dict = {}

        # Generate a bill per (project, billing_period) group. project_id=None
        # means overhead. `period_end` is the derived last day of the period
        # (15th or end-of-month) and doubles as bill_date + invoice_number
        # segment, so lines from work spanning two half-months land on two
        # separate bills.
        for group_key, items in project_groups.items():
            project_id, group_period = group_key
            if expected_period_end is not None and group_period != expected_period_end:
                result["errors"].append(
                    f"off-period group (project={project_id}, period={group_period}) skipped — "
                    f"intended period ends {expected_period_end}"
                )
                continue
            # Populated when we take the edit path (existing bill found);
            # purged only after successful new attachment upload so a
            # failure mid-flow leaves the reviewer with the prior PDF.
            orphan_attachment_ids: set[int] = set()
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

                # Bill date IS the period end (per Chris' bi-monthly rule).
                # Fall back to the first entry's fields only when the group's
                # computed period is missing (unexpected — every LI has a
                # line_date or entry.work_date to derive from).
                billing_period = group_period
                if not billing_period:
                    first_entry = items[0]["entry"]
                    billing_period = first_entry.billing_period_start or first_entry.work_date
                if not billing_period:
                    billing_period = datetime.now().strftime("%Y-%m-%d")

                # Two totals — the Bill entity and the PDF want DIFFERENT numbers:
                #
                #   Bill.TotalAmount = A/P (what we owe the vendor, i.e. COST).
                #     QBO sums line-item Amount (cost) as the bill total, so the
                #     header must match cost, never the marked-up Price.
                #     Price flows into Invoice (what we bill the client) later —
                #     it never touches QBO A/P.
                #     `ContractLaborLineItem` has no persisted `amount` column;
                #     Amount = Hours × Rate (pre-markup). Compute from primitives.
                #
                #   PDF Balance Due = Price (marked-up, client-facing). The PDF
                #     is generated once at bill creation and lives on Box as the
                #     draw-request document; per Chris' 2026-07-02 call, the PDF
                #     reflects Price throughout (per-line PRICE column already
                #     does — Balance Due row must match). Older code reused a
                #     single `total_amount` for both, producing PDFs where the
                #     Balance Due (cost) didn't match the PRICE column sum.
                # Bill.TotalAmount (A/P) = Σ Hours×Rate over ALL lines,
                # billable AND non-billable — we pay the contractor for every
                # logged hour. Non-billable hours are cost we absorb rather
                # than pass to the client; they still hit vendor A/P.
                total_amount_cost = sum(
                    (
                        Decimal(str(item["line_item"].hours or 0))
                        * Decimal(str(item["line_item"].rate or 0))
                    )
                    for item in items
                )
                # PDF Balance Due (client-facing draw request) = Σ Price of
                # BILLABLE lines only. Non-billable lines render at $0.00 on
                # the PDF (they aren't billed to the client) but do count in
                # the A/P total above.
                total_amount_pdf = sum(
                    Decimal(str(item["line_item"].price or 0))
                    for item in items
                    if item["line_item"].is_billable is not False
                )

                # Zero total COST in this (vendor, project, period) group
                # → don't spawn an empty $0 Bill (with its orphan Attachment
                # + PDF). This now fires only for a genuine 0-hour/0-rate
                # placeholder — a purely non-billable slice with real hours
                # HAS cost (A/P) and correctly produces a NotBillable bill.
                if total_amount_cost == 0:
                    result["errors"].append(
                        f"Skipped empty bill for {vendor.name} · "
                        f"{project_abbr} · {billing_period} — zero cost (no hours)."
                    )
                    continue

                invoice_number = self.generate_invoice_number(billing_period, project_abbr)
                due_date = self.get_due_date(billing_period)

                # Check for existing bill (edit path)
                existing_bill = self.bill_service.repo.read_by_bill_number_and_vendor_id(
                    bill_number=invoice_number, vendor_id=vendor.id
                )
                bill = existing_bill
                is_edit = bill is not None

                if bill is None:
                    # `require_attachment=False`: the universal rule that
                    # a Bill needs an attachment at create-time is aimed at
                    # human/agent-driven creation from a source PDF. This
                    # flow SYNTHESIZES the PDF from ready CL entries after
                    # the Bill + BLIs exist (see PDF generation below), so
                    # we take the same escape hatch QBO-pull uses. The
                    # PDF + Attachment + BLIA link are created a few
                    # dozen lines down.
                    bill = self.bill_service.create(
                        vendor_public_id=vendor.public_id,
                        bill_date=billing_period,
                        due_date=due_date,
                        bill_number=invoice_number,
                        total_amount=total_amount_cost,
                        memo=memo,
                        is_draft=True,
                        require_attachment=False,
                    )
                    result["bills_created"] += 1
                else:
                    if bill.is_draft is not True:
                        result["errors"].append(
                            f"Bill {invoice_number} refused — completed bills are never regenerated"
                        )
                        result["bills_refused"] += 1
                        continue

                    existing_blis = self.bill_line_item_service.read_by_bill_id(bill.id)
                    existing_bli_ids = {bli.id for bli in existing_blis if bli.id}

                    if billed_cls_cache is None:
                        billed_cls_cache = [
                            cl
                            for cl in self.cl_repo.read_by_vendor_id(vendor_id)
                            if cl.status == "billed"
                        ]

                    blocked_by_billed_feeder = self._billed_feeder_blocks_rebuild(
                        existing_bli_ids,
                        billed_cls_cache,
                        billed_cl_line_items_cache,
                    )

                    if blocked_by_billed_feeder:
                        result["errors"].append(
                            f"Bill {invoice_number} refused — existing line items are linked to "
                            f"billed contract-labor feeders; to rebuild this bill, reset ALL its "
                            f"period feeders to ready and null their BillLineItemId, then re-run"
                        )
                        result["bills_refused"] += 1
                        continue

                    result["bills_updated"] += 1
                    bill.bill_date = billing_period
                    bill.due_date = due_date
                    bill.total_amount = total_amount_cost
                    bill.memo = memo
                    bill = self.bill_service.repo.update_by_id(bill)
                    if not bill:
                        result["errors"].append(f"Row version conflict updating bill {invoice_number}")
                        continue
                    # Clean up existing BillLineItems and their attachment
                    # links. We MUST delete the BLIA link + BLI rows (FK
                    # from CL) up front to make room for the recreation.
                    # But the Attachment records themselves — which own the
                    # underlying blob file — are held back and only purged
                    # AFTER the new PDF + Attachment have been created and
                    # linked successfully. If any downstream step fails,
                    # the old attachments survive and the reviewer can
                    # still see the prior PDF.
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
                    # Deferred: orphan Attachment records + their blobs.
                    # Purged only after successful new-attachment creation
                    # below (search for "orphan_attachment_ids" purge).

                entry_id_to_first_bli_id = {}
                created_blis_this_project = []
                line_items_created_this_project = 0
                # Group by (SubCostCode, is_billable) so billable and
                # non-billable lines consolidate SEPARATELY — they carry a
                # different QBO BillableStatus and only billable lines get the
                # client markup. Both get a BillLineItem (we owe the vendor
                # for non-billable hours too); non-billable ones push to QBO
                # as NotBillable with no markup.
                by_scc = {}
                for item in items:
                    li = item["line_item"]
                    billable_flag = li.is_billable is not False
                    key = (li.sub_cost_code_id, billable_flag)  # tuple; None scc is valid
                    if key not in by_scc:
                        by_scc[key] = []
                    by_scc[key].append(item)

                try:
                    for (scc_id, billable_flag), group in by_scc.items():
                        first_item = group[0]
                        li_first = first_item["line_item"]
                        scc = first_item["scc"]
                        scc_cost = Decimal("0")   # Σ Hours×Rate — A/P we owe the vendor
                        scc_price = Decimal("0")  # Σ client Price (marked-up); == cost when non-billable
                        for item in group:
                            li = item["line_item"]
                            cost = Decimal(str(li.hours or 0)) * Decimal(str(li.rate or 0))
                            scc_cost += cost
                            if billable_flag:
                                scc_price += Decimal(str(li.price or 0))
                            else:
                                # Non-billable: no client markup — the line's
                                # "price" is irrelevant (never invoiced to the
                                # client), so price == cost on the BLI.
                                scc_price += cost

                        description = (scc.description if scc else None) or li_first.description or ""

                        # A group with zero cost (0-hour / 0-rate placeholder)
                        # creates nothing — nothing owed, nothing to bill.
                        if scc_cost == 0:
                            continue

                        if billable_flag and scc_cost:
                            effective_markup = (scc_price - scc_cost) / scc_cost
                        else:
                            effective_markup = Decimal("0")

                        bli = self.bill_line_item_service.create(
                            bill_public_id=bill.public_id,
                            sub_cost_code_id=scc_id,
                            project_public_id=project.public_id if project else None,
                            description=description,
                            quantity=1,
                            rate=scc_cost,
                            amount=scc_cost,
                            is_billable=billable_flag,
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
                            # Re-read the LI immediately before update so we
                            # use the CURRENT row_version. The cached
                            # `li.row_version_bytes` was captured at loop-
                            # top by `read_by_contract_labor_id`; if the
                            # LI has been touched by anything between then
                            # and now (edit-path BLI recreation, a
                            # concurrent React save, a prior partial run
                            # that made it partway), the cached token is
                            # stale and the optimistic-concurrency check
                            # fails. Re-reading closes that window without
                            # weakening the concurrency guarantee — we
                            # STILL send a row_version, just a fresh one.
                            fresh_li = self.line_item_repo.read_by_id(id=li.id)
                            if fresh_li is None:
                                # LI vanished between load and update
                                # (deleted concurrently). Log and skip;
                                # the BLI it would have linked to is
                                # already committed and covers other lines
                                # in this SCC group.
                                logger.warning(
                                    f"ContractLaborLineItem {li.id} disappeared "
                                    f"during generate-bills; skipping FK linkage"
                                )
                                continue
                            self.line_item_repo.update_by_id(
                                id=fresh_li.id,
                                row_version=fresh_li.row_version_bytes,
                                line_date=fresh_li.line_date,
                                project_id=fresh_li.project_id,
                                sub_cost_code_id=fresh_li.sub_cost_code_id,
                                description=fresh_li.description,
                                hours=fresh_li.hours,
                                rate=fresh_li.rate,
                                markup=fresh_li.markup,
                                price=fresh_li.price,
                                is_billable=fresh_li.is_billable if fresh_li.is_billable is not None else True,
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
                    total_amount=float(total_amount_pdf),
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
                    # New PDF is up + linked. Safe to purge the old
                    # attachments from the prior generation (edit path
                    # only — orphan_attachment_ids is empty when we
                    # created the bill fresh).
                    for att_id in orphan_attachment_ids:
                        try:
                            att = AttachmentService().read_by_id(id=att_id)
                            if att:
                                AttachmentService().delete_by_public_id(public_id=att.public_id)
                        except Exception:
                            logger.warning(f"Could not delete orphan attachment {att_id}")
                except Exception as e:
                    logger.error(f"Failed to upload PDF or create attachment: {e}")
                    result["errors"].append(f"Failed to upload PDF for {invoice_number}: {str(e)}")

                for entry_id in entry_ids_by_project[group_key]:
                    entry = self.cl_repo.read_by_id(id=entry_id)
                    if not entry:
                        continue

                    # Decide whether this CL is now FULLY billed. In a
                    # project-scoped run a multi-project CL may still have
                    # unbilled lines on other projects — those need a later
                    # pass, so keep the parent `ready`. Full-vendor run
                    # (project_id filter is None) always transitions.
                    fully_billed = True
                    if project_id_filter is not None:
                        current_lis = self.line_item_repo.read_by_contract_labor_id(
                            contract_labor_id=entry_id
                        )
                        fully_billed = all(
                            li.bill_line_item_id is not None for li in current_lis
                        )

                    if fully_billed:
                        entry.status = "billed"

                    # Single-project CL → parent's bill_line_item_id is
                    # unambiguous; keep it for legacy callers. Multi-project
                    # → leave NULL; child LI.BillLineItemId carries the
                    # per-project truth.
                    if entry_project_count.get(entry_id, 0) <= 1:
                        entry.bill_line_item_id = entry_id_to_first_bli_id.get(entry_id)
                    else:
                        entry.bill_line_item_id = None
                    updated = self.cl_repo.update_by_id(entry)
                    if updated and fully_billed:
                        result["entries_billed"] += 1

            except Exception as e:
                logger.exception(f"Error generating bill for project {project_id} period {group_period}")
                result["errors"].append(f"Error generating bill for project {project_id} period {group_period}: {str(e)}")

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

            # Collect every Bill referenced by this CL. Sources in preference
            # order:
            #   1. Parent's `bill_line_item_id` (single-project fast-path)
            #   2. Every child ContractLaborLineItem.BillLineItemId
            #      (authoritative; multi-project CLs need this — the parent
            #      is now deliberately NULL for those.)
            #   3. Legacy fallback: bill_number + vendor_id lookup.
            entry_line_items = self.line_item_repo.read_by_contract_labor_id(
                contract_labor_id=entry.id
            )
            candidate_bli_ids: set[int] = set()
            if entry.bill_line_item_id:
                candidate_bli_ids.add(entry.bill_line_item_id)
            for li in entry_line_items:
                if li.bill_line_item_id:
                    candidate_bli_ids.add(li.bill_line_item_id)

            entry_bills: list = []
            for bli_id in candidate_bli_ids:
                bli = bill_line_item_service.read_by_id(id=bli_id)
                if bli and bli.bill_id:
                    b = self.bill_service.read_by_id(id=bli.bill_id)
                    if b and all(existing.id != b.id for existing in entry_bills):
                        entry_bills.append(b)

            if not entry_bills and entry.bill_number and entry.vendor_id:
                b = self.bill_service.repo.read_by_bill_number_and_vendor_id(
                    bill_number=entry.bill_number, vendor_id=entry.vendor_id,
                )
                if b:
                    entry_bills.append(b)

            if not entry_bills:
                continue

            for bill in entry_bills:
                bill_id = bill.id
                if bill_id not in bills:
                    vendor = vendor_map.get(bill.vendor_id)
                    bills[bill_id] = {
                        "bill": bill,
                        "vendor": vendor,
                        "project_groups": {},
                    }

                # Only add this entry's lines that belong to THIS bill (i.e.
                # whose BLI parent bill matches). Prevents multi-project CLs
                # from double-rendering every line under both bills.
                for li in entry_line_items:
                    if li.bill_line_item_id:
                        bli = bill_line_item_service.read_by_id(id=li.bill_line_item_id)
                        if not bli or bli.bill_id != bill_id:
                            continue

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

        # Get ready entries for this vendor, filtered by billing period if
        # provided. Match on `vendor_id` (same rationale as
        # generate_bills_for_vendor above).
        ready_entries = self.cl_service.read_by_status(status="ready", billing_period_start=billing_period_start)
        vendor_entries = [e for e in ready_entries if e.vendor_id == vendor_id]

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
                Paragraph(f"<b>{_pdf_text(vendor_name)}</b><br/>{_pdf_text(vendor_address['address'])}<br/>{_pdf_text(vendor_address['city_state_zip'])}", styles['Normal']),
                Paragraph(f"<b>BILL TO</b><br/>{_pdf_text(BILL_TO['name'])}<br/>{_pdf_text(BILL_TO['address'])}<br/>{_pdf_text(BILL_TO['city_state_zip'])}", styles['Normal']),
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
            Paragraph(_pdf_text(invoice_number), details_style),
            bill_date_display,
            f"${total_amount:,.2f}",
            due_date_display,
            'Net 15',
            Paragraph(_pdf_text(project_display), details_style),
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

        # Line items table.
        # Column header is "PRICE" because the values in it are BillLineItem.price
        # (per-entry cost × 1+markup) — see line 1034's `float(li.price or 0)`.
        # Bill.TotalAmount, by contrast, is A/P (cost) — the summed line-item
        # Amount, not Price. The two are DIFFERENT numbers on the same document;
        # labeling this column "Amount" caused confusion during the 2026-07-02
        # completion review.
        line_items_header = ['DATE', 'SERVICE', 'DESCRIPTION', 'PRICE']
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
            description = Paragraph(_pdf_text(li.description), desc_style)
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
