# Expense Folder Processor
# Reads PDF files from a SharePoint source folder, parses filenames,
# creates draft expenses, and moves processed files to a processed folder.

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from shared.authz import current_user_id
from entities.attachment.business.service import AttachmentService
from entities.expense.business.service import ExpenseService
from entities.expense.persistence.folder_run_repo import (
    ExpenseFolderRunItemRepository,
    ExpenseFolderRunRepository,
)
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.vendor.business.service import VendorService
# Resolution helpers are generic — reuse the bill processor's module-level
# functions rather than duplicating them.
from entities.bill.business.folder_processor import (
    _parse_filename_date,
    _resolve_project,
    _resolve_sub_cost_code,
    _resolve_vendor,
)
from integrations.ms.sharepoint.driveitem.connector.expense_folder.business.service import DriveItemExpenseFolderConnector
from integrations.ms.sharepoint.external import client as sp_client
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


class ExpenseFolderEnumerationError(RuntimeError):
    """Raised when listing the SharePoint source folder fails."""


def enqueue_expense_folder_run(dedup_active: bool = False) -> dict:
    """
    List the SharePoint source folder, create an ExpenseFolderRun, and insert
    one ExpenseFolderRunItem (status='queued') per PDF. The scheduler tick
    drains the queue one file at a time.

    When `dedup_active=True` the enumerator skips item_ids that are
    'queued'/'processing' OR were attempted in the last hour (success,
    skip, or fail). The hour window prevents files that keep failing
    from churning a fresh queue row every 5-min tick — once the cause
    is fixed (or the window expires) the next pass picks them up. On
    noop, no run row is created so empty ticks don't pollute history.

    The button-driven path passes `dedup_active=False` so an enumeration
    failure is captured against a freshly-created run row (the UI shows
    the error tied to that run). The scheduled path passes `True`.
    """
    run_repo = ExpenseFolderRunRepository()
    item_repo = ExpenseFolderRunItemRepository()

    if dedup_active:
        try:
            pdfs = ExpenseFolderProcessor().enumerate_source_folder(company_id=1)
        except Exception as error:
            logger.exception("Folder enumeration failed (scheduled path)")
            raise ExpenseFolderEnumerationError(str(error)) from error
        active_ids = item_repo.read_active_item_ids()
        if active_ids:
            pdfs = [p for p in pdfs if p["item_id"] not in active_ids]
        if not pdfs:
            return {"status": "noop", "files_queued": 0}
        run = run_repo.create(created_by_user_id=current_user_id.get())
    else:
        run = run_repo.create(created_by_user_id=current_user_id.get())
        try:
            pdfs = ExpenseFolderProcessor().enumerate_source_folder(company_id=1)
        except Exception as error:
            logger.exception("Folder enumeration failed for run %s", run.public_id)
            run_repo.update_status(
                public_id=run.public_id,
                status="failed",
                result={"error": str(error)},
                set_completed=True,
            )
            raise ExpenseFolderEnumerationError(str(error)) from error

    actor_id = current_user_id.get()
    for pdf in pdfs:
        item_repo.create(
            run_id=run.id,
            filename=pdf["filename"],
            item_id=pdf["item_id"],
            created_by_user_id=actor_id,
        )

    if not pdfs:
        # Button click on an empty folder — close the run immediately so
        # the UI stops polling instead of waiting for a tick that won't fire.
        item_repo.check_and_complete_run(run_id=run.id)

    return {
        "status": "accepted",
        "run_id": run.public_id,
        "files_queued": len(pdfs),
    }


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExpenseFilenameParsedData:
    """Data extracted from an expense filename."""
    project_abbrev: Optional[str] = None
    vendor_name: Optional[str] = None
    reference_number: Optional[str] = None
    description: Optional[str] = None
    sub_cost_code_raw: Optional[str] = None
    amount: Optional[Decimal] = None
    expense_date: Optional[str] = None  # YYYY-MM-DD
    is_credit: bool = False
    # Resolved entity IDs
    project_public_id: Optional[str] = None
    vendor_public_id: Optional[str] = None
    sub_cost_code_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def parse_expense_filename(filename: str) -> ExpenseFilenameParsedData:
    """
    Parse an expense filename using the 7-segment convention:
    {Project} - {Vendor} - {ReferenceNumber} - {Description} - {SubCostCode} - {Amount} - {ExpenseDate}.pdf

    A leading `CR ` or `EXP-CR ` prefix (case-insensitive) flags the file as a
    credit/refund and is stripped before the 7-segment split.
    """
    data = ExpenseFilenameParsedData()
    stem = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE).strip()

    # Credit marker — strip the prefix before splitting.
    if re.match(r'^EXP-CR\s', stem, flags=re.IGNORECASE):
        data.is_credit = True
        stem = re.sub(r'^EXP-CR\s+', '', stem, flags=re.IGNORECASE).strip()
    elif re.match(r'^CR\s', stem, flags=re.IGNORECASE):
        data.is_credit = True
        stem = re.sub(r'^CR\s+', '', stem, flags=re.IGNORECASE).strip()

    segments = stem.split(' - ')
    if len(segments) != 7:
        return data

    data.project_abbrev = segments[0].strip() or None
    data.vendor_name = segments[1].strip() or None
    data.reference_number = segments[2].strip() or None
    data.description = segments[3].strip() or None
    data.sub_cost_code_raw = segments[4].strip() or None

    amount_str = segments[5].strip().replace('$', '').replace(',', '')
    if amount_str:
        try:
            data.amount = Decimal(amount_str)
        except InvalidOperation:
            pass

    date_str = segments[6].strip()
    if date_str:
        data.expense_date = _parse_filename_date(date_str)

    return data


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

