"""Finish a client draw: tracker tabs, the Trend column, and the 000 PDFs (U-786).

Owns
  The operator endpoint POST /finish/invoice/{public_id}/draw-packet. It runs after a
  draw invoice is published and does by machine what a human did by hand: add the
  next "Draw Request NN" tab and a Trend column to the project tracker workbook in
  Box, print the Invoice and Trend tabs, and file them as "000 - <invoice#> - Invoice.pdf"
  and "000 - <invoice#> - Trend.pdf" in the invoice's SharePoint and Box draw folders.

Steps
  1. Read the invoice (caller's project access applies). Require invoice_number,
     invoice_date, total_amount and project_id.
  2. Map the project to its tracker workbook (Box file id).
  3. Transform the workbook (the xlsx work lives in draw_tabs.py). A fresh draw gets a
     cloned tab and a trend column. A draw that is already finished is re-derived
     in memory and nothing is written. MONEY CHECK: the invoice total must equal the
     tab total to the cent, or the run refuses and uploads nothing.
  4. dry_run (default): run step 3 under the Box lock protocol with upload=False and
     return the summary. No Box file lock, no upload, no render, no filing.
  5. Apply, in this order:
     a. Both gates (ALLOW_BOX_WRITES and ALLOW_MS_WRITES) must be "true", else refuse.
     b. Pre-check: step 3 with upload=False, without the Box file lock. It does take the
        process app lock, so it can refuse tracker_busy. It validates the plan and the money
        and reveals already_finished. A refusal here writes nothing.
     c. Folders: resolve the SharePoint and the Box invoice folders. A refusal after this
        point may leave EMPTY invoice folders behind; they are reused on the next run.
     d. Locked run, fresh draws only: upload the workbook as a new Box version under the
        Box file lock. A draw the pre-check found already finished takes no Box file lock
        and uploads nothing.
     e. Render the two PDFs through Graph and file each in SharePoint and Box. A PDF over
        4 MB goes through the upload session.
     A dry run does NOT detect a tracker open in Excel for the web; the apply step can still
     refuse with tracker_open_in_excel.

Refusal codes (DrawPacketFinishRefused.code, mapped to HTTP 409 by the router)
  missing_fields              invoice lacks invoice_number / invoice_date / total_amount / project_id
  unmapped_workbook           the project has no Box tracker workbook mapped
  previous_draw_missing       the draw before this one has no Draw Request tab; finish it first
  unsupported_layout          the tracker is outside the draw_tabs v1 layout, or could not be evaluated
                              at all ("could not be evaluated (<error> in <step>)", logged with its
                              traceback as box.excel.draw_tabs_unexpected_error); nothing written
  tracker_error               any other draw_tabs refusal, with the sheet and cell named
  invoice_is_draft            the invoice is a draft; publish it first (checked before any Box call)
  invalid_invoice_date        invoice_date is not a calendar date (production reads it as a string)
  total_mismatch              invoice total != tab total; the DETAILS draw tags or amounts
                              disagree with the invoice. Fix DETAILS, never the invoice.
  writes_disabled             ALLOW_BOX_WRITES or ALLOW_MS_WRITES is not "true"
  tracker_open_in_excel       the tracker is open in Excel for the web or locked in Box; close it, re-run
  tracker_changed             the tracker changed during the run (stale Box etag); re-run
  tracker_busy                another write holds the tracker's app lock; re-run shortly
  sharepoint_folder_unresolved  the Invoices module's SharePoint draw folder cannot be read or created
  unmapped_draw_folder        the project has no Box draw-requests mapping
  draw_folder_name_drift      the live Box folder name is not a draw-requests folder
  box_folder_unresolved       the per-invoice Box subfolder cannot be read or created

A missing invoice raises DrawPacketInvoiceNotFound (LookupError, not ValueError); the
router maps that alone to 404.

Registry: when the workbook upload succeeded but recording the registry row failed,
the run continues (PDFs rendered and filed) and the result carries registry_error
(the exception class name). The workbook is uploaded in that case.

External writes
  Box: the tracker workbook as a new version, under the outbox lock protocol
  (BoxExcelUpdateService.apply_workbook_transform). Registry ownership is left alone.
  Graph: a temp .xlsx in the integration account's OneDrive, rendered to PDF and deleted.
  SharePoint + Box: the two "000 -" PDFs in the invoice's draw folders.
  Gated by ALLOW_BOX_WRITES and ALLOW_MS_WRITES.

Idempotency
  A re-run after a partial failure takes the already-finished path. It re-renders and
  re-files the PDFs and never adds a second tab. A Box PDF whose sha1 already matches is
  "unchanged". A different sha1 becomes a new version. SharePoint PUT replaces by name.
  Filing errors are reported per PDF in the result. They do not raise.

Money is Decimal only; amounts are returned as two-decimal strings.
"""

