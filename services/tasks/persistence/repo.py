# Python Standard Library Imports
import base64
import json
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.tasks.business.model import Task
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TaskRepository:
    """Repository for Task persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Task]:
        if not row:
            return None
        try:
            # Parse Context JSON if present
            context_json = getattr(row, "Context", None)
            context = None
            if context_json:
                try:
                    context = json.loads(context_json)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to parse Context JSON for task %s", getattr(row, "Id", None))
                    context = None

            return Task(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                tenant_id=getattr(row, "TenantId", None),
                task_type=getattr(row, "TaskType", None),
                reference_id=getattr(row, "ReferenceId", None),
                title=getattr(row, "Title", None),
                status=getattr(row, "Status", None),
                source_type=getattr(row, "SourceType", None),
                source_id=getattr(row, "SourceId", None),
                description=getattr(row, "Description", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                workflow_id=getattr(row, "WorkflowId", None),
                vendor_id=getattr(row, "VendorId", None),
                project_id=getattr(row, "ProjectId", None),
                bill_id=getattr(row, "BillId", None),
                context=context,
            )
        except Exception as error:
            logger.error("Error during Task mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        tenant_id: int,
        task_type: str,
        reference_id: int,
        title: Optional[str] = None,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        description: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        bill_id: Optional[int] = None,
        context: Optional[dict] = None,
    ) -> Task:
        try:
            # Serialize context dict to JSON if present
            context_json = None
            if context:
                try:
                    context_json = json.dumps(context)
                except (TypeError, ValueError) as e:
                    logger.warning("Failed to serialize context to JSON: %s", e)
                    context_json = None

            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateTask",
                    params={
                        "TenantId": tenant_id,
                        "TaskType": task_type,
                        "ReferenceId": reference_id,
                        "Title": title,
                        "Status": status,
                        "SourceType": source_type,
                        "SourceId": source_id,
                        "Description": description,
                        "CreatedByUserId": created_by_user_id,
                        "WorkflowId": workflow_id,
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "BillId": bill_id,
                        "Context": context_json,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateTask failed"))
                task = self._from_db(row)
                print(f"[TaskRepository] create succeeded TaskType={task_type} ReferenceId={reference_id} PublicId={task.public_id} WorkflowId={workflow_id}")
                return task
        except Exception as error:
            print(f"[TaskRepository] create failed: {error}")
            logger.error("Error during create Task: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Task]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaskByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Task by public ID: %s", error)
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Task]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaskById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Task by ID: %s", error)
            raise map_database_error(error)

    def read_by_task_type_and_reference_id(
        self,
        tenant_id: int,
        task_type: str,
        reference_id: int,
    ) -> Optional[Task]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaskByTaskTypeAndReferenceId",
                    params={
                        "TenantId": tenant_id,
                        "TaskType": task_type,
                        "ReferenceId": reference_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Task by type and reference: %s", error)
            raise map_database_error(error)

    def read_tasks(
        self,
        tenant_id: int,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        open_only: bool = False,
    ) -> List[Task]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTasks",
                    params={
                        "TenantId": tenant_id,
                        "Status": status,
                        "SourceType": source_type,
                        "SourceId": source_id,
                        "OpenOnly": 1 if open_only else 0,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read Tasks: %s", error)
            raise map_database_error(error)

    def update(
        self,
        public_id: str,
        title: Optional[str] = None,
        status: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[dict] = None,
        bill_id: Optional[int] = None,
    ) -> Optional[Task]:
        try:
            # Serialize context dict to JSON if present
            context_json = None
            if context is not None:
                try:
                    context_json = json.dumps(context)
                except (TypeError, ValueError) as e:
                    logger.warning("Failed to serialize context to JSON: %s", e)
                    context_json = None

            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateTask",
                    params={
                        "PublicId": public_id,
                        "Title": title,
                        "Status": status,
                        "Description": description,
                        "Context": context_json,
                        "BillId": bill_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update Task: %s", error)
            raise map_database_error(error)

    def read_by_workflow_id(self, workflow_id: int) -> Optional[Task]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaskByWorkflowId",
                    params={"WorkflowId": workflow_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read Task by workflow ID: %s", error)
            raise map_database_error(error)
