# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class MsOutbox:
    """
    Row in `[ms].[Outbox]` — a durable record of a pending MS Graph write.

    The outbox worker drains rows whose status is `pending` or `failed` and
    whose `next_retry_at` / `ready_after` gates have elapsed. On success →
    `done`; on retryable failure → `failed` with new `next_retry_at`; on
    exhausted/non-retryable failure → `dead_letter` for human triage.

    `payload` is a handler-specific JSON blob (nullable). Only
    `upload_sharepoint_file` currently uses it — to persist upload-session
    state (`uploadUrl`, `completed_bytes`) across retries/restarts so a
    partially-completed multi-chunk upload can resume instead of restarting.
    """

    # Standard columns
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    # What to do
    kind: Optional[str] = None
    entity_type: Optional[str] = None
    entity_public_id: Optional[str] = None
    tenant_id: Optional[str] = None
    request_id: Optional[str] = None
    payload: Optional[str] = None

    # Lifecycle
    status: Optional[str] = None
    attempts: Optional[int] = None
    next_retry_at: Optional[str] = None
    ready_after: Optional[str] = None
    last_error: Optional[str] = None
    correlation_id: Optional[str] = None

    # Attempt tracking
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    dead_lettered_at: Optional[str] = None