import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from entities.invoice.business.box_verify import is_draw_requests_folder
from entities.invoice.business.service import InvoiceService
from integrations.box.base import client as box_client
from integrations.box.base.client import BoxHttpClient
from integrations.box.base.errors import (
    BoxConflictError,
    BoxLockedError,
    BoxPreconditionError,
    BoxServerError,
)
from shared.api.money import round_money
from integrations.box.excel.business.draw_tabs import (
    DrawAlreadyFinished,
    DrawTabsError,
    PreviousDrawMissing,
    UnsupportedTrackerLayout,
    echo_text,
    finish_draw_tabs,
    render_existing,
)
from integrations.box.excel.business.mapping_service import BoxProjectWorkbookService
from integrations.box.excel.business.service import BoxExcelUpdateService
from integrations.box.folder.business.service import (
    DOC_CLASS_DRAW_REQUESTS,
    BoxProjectFolderService,
)
from integrations.ms.base import client as ms_client
from integrations.ms.base.paths import sharepoint_safe_name
from integrations.ms.sharepoint.external.client import upload_large_file, upload_small_file
from integrations.ms.sharepoint.external.render import render_xlsx_to_pdf

logger = logging.getLogger(__name__)

PDF_CONTENT_TYPE = "application/pdf"
_REQUIRED_FIELDS = ("invoice_number", "invoice_date", "total_amount", "project_id")


def _error_text(error: BaseException) -> str:
    """Per-PDF filing error: the exception class and an HTTP status when there is one. Never its message (U-786 S4)."""
    status = getattr(error, "status_code", None)
    suffix = f" status {status}" if isinstance(status, int) else ""
    return f"error: {type(error).__name__}{suffix}"


class RefusalCode:
    """Values of DrawPacketFinishRefused.code. The router maps every one of them to HTTP 409."""

    MISSING_FIELDS = "missing_fields"
    UNMAPPED_WORKBOOK = "unmapped_workbook"
    PREVIOUS_DRAW_MISSING = "previous_draw_missing"
    UNSUPPORTED_LAYOUT = "unsupported_layout"
    TRACKER_ERROR = "tracker_error"
    INVOICE_IS_DRAFT = "invoice_is_draft"
    INVALID_INVOICE_DATE = "invalid_invoice_date"
    TOTAL_MISMATCH = "total_mismatch"
    WRITES_DISABLED = "writes_disabled"
    TRACKER_OPEN_IN_EXCEL = "tracker_open_in_excel"
    TRACKER_CHANGED = "tracker_changed"
    TRACKER_BUSY = "tracker_busy"
    SHAREPOINT_FOLDER_UNRESOLVED = "sharepoint_folder_unresolved"
    UNMAPPED_DRAW_FOLDER = "unmapped_draw_folder"
    DRAW_FOLDER_NAME_DRIFT = "draw_folder_name_drift"
    BOX_FOLDER_UNRESOLVED = "box_folder_unresolved"


class DrawPacketFinishRefused(ValueError):
    """A refusal with a short machine `code` and a human message."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class DrawPacketInvoiceNotFound(LookupError):
    """The invoice public id does not resolve. The router maps this alone to 404."""


# Graph's simple PUT (upload_small_file) is documented up to 4 MB; larger PDFs use the
# upload session (upload_large_file).
SIMPLE_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024


def _invoice_date(value) -> date:
    """Production reads CONVERT(VARCHAR(19), InvoiceDate, 120): '2026-10-30 00:00:00'."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    raise DrawPacketFinishRefused(
        RefusalCode.INVALID_INVOICE_DATE, f"invoice date {echo_text(value)} is not a calendar date"
    )


def _pdf_names(invoice_number: str) -> tuple:
    """The SharePoint and Box file names share one safe rendering of the invoice number."""
    safe = sharepoint_safe_name(invoice_number)
    return (f"000 - {safe} - Invoice.pdf", f"000 - {safe} - Trend.pdf")


