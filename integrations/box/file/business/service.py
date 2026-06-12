# Python Standard Library Imports
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Third-party Imports

# Local Imports
from integrations.box.base.errors import (
    BoxConflictError,
    BoxUnexpectedError,
    BoxValidationError,
)
from integrations.box.base.logger import get_box_logger
from integrations.box.file.business.model import BoxFile
from integrations.box.file.persistence.repo import (
    BoxFileRepository,
    BoxPushLogRepository,
)

logger = get_box_logger(__name__)


class BoxFileService:
    """
    Push blob-stored documents into Box folders and maintain the
    `[box].[File]` registry + `[box].[PushLog]` audit trail.

    Conflict discipline: filenames are deterministic (see
    `integrations.box.file.business.naming.sanitize_filename`), so an outbox
    retry that re-uploads hits Box's 409 `item_name_in_use`. The registry is
    the ownership guard — a conflicting Box file id registered to the SAME
    entity_public_id is ours and gets a new version (`upload_file_version`,
    no if_match: the registry check IS the guard); anything else is a foreign
    file and raises a non-retryable BoxValidationError so the outbox row
    dead-letters for human review.
    """

    def __init__(
        self,
        repo: Optional[BoxFileRepository] = None,
        push_log_repo: Optional[BoxPushLogRepository] = None,
    ):
        self.repo = repo or BoxFileRepository()
        self.push_log_repo = push_log_repo or BoxPushLogRepository()

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    def push_blob_to_box(
        self,
        *,
        client,
        payload: dict,
        outbox_id: int,
        request_id: str,
        actor_user_id: Optional[int],
    ) -> dict:
        """
        Download the blob at `payload["blob_path"]`, upload it into
        `payload["box_folder_id"]` as `payload["filename"]`, verify content
        integrity via sha1, then record the registry row + push log.

        Expected payload keys: blob_path, filename, content_type,
        box_folder_id, doc_kind, attachment_id, project_id — plus
        entity_type / entity_public_id added by the outbox worker from the
        claimed row (required for conflict-ownership recovery).

        Returns the Box file entry dict (entries[0] of the upload response).
        """
        blob_path = payload.get("blob_path")
        filename = payload.get("filename")
        box_folder_id = payload.get("box_folder_id")
        content_type = payload.get("content_type") or "application/octet-stream"
        doc_kind = payload.get("doc_kind") or "document"
        entity_type = payload.get("entity_type")
        entity_public_id = payload.get("entity_public_id")
        attachment_id = payload.get("attachment_id")
        project_id = payload.get("project_id")

        if not all([blob_path, filename, box_folder_id]):
            raise ValueError(
                f"box upload payload missing required fields: got {list(payload.keys())}"
            )

        data = self._fetch_blob(blob_path)
        computed_sha1 = hashlib.sha1(data).hexdigest()

        try:
            result = client.upload_file(
                box_folder_id,
                filename,
                data,
                content_type=content_type,
                operation_name="box.file.upload",
            )
            entry = self._unwrap_entry(result)
        except BoxConflictError as error:
            entry = self._recover_name_conflict(
                client=client,
                error=error,
                filename=filename,
                data=data,
                content_type=content_type,
                entity_public_id=entity_public_id,
            )

        returned_sha1 = entry.get("sha1")
        if not returned_sha1 or returned_sha1.lower() != computed_sha1.lower():
            raise BoxValidationError(
                f"sha1 mismatch after Box upload of {filename!r}: "
                f"computed {computed_sha1}, Box returned {returned_sha1}",
                code="sha1_mismatch",
            )

        box_file_id = str(entry.get("id"))
        file_version = entry.get("file_version") or {}
        file_version_id = file_version.get("id")
        etag = entry.get("etag")

        # Registry upsert is load-bearing (it's the conflict-ownership guard)
        # — let failures propagate so the row retries rather than losing the
        # registration.
        self.repo.upsert(
            box_file_id=box_file_id,
            box_folder_id=str(box_folder_id),
            name=entry.get("name") or filename,
            kind=doc_kind,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            attachment_id=attachment_id,
            project_id=project_id,
            sha1=computed_sha1,
            etag=etag,
            file_version_id=file_version_id,
            last_pushed_at=datetime.now(timezone.utc),
        )

        # Push-log failure must NOT fail the push — the file is uploaded and
        # registered; the audit row is best-effort.
        try:
            self.push_log_repo.create(
                box_file_id=box_file_id,
                file_version_id=file_version_id,
                sha1=computed_sha1,
                request_id=request_id,
                outbox_id=outbox_id,
                actor_user_id=actor_user_id,
            )
        except Exception as error:
            logger.warning(
                "box.file.push_log.create_failed",
                extra={
                    "event_name": "box.file.push_log.create_failed",
                    "box_file_id": box_file_id,
                    "outbox_id": outbox_id,
                    "error_class": type(error).__name__,
                },
            )

        logger.info(
            "box.file.push.completed",
            extra={
                "event_name": "box.file.push.completed",
                "box_file_id": box_file_id,
                "box_folder_id": str(box_folder_id),
                # "filename" is a reserved LogRecord attribute — using it in
                # extra raises KeyError at log time.
                "file_name": filename,
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "outbox_id": outbox_id,
                "outcome": "success",
            },
        )
        return entry

    def read_by_box_file_id(self, box_file_id: str) -> Optional[BoxFile]:
        """Read a registry row by Box's string file id."""
        return self.repo.read_by_box_file_id(box_file_id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _recover_name_conflict(
        self,
        *,
        client,
        error: BoxConflictError,
        filename: str,
        data: bytes,
        content_type: str,
        entity_public_id: Optional[str],
    ) -> Dict[str, Any]:
        """
        409 `item_name_in_use` recovery. Resolve the conflicting Box file id
        from `context_info.conflicts`, prove ownership via the registry, and
        upload a new version. Foreign files raise non-retryable
        BoxValidationError → dead-letter for human review.
        """
        conflict_file_id = self._extract_conflict_file_id(error)
        if not conflict_file_id:
            # Conflict without a recoverable file id — surface the original
            # 409 (non-retryable) rather than guess.
            raise error

        registry_row = self.repo.read_by_box_file_id(conflict_file_id)
        registered_entity = (
            str(registry_row.entity_public_id) if registry_row and registry_row.entity_public_id else None
        )
        if (
            registry_row is None
            or not registered_entity
            or not entity_public_id
            or registered_entity.lower() != str(entity_public_id).lower()
        ):
            raise BoxValidationError(
                f"name collision with foreign file {conflict_file_id}",
                code="name_collision_foreign_file",
                context_info=error.context_info,
            )

        logger.info(
            "box.file.conflict.recovered_as_version",
            extra={
                "event_name": "box.file.conflict.recovered_as_version",
                "box_file_id": conflict_file_id,
                "file_name": filename,
                "entity_public_id": entity_public_id,
            },
        )
        # No if_match — we own this identity; the registry check IS the guard.
        result = client.upload_file_version(
            conflict_file_id,
            filename,
            data,
            content_type=content_type,
            operation_name="box.file.upload_version",
        )
        return self._unwrap_entry(result)

    @staticmethod
    def _extract_conflict_file_id(error: BoxConflictError) -> Optional[str]:
        """
        Pull the conflicting file id out of `context_info.conflicts`.
        Box returns either a dict or a single-element list depending on the
        endpoint — handle both.
        """
        context_info = error.context_info or {}
        conflicts = context_info.get("conflicts")
        if isinstance(conflicts, list):
            conflicts = conflicts[0] if conflicts else None
        if isinstance(conflicts, dict):
            file_id = conflicts.get("id")
            return str(file_id) if file_id else None
        return None

    @staticmethod
    def _unwrap_entry(result: Dict[str, Any]) -> Dict[str, Any]:
        """Box upload endpoints return a collection: entries[0] is the file."""
        entries = result.get("entries") if isinstance(result, dict) else None
        if not entries or not isinstance(entries[0], dict):
            raise BoxUnexpectedError(
                f"Box upload response missing entries[0]: {str(result)[:200]}"
            )
        return entries[0]

    @staticmethod
    def _fetch_blob(blob_path: str) -> bytes:
        """
        Fetch content from Azure Blob Storage. `blob_path` is the same
        `blob_url`-derived value the MS outbox upload handler resolves —
        identical resolution: `AzureBlobStorage().download_file(blob_path)`.
        """
        from shared.storage import AzureBlobStorage

        content, _metadata = AzureBlobStorage().download_file(blob_path)
        return content
