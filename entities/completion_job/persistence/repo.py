# Python Standard Library Imports
import logging
from typing import Optional
from uuid import UUID

# Third-party Imports
import pyodbc

# Local Imports
from entities.completion_job.business.model import CompletionJob
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class CompletionJobRepository:
    """Repository for CompletionJob persistence operations."""

    def _from_db(self, row: pyodbc.Row) -> Optional[CompletionJob]:
        if not row:
            return None
        try:
            return CompletionJob(
                id=row.Id,
                entity_type=row.EntityType,
                entity_public_id=str(row.EntityPublicId),
                status=row.Status,
                attempts=int(row.Attempts),
                max_attempts=int(row.MaxAttempts),
                claimed_at=getattr(row, "ClaimedAt", None),
                last_error=getattr(row, "LastError", None),
                public_id=str(row.PublicId),
                was_created=bool(getattr(row, "WasCreated", False)),
            )
        except Exception as error:
            logger.error("Unexpected error during completion job mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, entity_type: str, entity_public_id: str, company_id: Optional[int] = None) -> CompletionJob:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "EntityType": entity_type,
                    "EntityPublicId": UUID(entity_public_id),
                }
                if company_id is not None:
                    params["CompanyId"] = company_id
                call_procedure(cursor=cursor, name="CreateCompletionJob", params=params)
                row = cursor.fetchone()
                if not row:
                    # Coalesce-then-terminal race: concurrent completion finished
                    # between the failed INSERT and the processing-row SELECT.
                    return CompletionJob(
                        id=0,
                        entity_type=entity_type,
                        entity_public_id=entity_public_id,
                        status="",
                        attempts=0,
                        max_attempts=5,
                        claimed_at=None,
                        last_error=None,
                        public_id=None,
                        was_created=False,
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error("Error creating completion job: %s", error)
            raise map_database_error(error)

    def claim_next_stuck(
        self, *, reclaim_after_seconds: int = 1800, max_attempts: int = 5
    ) -> Optional[CompletionJob]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClaimNextStuckCompletionJob",
                    params={
                        "ReclaimAfterSeconds": reclaim_after_seconds,
                        "MaxAttempts": max_attempts,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error("Error claiming stuck completion job: %s", error)
            raise map_database_error(error)

    def mark_success(self, *, public_id: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCompletionJobSuccess",
                    params={"PublicId": UUID(public_id)},
                )
        except Exception as error:
            logger.error("Error marking completion job success: %s", error)
            raise map_database_error(error)

    def mark_failure(
        self, *, public_id: str, last_error: Optional[str] = None, max_attempts: int = 5
    ) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCompletionJobFailure",
                    params={
                        "PublicId": UUID(public_id),
                        "LastError": last_error,
                        "MaxAttempts": max_attempts,
                    },
                )
        except Exception as error:
            logger.error("Error marking completion job failure: %s", error)
            raise map_database_error(error)
