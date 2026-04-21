# Python Standard Library Imports
import base64
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from core.workflow.business.models import WorkflowEvent
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


def _json_serial(obj: Any) -> Any:
    """Convert non-JSON-serializable values (e.g. Decimal) for json.dumps."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class WorkflowEventRepository:
    """
    Repository for WorkflowEvent persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[WorkflowEvent]:
        if not row:
            return None

        try:
            # Parse data JSON
            data_str = getattr(row, "Data", None)
            data = None
            if data_str:
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse workflow event data JSON")
                    data = {}

            return WorkflowEvent(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                workflow_id=getattr(row, "WorkflowId", None),
                event_type=getattr(row, "EventType", None),
                from_state=getattr(row, "FromState", None),
                to_state=getattr(row, "ToState", None),
                step_name=getattr(row, "StepName", None),
                data=data,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                created_by=getattr(row, "CreatedBy", None),
            )
        except Exception as error:
            logger.error("Error during WorkflowEvent mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        workflow_id: int,
        event_type: str,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        step_name: Optional[str] = None,
        data: Optional[dict] = None,
        created_by: Optional[str] = None,
    ) -> WorkflowEvent:
        try:
            data_json = json.dumps(data, default=_json_serial) if data else None
            
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateWorkflowEvent",
                    params={
                        "WorkflowId": workflow_id,
                        "EventType": event_type,
                        "FromState": from_state,
                        "ToState": to_state,
                        "StepName": step_name,
                        "Data": data_json,
                        "CreatedBy": created_by,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("create WorkflowEvent failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create WorkflowEvent: %s", error)
            raise map_database_error(error)

    def read_by_workflow_id(self, workflow_id: int) -> list[WorkflowEvent]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadWorkflowEventsByWorkflowId",
                    params={"WorkflowId": workflow_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read WorkflowEvents by workflow ID: %s", error)
            raise map_database_error(error)

    def read_by_type(self, workflow_id: int, event_type: str) -> list[WorkflowEvent]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadWorkflowEventsByType",
                    params={"WorkflowId": workflow_id, "EventType": event_type},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read WorkflowEvents by type: %s", error)
            raise map_database_error(error)

    def read_latest(self, workflow_id: int) -> Optional[WorkflowEvent]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadLatestWorkflowEvent",
                    params={"WorkflowId": workflow_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read latest WorkflowEvent: %s", error)
            raise map_database_error(error)