class ExpenseFolderProcessor:
    """
    Reads PDF files from a SharePoint source folder, creates draft expenses
    from filename metadata, and moves processed files to a processed folder.
    """

    def __init__(self):
        self.folder_connector = DriveItemExpenseFolderConnector()
        self.expense_service = ExpenseService()
        self.attachment_service = AttachmentService()

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
            parsed = parse_expense_filename(filename)

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
            if not parsed.reference_number:
                issues.append("No reference number")
            if not parsed.expense_date:
                issues.append("No expense date")
            if not parsed.vendor_name:
                issues.append("Could not parse filename")

            # Check duplicate
            is_duplicate = False
            if vendor_public_id and parsed.reference_number:
                existing = self.expense_service.read_by_reference_number_and_vendor_public_id(
                    reference_number=parsed.reference_number, vendor_public_id=vendor_public_id,
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
                "reference_number": parsed.reference_number,
                "description": parsed.description,
                "sub_cost_code": parsed.sub_cost_code_raw,
                "amount": str(parsed.amount) if parsed.amount else None,
                "date": parsed.expense_date,
                "is_credit": parsed.is_credit,
                "status": status,
                "is_ready": not issues,
                "is_duplicate": is_duplicate,
            })

        return pending

    # ---------------------------------------------------------------------
    # Per-item flow. The POST enumerates, then the scheduler ticks
    # process_single_item one file at a time so no single HTTP call runs
    # long enough to hit Azure App Service idle timeouts.
    # ---------------------------------------------------------------------

    def enumerate_source_folder(self, company_id: int = 1) -> list[dict]:
        """
        List PDF files in the SharePoint source folder.

        Returns a list of {"filename": str, "item_id": str} dicts, one per
        PDF ready to queue for per-item processing. Raises ValueError when
        the source folder is not linked or Graph listing fails.
        """
        source_folder = self.folder_connector.get_folder(company_id, "source")
        if not source_folder:
            raise ValueError("No source folder linked for this company.")
        drive_id = source_folder.get("drive_id")
        source_item_id = source_folder.get("item_id")
        if not drive_id or not source_item_id:
            raise ValueError("Source folder missing drive_id or item_id.")

        children_result = sp_client.list_drive_item_children(drive_id, source_item_id)
        if children_result.get("status_code") != 200:
            raise ValueError(f"Failed to list source folder: {children_result.get('message')}")

        pdfs = []
        for item in children_result.get("items", []):
            if item.get("item_type") != "file":
                continue
            name = item.get("name", "")
            if not name.lower().endswith(".pdf"):
                continue
            pdfs.append({"filename": name, "item_id": item.get("item_id", "")})
        return pdfs

    def process_single_item(
        self,
        filename: str,
        item_id: str,
        company_id: int = 1,
        tenant_id: int = 1,
    ) -> dict:
        """
        Process a single PDF by filename + SharePoint item_id.

        Returns:
            {"status": "completed", "expense_public_id": ..., "reference_number": ...}
                expense created, file moved to processed/.
            {"status": "skipped", "reason": ...}
                parse/resolve error or duplicate; file moved when possible.

        Raises for transient errors (SharePoint or blob upload failures,
        DB exceptions) so the caller can re-queue the item for retry.
        """
        logger.info("Processing file: %s", filename)

        source_folder = self.folder_connector.get_folder(company_id, "source")
        processed_folder = self.folder_connector.get_folder(company_id, "processed")
        if not source_folder or not processed_folder:
            raise ValueError("Source or processed folder not configured.")
        drive_id = source_folder.get("drive_id")
        processed_item_id = processed_folder.get("item_id")
        if not drive_id or not processed_item_id:
            raise ValueError("Source or processed folder missing drive_id/item_id.")

        projects = ProjectService().read_all()
        vendors = VendorService().read_all()
        sub_cost_codes = SubCostCodeService().read_all()

        parsed = parse_expense_filename(filename)
        if parsed.project_abbrev:
            parsed.project_public_id = _resolve_project(parsed.project_abbrev, projects)
        if parsed.vendor_name:
            parsed.vendor_public_id = _resolve_vendor(parsed.vendor_name, vendors)
        if parsed.sub_cost_code_raw:
            parsed.sub_cost_code_id = _resolve_sub_cost_code(
                parsed.sub_cost_code_raw, sub_cost_codes
            )

        # Permanent skips — these are data issues, not transient. Leave the
        # file in source/ so the operator can rename/fix and re-run.
        if not parsed.vendor_name:
            return {"status": "skipped", "reason": "Could not parse filename (7-segment convention)."}
        if not parsed.vendor_public_id:
            return {"status": "skipped", "reason": f"Could not resolve vendor '{parsed.vendor_name}'."}
        if not parsed.reference_number:
            return {"status": "skipped", "reason": "No reference number in filename."}
        if not parsed.expense_date:
            return {"status": "skipped", "reason": "No expense date in filename."}

        existing_expense = self.expense_service.read_by_reference_number_and_vendor_public_id(
            reference_number=parsed.reference_number, vendor_public_id=parsed.vendor_public_id,
        )
        if existing_expense:
            # Move can raise — let it propagate so the item lands in 'failed'
            # with the move error visible. After 3 attempts the row is
            # permanently 'failed' and the operator can investigate. The
            # expense itself already exists, so retries are safe.
            self._move_file_to_processed(drive_id, item_id, processed_item_id, filename)
            return {
                "status": "skipped",
                "reason": "Duplicate expense already exists.",
                "expense_public_id": existing_expense.public_id,
            }

        content_result = sp_client.get_drive_item_content(drive_id, item_id)
        if content_result.get("status_code") != 200:
            raise RuntimeError(f"Failed to download file: {content_result.get('message')}")
        file_bytes = content_result.get("content")
        if not file_bytes:
            raise RuntimeError("Downloaded file has no content")

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        blob_name = f"expenses/{uuid.uuid4()}.pdf"
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=file_bytes,
            content_type="application/pdf",
        )

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
            category="expense",
        )

        # ExpenseService.create populates the placeholder ExpenseLineItem
        # inline from the line_* fields and links the receipt via
        # ExpenseLineItemAttachment — so there is no follow-up line-item
        # update step (unlike the bill flow).
        expense = self.expense_service.create(
            tenant_id=tenant_id,
            vendor_public_id=parsed.vendor_public_id,
            expense_date=parsed.expense_date,
            reference_number=parsed.reference_number,
            total_amount=parsed.amount,
            is_draft=True,
            is_credit=parsed.is_credit,
            attachment_public_id=attachment.public_id,
            line_description=parsed.description or parsed.vendor_name,
            line_quantity=1,
            line_rate=parsed.amount,
            line_amount=parsed.amount,
            line_markup=Decimal("0"),
            line_price=parsed.amount,
            line_is_billable=True,
            line_sub_cost_code_id=parsed.sub_cost_code_id,
            line_project_public_id=parsed.project_public_id,
        )

        # Expense is durable now. If move fails the next claim's duplicate
        # check will short-circuit (reference_number+vendor already exists) so
        # retrying is cheap and idempotent — and on terminal failure the
        # item shows up in the run's error list with the actual reason.
        self._move_file_to_processed(drive_id, item_id, processed_item_id, filename)

        logger.info("Created expense draft: vendor=%s, reference_number=%s, amount=%s, file=%s",
                     parsed.vendor_public_id, parsed.reference_number, parsed.amount, filename)
        return {
            "status": "completed",
            "expense_public_id": expense.public_id,
            "reference_number": parsed.reference_number,
        }

    def _move_file_to_processed(
        self, drive_id: str, file_item_id: str, processed_item_id: str, filename: str,
    ) -> None:
        """
        Move a file from the source folder to the processed folder. Raises
        on unrecoverable failure so the caller can surface the error
        instead of leaving the PDF stranded in /source. The legacy code
        warned-and-returned, which is why we accumulated 60+ stale files.
        """
        move_result = sp_client.move_item(drive_id, file_item_id, processed_item_id)
        if move_result.get("status_code") == 200:
            return

        if "nameAlreadyExists" not in str(move_result.get("message", "")):
            raise RuntimeError(f"Move failed: {move_result.get('message')}")

        # Conflict — delete the existing copy in /processed, then retry.
        children = sp_client.list_drive_item_children(drive_id, processed_item_id)
        if children.get("status_code") == 200:
            for item in children.get("items", []):
                if item.get("name") == filename:
                    sp_client.delete_item(drive_id, item.get("item_id"))
                    logger.info("Deleted existing '%s' from processed folder", filename)
                    break

        retry_result = sp_client.move_item(drive_id, file_item_id, processed_item_id)
        if retry_result.get("status_code") != 200:
            raise RuntimeError(f"Retry move failed: {retry_result.get('message')}")
