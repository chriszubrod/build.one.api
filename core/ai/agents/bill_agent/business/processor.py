# Python Standard Library Imports
import hashlib
import logging
import re
import time
import uuid
from dataclasses import field
from decimal import Decimal, InvalidOperation
from typing import Optional

# Local Imports
from core.ai.agents.bill_agent.business.models import (
    FilenameParsedData,
    ProcessingResult,
)
from entities.attachment.business.service import AttachmentService
from entities.bill.business.service import BillService
from entities.bill.business.claude_extraction_service import ClaudeExtractionService
from entities.bill.business.extraction_mapper import BillExtractionMapper
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.payment_term.business.service import PaymentTermService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.sub_cost_code.business.alias_service import SubCostCodeAliasService
from entities.vendor.business.service import VendorService
from integrations.azure.ai.document_intelligence import AzureDocumentIntelligence
from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
from integrations.ms.sharepoint.external import client as sp_client
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


def parse_bill_filename(filename: str) -> FilenameParsedData:
    """
    Parse a bill filename using the 7-segment convention:
    {Project} - {Vendor} - {BillNumber} - {Description} - {SubCostCode} - {Rate} - {BillDate}

    Examples:
        HP2 - Teran Remodeling - 10061 - Garage Pad - 18.1 - $4,280.00 - 2-18-2026.pdf
        HP - Teran Remodeling - 10054 - Hardscapes - 55.0 - $18,025.00 - 12-22-2025
    """
    data = FilenameParsedData()

    # Strip .pdf extension (case-insensitive)
    stem = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE).strip()

    # Split on ' - ' delimiter
    segments = stem.split(' - ')
    if len(segments) != 7:
        return data

    data.project_abbrev = segments[0].strip() or None
    data.vendor_name = segments[1].strip() or None
    data.bill_number = segments[2].strip() or None
    data.description = segments[3].strip() or None
    data.sub_cost_code_raw = segments[4].strip() or None

    # Parse rate: strip $ and commas
    rate_str = segments[5].strip().replace('$', '').replace(',', '')
    if rate_str:
        try:
            data.rate = Decimal(rate_str)
        except InvalidOperation:
            pass

    # Parse date: M-DD-YYYY -> YYYY-MM-DD
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
    """
    Normalize sub cost code from filename format.
    18.1 -> 18.01, 55.0 -> 55.00, 18.10 -> 18.10, 26.0 -> 26.00
    """
    if not raw:
        return None

    parts = raw.split('.')
    if len(parts) == 2:
        major = parts[0]
        minor = parts[1]
        # Zero-pad minor part to 2 digits
        if len(minor) == 1:
            minor = minor + '0'
        return f"{major}.{minor}"
    return raw


def _resolve_project(abbrev: str, projects: list) -> Optional[str]:
    """
    Fuzzy prefix match: project abbreviation matches the prefix before ' - ' in project.name.
    e.g. "HP" matches "HP - 6135 Hillsboro Road"
    Returns project.public_id or None.
    """
    if not abbrev:
        return None

    abbrev_lower = abbrev.lower().strip()

    for project in projects:
        name = getattr(project, 'name', '') or ''
        # Check if project name starts with abbreviation followed by ' - '
        name_lower = name.lower()
        if name_lower.startswith(abbrev_lower + ' - ') or name_lower == abbrev_lower:
            return project.public_id

    # Fallback: substring/prefix match
    for project in projects:
        name = getattr(project, 'name', '') or ''
        if name.lower().startswith(abbrev_lower):
            return project.public_id

    return None


def _resolve_vendor(vendor_name: str, vendors: list) -> Optional[str]:
    """
    Match vendor name from filename to database vendors.
    Uses exact match first, then Jaccard/containment fuzzy match.
    Returns vendor.public_id or None.
    """
    if not vendor_name:
        return None

    name_lower = vendor_name.lower().strip()

    # Exact match
    for vendor in vendors:
        if (getattr(vendor, 'name', '') or '').lower().strip() == name_lower:
            return vendor.public_id

    # Fuzzy match: containment + Jaccard
    query_tokens = set(re.split(r'\W+', name_lower))
    query_tokens.discard('')

    best_public_id = None
    best_score = 0.0

    for vendor in vendors:
        vname = (getattr(vendor, 'name', '') or '').lower().strip()
        v_tokens = set(re.split(r'\W+', vname))
        v_tokens.discard('')

        if not v_tokens:
            continue

        intersection = query_tokens & v_tokens
        union = query_tokens | v_tokens
        jaccard = len(intersection) / len(union) if union else 0.0
        containment = len(intersection) / len(query_tokens) if query_tokens else 0.0

        score = max(jaccard, containment * 0.85)

        # Substring bonus
        if name_lower in vname or vname in name_lower:
            score = max(score, 0.75)

        if score > best_score and score >= 0.5:
            best_score = score
            best_public_id = vendor.public_id

    return best_public_id


