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

    def read_unresolved_issue_keys_by_drift_type(self, drift_type: str) -> List[tuple]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboUnresolvedIssueKeysByDriftType",
                        params={"DriftType": drift_type},
                    )
                    rows = cursor.fetchall()
                    return [
                        (
                            getattr(r, "RealmId", None),
                            getattr(r, "EntityType", None),
                            getattr(r, "QboId", None),
                        )
                        for r in rows if r
                    ]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read unresolved issue keys by drift type: {error}")
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

    def acknowledge(self, id: int) -> Optional[ReconciliationIssue]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="AcknowledgeQboReconciliationIssue",
                        params={"Id": id},
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during acknowledge reconciliation issue: {error}")
            raise map_database_error(error)

    def resolve(self, id: int) -> Optional[ReconciliationIssue]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ResolveQboReconciliationIssue",
                        params={"Id": id},
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during resolve reconciliation issue: {error}")
            raise map_database_error(error)

    # ---------------------------------------------------------------- bulk ops
    # SQL-FIRST: these four methods bind params BY NAME. The @Severity / @Action /
    # @KeepNewestPerGroup params and the whole BulkAcknowledge... sproc must be
    # applied to prod (integrations/intuit/qbo/reconciliation/sql/
    # qbo.reconciliation_issue.sql) BEFORE these run, or SQL Server raises 8145.

    # NB the param dicts below are written out LITERALLY at each call_procedure
    # site rather than built by a shared helper. tests/test_repo_sproc_param_contract
    # walks the AST for dict literals (or a local `params` var); a helper CALL is
    # opaque to it, which would silently switch off 8145-detection on exactly the
    # four riskiest call sites in this repo. Verbosity is the price of that guard.

    @staticmethod
    def _preview_row(r) -> dict:
        return {
            "id": r.Id,
            "drift_type": r.DriftType,
            "entity_type": r.EntityType,
            "qbo_id": r.QboId,
            "severity": getattr(r, "Severity", None),
            "action": getattr(r, "Action", None),
            "created_datetime": r.CreatedDatetime,
            "total_match_count": r.TotalMatchCount,
            # Only bulk-resolve's preview returns TotalKeptCount (rows withheld by
            # keep-newest). Absent on bulk-acknowledge, which has no keep-newest.
            "total_kept_count": getattr(r, "TotalKeptCount", None),
        }

    def preview_bulk_resolve(
        self,
        *,
        drift_type: Optional[str] = None,
        entity_type: Optional[str] = None,
        created_before=None,
        realm_id: Optional[str] = None,
        severity: Optional[str] = None,
        action: Optional[str] = None,
        status: str = "open",
        max_rows: int = 1000,
        keep_newest_per_group: bool = False,
    ) -> List[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="BulkResolveQboReconciliationIssuesByFilter",
                        params={
                            "DriftType": drift_type,
                            "EntityType": entity_type,
                            "CreatedBefore": created_before,
                            "RealmId": realm_id,
                            "Severity": severity,
                            "Action": action,
                            "Status": status,
                            "MaxRows": max_rows,
                            "KeepNewestPerGroup": keep_newest_per_group,
                            "DryRun": True,
                        },
                    )
                    rows = cursor.fetchall()
                    if not rows:
                        return []
                    return [self._preview_row(r) for r in rows if r]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during preview bulk resolve reconciliation issues: {error}")
            raise map_database_error(error)

    def bulk_resolve(
        self,
        *,
        drift_type: Optional[str] = None,
        entity_type: Optional[str] = None,
        created_before=None,
        realm_id: Optional[str] = None,
        severity: Optional[str] = None,
        action: Optional[str] = None,
        status: str = "open",
        max_rows: int = 1000,
        keep_newest_per_group: bool = False,
    ) -> List[int]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="BulkResolveQboReconciliationIssuesByFilter",
                        params={
                            "DriftType": drift_type,
                            "EntityType": entity_type,
                            "CreatedBefore": created_before,
                            "RealmId": realm_id,
                            "Severity": severity,
                            "Action": action,
                            "Status": status,
                            "MaxRows": max_rows,
                            "KeepNewestPerGroup": keep_newest_per_group,
                            "DryRun": False,
                        },
                    )
                    rows = cursor.fetchall()
                    return [r.Id for r in rows if r]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during bulk resolve reconciliation issues: {error}")
            raise map_database_error(error)

    def preview_bulk_acknowledge(
        self,
        *,
        drift_type: Optional[str] = None,
        entity_type: Optional[str] = None,
        created_before=None,
        realm_id: Optional[str] = None,
        severity: Optional[str] = None,
        action: Optional[str] = None,
        status: str = "open",
        max_rows: int = 1000,
    ) -> List[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="BulkAcknowledgeQboReconciliationIssuesByFilter",
                        params={
                            "DriftType": drift_type,
                            "EntityType": entity_type,
                            "CreatedBefore": created_before,
                            "RealmId": realm_id,
                            "Severity": severity,
                            "Action": action,
                            "Status": status,
                            "MaxRows": max_rows,
                            "DryRun": True,
                        },
                    )
                    rows = cursor.fetchall()
                    if not rows:
                        return []
                    return [self._preview_row(r) for r in rows if r]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during preview bulk acknowledge reconciliation issues: {error}")
            raise map_database_error(error)

    def bulk_acknowledge(
        self,
        *,
        drift_type: Optional[str] = None,
        entity_type: Optional[str] = None,
        created_before=None,
        realm_id: Optional[str] = None,
        severity: Optional[str] = None,
        action: Optional[str] = None,
        status: str = "open",
        max_rows: int = 1000,
    ) -> List[int]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="BulkAcknowledgeQboReconciliationIssuesByFilter",
                        params={
                            "DriftType": drift_type,
                            "EntityType": entity_type,
                            "CreatedBefore": created_before,
                            "RealmId": realm_id,
                            "Severity": severity,
                            "Action": action,
                            "Status": status,
                            "MaxRows": max_rows,
                            "DryRun": False,
                        },
                    )
                    rows = cursor.fetchall()
                    return [r.Id for r in rows if r]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during bulk acknowledge reconciliation issues: {error}")
            raise map_database_error(error)

    def triage_summary(self) -> List[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboReconciliationIssueTriageSummary",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [
                        {
                            "drift_type": r.DriftType,
                            "entity_type": r.EntityType,
                            "severity": r.Severity,
                            "action": r.Action,
                            "status": r.Status,
                            "row_count": r.RowCount,
                            "unique_key_count": r.UniqueKeyCount,
                            "first_seen": r.FirstSeen,
                            "last_seen": r.LastSeen,
                        }
                        for r in rows if r
                    ]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during triage summary reconciliation issues: {error}")
            raise map_database_error(error)
