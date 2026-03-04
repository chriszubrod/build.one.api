# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.classification_override.business.model import ClassificationOverride
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ClassificationOverrideRepository:
    """Repository for ClassificationOverride persistence operations."""

    def _from_db(self, row: pyodbc.Row) -> Optional[ClassificationOverride]:
        """Convert a database row into a ClassificationOverride dataclass."""
        if not row:
            return None

        try:
            return ClassificationOverride(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                match_type=row.MatchType,
                match_value=row.MatchValue,
                classification_type=row.ClassificationType,
                notes=getattr(row, "Notes", None),
                is_active=bool(getattr(row, "IsActive", True)),
                created_by=getattr(row, "CreatedBy", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during override mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during override mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        match_type: str,
        match_value: str,
        classification_type: str,
        notes: Optional[str] = None,
        is_active: bool = True,
        created_by: Optional[str] = None,
    ) -> Optional[ClassificationOverride]:
        """Create a new classification override."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateClassificationOverride",
                    params={
                        "MatchType": match_type,
                        "MatchValue": match_value,
                        "ClassificationType": classification_type,
                        "Notes": notes,
                        "IsActive": 1 if is_active else 0,
                        "CreatedBy": created_by,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error creating classification override: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[ClassificationOverride]:
        """Read all classification overrides."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadClassificationOverrides",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error reading classification overrides: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ClassificationOverride]:
        """Read a classification override by public ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadClassificationOverrideByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error reading override by public_id: {error}")
            raise map_database_error(error)

    def update(
        self,
        *,
        public_id: str,
        row_version: str,
        match_type: str,
        match_value: str,
        classification_type: str,
        notes: Optional[str] = None,
        is_active: bool = True,
    ) -> Optional[ClassificationOverride]:
        """Update a classification override (optimistic concurrency via RowVersion)."""
        try:
            row_version_bytes = base64.b64decode(row_version)
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateClassificationOverride",
                    params={
                        "PublicId": public_id,
                        "RowVersion": row_version_bytes,
                        "MatchType": match_type,
                        "MatchValue": match_value,
                        "ClassificationType": classification_type,
                        "Notes": notes,
                        "IsActive": 1 if is_active else 0,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error updating classification override: {error}")
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> bool:
        """Delete a classification override by public ID. Returns True if deleted."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteClassificationOverrideByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return row and row.DeletedCount > 0
        except Exception as error:
            logger.error(f"Error deleting classification override: {error}")
            raise map_database_error(error)

    def find_by_email(self, email: str) -> Optional[ClassificationOverride]:
        """Find an active override matching an email address (exact or domain)."""
        if not email:
            return None
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FindClassificationOverride",
                    params={"Email": email},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error finding classification override: {error}")
            raise map_database_error(error)
