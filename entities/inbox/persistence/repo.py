# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.inbox.business.model import InboxRecord
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InboxRecordRepository:
    """Repository for InboxRecord persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[InboxRecord]:
        """Convert a database row into an InboxRecord dataclass."""
        if not row:
            return None

        try:
            return InboxRecord(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                message_id=row.MessageId,
                status=getattr(row, "Status", None),
                submitted_to_email=getattr(row, "SubmittedToEmail", None),
                submitted_at=getattr(row, "SubmittedAt", None),
                processed_at=getattr(row, "ProcessedAt", None),
                record_type=getattr(row, "RecordType", None),
                record_public_id=getattr(row, "RecordPublicId", None),
                classification_type=getattr(row, "ClassificationType", None),
                classification_confidence=float(getattr(row, "ClassificationConfidence", None))
                    if getattr(row, "ClassificationConfidence", None) is not None else None,
                classification_signals=getattr(row, "ClassificationSignals", None),
                classified_at=getattr(row, "ClassifiedAt", None),
                user_override_type=getattr(row, "UserOverrideType", None),
                subject=getattr(row, "Subject", None),
                from_email=getattr(row, "FromEmail", None),
                from_name=getattr(row, "FromName", None),
                has_attachments=bool(getattr(row, "HasAttachments", False))
                    if getattr(row, "HasAttachments", None) is not None else None,
                processed_via=getattr(row, "ProcessedVia", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during inbox record mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during inbox record mapping: {error}")
            raise map_database_error(error)

    def upsert(
        self,
        *,
        message_id: str,
        status: str = "new",
        submitted_to_email: Optional[str] = None,
        submitted_at: Optional[str] = None,
        processed_at: Optional[str] = None,
        record_type: Optional[str] = None,
        record_public_id: Optional[str] = None,
        classification_type: Optional[str] = None,
        classification_confidence: Optional[float] = None,
        classification_signals: Optional[str] = None,
        classified_at: Optional[str] = None,
        user_override_type: Optional[str] = None,
        subject: Optional[str] = None,
        from_email: Optional[str] = None,
        from_name: Optional[str] = None,
        has_attachments: Optional[bool] = None,
        processed_via: Optional[str] = None,
    ) -> Optional[InboxRecord]:
        """Insert or update an InboxRecord by MessageId."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertInboxRecord",
                    params={
                        "MessageId": message_id,
                        "Status": status,
                        "SubmittedToEmail": submitted_to_email,
                        "SubmittedAt": submitted_at,
                        "ProcessedAt": processed_at,
                        "RecordType": record_type,
                        "RecordPublicId": record_public_id,
                        "ClassificationType": classification_type,
                        "ClassificationConfidence": classification_confidence,
                        "ClassificationSignals": classification_signals,
                        "ClassifiedAt": classified_at,
                        "UserOverrideType": user_override_type,
                        "Subject": subject,
                        "FromEmail": from_email,
                        "FromName": from_name,
                        "HasAttachments": 1 if has_attachments else (0 if has_attachments is False else None),
                        "ProcessedVia": processed_via,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during upsert inbox record: {error}")
            raise map_database_error(error)

    def read_by_record_public_id(self, record_public_id: str) -> Optional[InboxRecord]:
        """Read an InboxRecord by the public_id of the bill/expense/credit it created."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInboxRecordByRecordPublicId",
                    params={"RecordPublicId": record_public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read inbox record by record public ID: {error}")
            raise map_database_error(error)

    def read_by_message_id(self, message_id: str) -> Optional[InboxRecord]:
        """Read an InboxRecord by MS Graph message ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInboxRecordByMessageId",
                    params={"MessageId": message_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read inbox record by message ID: {error}")
            raise map_database_error(error)

    def get_classification_stats(self) -> dict:
        """
        Get aggregate classification accuracy stats for the admin dashboard.
        Calls GetInboxClassificationStats which returns 6 result sets.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="GetInboxClassificationStats",
                    params={},
                )

                # Result set 1: Status distribution
                status_dist = {}
                for row in cursor.fetchall():
                    status_dist[row.Status] = row.Count

                # Result set 2: Overall accuracy
                cursor.nextset()
                acc_row = cursor.fetchone()
                accuracy = {
                    "total_processed": acc_row.TotalProcessed if acc_row else 0,
                    "correct": acc_row.CorrectClassifications if acc_row else 0,
                    "overridden": acc_row.OverriddenClassifications if acc_row else 0,
                    "avg_confidence": round(float(acc_row.AvgConfidence), 3) if acc_row and acc_row.AvgConfidence else None,
                }

                # Result set 3: Accuracy by type
                cursor.nextset()
                accuracy_by_type = []
                for row in cursor.fetchall():
                    accuracy_by_type.append({
                        "classification_type": row.ClassificationType,
                        "total": row.Total,
                        "correct": row.Correct,
                        "overridden": row.Overridden,
                    })

                # Result set 4: Confidence histogram
                cursor.nextset()
                confidence_histogram = []
                for row in cursor.fetchall():
                    confidence_histogram.append({
                        "bucket": row.ConfidenceBucket,
                        "count": row.Count,
                    })

                # Result set 5: Common override patterns
                cursor.nextset()
                common_overrides = []
                for row in cursor.fetchall():
                    common_overrides.append({
                        "from_email": row.FromEmail,
                        "predicted_type": row.PredictedType,
                        "actual_type": row.ActualType,
                        "override_count": row.OverrideCount,
                    })

                # Result set 6: Recent misclassifications
                cursor.nextset()
                recent_misclassifications = []
                for row in cursor.fetchall():
                    recent_misclassifications.append({
                        "public_id": row.PublicId,
                        "subject": row.Subject,
                        "from_email": row.FromEmail,
                        "classification_type": row.ClassificationType,
                        "classification_confidence": round(float(row.ClassificationConfidence), 3) if row.ClassificationConfidence else None,
                        "record_type": row.RecordType,
                        "user_override_type": row.UserOverrideType,
                        "processed_at": row.ProcessedAt,
                    })

                return {
                    "status_distribution": status_dist,
                    "accuracy": accuracy,
                    "accuracy_by_type": accuracy_by_type,
                    "confidence_histogram": confidence_histogram,
                    "common_overrides": common_overrides,
                    "recent_misclassifications": recent_misclassifications,
                }
        except Exception as error:
            logger.error(f"Error getting classification stats: {error}")
            raise map_database_error(error)

    def read_by_sender(self, from_email: str, limit: int = 10) -> List[InboxRecord]:
        """Read recent InboxRecords for a given sender email address."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Direct query since no stored procedure exists yet for this
                cursor.execute(
                    "SELECT TOP (?) * FROM dbo.InboxRecord "
                    "WHERE FromEmail = ? AND ClassificationType IS NOT NULL "
                    "ORDER BY CreatedDatetime DESC",
                    limit,
                    from_email,
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error reading inbox records by sender: {error}")
            return []  # Non-fatal — agent can still classify without history

    def read_by_message_ids(self, message_ids: List[str]) -> List[InboxRecord]:
        """Batch lookup of InboxRecords by a list of MS Graph message IDs."""
        if not message_ids:
            return []

        try:
            csv = ",".join(message_ids)
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInboxRecordsByMessageIds",
                    params={"MessageIds": csv},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during batch read inbox records: {error}")
            raise map_database_error(error)
