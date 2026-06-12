# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class BoxOutbox:
    """
    Row in `[box].[Outbox]` — a durable record of a pending Box write.

    The outbox worker drains rows whose status is `pending` or `failed` and
    whose `next_retry_at` / `ready_after` gates have elapsed. On success →
    `done`; on retryable failure → `failed` with new `next_retry_at`; on
    exhausted/non-retryable failure → `dead_letter` for human triage.

    `payload` is a handler-specific JSON blob (nullable). For
    `upload_box_file` it carries the upload descriptor:
    `{"blob_path","filename","content_type","box_folder_id","attachment_id",
    "doc_kind","project_id"}` — the worker downloads the blob at drain time
    and pushes it into the mapped Box folder.

    Unlike `[ms].[Outbox]` there is no TenantId (Box CCG auth is keyed off a
    single enterprise id, not a tenant), and the row carries
    `created_by_user_id` so push provenance survives into `[box].[PushLog]`.
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
    request_id: Optional[str] = None
    payload: Optional[str] = None

    # Lifecycle
    status: Optional[str] = None
    attempts: Optional[int] = None
    next_retry_at: Optional[str] = None
    ready_after: Optional[str] = None
    last_error: Optional[str] = None
    correlation_id: Optional[str] = None
    created_by_user_id: Optional[int] = None

    # Attempt tracking
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    dead_lettered_at: Optional[str] = None
