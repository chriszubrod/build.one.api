# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Task:
    """
    Model representing a task (hybrid row pointing at an entity requiring review).
    Source of truth stays in the underlying table (e.g. Workflow); Task is for listing and linking.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    tenant_id: Optional[int] = None
    task_type: Optional[str] = None  # e.g. 'workflow'
    reference_id: Optional[int] = None  # Id of the row in the table for TaskType (e.g. Workflow.Id)
    title: Optional[str] = None
    status: Optional[str] = None
    source_type: Optional[str] = None  # e.g. 'email'
    source_id: Optional[str] = None  # e.g. conversation_id for "tasks from this email"

    def to_dict(self) -> dict:
        return asdict(self)
