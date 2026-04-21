# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class QboOutbox:
    """
    Row in `[qbo].[Outbox]` — a durable record of a pending QBO write operation.

    The outbox worker drains rows whose status is `pending` or `failed` and whose
    `next_retry_at` / `ready_after` gates have elapsed. On success the worker
    transitions the row to `done`; on retryable failure it extends `next_retry_at`
    via backoff; on exhausted/non-retryable failure it transitions to `dead_letter`
    for human triage.
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
    realm_id: Optional[str] = None
    request_id: Optional[str] = None

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
