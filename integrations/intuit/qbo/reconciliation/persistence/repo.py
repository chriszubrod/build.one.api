# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.reconciliation.business.model import ReconciliationIssue
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class ReconciliationIssueRepository:
    """Persistence for `[qbo].[ReconciliationIssue]`."""

    def _from_db(self, row: pyodbc.Row) -> Optional[ReconciliationIssue]:
        if not row:
            return None
        try:
            return ReconciliationIssue(
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
                qbo_id=getattr(row, "QboId", None),
                realm_id=getattr(row, "RealmId", None),
                details=getattr(row, "Details", None),
                status=getattr(row, "Status", None),
                acknowledged_at=getattr(row, "AcknowledgedAt", None),
                resolved_at=getattr(row, "ResolvedAt", None),
                reconcile_run_id=str(row.ReconcileRunId) if getattr(row, "ReconcileRunId", None) else None,
            )
        except Exception as error:
            logger.error(f"Error mapping ReconciliationIssue row: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        drift_type: str,
        severity: str,
        action: str,
        entity_type: str,
        realm_id: str,
        entity_public_id: Optional[str] = None,
        qbo_id: Optional[str] = None,
        details: Optional[str] = None,
        reconcile_run_id: Optional[str] = None,
    ) -> ReconciliationIssue:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboReconciliationIssue",
                        params={
                            "DriftType": drift_type,
                            "Severity": severity,
                            "Action": action,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "QboId": qbo_id,
                            "RealmId": realm_id,
                            "Details": details,
                            "ReconcileRunId": reconcile_run_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create reconciliation issue failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create reconciliation issue: {error}")
            raise map_database_error(error)

    def read_by_status(self, status: str) -> List[ReconciliationIssue]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboReconciliationIssuesByStatus",
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
            logger.error(f"Error during read reconciliation issues by status: {error}")
            raise map_database_error(error)

    def count_by_group(self) -> List[dict]:
        """Return aggregated counts grouped by (drift_type, severity, action, status)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CountQboReconciliationIssues",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [
                        {
                            "drift_type": r.DriftType,
                            "severity": r.Severity,
                            "action": r.Action,
                            "status": r.Status,
                            "count": r.Count,
                        }
                        for r in rows if r
                    ]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during count reconciliation issues: {error}")
            raise map_database_error(error)
