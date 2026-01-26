# Python Standard Library Imports
import base64
import json
import logging
from datetime import datetime
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from agents.models import Workflow, WorkflowEvent
from shared.database import (
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


def _call_agents_procedure(cursor: pyodbc.Cursor, name: str, params: dict):
    """
    Call a stored procedure in the agents schema.
    
    Similar to shared.database.call_procedure but uses 'agents' schema.
    """
    placeholders = ", ".join([f"@{k}=?" for k in params.keys()])
    sql = f"EXEC agents.{name} {placeholders}"
    cursor.execute(sql, list(params.values()))
    return cursor


class WorkflowRepository:
    """
    Repository for Workflow persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Workflow]:
        if not row:
            return None

        try:
            # Parse context JSON
            context_str = getattr(row, "Context", None)
            context = None
            if context_str:
                try:
                    context = json.loads(context_str)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse workflow context JSON")
                    context = {}

            return Workflow(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                tenant_id=getattr(row, "TenantId", None),
                workflow_type=getattr(row, "WorkflowType", None),
                state=getattr(row, "State", None),
                parent_workflow_id=getattr(row, "ParentWorkflowId", None),
                conversation_id=getattr(row, "ConversationId", None),
                trigger_message_id=getattr(row, "TriggerMessageId", None),
                vendor_id=getattr(row, "VendorId", None),
                project_id=getattr(row, "ProjectId", None),
                bill_id=getattr(row, "BillId", None),
                context=context,
                created_at=str(getattr(row, "CreatedAt", None)) if getattr(row, "CreatedAt", None) else None,
                updated_at=str(getattr(row, "UpdatedAt", None)) if getattr(row, "UpdatedAt", None) else None,
                completed_at=str(getattr(row, "CompletedAt", None)) if getattr(row, "CompletedAt", None) else None,
            )
        except Exception as error:
            logger.error("Error during Workflow mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        tenant_id: int,
        workflow_type: str,
        state: str,
        parent_workflow_id: Optional[int] = None,
        conversation_id: Optional[str] = None,
        trigger_message_id: Optional[str] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        bill_id: Optional[int] = None,
        context: Optional[dict] = None,
    ) -> Workflow:
        try:
            context_json = json.dumps(context) if context else None
            
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="CreateWorkflow",
                    params={
                        "TenantId": tenant_id,
                        "WorkflowType": workflow_type,
                        "State": state,
                        "ParentWorkflowId": parent_workflow_id,
                        "ConversationId": conversation_id,
                        "TriggerMessageId": trigger_message_id,
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "BillId": bill_id,
                        "Context": context_json,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("create Workflow failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create Workflow: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Workflow by public ID: %s", error)
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Workflow by ID: %s", error)
            raise map_database_error(error)

    def read_by_conversation_id(self, conversation_id: str) -> list[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowsByConversationId",
                    params={"ConversationId": conversation_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read Workflows by conversation ID: %s", error)
            raise map_database_error(error)

    def get_all_conversation_ids(self) -> set[str]:
        """
        Get all conversation IDs that have workflows.
        Used to filter out conversations that already have workflows.
        
        Returns:
            Set of conversation IDs
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT ConversationId FROM agents.Workflow WHERE ConversationId IS NOT NULL")
                rows = cursor.fetchall()
                return {row[0] for row in rows if row[0]}
        except Exception as error:
            logger.error("Error during get all conversation IDs: %s", error)
            return set()  # Return empty set on error to avoid blocking inbox

    def read_by_trigger_message_id(self, trigger_message_id: str) -> Optional[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowByTriggerMessageId",
                    params={"TriggerMessageId": trigger_message_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Workflow by trigger message ID: %s", error)
            raise map_database_error(error)

    def read_by_tenant_and_state(
        self,
        tenant_id: int,
        state: Optional[str] = None,
    ) -> list[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowsByTenantAndState",
                    params={"TenantId": tenant_id, "State": state},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read Workflows by tenant and state: %s", error)
            raise map_database_error(error)

    def read_active_workflows(self, tenant_id: int) -> list[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadActiveWorkflows",
                    params={"TenantId": tenant_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read active Workflows: %s", error)
            raise map_database_error(error)

    def read_child_workflows(self, parent_workflow_id: int) -> list[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadChildWorkflows",
                    params={"ParentWorkflowId": parent_workflow_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read child Workflows: %s", error)
            raise map_database_error(error)

    def update_state(
        self,
        public_id: str,
        state: str,
        context: Optional[dict] = None,
    ) -> Optional[Workflow]:
        try:
            context_json = json.dumps(context) if context else None
            
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="UpdateWorkflowState",
                    params={
                        "PublicId": public_id,
                        "State": state,
                        "Context": context_json,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update Workflow state: %s", error)
            raise map_database_error(error)

    def update_entities(
        self,
        public_id: str,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        bill_id: Optional[int] = None,
        context: Optional[dict] = None,
    ) -> Optional[Workflow]:
        try:
            context_json = json.dumps(context) if context else None
            
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="UpdateWorkflowEntities",
                    params={
                        "PublicId": public_id,
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "BillId": bill_id,
                        "Context": context_json,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update Workflow entities: %s", error)
            raise map_database_error(error)

    def update_context(
        self,
        public_id: str,
        context: dict,
    ) -> Optional[Workflow]:
        try:
            context_json = json.dumps(context)
            
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="UpdateWorkflowContext",
                    params={
                        "PublicId": public_id,
                        "Context": context_json,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update Workflow context: %s", error)
            raise map_database_error(error)

    def read_past_timeout(
        self,
        tenant_id: int,
        state: str,
        timeout_days: int,
    ) -> list[Workflow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowsPastTimeout",
                    params={
                        "TenantId": tenant_id,
                        "State": state,
                        "TimeoutDays": timeout_days,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read Workflows past timeout: %s", error)
            raise map_database_error(error)

    def read_created_between(
        self,
        tenant_id: int,
        start: datetime,
        end: datetime,
    ) -> List[Workflow]:
        """Read workflows created between two dates."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowsCreatedBetween",
                    params={
                        "TenantId": tenant_id,
                        "StartDate": start,
                        "EndDate": end,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read Workflows created between: %s", error)
            raise map_database_error(error)

    def read_completed_between(
        self,
        tenant_id: int,
        start: datetime,
        end: datetime,
    ) -> List[Workflow]:
        """Read workflows completed between two dates."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadWorkflowsCompletedBetween",
                    params={
                        "TenantId": tenant_id,
                        "StartDate": start,
                        "EndDate": end,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read Workflows completed between: %s", error)
            raise map_database_error(error)


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
                workflow_id=getattr(row, "WorkflowId", None),
                event_type=getattr(row, "EventType", None),
                from_state=getattr(row, "FromState", None),
                to_state=getattr(row, "ToState", None),
                step_name=getattr(row, "StepName", None),
                data=data,
                created_at=str(getattr(row, "CreatedAt", None)) if getattr(row, "CreatedAt", None) else None,
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
            data_json = json.dumps(data) if data else None
            
            with get_connection() as conn:
                cursor = conn.cursor()
                _call_agents_procedure(
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
                _call_agents_procedure(
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
                _call_agents_procedure(
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
                _call_agents_procedure(
                    cursor=cursor,
                    name="ReadLatestWorkflowEvent",
                    params={"WorkflowId": workflow_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read latest WorkflowEvent: %s", error)
            raise map_database_error(error)
