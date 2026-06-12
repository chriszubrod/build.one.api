# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class BoxFile:
    """
    Row in `[box].[File]` — the local registry of files we've pushed to Box.

    `box_file_id` / `box_folder_id` / `file_version_id` / `etag` are Box's
    string identifiers, NOT local BIGINT keys. The registry is the ownership
    guard for 409 name-collision recovery: a conflicting Box file id found
    in the registry with a matching `entity_public_id` is OURS (safe to
    `upload_file_version`); anything else is a foreign file and dead-letters
    for human review.
    """

    # Standard columns
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    # Box identifiers (string keyspace)
    box_file_id: Optional[str] = None
    box_folder_id: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None

    # Local provenance
    entity_type: Optional[str] = None
    entity_public_id: Optional[str] = None
    attachment_id: Optional[int] = None
    project_id: Optional[int] = None

    # Content / version state from the last push
    sha1: Optional[str] = None
    etag: Optional[str] = None
    file_version_id: Optional[str] = None
    last_pushed_at: Optional[str] = None


@dataclass
class BoxPushLog:
    """
    Row in `[box].[PushLog]` — an append-only audit record of every
    successful push (initial upload or new version) to Box.
    """

    id: Optional[int] = None
    public_id: Optional[str] = None
    created_datetime: Optional[str] = None

    box_file_id: Optional[str] = None
    file_version_id: Optional[str] = None
    sha1: Optional[str] = None
    request_id: Optional[str] = None
    outbox_id: Optional[int] = None
    actor_user_id: Optional[int] = None