def _box_refusal(error: Exception) -> Optional[DrawPacketFinishRefused]:
    """Map Box contention to an operator refusal. None for anything else (propagates)."""
    if isinstance(error, BoxLockedError):
        return DrawPacketFinishRefused(
            RefusalCode.TRACKER_OPEN_IN_EXCEL,
            "the tracker is open in Excel for the web or locked in Box; close it and re-run",
        )
    if isinstance(error, BoxPreconditionError):
        return DrawPacketFinishRefused(
            RefusalCode.TRACKER_CHANGED, "the tracker changed during the run; re-run"
        )
    # apply_workbook_transform raises the app-lock-busy case as a BoxServerError whose
    # request_method is "LOCK" (no Box call is made; a structured field, not free text).
    if isinstance(error, BoxServerError) and getattr(error, "request_method", None) == "LOCK":
        return DrawPacketFinishRefused(
            RefusalCode.TRACKER_BUSY, "another write holds the tracker's app lock; re-run shortly"
        )
    return None


def _cents(value) -> Decimal:
    return round_money(Decimal(str(value)))


def _writes_enabled() -> dict:
    return {"box": box_client.writes_allowed(), "ms": ms_client.writes_allowed()}


def _finish_or_re_render(data: bytes, *, invoice_number: str, draw_date):
    """(FinishedDrawTabs, already_finished). Maps draw_tabs refusals to ours."""
    try:
        try:
            return finish_draw_tabs(data, invoice_number=invoice_number, draw_date=draw_date), False
        except DrawAlreadyFinished:
            return render_existing(data, invoice_number=invoice_number), True
    except PreviousDrawMissing as error:
        raise DrawPacketFinishRefused(
            RefusalCode.PREVIOUS_DRAW_MISSING,
            f"previous draw {echo_text(error.previous_label)} has no Draw Request tab; finish that invoice first",
        ) from error
    except UnsupportedTrackerLayout as error:
        raise DrawPacketFinishRefused(RefusalCode.UNSUPPORTED_LAYOUT, str(error)) from error
    except DrawTabsError as error:
        raise DrawPacketFinishRefused(RefusalCode.TRACKER_ERROR, str(error)) from error


