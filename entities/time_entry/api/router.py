# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

# Local Imports
from entities.time_entry.business.service import TimeEntryService
from entities.time_entry.business.time_log_service import TimeLogService
from entities.time_entry.business.time_entry_status_service import TimeEntryStatusService
from entities.time_entry.api.schemas import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimeEntryReviewFlag,
    TimeLogCreate,
    TimeLogUpdate,
    TimeEntryApprove,
    TimeEntryReject,
)
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found, raise_database_error
from shared.database import DatabaseError
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)


def _resolve_user_id(current_user: dict) -> int:
    """Resolve the internal User.Id from the JWT sub (Auth.PublicId)."""
    from entities.auth.business.service import AuthService
    auth = AuthService().read_by_public_id(public_id=current_user.get("sub"))
    if not auth or not auth.user_id:
        raise ValueError("Could not resolve user from token.")
    return auth.user_id


def _entry_dict_with_current_status(
    entry,
    *,
    project_ids: Optional[list[int]] = None,
    time_logs: Optional[list[dict]] = None,
) -> dict:
    """
    Inject `current_status` into a TimeEntry's serialized dict by reading
    the latest TimeEntryStatus row. The column doesn't exist on the
    TimeEntry table itself — status lives in TimeEntryStatus history.

    The status sproc enforces Phase-3 scope: with NULL ActorUserId AND
    NULL ActorIsSystemAdmin it returns zero rows, which would silently
    default current_status to "draft" for every entry — exactly the bug
    that made the list page show "Draft" for already-submitted entries.

    The parent entry was already access-scoped by the caller (list query
    forwards the actor; submit/approve/reject sproc already proved the
    caller is authorized). So we bypass scope on the status lookup the
    same way TimeEntryService.submit/approve/reject already do.

    `project_ids` — when supplied (list endpoints), populates
    `distinct_project_ids` for the Project column on the React list page.
    Single-entry endpoints can omit it; the field stays absent.
    """
    d = entry.to_dict()
    current = TimeEntryStatusService().repo.read_current(
        time_entry_id=entry.id,
        actor_is_system_admin=True,
    )
    d["current_status"] = current.status if current else "draft"
    if project_ids is not None:
        d["distinct_project_ids"] = project_ids
    # time_logs only included when the caller asked for them (include_logs=true
    # on the list endpoint) — single-entry endpoints omit, callers that don't
    # need logs avoid the bandwidth.
    if time_logs is not None:
        d["time_logs"] = time_logs
    return d

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
    include_logs: bool = Query(
        default=False,
        description=(
            "When true, each entry in the response includes its time_logs[]"
            " inline. Used by the PastDayScreen team view to collapse N+1"
            " detail round-trips into a single list fetch."
        ),
    ),
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
    # Inject current_status into every entry — see _entry_dict_with_current_status
    # for the why. N+1 lookup is acceptable at typical list sizes; bump to a
    # batched fetch if the list ever grows materially.
    # Batch-fetch distinct ProjectIds per entry for the list-page Project
    # column (replaces an N+1 TimeLog read; backed by sproc
    # ReadDistinctProjectIdsByTimeEntryIds).
    project_ids_by_entry = service.repo.read_distinct_project_ids_for(
        time_entry_ids=[e.id for e in results],
    )

    # When the caller asks for logs (PastDayScreen team view), fetch them all
    # in a single batch sproc keyed on the page's entry IDs and group client-
    # side. Avoids the N detail round-trips the React page was doing.
    logs_by_entry: dict[int, list[dict]] = {}
    if include_logs and results:
        from entities.time_entry.persistence.time_log_repo import TimeLogRepository
        entry_ids = [e.id for e in results if e.id is not None]
        all_logs = TimeLogRepository().read_by_time_entry_ids(entry_ids)
        for log in all_logs:
            logs_by_entry.setdefault(log.time_entry_id, []).append(log.to_dict())

    return list_response(
        data=[
            _entry_dict_with_current_status(
                entry,
                project_ids=project_ids_by_entry.get(entry.id, []),
                time_logs=logs_by_entry.get(entry.id, []) if include_logs else None,
            )
            for entry in results
        ],
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
    return item_response({"count": total_count})


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


@router.post("/{public_id}/review-flag")
def flag_time_entry_for_human_review(
    public_id: str,
    body: TimeEntryReviewFlag,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_update")),
):
    """
    Stamp ReviewPriority + ReviewReasons on a TimeEntry. Used by the
    time_tracking_specialist agent to record its bucketing decision after
    running validate-completeness. Does NOT transition CurrentStatus.

    Returns the stamped state for confirmation.
    """
    service = TimeEntryService()
    try:
        result = service.stamp_review(
            public_id=public_id,
            priority=body.priority,
            reasons=body.reasons,
        )
        return item_response(result)
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to stamp review flag")


@router.get("/{public_id}/validate-completeness")
def validate_time_entry_completeness(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """
    Run the deterministic completeness + anomaly checklist against this
    time entry. Read-only — no mutation, no status transition.

    Used by the time_tracking_specialist agent to inform its
    ReviewPriority decision; also safe to call from human review UI.

    Row-scope bypass: this endpoint reviews TimeEntries the caller did NOT
    submit (the agent / Approver acts on other users' entries). Mirrors the
    submit/approve/reject pattern which also bypasses UserId scope at the
    service layer. Authorization is the endpoint's `can_read` gate above.
    """
    from entities.time_entry.business.validation import validate_completeness
    from entities.time_entry.persistence.repo import TimeEntryRepository
    from entities.time_entry.persistence.time_log_repo import TimeLogRepository

    entry = TimeEntryRepository().read_by_public_id(
        public_id=public_id,
        actor_is_system_admin=True,
    )
    if not entry:
        raise_not_found("Time entry")

    logs = TimeLogRepository().read_by_time_entry_id(
        time_entry_id=entry.id,
        actor_is_system_admin=True,
    )
    report = validate_completeness(entry=entry, logs=logs)
    return item_response(report)


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
        return item_response(_entry_dict_with_current_status(entry))
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to submit time entry")


@router.post("/{public_id}/approve")
def approve_time_entry(
    public_id: str,
    body: TimeEntryApprove = None,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_approve")),
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
        return item_response(_entry_dict_with_current_status(entry))
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to approve time entry")


@router.post("/{public_id}/reject")
def reject_time_entry(
    public_id: str,
    body: TimeEntryReject = None,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_approve")),
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
        return item_response(_entry_dict_with_current_status(entry))
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


@router.get("/{public_id}/billed-lineage")
def read_time_entry_billed_lineage(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING)),
):
    """Downstream lineage — which ContractLabor/EmployeeLabor rows came from
    this TimeEntry, and whether those rows are linked to a Bill/Invoice yet.

    Returns 0..N rows. Empty list means the entry hasn't been aggregated yet
    (still draft, or aggregation failed and the entry is flagged).
    """
    from entities.time_entry.persistence.repo import TimeEntryRepository

    entry = TimeEntryService().read_by_public_id(public_id=public_id)
    if not entry:
        raise_not_found("TimeEntry")
    rows = TimeEntryRepository().read_billed_lineage(time_entry_id=int(entry.id))
    return list_response(rows)


@router.post("/import/external-csv")
async def import_external_timesheet_csv(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_module_api(Modules.TIME_TRACKING, "can_approve")),
):
    """Phase 6c — bulk-import a third-party timesheet CSV.

    See `scripts/samples/README.md` for the column contract + expected
    behavior. Internally:
      1. Parses + validates headers (BOM-tolerant).
      2. Pre-aggregates per (Worker × Project × Day) — required because
         the Phase 4 aggregation sproc overwrites on natural key per
         TimeEntry, not across multiple TimeEntries.
      3. Resolves project names → public_ids in a single lookup pass.
      4. Hands the rows to TimeEntryBulkImportService, which creates
         TimeEntry + TimeLog rows and submits — Phase 4 aggregation
         fires automatically, routing to ContractLabor (vendor path)
         or EmployeeLabor (employee path) based on the worker's
         User.VendorId vs User.EmployeeId link.

    Gated on `Time Tracking can_approve` — same gate as the approve
    endpoint, only Tenant Admin / Controller / PM / Reviewer roles.
    """
    from entities.time_entry.business.external_timesheet_import_service import (
        ExternalTimesheetImportService,
    )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv.")

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    result = ExternalTimesheetImportService().import_csv(
        file_content=file_content,
        filename=file.filename,
    )
    return item_response(result)


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
            project_id=time_log.project_id,
            note=time_log.note,
        )
        return item_response(log.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to create time log")
    except DatabaseError as e:
        raise_database_error(e)


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
            project_id=time_log.project_id,
            note=time_log.note,
        )
        return item_response(log.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to update time log")
    except DatabaseError as e:
        # Same duplicate-shape contract as create — an UPDATE that lands a
        # (TimeEntryId, ClockIn) collision must be claimable client-side.
        raise_database_error(e)


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
