# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Query

# Local Imports
from entities.task.business.aggregator import TaskAggregator
from entities.task.business.entity_modules import ENTITY_MODULE_BY_TYPE, readable_entity_types
from shared.api.auth_user import resolve_user_id
from shared.api.responses import list_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "task"])


_VALID_SCOPES = {"mine", "all", "mine_submitted"}
_VALID_ENTITY_TYPES = set(ENTITY_MODULE_BY_TYPE)


@router.get("/get/tasks/inbox")
def get_tasks_inbox(
    scope: str = Query("mine"),
    task_type: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    status_public_id: Optional[str] = Query(None),
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_module_api(Modules.TASKS)),
):
    """
    Reviewer inbox — cross-entity worklist of items still pending action.

    Scopes:
      - mine            (default): items where I am a PM/Owner on the project
      - all                       : items I have any UserProject access to
      - mine_submitted            : items I submitted (sent-box)

    `task_type` filters to a single feed (e.g. "Review"); omit for all feeds.
    `entity_type` further narrows within a feed.
    """
    scope = scope if scope in _VALID_SCOPES else "mine"
    if entity_type is not None and entity_type not in _VALID_ENTITY_TYPES:
        entity_type = None

    # TASKS gates the route; each entity type is additionally gated by
    # its own module. A type the caller cannot read just disappears (no 403 — one
    # walk's 403 would fail the device's whole inbox load).
    allowed = readable_entity_types(current_user)
    if not allowed or (entity_type is not None and entity_type not in allowed):
        return list_response([])

    user_id = resolve_user_id(current_user)
    is_system_admin = bool(current_user.get("is_system_admin"))

    rows = TaskAggregator().read_inbox(
        current_user_id=user_id,
        is_system_admin=is_system_admin,
        scope=scope,
        task_type=task_type,
        entity_type=entity_type,
        allowed_entity_types=allowed,
        status_public_id=status_public_id,
        page=page,
        page_size=page_size,
    )
    # Fail closed at the edge: the sproc filter is not the only gate; a feed that ignores the grant is dropped and logged.
    kept = [r for r in rows if r.entity_type in allowed]
    if len(kept) != len(rows):
        logger.error(
            "task inbox: feed %s returned %d row(s) outside the caller's grants — dropped",
            task_type or "*", len(rows) - len(kept),
        )
    return list_response([r.to_dict() for r in kept])


@router.get("/get/tasks/inbox/counts")
def get_tasks_inbox_counts(
    task_type: Optional[str] = Query(None),
    current_user: dict = Depends(require_module_api(Modules.TASKS)),
):
    """Sidebar / tab counts. One row per (task_type, entity_type, is_credit)."""
    allowed = readable_entity_types(current_user)
    user_id = resolve_user_id(current_user)
    is_system_admin = bool(current_user.get("is_system_admin"))

    counts = TaskAggregator().read_counts(
        current_user_id=user_id,
        is_system_admin=is_system_admin,
        task_type=task_type,
    )
    return list_response([
        {
            "task_type": c.task_type,
            "entity_type": c.entity_type,
            "is_credit": c.is_credit,
            "mine": c.mine,
            "total": c.total,
            "mine_submitted": c.mine_submitted,
        }
        for c in counts
        # Python, not the sproc: counts are one row per (EntityType, IsCredit), so this is exact;
        # the inbox list filters in SQL only because it pages.
        if c.entity_type in allowed
    ])