def _resolve_sub_cost_code(raw: str, sub_cost_codes: list, aliases: list = None) -> Optional[int]:
    """
    Match sub cost code number from filename to database.
    Normalizes decimal format: 18.1 -> 18.01 before matching.
    Falls back to alias matching if direct number match fails.
    Returns sub_cost_code.id or None.
    """
    if not raw:
        return None

    normalized = _normalize_sub_cost_code(raw)
    if not normalized:
        return None

    # Direct number match
    for scc in sub_cost_codes:
        scc_number = getattr(scc, 'number', None)
        if scc_number and str(scc_number).strip() == normalized:
            return scc.id

    # Alias fallback
    if aliases:
        raw_stripped = raw.strip()
        for alias_entry in aliases:
            alias_value = getattr(alias_entry, 'alias', None)
            if alias_value and alias_value.strip() == normalized:
                return alias_entry.sub_cost_code_id
            if alias_value and alias_value.strip() == raw_stripped:
                return alias_entry.sub_cost_code_id

    return None


class BillFolderProcessor:
    """
    Deterministic processing service for bill folder files.
    Lists files from SharePoint source folder, extracts data from each PDF,
    creates bill drafts, and moves processed files to the processed folder.
    """

    def __init__(self):
        self.folder_connector = DriveItemBillFolderConnector()
        self.bill_service = BillService()
        self.bill_line_item_service = BillLineItemService()
        self.attachment_service = AttachmentService()
        self.bill_line_item_attachment_service = BillLineItemAttachmentService()
        self.project_service = ProjectService()
        self.vendor_service = VendorService()
        self.sub_cost_code_service = SubCostCodeService()
        self.payment_term_service = PaymentTermService()
        self.extractor = AzureDocumentIntelligence()
        self.claude_extractor = ClaudeExtractionService()
        self.mapper = BillExtractionMapper()
        self.blob_storage = AzureBlobStorage()

    def process(self, company_id: int, tenant_id: int = 1, on_progress=None) -> ProcessingResult:
        """
        Process all PDF files in the source folder.

        1. Get source and processed folder configs
        2. List PDF files in source folder
        3. For each file: parse filename, OCR, extract, create bill draft, move file
        """
        result = ProcessingResult()

        # Get source folder
        source_folder = self.folder_connector.get_folder(company_id, "source")
        if not source_folder:
            raise ValueError("No source folder linked for this company. Link a source folder first.")

        source_drive_id = source_folder.get("drive_id")
        source_item_id = source_folder.get("item_id")
        if not source_drive_id or not source_item_id:
            raise ValueError("Source folder missing drive_id or item_id.")

        # Get processed folder
        processed_folder = self.folder_connector.get_folder(company_id, "processed")
        if not processed_folder:
            raise ValueError("No processed folder linked for this company. Link a processed folder first.")

        processed_item_id = processed_folder.get("item_id")
        if not processed_item_id:
            raise ValueError("Processed folder missing item_id.")

        # List files in source folder
        children_result = sp_client.list_drive_item_children(source_drive_id, source_item_id)
        if children_result.get("status_code") != 200:
            raise ValueError(f"Failed to list source folder: {children_result.get('message')}")

        items = children_result.get("items", [])

        # Filter to PDF files (or files with no extension that could be PDFs)
        pdf_files = []
        for item in items:
            item_name = item.get("name", "")
            item_type = item.get("item_type", "")
            if item_type != "file":
                continue
            if item_name.lower().endswith('.pdf') or '.' not in item_name:
                pdf_files.append(item)

        result.files_found = len(pdf_files)
        if not pdf_files:
            logger.info("No PDF files found in source folder")
            return result

        # Load reference data once
        projects = self.project_service.read_all()
        vendors = self.vendor_service.read_all()
        sub_cost_codes = self.sub_cost_code_service.read_all()
        sub_cost_code_aliases = SubCostCodeAliasService().read_all()

        # Look up "Due on receipt" payment term
        payment_term = self.payment_term_service.read_by_name("Due on receipt")
        payment_term_public_id = payment_term.public_id if payment_term else None
        if not payment_term:
            logger.warning("Payment term 'Due on receipt' not found — bills will have no payment term")

        # Process each file
        for i, file_item in enumerate(pdf_files):
            try:
                self._process_single_file(
                    file_item=file_item,
                    drive_id=source_drive_id,
                    processed_item_id=processed_item_id,
                    projects=projects,
                    vendors=vendors,
                    sub_cost_codes=sub_cost_codes,
                    sub_cost_code_aliases=sub_cost_code_aliases,
                    tenant_id=tenant_id,
                    payment_term_public_id=payment_term_public_id,
                    result=result,
                )
                if on_progress:
                    on_progress(result)
            except Exception as e:
                filename = file_item.get("name", "unknown")
                error_msg = f"Error processing '{filename}': {str(e)}"
                logger.exception(error_msg)
                result.errors.append(error_msg)
                result.files_skipped += 1
                if on_progress:
                    on_progress(result)

        return result

    def _move_file_to_processed(
        self, drive_id: str, file_item_id: str, processed_item_id: str, filename: str,
    ) -> None:
        """Move a file to the processed folder, deleting any existing file with the same name."""
        move_result = sp_client.move_item(drive_id, file_item_id, processed_item_id)
        if move_result.get("status_code") == 200:
            return

        if "nameAlreadyExists" not in str(move_result.get("message", "")):
            logger.warning("Move failed for '%s': %s", filename, move_result.get("message"))
            return

        # Find and delete the existing file in the processed folder, then retry
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

    def _process_single_file(
        self,
        file_item: dict,
        drive_id: str,
        processed_item_id: str,
        projects: list,
        vendors: list,
        sub_cost_codes: list,
        sub_cost_code_aliases: list,
        tenant_id: int,
        payment_term_public_id: Optional[str],
        result: ProcessingResult,
    ) -> None:
        """Process a single PDF file: extract data, create bill draft, move file."""
        filename = file_item.get("name", "")
        file_item_id = file_item.get("item_id", "")

        logger.info("Processing file: %s", filename)

        # Step 1: Parse filename (deterministic, free)
        parsed = parse_bill_filename(filename)

        # Resolve entities from filename
        if parsed.project_abbrev:
            parsed.project_public_id = _resolve_project(parsed.project_abbrev, projects)
        if parsed.vendor_name:
            parsed.vendor_public_id = _resolve_vendor(parsed.vendor_name, vendors)
        if parsed.sub_cost_code_raw:
            parsed.sub_cost_code_id = _resolve_sub_cost_code(parsed.sub_cost_code_raw, sub_cost_codes, sub_cost_code_aliases)

        # Step 2: Download file
        content_result = sp_client.get_drive_item_content(drive_id, file_item_id)
        if content_result.get("status_code") != 200:
            raise ValueError(f"Failed to download file: {content_result.get('message')}")

        file_bytes = content_result.get("content")
        if not file_bytes:
            raise ValueError("Downloaded file has no content")

        # Step 3: OCR extraction (skip if filename gave us all 7 fields)
        filename_complete = all([
            parsed.vendor_public_id, parsed.bill_number, parsed.bill_date,
            parsed.description, parsed.rate, parsed.project_public_id,
        ])
        extraction = None
        if not filename_complete:
            # Rate limit: Azure F0 tier allows ~2 req/min
            if hasattr(self, '_last_ocr_time'):
                elapsed = time.time() - self._last_ocr_time
                if elapsed < 20:
                    time.sleep(20 - elapsed)
            try:
                extraction = self.extractor.extract_document(file_bytes, "application/pdf")
            except Exception as e:
                logger.warning("OCR extraction failed for '%s': %s", filename, e)
            self._last_ocr_time = time.time()
        else:
            logger.info("Filename provided all fields, skipping OCR for '%s'", filename)

        # Step 4: Claude extraction for supplemental data
        extraction_result = None
        if extraction:
            try:
                extraction_result = self.claude_extractor.extract(
                    extraction,
                    from_email=None,
                    email_subject=None,
                    attachment_filename=filename,
                    projects=projects,
                    sub_cost_codes=sub_cost_codes,
                )
            except Exception as e:
                logger.warning("Claude extraction failed for '%s': %s", filename, e)

            # Fallback to heuristic mapper
            if not extraction_result:
                try:
                    extraction_result = self.mapper.map(
                        extraction,
                        from_email=None,
                        email_subject=None,
                        attachment_filename=filename,
                    )
                    extraction_result = self.mapper.resolve_entities(extraction_result, tenant_id)
                except Exception as e:
                    logger.warning("Heuristic extraction failed for '%s': %s", filename, e)

        # Step 5: Merge filename + extraction data (filename takes priority)
        vendor_public_id = parsed.vendor_public_id
        bill_number = parsed.bill_number
        bill_date = parsed.bill_date
        due_date = parsed.bill_date  # Default: due_date = bill_date
        description = parsed.description
        project_public_id = parsed.project_public_id
        sub_cost_code_id = parsed.sub_cost_code_id
        rate = parsed.rate
        total_amount = parsed.rate  # Single line item: total = rate
        memo = parsed.description

        # Fill gaps from extraction
        if extraction_result:
            if not vendor_public_id and extraction_result.vendor_match:
                vendor_public_id = extraction_result.vendor_match.public_id
            if not bill_number and extraction_result.bill_number:
                bill_number = extraction_result.bill_number
            if not bill_date and extraction_result.bill_date:
                bill_date = extraction_result.bill_date
            if extraction_result.due_date:
                due_date = extraction_result.due_date
            if not due_date and bill_date:
                due_date = bill_date
            if extraction_result.total_amount and not total_amount:
                total_amount = extraction_result.total_amount
            if extraction_result.memo and not memo:
                memo = extraction_result.memo
            if not project_public_id and extraction_result.project_match:
                project_public_id = extraction_result.project_match.public_id
            if not sub_cost_code_id and extraction_result.sub_cost_code_match:
                sub_cost_code_id = int(extraction_result.sub_cost_code_match.id)

        # Step 6: Validate minimum required fields
        if not vendor_public_id:
            raise ValueError(f"Could not resolve vendor for '{filename}'")
        if not bill_number:
            raise ValueError(f"No bill number found for '{filename}'")
        if not bill_date:
            raise ValueError(f"No bill date found for '{filename}'")
        if not due_date:
            due_date = bill_date

        # Step 6b: Check for duplicate bill (same bill number + vendor)
        existing_bill = self.bill_service.read_by_bill_number_and_vendor_public_id(
            bill_number=bill_number, vendor_public_id=vendor_public_id,
        )
        if existing_bill:
            logger.info(
                "Bill already exists for vendor=%s, bill_number=%s — skipping creation, moving file: %s",
                vendor_public_id, bill_number, filename,
            )
            try:
                self._move_file_to_processed(drive_id, file_item_id, processed_item_id, filename)
            except Exception as e:
                logger.warning("Failed to move duplicate '%s' to processed folder: %s", filename, e)
            result.files_skipped += 1
            return

        # Step 7: Upload PDF to Azure Blob Storage
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        file_extension = ".pdf"
        blob_name = f"bills/{uuid.uuid4()}{file_extension}"
        blob_url = self.blob_storage.upload_file(
            blob_name=blob_name,
            file_content=file_bytes,
            content_type="application/pdf",
        )

        attachment = self.attachment_service.create(
            tenant_id=tenant_id,
            filename=blob_name,
            original_filename=filename,
            file_extension=file_extension,
            content_type="application/pdf",
            file_size=len(file_bytes),
            file_hash=file_hash,
            blob_url=blob_url,
            description=memo,
            category="bill",
        )

        # Step 8: Create Bill draft
        bill = self.bill_service.create(
            tenant_id=tenant_id,
            vendor_public_id=vendor_public_id,
            payment_term_public_id=payment_term_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=True,
        )

        # Step 9: Create BillLineItem (primary from filename)
        line_item = self.bill_line_item_service.create(
            tenant_id=tenant_id,
            bill_public_id=bill.public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=description or memo,
            quantity=1,
            rate=rate,
            amount=rate,
            markup=Decimal("0"),
            price=rate,
            is_draft=True,
        )

        # Step 10: Link attachment to bill line item
        self.bill_line_item_attachment_service.create(
            tenant_id=tenant_id,
            bill_line_item_public_id=line_item.public_id,
            attachment_public_id=attachment.public_id,
        )

        # Step 11: Move file to processed folder
        try:
            self._move_file_to_processed(drive_id, file_item_id, processed_item_id, filename)
        except Exception as e:
            logger.warning("Failed to move '%s' to processed folder: %s (bill was still created)", filename, e)

        result.files_processed += 1
        result.bills_created += 1
        logger.info(
            "Created bill draft: vendor=%s, bill_number=%s, amount=%s, file=%s",
            vendor_public_id, bill_number, total_amount, filename,
        )
