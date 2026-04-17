# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Query, status

# Local Imports
from entities.time_entry.business.service import TimeEntryService
from entities.time_entry.business.time_log_service import TimeLogService
from entities.time_entry.business.time_entry_status_service import TimeEntryStatusService
from entities.time_entry.api.schemas import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimeLogCreate,
    TimeLogUpdate,
    TimeEntryApprove,
    TimeEntryReject,
)
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)


def _resolve_user_id(current_user: dict) -> int:
    """Resolve the internal User.Id from the JWT sub (Auth.PublicId)."""
    from entities.auth.business.service import AuthService
    auth = AuthService().read_by_public_id(public_id=current_user.get("sub"))
    if not auth or not auth.user_id:
        raise ValueError("Could not resolve user from token.")
    return auth.user_id

router = APIRouter(
    prefix="/api/v1/time-entries",
    tags=["api", "Time Tracking"],
)


# ============================================
# TimeEntry Routes (through ProcessEngine)
# ============================================

@router.post("", status_code=201)
def create_time_entry(
    time_entry: TimeEntryCreate,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_create")),
):
    """
    Create a new time entry in 'draft' status.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=_resolve_user_id(current_user),
        payload={
            "user_public_id": time_entry.user_public_id,
            "project_public_id": time_entry.project_public_id,
            "work_date": time_entry.work_date,
            "note": time_entry.note,
            "created_by_user_id": _resolve_user_id(current_user),
        },
        workflow_type="time_entry_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create time entry")

    return item_response(result.get("data"))


@router.get("")
def read_time_entries(
    page_number: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search_term: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    sort_by: str = Query(default="WorkDate"),
    sort_direction: str = Query(default="DESC"),
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """
    Read time entries with pagination and filtering.
    """
    service = TimeEntryService()
    results = service.read_paginated(
        page_number=page_number,
        page_size=page_size,
        search_term=search_term,
        user_id=user_id,
        project_id=project_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    total_count = service.count(
        search_term=search_term,
        user_id=user_id,
        project_id=project_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    return list_response(
        data=[r.to_dict() for r in results],
        count=total_count,
    )


@router.get("/count")
def count_time_entries(
    search_term: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """
    Count time entries matching the filter criteria.
    """
    service = TimeEntryService()
    total_count = service.count(
        search_term=search_term,
        user_id=user_id,
        project_id=project_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )
    return {"count": total_count}


@router.get("/{public_id}")
def read_time_entry(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """
    Read a single time entry by public ID, including nested time logs and status history.
    """
    service = TimeEntryService()
    entry = service.read_by_public_id(public_id=public_id)
    if not entry:
        raise_not_found("Time entry")

    # Include nested time logs and status history
    time_logs = TimeLogService().read_by_time_entry_public_id(time_entry_public_id=public_id)
    status_history = TimeEntryStatusService().read_by_time_entry_public_id(time_entry_public_id=public_id)

    result = entry.to_dict()
    result["time_logs"] = [log.to_dict() for log in time_logs]
    result["status_history"] = [s.to_dict() for s in status_history]
    result["current_status"] = status_history[-1].status if status_history else None

    return item_response(result)


@router.put("/{public_id}")
def update_time_entry(
    public_id: str,
    time_entry: TimeEntryUpdate,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_update")),
):
    """
    Update a time entry. Only allowed when status is 'draft'.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=_resolve_user_id(current_user),
        payload={
            "public_id": public_id,
            "row_version": time_entry.row_version,
            "user_public_id": time_entry.user_public_id,
            "project_public_id": time_entry.project_public_id,
            "work_date": time_entry.work_date,
            "note": time_entry.note,
        },
        workflow_type="time_entry_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update time entry")

    return item_response(result.get("data"))


