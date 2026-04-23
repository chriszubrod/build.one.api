# Python Standard Library Imports
import asyncio
import logging
import threading
import time

# Third-party Imports
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from decimal import Decimal

# Local Imports
from entities.bill.api.schemas import BillCreate, BillUpdate
from entities.bill.business.service import BillService
from entities.bill.persistence.folder_run_repo import BillFolderRunRepository
from entities.bill.persistence.repo import BillRepository
from shared.api.responses import list_response, item_response, accepted_response, raise_workflow_error, raise_not_found
from shared.database import get_connection
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])

_FOLDER_SUMMARY_TTL_SECONDS = 300.0
_folder_summary_cache: dict[int, tuple[float, dict]] = {}
_folder_summary_lock = threading.Lock()


@router.post("/create/bill")
async def create_bill_router(
    body: BillCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_create")),
):
    """
    Create a new bill.

    Routes through the workflow engine for audit logging and state tracking.
    When is_draft=False, triggers background completion (SharePoint, Excel, QBO).
    """
    is_draft = body.is_draft if body.is_draft is not None else True

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "payment_term_public_id": body.payment_term_public_id,
            "bill_date": body.bill_date,
            "due_date": body.due_date,
            "bill_number": body.bill_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            "memo": body.memo,
            "is_draft": is_draft,
        },
        workflow_type="bill_create",
    )

    result = await asyncio.to_thread(ProcessEngine().execute_synchronous, context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create bill")

    data = result.get("data")

    if not is_draft and data and data.get("public_id"):
        background_tasks.add_task(_run_complete_bill, data["public_id"])
        import json
        serializable = json.loads(json.dumps(data, default=str))
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"data": serializable, "status": "accepted"},
        )

    return item_response(data)


