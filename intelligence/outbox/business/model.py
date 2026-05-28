# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class TimeTrackingOutbox:
    """
    Row in `[dbo].[TimeTrackingOutbox]` — a pending agent-review pass on
    an iOS-submitted TimeEntry.

    The build.one.scheduler Function App polls a tick endpoint (~30s) that
    claims the oldest pending row, kicks off a time_tracking_specialist
    agent run, and marks the row done / failed / dead_letter.

    Unlike the external-integration outboxes (`[ms].[Outbox]`, `[qbo].[Outbox]`),
    there is no Payload — the agent re-reads TimeEntry + TimeLog state
    from the current DB at drain time. Likewise no TenantId / RequestId.
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
