# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.box.reconciliation.business.model import BoxReconciliationIssue
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BoxReconciliationIssueRepository:
    """Persistence for `[box].[ReconciliationIssue]`."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BoxReconciliationIssue]:
        if not row:
            return None
        try:
            return BoxReconciliationIssue(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                drift_type=getattr(row, "DriftType", None),
                severity=getattr(row, "Severity", None),
                action=getattr(row, "Action", None),
                entity_type=getattr(row, "EntityType", None),
                entity_public_id=str(row.EntityPublicId) if getattr(row, "EntityPublicId", None) else None,
                tenant_id=getattr(row, "TenantId", None),
                drive_item_id=getattr(row, "DriveItemId", None),
                worksheet_name=getattr(row, "WorksheetName", None),
                outbox_public_id=str(row.OutboxPublicId) if getattr(row, "OutboxPublicId", None) else None,
                details=getattr(row, "Details", None),
                status=getattr(row, "Status", None),
                acknowledged_at=getattr(row, "AcknowledgedAt", None),
                resolved_at=getattr(row, "ResolvedAt", None),
                reconcile_run_id=str(row.ReconcileRunId) if getattr(row, "ReconcileRunId", None) else None,
            )
        except Exception as error:
            logger.error(f"Error mapping BoxReconciliationIssue row: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        drift_type: str,
        severity: str,
        action: str,
        entity_type: str,
        tenant_id: str,
        entity_public_id: Optional[str] = None,
        drive_item_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        outbox_public_id: Optional[str] = None,
        details: Optional[str] = None,
        reconcile_run_id: Optional[str] = None,
    ) -> BoxReconciliationIssue:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBoxReconciliationIssue",
                        params={
                            "DriftType": drift_type,
                            "Severity": severity,
                            "Action": action,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "TenantId": tenant_id,
                            "DriveItemId": drive_item_id,
                            "WorksheetName": worksheet_name,
                            "OutboxPublicId": outbox_public_id,
                            "Details": details,
                            "ReconcileRunId": reconcile_run_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create box reconciliation issue failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create box reconciliation issue: {error}")
            raise map_database_error(error)

    def read_by_status(self, status: str) -> List[BoxReconciliationIssue]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxReconciliationIssuesByStatus",
                        params={"Status": status},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(r) for r in rows if r]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box reconciliation issues by status: {error}")
            raise map_database_error(error)
