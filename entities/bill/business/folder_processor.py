# Bill Folder Processor
# Reads PDF files from a SharePoint source folder, parses filenames,
# creates draft bills, and moves processed files to a processed folder.

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from entities.attachment.business.service import AttachmentService
from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.payment_term.business.service import PaymentTermService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.vendor.business.service import VendorService
from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
from integrations.ms.sharepoint.external import client as sp_client
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FilenameParsedData:
    """Data extracted from a bill filename."""
    project_abbrev: Optional[str] = None
    vendor_name: Optional[str] = None
    bill_number: Optional[str] = None
    description: Optional[str] = None
    sub_cost_code_raw: Optional[str] = None
    rate: Optional[Decimal] = None
    bill_date: Optional[str] = None  # YYYY-MM-DD
    # Resolved entity IDs
    project_public_id: Optional[str] = None
    vendor_public_id: Optional[str] = None
    sub_cost_code_id: Optional[int] = None


@dataclass
class ProcessingResult:
    """Result of a bill folder processing run."""
    files_found: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    bills_created: int = 0
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_found": self.files_found,
            "files_processed": self.files_processed,
            "files_skipped": self.files_skipped,
            "bills_created": self.bills_created,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def parse_bill_filename(filename: str) -> FilenameParsedData:
    """
    Parse a bill filename using the 7-segment convention:
    {Project} - {Vendor} - {BillNumber} - {Description} - {SubCostCode} - {Rate} - {BillDate}.pdf
    """
    data = FilenameParsedData()
    stem = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE).strip()
    segments = stem.split(' - ')
    if len(segments) != 7:
        return data

    data.project_abbrev = segments[0].strip() or None
    data.vendor_name = segments[1].strip() or None
    data.bill_number = segments[2].strip() or None
    data.description = segments[3].strip() or None
    data.sub_cost_code_raw = segments[4].strip() or None

    rate_str = segments[5].strip().replace('$', '').replace(',', '')
    if rate_str:
        try:
            data.rate = Decimal(rate_str)
        except InvalidOperation:
            pass

    date_str = segments[6].strip()
    if date_str:
        data.bill_date = _parse_filename_date(date_str)

    return data


def _parse_filename_date(date_str: str) -> Optional[str]:
    """Parse M-DD-YYYY or MM-DD-YYYY to YYYY-MM-DD."""
    m = re.match(r'^(\d{1,2})-(\d{1,2})-(\d{4})$', date_str)
    if m:
        month, day, year = m.groups()
        return f"{int(year)}-{int(month):02d}-{int(day):02d}"
    return None


def _normalize_sub_cost_code(raw: str) -> Optional[str]:
    """Normalize sub cost code: 18.1 -> 18.01, 55.0 -> 55.00."""
    if not raw:
        return None
    parts = raw.split('.')
    if len(parts) == 2:
        major = parts[0]
        minor = parts[1]
        if len(minor) == 1:
            minor = minor + '0'
        return f"{major}.{minor}"
    return raw


# ---------------------------------------------------------------------------
# Entity resolution (fuzzy matching)
# ---------------------------------------------------------------------------

def _resolve_project(abbrev: str, projects: list) -> Optional[str]:
    """Match project abbreviation to project.public_id."""
    if not abbrev:
        return None
    abbrev_lower = abbrev.lower().strip()
    for project in projects:
        name = (getattr(project, 'name', '') or '').lower()
        if name.startswith(abbrev_lower + ' - ') or name == abbrev_lower:
            return project.public_id
    for project in projects:
        name = (getattr(project, 'name', '') or '').lower()
        if name.startswith(abbrev_lower):
            return project.public_id
    return None


def _resolve_vendor(vendor_name: str, vendors: list) -> Optional[str]:
    """Match vendor name from filename to vendor.public_id using Jaccard fuzzy match."""
    if not vendor_name:
        return None
    name_lower = vendor_name.lower().strip()

    # Exact match
    for vendor in vendors:
        if (getattr(vendor, 'name', '') or '').lower().strip() == name_lower:
            return vendor.public_id

    # Fuzzy match — strip apostrophes before tokenizing so "G's" and "Gs" match
    normalized_query = re.sub(r"['\u2019]", "", name_lower)
    query_tokens = set(re.split(r'\W+', normalized_query))
    query_tokens.discard('')
    best_public_id = None
    best_score = 0.0

    for vendor in vendors:
        vname = (getattr(vendor, 'name', '') or '').lower().strip()
        normalized_vname = re.sub(r"['\u2019]", "", vname)
        v_tokens = set(re.split(r'\W+', normalized_vname))
        v_tokens.discard('')
        if not v_tokens:
            continue

        intersection = query_tokens & v_tokens
        union = query_tokens | v_tokens
        jaccard = len(intersection) / len(union) if union else 0.0
        containment = len(intersection) / len(query_tokens) if query_tokens else 0.0
        score = max(jaccard, containment * 0.85)

        if name_lower in vname or vname in name_lower:
            score = max(score, 0.75)

        if score > best_score and score >= 0.5:
            best_score = score
            best_public_id = vendor.public_id

    return best_public_id