@router.get("/get/bills")
async def get_bills_router(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    is_draft: Optional[bool] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Read bills with pagination.
    """
    def _fetch():
        service = BillService()
        repo = BillRepository()
        with get_connection() as conn:
            bills = service.read_paginated(
                page_number=page,
                page_size=page_size,
                search_term=search,
                vendor_id=vendor_id,
                is_draft=is_draft,
                conn=conn,
            )
            total = service.count(
                search_term=search,
                vendor_id=vendor_id,
                is_draft=is_draft,
                conn=conn,
            )
            bill_ids = [b.id for b in bills if b.id]
            project_map = repo.read_first_line_item_projects(bill_ids, conn=conn) if bill_ids else {}
        return bills, total, project_map

    bills, total, project_map = await asyncio.to_thread(_fetch)
    bill_dicts = [bill.to_dict() for bill in bills]
    for bd in bill_dicts:
        bd["project_id"] = project_map.get(bd["id"])

    return {
        "data": bill_dicts,
        "count": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/get/bill/by-bill-number-and-vendor")
async def get_bill_by_bill_number_and_vendor_router(bill_number: str, vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a bill by bill number and vendor public ID.
    """
    bill = await asyncio.to_thread(
        BillService().read_by_bill_number_and_vendor_public_id,
        bill_number=bill_number,
        vendor_public_id=vendor_public_id,
    )
    if not bill:
        raise_not_found("Bill")
    return item_response(bill.to_dict())


@router.get("/get/bill/{public_id}/completion-result")
def get_bill_completion_result_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Return the completion result for a bill (Build One, SharePoint, Excel, QBO).
    """
    result = BillRepository().get_completion_result(public_id)
    if result is None:
        raise_not_found("Completion result")
    return item_response(result)


@router.get("/get/bill/{public_id}")
async def get_bill_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a bill by public ID.
    """
    bill = await asyncio.to_thread(BillService().read_by_public_id, public_id=public_id)
    if not bill:
        raise_not_found("Bill")
    return item_response(bill.to_dict())


@router.get("/get/bill/id/{id}")
def get_bill_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a bill by ID.
    """
    bill = BillService().read_by_id(id=id)
    if not bill:
        raise_not_found("Bill")
    return item_response(bill.to_dict())


@router.put("/update/bill/{public_id}")
async def update_bill_by_public_id_router(
    public_id: str,
    body: BillUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_update")),
):
    """
    Update a bill by public ID.
    When is_draft transitions from True to False, triggers background completion
    (SharePoint, Excel, QBO).
    """
    # Check if this is a draft-to-complete transition
    is_completing = body.is_draft is False
    if is_completing:
        bill = await asyncio.to_thread(BillService().read_by_public_id, public_id=public_id)
        if bill and not getattr(bill, "is_draft", True):
            is_completing = False  # Already completed, don't re-trigger

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "vendor_public_id": body.vendor_public_id,
            "payment_term_public_id": body.payment_term_public_id,
            "bill_date": body.bill_date,
            "due_date": body.due_date,
            "bill_number": body.bill_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount else None,
            "memo": body.memo,
            "is_draft": body.is_draft,
        },
        workflow_type="bill_update",
    )

    result = await asyncio.to_thread(ProcessEngine().execute_synchronous, context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update bill")

    data = result.get("data")

    # If completing, queue background pipeline
    if is_completing:
        background_tasks.add_task(_run_complete_bill, public_id)
        import json
        serializable = json.loads(json.dumps(data, default=str))
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"data": serializable, "status": "accepted"},
        )

    return item_response(data)


@router.delete("/delete/bill/{public_id}")
async def delete_bill_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS, "can_delete"))):
    """
    Delete a bill by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="bill_delete",
    )

    result = await asyncio.to_thread(ProcessEngine().execute_synchronous, context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete bill")

    return item_response(result.get("data"))


def _run_complete_bill(public_id: str) -> None:
    """Background task: run full bill completion (Build One, SharePoint, Excel, QBO)."""
    try:
        result = BillService().complete_bill(public_id=public_id)
        logger.info(
            "Complete bill background result: public_id=%s, status_code=%s, bill_finalized=%s",
            public_id, result.get("status_code"), result.get("bill_finalized"),
        )
        BillRepository().set_completion_result(public_id, result)
        logger.info("Completion result saved for bill %s (status_code=%s)", public_id, result.get("status_code"))
        if result.get("status_code") >= 400:
            logger.warning("Complete bill failed in background: %s", result.get("message"))
    except Exception as e:
        logger.exception("Complete bill background task failed: public_id=%s", public_id)


@router.post("/complete/bill/{public_id}")
def complete_bill_router(
    public_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_complete")),
):
    """
    Queue bill completion (finalize, SharePoint, Excel). Returns 202 immediately;
    work runs in background. Client can poll GET /api/v1/get/bill/{public_id} (is_draft) or use list page banner.
    """
    logger.info("Complete bill API called: public_id=%s (queuing background task)", public_id)
    bill_service = BillService()
    bill = bill_service.read_by_public_id(public_id=public_id)
    if not bill:
        raise_not_found("Bill")
    if not getattr(bill, "is_draft", True):
        raise HTTPException(status_code=400, detail="Bill is already completed")

    background_tasks.add_task(_run_complete_bill, public_id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=accepted_response(public_id, "bill_public_id"),
    )


# ---------------------------------------------------------------------------
# Bill Folder Processing
# ---------------------------------------------------------------------------
# Run state lives in dbo.BillFolderRun so POST and polling GET see the same
# row regardless of which gunicorn worker handled them (was broken under
# -w 2 with an in-process dict).

_folder_run_repo = BillFolderRunRepository()


def _run_folder_processing(run_id: str, company_id: int, tenant_id: int):
    """Background task for bill folder processing."""
    try:
        from entities.bill.business.folder_processor import BillFolderProcessor
        processor = BillFolderProcessor()
        result = processor.process(company_id=company_id, tenant_id=tenant_id)
        _folder_run_repo.update_status(
            public_id=run_id,
            status="completed",
            result=result.to_dict(),
            set_completed=True,
        )
        logger.info(
            "Folder processing %s completed: %d/%d files, %d bills created",
            run_id, result.files_processed, result.files_found, result.bills_created,
        )
    except Exception as e:
        logger.exception("Folder processing %s failed", run_id)
        try:
            _folder_run_repo.update_status(
                public_id=run_id,
                status="failed",
                result={"errors": [str(e)]},
                set_completed=True,
            )
        except Exception:
            logger.exception("Failed to persist folder-processing failure for run %s", run_id)


@router.post("/process/bill-folder")
def process_bill_folder_router(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Trigger bill folder processing. Returns 202 immediately; processing
    runs in background. Poll GET /api/v1/process/bill-folder/{run_id}.
    Run state is persisted so any worker can serve the poll.
    """
    run = _folder_run_repo.create()
    background_tasks.add_task(_run_folder_processing, run.public_id, company_id=1, tenant_id=1)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "run_id": run.public_id},
    )


@router.get("/process/bill-folder/{run_id}")
def get_bill_folder_status_router(
    run_id: str,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """Get the status of a bill folder processing run."""
    run = _folder_run_repo.read_by_public_id(public_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    payload: dict = {"status": run.status}
    if run.result:
        payload.update(run.result)
    return payload


@router.get("/get/bill-folder-summary")
def get_bill_folder_summary_router(
    response: Response,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """Get summary of the linked SharePoint bill source folder."""
    company_id = 1

    with _folder_summary_lock:
        cached = _folder_summary_cache.get(company_id)
    if cached is not None:
        expires_at, payload = cached
        if time.monotonic() < expires_at:
            response.headers["X-Cache"] = "hit"
            return item_response(payload)

    response.headers["X-Cache"] = "miss"
    try:
        from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
        from integrations.ms.sharepoint.external import client as sp_client

        connector = DriveItemBillFolderConnector()
        source_folder = connector.get_folder(company_id, "source")

        if not source_folder:
            payload = {"is_linked": False}
        else:
            drive_id = source_folder.get("drive_id")
            item_id = source_folder.get("item_id")

            file_count = 0
            folder_name = source_folder.get("name")
            folder_web_url = source_folder.get("web_url")

            if drive_id and item_id:
                # Get fresh metadata from Graph API (stored web_url may be stale)
                try:
                    item_meta = sp_client.get_drive_item(drive_id, item_id)
                    if item_meta.get("status_code") == 200:
                        live_item = item_meta.get("item", {})
                        folder_name = live_item.get("name") or folder_name
                        folder_web_url = live_item.get("web_url") or folder_web_url
                except Exception as e:
                    logger.warning("Failed to get folder metadata: %s", e)

                try:
                    children = sp_client.list_drive_item_children(drive_id, item_id)
                    if children.get("status_code") == 200:
                        for child in children.get("items", []):
                            name = child.get("name", "")
                            if child.get("item_type") == "file" and (name.lower().endswith(".pdf") or "." not in name):
                                file_count += 1
                except Exception as e:
                    logger.warning("Failed to count files in source folder: %s", e)

            payload = {
                "is_linked": True,
                "folder_name": folder_name,
                "folder_web_url": folder_web_url,
                "file_count": file_count,
            }

        with _folder_summary_lock:
            _folder_summary_cache[company_id] = (
                time.monotonic() + _FOLDER_SUMMARY_TTL_SECONDS,
                payload,
            )
        return item_response(payload)
    except Exception as e:
        logger.exception("Error getting bill folder summary")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/process/bill-folder-pending")
def list_pending_files_router(
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """List files in the source folder with parsed data and resolve status."""
    from entities.bill.business.folder_processor import BillFolderProcessor
    try:
        pending = BillFolderProcessor().list_pending(company_id=1)
        return {"files": pending, "count": len(pending)}
    except Exception as e:
        logger.exception("Failed to list pending files")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process/bill-folder-single")
def process_single_file_router(
    body: dict,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """Process a single file from the source folder by item_id."""
    item_id = body.get("item_id")
    filename = body.get("filename")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id is required")

    import uuid
    run_id = str(uuid.uuid4())
    _folder_processing_results[run_id] = {"status": "processing"}

    background_tasks.add_task(_run_single_file_processing, run_id, item_id, filename)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "run_id": run_id},
    )


@router.post("/process/bill-folder-prepare")
def prepare_file_for_create_router(
    body: dict,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Download a file from the source folder, upload to blob, create Attachment,
    and return the attachment public_id so the create page can link it.
    """
    item_id = body.get("item_id")
    filename = body.get("filename")
    if not item_id or not filename:
        raise HTTPException(status_code=400, detail="item_id and filename are required")

    import hashlib
    import uuid as _uuid
    from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
    from integrations.ms.sharepoint.external import client as sp_client
    from entities.attachment.business.service import AttachmentService
    from shared.storage import AzureBlobStorage

    connector = DriveItemBillFolderConnector()
    source_folder = connector.get_folder(1, "source")
    if not source_folder:
        raise HTTPException(status_code=400, detail="Source folder not configured")

    drive_id = source_folder.get("drive_id")

    # Download from SharePoint
    content_result = sp_client.get_drive_item_content(drive_id, item_id)
    if content_result.get("status_code") != 200:
        raise HTTPException(status_code=502, detail=f"Failed to download: {content_result.get('message')}")

    file_bytes = content_result.get("content")
    if not file_bytes:
        raise HTTPException(status_code=502, detail="Downloaded file has no content")

    # Upload to blob
    blob_name = f"bills/{_uuid.uuid4()}.pdf"
    storage = AzureBlobStorage()
    blob_url = storage.upload_file(blob_name=blob_name, file_content=file_bytes, content_type="application/pdf")

    # Create Attachment
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    attachment_service = AttachmentService()
    attachment = attachment_service.create(
        tenant_id=current_user.get("tenant_id", 1),
        filename=blob_name,
        original_filename=filename,
        file_extension="pdf",
        content_type="application/pdf",
        file_size=len(file_bytes),
        file_hash=file_hash,
        blob_url=blob_url,
        category="bill",
    )

    return {
        "attachment_public_id": attachment.public_id,
        "original_filename": filename,
        "item_id": item_id,
    }


@router.post("/process/bill-folder-move")
def move_file_to_processed_router(
    body: dict,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """Move a file from source folder to processed folder."""
    item_id = body.get("item_id")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id is required")

    from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
    from integrations.ms.sharepoint.external import client as sp_client

    connector = DriveItemBillFolderConnector()
    source_folder = connector.get_folder(1, "source")
    processed_folder = connector.get_folder(1, "processed")

    if not source_folder or not processed_folder:
        raise HTTPException(status_code=400, detail="Folders not configured")

    drive_id = source_folder.get("drive_id")
    processed_item_id = processed_folder.get("item_id")

    move_result = sp_client.move_item(drive_id, item_id, processed_item_id)
    if move_result.get("status_code") == 200:
        return {"status": "moved"}

    # Handle name conflict — delete existing then retry
    if "nameAlreadyExists" in str(move_result.get("message", "")):
        children = sp_client.list_drive_item_children(drive_id, processed_item_id)
        if children.get("status_code") == 200:
            # Get the source file name to find the conflict
            source_meta = sp_client.get_drive_item(drive_id, item_id)
            source_name = source_meta.get("item", {}).get("name", "") if source_meta.get("status_code") == 200 else ""
            for child in children.get("items", []):
                if child.get("name") == source_name:
                    sp_client.delete_item(drive_id, child.get("item_id"))
                    break
        retry = sp_client.move_item(drive_id, item_id, processed_item_id)
        if retry.get("status_code") == 200:
            return {"status": "moved"}

    return {"status": "move_failed", "message": move_result.get("message", "")}


def _run_single_file_processing(run_id: str, item_id: str, filename: str):
    """Background task for single file processing."""
    try:
        from entities.bill.business.folder_processor import BillFolderProcessor, ProcessingResult
        from entities.project.business.service import ProjectService
        from entities.vendor.business.service import VendorService
        from entities.sub_cost_code.business.service import SubCostCodeService
        from entities.payment_term.business.service import PaymentTermService
        from integrations.ms.sharepoint.external import client as sp_client

        processor = BillFolderProcessor()
        source_folder = processor.folder_connector.get_folder(1, "source")
        processed_folder = processor.folder_connector.get_folder(1, "processed")

        if not source_folder or not processed_folder:
            raise ValueError("Source or processed folder not configured")

        drive_id = source_folder.get("drive_id")
        processed_item_id = processed_folder.get("item_id")

        projects = ProjectService().read_all()
        vendors = VendorService().read_all()
        sccs = SubCostCodeService().read_all()
        pt = PaymentTermService().read_by_name("Due on receipt")

        result = ProcessingResult()
        file_item = {"name": filename, "item_id": item_id}

        processor._process_single_file(
            file_item=file_item,
            drive_id=drive_id,
            processed_item_id=processed_item_id,
            projects=projects,
            vendors=vendors,
            sub_cost_codes=sccs,
            tenant_id=1,
            payment_term_public_id=pt.public_id if pt else None,
            result=result,
        )

        _folder_processing_results[run_id] = {
            "status": "completed",
            **result.to_dict(),
        }
    except Exception as e:
        logger.exception("Single file processing %s failed", run_id)
        _folder_processing_results[run_id] = {
            "status": "failed",
            "errors": [str(e)],
        }