@router.delete("/{public_id}")
def delete_time_entry(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_delete")),
):
    """
    Delete a time entry. Only allowed when status is 'draft'.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=_resolve_user_id(current_user),
        payload={
            "public_id": public_id,
        },
        workflow_type="time_entry_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete time entry")

    return item_response(result.get("data"))


# ============================================
# Status Transition Routes
# ============================================

@router.post("/{public_id}/submit")
def submit_time_entry(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_update")),
):
    """
    Submit a time entry for review. Transitions from 'draft' to 'submitted'.
    """
    service = TimeEntryService()
    try:
        entry = service.submit(public_id=public_id, user_id=_resolve_user_id(current_user))
        return item_response(entry.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to submit time entry")


@router.post("/{public_id}/approve")
def approve_time_entry(
    public_id: str,
    body: TimeEntryApprove = None,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_update")),
):
    """
    Approve a submitted time entry. Transitions from 'submitted' to 'approved'.
    """
    service = TimeEntryService()
    try:
        note = body.note if body else None
        entry = service.approve(
            public_id=public_id,
            user_id=_resolve_user_id(current_user),
            note=note,
        )
        return item_response(entry.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to approve time entry")


@router.post("/{public_id}/reject")
def reject_time_entry(
    public_id: str,
    body: TimeEntryReject = None,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_update")),
):
    """
    Reject a submitted time entry. Transitions from 'submitted' back to 'draft'.
    """
    service = TimeEntryService()
    try:
        note = body.note if body else None
        entry = service.reject(
            public_id=public_id,
            user_id=_resolve_user_id(current_user),
            note=note,
        )
        return item_response(entry.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to reject time entry")


# ============================================
# Status History Routes (read-only)
# ============================================

@router.get("/{public_id}/statuses")
def read_time_entry_statuses(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """
    Read the full status history for a time entry.
    """
    service = TimeEntryStatusService()
    try:
        statuses = service.read_by_time_entry_public_id(time_entry_public_id=public_id)
        return list_response(
            data=[s.to_dict() for s in statuses],
            count=len(statuses),
        )
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to read time entry statuses")


# ============================================
# TimeLog Routes (direct CRUD, no ProcessEngine)
# ============================================

@router.post("/{public_id}/logs", status_code=201)
def create_time_log(
    public_id: str,
    time_log: TimeLogCreate,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_create")),
):
    """
    Create a new time log for a time entry. Only allowed when entry is in 'draft' status.
    """
    service = TimeLogService()
    try:
        log = service.create(
            time_entry_public_id=public_id,
            clock_in=time_log.clock_in,
            clock_out=time_log.clock_out,
            log_type=time_log.log_type,
            latitude=time_log.latitude,
            longitude=time_log.longitude,
        )
        return item_response(log.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to create time log")


@router.get("/{public_id}/logs")
def read_time_logs(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """
    Read all time logs for a time entry.
    """
    service = TimeLogService()
    try:
        logs = service.read_by_time_entry_public_id(time_entry_public_id=public_id)
        return list_response(
            data=[log.to_dict() for log in logs],
            count=len(logs),
        )
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to read time logs")


# TimeLog update/delete routes use the log's own public_id
time_log_router = APIRouter(
    prefix="/api/v1/time-logs",
    tags=["api", "Time Tracking"],
)


@time_log_router.put("/{public_id}")
def update_time_log(
    public_id: str,
    time_log: TimeLogUpdate,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_update")),
):
    """
    Update a time log. Only allowed when parent time entry is in 'draft' status.
    """
    service = TimeLogService()
    try:
        log = service.update_by_public_id(
            public_id=public_id,
            row_version=time_log.row_version,
            clock_in=time_log.clock_in,
            clock_out=time_log.clock_out,
            log_type=time_log.log_type,
            latitude=time_log.latitude,
            longitude=time_log.longitude,
        )
        return item_response(log.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to update time log")


@time_log_router.delete("/{public_id}")
def delete_time_log(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_delete")),
):
    """
    Delete a time log. Only allowed when parent time entry is in 'draft' status.
    """
    service = TimeLogService()
    try:
        log = service.delete_by_public_id(public_id=public_id)
        if not log:
            raise_not_found("Time log")
        return item_response(log.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to delete time log")