class DrawPacketFinishService:
    def __init__(
        self,
        *,
        invoice_service: Optional[InvoiceService] = None,
        excel_service: Optional[BoxExcelUpdateService] = None,
        workbook_service: Optional[BoxProjectWorkbookService] = None,
        folder_service: Optional[BoxProjectFolderService] = None,
    ):
        self.invoice_service = invoice_service or InvoiceService()
        self.excel_service = excel_service or BoxExcelUpdateService()
        self.workbook_service = workbook_service or BoxProjectWorkbookService()
        self.folder_service = folder_service or BoxProjectFolderService()

    def finish(self, public_id: str, *, dry_run: bool = True) -> dict:
        invoice = self.invoice_service.read_by_public_id(public_id)
        if invoice is None:
            raise DrawPacketInvoiceNotFound(f"invoice {public_id} not found")
        invoice_number = str(getattr(invoice, "invoice_number", None) or "").strip()
        values = {
            "invoice_number": invoice_number,
            "invoice_date": getattr(invoice, "invoice_date", None),
            "total_amount": getattr(invoice, "total_amount", None),
            "project_id": getattr(invoice, "project_id", None),
        }
        missing = [f for f in _REQUIRED_FIELDS if values[f] in (None, "")]
        if missing:
            raise DrawPacketFinishRefused(
                RefusalCode.MISSING_FIELDS, f"invoice is missing {', '.join(missing)}"
            )
        if getattr(invoice, "is_draft", False):
            raise DrawPacketFinishRefused(
                RefusalCode.INVOICE_IS_DRAFT,
                f"invoice {invoice_number} is a draft; publish it before finishing its draw packet",
            )
        draw_date = _invoice_date(invoice.invoice_date)
        invoice_total = _cents(invoice.total_amount)

        mapping = self.workbook_service.read_by_project_id(invoice.project_id)
        if not mapping or not mapping.get("box_file_id"):
            raise DrawPacketFinishRefused(
                RefusalCode.UNMAPPED_WORKBOOK, f"project {invoice.project_id} has no Box tracker workbook"
            )
        box_file_id = str(mapping["box_file_id"])

        # The transform result is (finished, already). It is memoised on the downloaded bytes
        # for this call only, so the locked run reuses the pre-check's work when the tracker did
        # not change. Errors are never cached, and the money check runs on every call.
        memo: dict = {}

        def transform(data: bytes):
            key = hashlib.sha256(data).hexdigest()  # U-786 S8
            hit = memo.get(key)
            if hit is None:
                hit = _finish_or_re_render(data, invoice_number=invoice_number, draw_date=draw_date)
                memo[key] = hit
            finished, already = hit
            tab_total = _cents(finished.tab_total)
            if tab_total != invoice_total:
                raise DrawPacketFinishRefused(
                    RefusalCode.TOTAL_MISMATCH,
                    f"invoice {invoice_number} total {invoice_total} does not match the draw tab "
                    f"total {tab_total}; nothing was written. Fix the DETAILS draw tags or amounts, "
                    "then re-run.",
                )
            return (None if already else finished.workbook), (finished, already)

        if dry_run:
            out = self._run_transform(
                box_file_id=box_file_id, transform=transform,
                project_id=invoice.project_id, upload=False,
            )
            finished, already = out["result"]
            return self._summary(
                dry_run=True, invoice_number=invoice_number, invoice_total=invoice_total,
                finished=finished, already=already, box_file_id=box_file_id,
                box_etag=out["etag"], writes=_writes_enabled(),
            )

        writes = _writes_enabled()
        if not all(writes.values()):
            raise DrawPacketFinishRefused(
                RefusalCode.WRITES_DISABLED,
                "ALLOW_BOX_WRITES and ALLOW_MS_WRITES must both be 'true' to finish a draw packet",
            )

        # Pre-check: no Box file lock, nothing written. Every refusal of the plan and
        # the money check stops here, before any folder is created or lock is taken.
        pre = self._run_transform(
            box_file_id=box_file_id, transform=transform,
            project_id=invoice.project_id, upload=False,
        )
        already_finished = pre["result"][1]

        drive_id, folder_item_id = self._resolve_sharepoint_folder(invoice)
        box_folder_id = self._resolve_box_folder(invoice)

        if already_finished:
            # Re-file from the pre-check's result. No Box lock, so an Excel-for-web
            # user does not block a re-file.
            out = {"uploaded": False, "result": pre["result"], "etag": pre["etag"],
                   "sha1": None, "registry_error": None}
        else:
            out = self._run_transform(
                box_file_id=box_file_id, transform=transform,
                project_id=invoice.project_id, upload=True,
            )
        finished, already = out["result"]

        names = _pdf_names(invoice_number)
        pdfs = []
        for label, package, name in (
            ("invoice", finished.render_invoice, names[0]),
            ("trend", finished.render_trend, names[1]),
        ):
            entry = {"name": name, "bytes": None, "sharepoint": "error: not attempted",
                     "box": "error: not attempted"}
            try:
                pdf = render_xlsx_to_pdf(package, name_hint=f"{invoice_number} {label}")
            except Exception as error:
                message = f"error: render failed: {type(error).__name__}"
                entry["sharepoint"] = entry["box"] = message
                pdfs.append(entry)
                continue
            entry["bytes"] = len(pdf)
            entry["sharepoint"] = self._file_sharepoint(drive_id, folder_item_id, name, pdf)
            entry["box"] = self._file_box(box_folder_id, name, pdf)
            pdfs.append(entry)

        result = self._summary(
            dry_run=False, invoice_number=invoice_number, invoice_total=invoice_total,
            finished=finished, already=already, box_file_id=box_file_id,
            box_etag=out["etag"], writes=writes,
        )
        result.update({
            "workbook_uploaded": bool(out["uploaded"]),
            "box_sha1": out["sha1"],
            "registry_error": out.get("registry_error"),
            "pdfs": pdfs,
            "complete": all(
                p["sharepoint"] == "uploaded" and p["box"] in ("uploaded", "versioned", "unchanged")
                for p in pdfs
            ) and len(pdfs) == 2,
        })
        logger.info(
            "invoice.draw_packet_finish.done",
            extra={
                "event_name": "invoice.draw_packet_finish.done",
                "invoice_public_id": public_id,
                "dry_run": False,
                "already_finished": result["already_finished"],
                "workbook_uploaded": result["workbook_uploaded"],
                "pdfs_filed": sum(1 for p in pdfs if p["sharepoint"] == "uploaded"),
                "complete": result["complete"],
                "tab_total": result["tab_total"],
                "trend_total": result["trend_total"],
            },
        )
        return result

    # ------------------------------------------------------------ internals

    def _run_transform(self, *, box_file_id, transform, project_id, upload: bool) -> dict:
        """apply_workbook_transform, with Box contention mapped to operator refusals."""
        try:
            return self.excel_service.apply_workbook_transform(
                box_file_id=box_file_id,
                transform=transform,
                entity_type=None,
                entity_public_id=None,
                project_id=project_id,
                upload=upload,
            )
        except (BoxLockedError, BoxPreconditionError, BoxServerError) as error:
            refusal = _box_refusal(error)
            if refusal is None:
                raise
            raise refusal from error

    @staticmethod
    def _summary(*, dry_run, invoice_number, invoice_total, finished, already,
                 box_file_id, box_etag, writes) -> dict:
        return {
            "dry_run": dry_run,
            "invoice_number": invoice_number,
            "invoice_total": str(invoice_total),
            "tab_total": str(finished.tab_total),
            "trend_total": str(finished.trend_total),
            "new_tab": finished.plan.new_tab,
            "source_tab": finished.plan.source_tab,
            "already_finished": already,
            "box_file_id": box_file_id,
            "box_etag": box_etag,
            "pdf_names": list(_pdf_names(invoice_number)),
            "writes_enabled": writes,
        }

    def _resolve_sharepoint_folder(self, invoice):
        """(drive_id, folder_item_id) for the invoice's SharePoint draw subfolder."""
        try:
            return self.invoice_service.resolve_invoice_sharepoint_folder(invoice)
        except LookupError as error:
            raise DrawPacketFinishRefused(
                RefusalCode.SHAREPOINT_FOLDER_UNRESOLVED, f"SharePoint invoice folder unresolved: {echo_text(str(error))}"
            ) from error

    def _resolve_box_folder(self, invoice) -> str:
        """Box id of the per-invoice subfolder under the project's draw-requests folder.

        Named as InvoiceService._enqueue_box_line_pdfs names it (raw, unstripped).
        """
        mapping = self.folder_service.read_mapping_by_project_id_and_class(
            invoice.project_id, DOC_CLASS_DRAW_REQUESTS
        )
        if mapping is None:
            raise DrawPacketFinishRefused(
                RefusalCode.UNMAPPED_DRAW_FOLDER, f"project {invoice.project_id} has no Box draw-requests folder"
            )
        with BoxHttpClient() as client:
            # Live-name guard, as in InvoiceService._enqueue_box_line_pdfs. A failed read of
            # the folder name is logged and skipped, not refused.
            try:
                live_name = str((client.get_folder_info(mapping["box_folder_id"]) or {}).get("name") or "")
            except Exception as guard_error:
                logger.info(
                    "invoice.draw_packet_finish.folder_guard_skipped",
                    extra={"event_name": "invoice.draw_packet_finish.folder_guard_skipped",
                           "error_class": type(guard_error).__name__},
                )
                live_name = None
            if live_name is not None and not is_draw_requests_folder(live_name):
                raise DrawPacketFinishRefused(
                    RefusalCode.DRAW_FOLDER_NAME_DRIFT,
                    f"Box folder {mapping['box_folder_id']} is named {echo_text(live_name)}, not a draw-requests folder",
                )
            try:
                subfolder = self.folder_service.read_or_create_child_folder(
                    client=client,
                    parent_box_folder_id=mapping["box_folder_id"],
                    folder_name=str(invoice.invoice_number or invoice.public_id),
                )
            except Exception as error:
                raise DrawPacketFinishRefused(
                    RefusalCode.BOX_FOLDER_UNRESOLVED, f"Box invoice subfolder unresolved: {type(error).__name__}"
                ) from error
        return str(subfolder["box_folder_id"])

    @staticmethod
    def _file_sharepoint(drive_id: str, folder_item_id: str, name: str, pdf: bytes) -> str:
        large = len(pdf) > SIMPLE_UPLOAD_LIMIT_BYTES
        upload = upload_large_file if large else upload_small_file
        try:
            result = upload(drive_id, folder_item_id, name, pdf, content_type=PDF_CONTENT_TYPE)
        except Exception as error:
            return _error_text(error)
        status = (result or {}).get("status_code")
        if status not in (200, 201):
            return f"error: status {status}"
        if large and not (result or {}).get("item"):  # U-786 S7: a 200 with no item is not a filed PDF
            return "error: upload returned no item"
        return "uploaded"

    @staticmethod
    def _file_box(box_folder_id: str, name: str, pdf: bytes) -> str:
        try:
            with BoxHttpClient() as client:
                try:
                    client.upload_file(box_folder_id, name, pdf, content_type=PDF_CONTENT_TYPE)
                    return "uploaded"
                except BoxConflictError as conflict:
                    existing_id = conflict.conflicting_item_id()
                    if not existing_id:
                        return "error: name conflict without an existing file id"
                    meta = client.get(
                        f"files/{existing_id}",
                        params={"fields": "sha1"},
                        operation_name="draw_packet.box.meta",
                    )
                    if (meta or {}).get("sha1") == hashlib.sha1(pdf).hexdigest():
                        return "unchanged"
                    client.upload_file_version(
                        existing_id, name, pdf, content_type=PDF_CONTENT_TYPE
                    )
                    return "versioned"
        except Exception as error:
            return _error_text(error)