def _resolve_sub_cost_code(raw: str, sub_cost_codes: list) -> Optional[int]:
    """Match sub cost code number or alias to sub_cost_code.id."""
    if not raw:
        return None
    normalized = _normalize_sub_cost_code(raw)
    if not normalized:
        return None

    raw_stripped = raw.strip()

    for scc in sub_cost_codes:
        scc_number = getattr(scc, 'number', None)
        if scc_number and str(scc_number).strip() == normalized:
            return scc.id

    # Check aliases (pipe-delimited field on SubCostCode)
    for scc in sub_cost_codes:
        aliases_str = getattr(scc, 'aliases', None)
        if not aliases_str:
            continue
        for alias_value in aliases_str.split('|'):
            alias_stripped = alias_value.strip()
            if alias_stripped == normalized or alias_stripped == raw_stripped:
                return scc.id

    return None


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

class BillFolderProcessor:
    """
    Reads PDF files from a SharePoint source folder, creates draft bills
    from filename metadata, and moves processed files to a processed folder.
    """

    def __init__(self):
        self.folder_connector = DriveItemBillFolderConnector()
        self.bill_service = BillService()
        self.bill_line_item_service = BillLineItemService()
        self.attachment_service = AttachmentService()
        self.bill_line_item_attachment_service = BillLineItemAttachmentService()

    def list_pending(self, company_id: int = 1) -> list:
        """List files in the source folder with parsed data and resolve status."""
        source_folder = self.folder_connector.get_folder(company_id, "source")
        if not source_folder:
            return []
        source_drive_id = source_folder.get("drive_id")
        source_item_id = source_folder.get("item_id")
        if not source_drive_id or not source_item_id:
            return []

        children_result = sp_client.list_drive_item_children(source_drive_id, source_item_id)
        if children_result.get("status_code") != 200:
            return []

        items = children_result.get("items", [])
        pdf_files = [
            item for item in items
            if item.get("item_type") == "file" and item.get("name", "").lower().endswith('.pdf')
        ]

        if not pdf_files:
            return []

        # Load reference data
        projects = ProjectService().read_all()
        vendors = VendorService().read_all()
        sub_cost_codes = SubCostCodeService().read_all()

        pending = []
        for file_item in pdf_files:
            filename = file_item.get("name", "")
            item_id = file_item.get("item_id", "")
            parsed = parse_bill_filename(filename)

            vendor_public_id = None
            project_public_id = None
            sub_cost_code_id = None
            vendor_name_resolved = None
            project_name_resolved = None

            if parsed.project_abbrev:
                parsed.project_public_id = _resolve_project(parsed.project_abbrev, projects)
                if parsed.project_public_id:
                    project_public_id = parsed.project_public_id
                    proj = next((p for p in projects if p.public_id == parsed.project_public_id), None)
                    project_name_resolved = proj.name if proj else None

            if parsed.vendor_name:
                parsed.vendor_public_id = _resolve_vendor(parsed.vendor_name, vendors)
                if parsed.vendor_public_id:
                    vendor_public_id = parsed.vendor_public_id
                    v = next((v for v in vendors if v.public_id == parsed.vendor_public_id), None)
                    vendor_name_resolved = v.name if v else None

            if parsed.sub_cost_code_raw:
                parsed.sub_cost_code_id = _resolve_sub_cost_code(
                    parsed.sub_cost_code_raw, sub_cost_codes
                )
                sub_cost_code_id = parsed.sub_cost_code_id

            # Determine status
            issues = []
            if not vendor_public_id:
                issues.append("Vendor not found")
            if not parsed.bill_number:
                issues.append("No bill number")
            if not parsed.bill_date:
                issues.append("No bill date")
            if len(parse_bill_filename(filename).__dict__) and not parsed.vendor_name:
                issues.append("Could not parse filename")

            # Check duplicate
            is_duplicate = False
            if vendor_public_id and parsed.bill_number:
                existing = self.bill_service.read_by_bill_number_and_vendor_public_id(
                    bill_number=parsed.bill_number, vendor_public_id=vendor_public_id,
                )
                if existing:
                    issues.append("Duplicate")
                    is_duplicate = True

            status = "Ready" if not issues else "; ".join(issues)

            pending.append({
                "filename": filename,
                "item_id": item_id,
                "vendor_parsed": parsed.vendor_name,
                "vendor_resolved": vendor_name_resolved,
                "project_parsed": parsed.project_abbrev,
                "project_resolved": project_name_resolved,
                "bill_number": parsed.bill_number,
                "description": parsed.description,
                "sub_cost_code": parsed.sub_cost_code_raw,
                "amount": str(parsed.rate) if parsed.rate else None,
                "date": parsed.bill_date,
                "status": status,
                "is_ready": not issues,
                "is_duplicate": is_duplicate,
            })

        return pending

    def process(self, company_id: int = 1, tenant_id: int = 1) -> ProcessingResult:
        """Process all PDF files in the source folder."""
        result = ProcessingResult()

        # Get source folder
        source_folder = self.folder_connector.get_folder(company_id, "source")
        if not source_folder:
            raise ValueError("No source folder linked for this company.")
        source_drive_id = source_folder.get("drive_id")
        source_item_id = source_folder.get("item_id")
        if not source_drive_id or not source_item_id:
            raise ValueError("Source folder missing drive_id or item_id.")

        # Get processed folder
        processed_folder = self.folder_connector.get_folder(company_id, "processed")
        if not processed_folder:
            raise ValueError("No processed folder linked for this company.")
        processed_item_id = processed_folder.get("item_id")
        if not processed_item_id:
            raise ValueError("Processed folder missing item_id.")

        # List files
        children_result = sp_client.list_drive_item_children(source_drive_id, source_item_id)
        if children_result.get("status_code") != 200:
            raise ValueError(f"Failed to list source folder: {children_result.get('message')}")

        items = children_result.get("items", [])
        pdf_files = [
            item for item in items
            if item.get("item_type") == "file" and item.get("name", "").lower().endswith('.pdf')
        ]

        result.files_found = len(pdf_files)
        if not pdf_files:
            logger.info("No PDF files found in source folder")
            return result

        # Load reference data once
        projects = ProjectService().read_all()
        vendors = VendorService().read_all()
        sub_cost_codes = SubCostCodeService().read_all()

        payment_term = PaymentTermService().read_by_name("Due on receipt")
        payment_term_public_id = payment_term.public_id if payment_term else None

        # Process each file
        for file_item in pdf_files:
            try:
                self._process_single_file(
                    file_item=file_item,
                    drive_id=source_drive_id,
                    processed_item_id=processed_item_id,
                    projects=projects,
                    vendors=vendors,
                    sub_cost_codes=sub_cost_codes,
                    tenant_id=tenant_id,
                    payment_term_public_id=payment_term_public_id,
                    result=result,
                )
            except Exception as e:
                filename = file_item.get("name", "unknown")
                error_msg = f"Error processing '{filename}': {str(e)}"
                logger.exception(error_msg)
                result.errors.append(error_msg)
                result.files_skipped += 1

        return result

    def _process_single_file(
        self,
        file_item: dict,
        drive_id: str,
        processed_item_id: str,
        projects: list,
        vendors: list,
        sub_cost_codes: list,
        tenant_id: int,
        payment_term_public_id: Optional[str],
        result: ProcessingResult,
    ) -> None:
        """Process a single PDF file."""
        filename = file_item.get("name", "")
        file_item_id = file_item.get("item_id", "")
        logger.info("Processing file: %s", filename)

        # Parse filename
        parsed = parse_bill_filename(filename)
        if parsed.project_abbrev:
            parsed.project_public_id = _resolve_project(parsed.project_abbrev, projects)
        if parsed.vendor_name:
            parsed.vendor_public_id = _resolve_vendor(parsed.vendor_name, vendors)
        if parsed.sub_cost_code_raw:
            parsed.sub_cost_code_id = _resolve_sub_cost_code(
                parsed.sub_cost_code_raw, sub_cost_codes
            )

        # Validate required fields
        if not parsed.vendor_public_id:
            raise ValueError(f"Could not resolve vendor for '{filename}'")
        if not parsed.bill_number:
            raise ValueError(f"No bill number found for '{filename}'")
        if not parsed.bill_date:
            raise ValueError(f"No bill date found for '{filename}'")

        due_date = parsed.bill_date

        # Duplicate check
        existing_bill = self.bill_service.read_by_bill_number_and_vendor_public_id(
            bill_number=parsed.bill_number, vendor_public_id=parsed.vendor_public_id,
        )
        if existing_bill:
            logger.info("Bill already exists for vendor=%s, bill_number=%s — skipping: %s",
                        parsed.vendor_public_id, parsed.bill_number, filename)
            try:
                self._move_file_to_processed(drive_id, file_item_id, processed_item_id, filename)
            except Exception as e:
                logger.warning("Failed to move duplicate '%s': %s", filename, e)
            result.files_skipped += 1
            return

        # Download file from SharePoint
        content_result = sp_client.get_drive_item_content(drive_id, file_item_id)
        if content_result.get("status_code") != 200:
            raise ValueError(f"Failed to download file: {content_result.get('message')}")
        file_bytes = content_result.get("content")
        if not file_bytes:
            raise ValueError("Downloaded file has no content")

        # Upload to Azure blob
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        blob_name = f"bills/{uuid.uuid4()}.pdf"
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=file_bytes,
            content_type="application/pdf",
        )

        # Create Attachment record
        attachment = self.attachment_service.create(
            tenant_id=tenant_id,
            filename=blob_name,
            original_filename=filename,
            file_extension="pdf",
            content_type="application/pdf",
            file_size=len(file_bytes),
            file_hash=file_hash,
            blob_url=blob_url,
            description=parsed.description,
            category="bill",
        )

        # Create Bill draft
        bill = self.bill_service.create(
            tenant_id=tenant_id,
            vendor_public_id=parsed.vendor_public_id,
            payment_term_public_id=payment_term_public_id,
            bill_date=parsed.bill_date,
            due_date=due_date,
            bill_number=parsed.bill_number,
            total_amount=parsed.rate,
            memo=parsed.description,
            is_draft=True,
        )

        # Create BillLineItem
        line_item = self.bill_line_item_service.create(
            tenant_id=tenant_id,
            bill_public_id=bill.public_id,
            sub_cost_code_id=parsed.sub_cost_code_id,
            project_public_id=parsed.project_public_id,
            description=parsed.description or parsed.vendor_name,
            quantity=1,
            rate=parsed.rate,
            amount=parsed.rate,
            markup=Decimal("0"),
            price=parsed.rate,
            is_draft=True,
        )

        # Link attachment
        self.bill_line_item_attachment_service.create(
            tenant_id=tenant_id,
            bill_line_item_public_id=line_item.public_id,
            attachment_public_id=attachment.public_id,
        )

        # Move to processed folder
        try:
            self._move_file_to_processed(drive_id, file_item_id, processed_item_id, filename)
        except Exception as e:
            logger.warning("Failed to move '%s' to processed folder: %s (bill was still created)", filename, e)

        result.files_processed += 1
        result.bills_created += 1
        logger.info("Created bill draft: vendor=%s, bill_number=%s, amount=%s, file=%s",
                     parsed.vendor_public_id, parsed.bill_number, parsed.rate, filename)

    def _move_file_to_processed(
        self, drive_id: str, file_item_id: str, processed_item_id: str, filename: str,
    ) -> None:
        """Move a file to the processed folder."""
        move_result = sp_client.move_item(drive_id, file_item_id, processed_item_id)
        if move_result.get("status_code") == 200:
            return

        if "nameAlreadyExists" not in str(move_result.get("message", "")):
            logger.warning("Move failed for '%s': %s", filename, move_result.get("message"))
            return

        # Delete existing file in processed folder, then retry
        children = sp_client.list_drive_item_children(drive_id, processed_item_id)
        if children.get("status_code") == 200:
            for item in children.get("items", []):
                if item.get("name") == filename:
                    sp_client.delete_item(drive_id, item.get("item_id"))
                    logger.info("Deleted existing '%s' from processed folder", filename)
                    break

        retry_result = sp_client.move_item(drive_id, file_item_id, processed_item_id)
        if retry_result.get("status_code") != 200:
            logger.warning("Retry move failed for '%s': %s", filename, retry_result.get("message"))
