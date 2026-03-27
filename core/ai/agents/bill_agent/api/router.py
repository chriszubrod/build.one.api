# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends

# Local Imports
from core.ai.agents.bill_agent.api.schemas import (
    BillAgentRunRequest,
    BillAgentRunResponse,
    BillAgentFolderStatusResponse,
)
from core.ai.agents.bill_agent.business.runner import run_bill_folder_processing
from core.ai.agents.bill_agent.business.service import BillAgentService
from entities.auth.business.service import get_current_user_api
from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
from integrations.ms.sharepoint.external import client as sp_client

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/bill-agent", tags=["api", "bill-agent"])


@router.post("/run", response_model=BillAgentRunResponse)
def trigger_run(
    request: BillAgentRunRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user_api),
):
    """Trigger a bill folder processing run (runs in background)."""
    service = BillAgentService()
    run = service.start_run(
        trigger_source=request.trigger_source,
        created_by=current_user.get("email", "unknown"),
    )

    background_tasks.add_task(
        _run_processing_background,
        run_public_id=run.public_id,
        company_id=request.company_id,
        trigger_source=request.trigger_source,
        user_id=current_user.get("email", "unknown"),
    )

    return BillAgentRunResponse(
        message="Processing started",
        status_code=202,
        run_public_id=run.public_id,
        status="running",
    )


def _run_processing_background(
    run_public_id: str,
    company_id: int,
    trigger_source: str,
    user_id: str,
):
    """Background task wrapper — runs in a separate thread to avoid blocking the event loop."""
    import threading

    def _run():
        from core.ai.agents.bill_agent.business.processor import BillFolderProcessor
        service = BillAgentService()

        try:
            processor = BillFolderProcessor()
            result = processor.process(
                company_id=company_id,
                on_progress=lambda r: service.update_progress(run_public_id, r),
            )
            service.complete_run(run_public_id, result)
        except Exception as e:
            logger.exception("Background bill folder processing failed")
            service.fail_run(run_public_id, error=str(e))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


@router.get("/run/{public_id}", response_model=BillAgentRunResponse)
def get_run(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Check the status/results of a processing run."""
    service = BillAgentService()
    run = service.get_run(public_id)
    if not run:
        return BillAgentRunResponse(
            message="Run not found",
            status_code=404,
        )

    return BillAgentRunResponse(
        message=f"Run {run.status}",
        status_code=200,
        run_public_id=run.public_id,
        status=run.status,
        files_found=run.files_found,
        files_processed=run.files_processed,
        files_skipped=run.files_skipped,
        bills_created=run.bills_created,
        error_count=run.error_count,
    )


@router.get("/runs")
def list_runs(
    current_user: dict = Depends(get_current_user_api),
):
    """List recent processing runs."""
    service = BillAgentService()
    runs = service.get_recent_runs(limit=20)
    return {
        "message": "Recent runs",
        "status_code": 200,
        "runs": [run.to_dict() for run in runs],
    }


@router.get("/folder-status/{company_id}", response_model=BillAgentFolderStatusResponse)
def get_folder_status(
    company_id: int,
    current_user: dict = Depends(get_current_user_api),
):
    """Get the source folder file count and status for the UI summary."""
    connector = DriveItemBillFolderConnector()
    source_folder = connector.get_folder(company_id, "source")

    if not source_folder:
        return BillAgentFolderStatusResponse(
            message="No source folder linked",
            status_code=200,
            is_linked=False,
        )

    drive_id = source_folder.get("drive_id")
    item_id = source_folder.get("item_id")
    folder_name = source_folder.get("name")
    web_url = source_folder.get("web_url")

    file_count = 0
    if drive_id and item_id:
        try:
            children = sp_client.list_drive_item_children(drive_id, item_id)
            if children.get("status_code") == 200:
                for item in children.get("items", []):
                    name = item.get("name", "")
                    if item.get("item_type") == "file" and (name.lower().endswith('.pdf') or '.' not in name):
                        file_count += 1
        except Exception as e:
            logger.warning("Failed to count files in source folder: %s", e)

    # Get last run datetime
    last_run_datetime = None
    service = BillAgentService()
    recent_runs = service.get_recent_runs(limit=1)
    if recent_runs and recent_runs[0].completed_datetime:
        last_run_datetime = recent_runs[0].completed_datetime

    return BillAgentFolderStatusResponse(
        message="Folder status",
        status_code=200,
        is_linked=True,
        folder_name=folder_name,
        folder_web_url=web_url,
        file_count=file_count,
        last_run_datetime=last_run_datetime,
    )
