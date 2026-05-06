# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.project.business.model import Project
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ProjectRepository:
    """
    Repository for Project persistence operations.
    """

    def __init__(self):
        """Initialize the ProjectRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Project]:
        """
        Convert a database row into a Project dataclass.
        """
        if not row:
            return None

        try:
            return Project(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                description=row.Description,
                status=row.Status,
                customer_id=row.CustomerId,
                abbreviation=row.Abbreviation,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during project mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during project mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, name: str, description: str, status: str, customer_id: Optional[int] = None, abbreviation: Optional[str] = None) -> Project:
        """
        Create a new project.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            name: Project name
            description: Project description
            status: Project status
            customer_id: Optional customer ID
            abbreviation: Optional project abbreviation
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                call_procedure(
                    cursor=cursor,
                    name="CreateProject",
                    params={
                        "Name": name,
                        "Description": description,
                        "Status": status,
                        "CustomerId": customer_id,
                        "Abbreviation": abbreviation,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateProject did not return a row.")
                    raise map_database_error(Exception("CreateProject failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create project: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Project]:
        """
        Read all projects.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjects",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all projects: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> list[Project]:
        """
        Read projects the user has access to (joined through dbo.UserProject).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectsByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read projects by user_id: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Project]:
        """
        Read a project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Project]:
        """
        Read a project by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Project]:
        """
        Read a project by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project by name: {error}")
            raise map_database_error(error)

    def find_for_invoice(self, *, address_hint: Optional[str] = None,
                         project_name_hint: Optional[str] = None) -> list[dict]:
        """Multi-strategy ranked Project lookup for invoice classification.
        Mirrors VendorRepository.find_for_invoice — used by the
        project_specialist agent (delegated from bill_specialist) when an
        invoice's job-site address needs to be bound to an existing
        Project row. Returns up to 5 candidates with strategy + confidence."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FindProjectForInvoice",
                    params={
                        "AddressHint": address_hint,
                        "ProjectNameHint": project_name_hint,
                    },
                )
                out: list[dict] = []
                for row in cursor.fetchall():
                    out.append({
                        "project": {
                            "id": row.ProjectId,
                            "public_id": row.ProjectPublicId,
                            "name": row.ProjectName,
                            "abbreviation": row.Abbreviation,
                            "status": row.Status,
                        },
                        "confidence": float(row.Confidence) if row.Confidence is not None else None,
                        "strategy": row.Strategy,
                        "matched_term": row.MatchedTerm,
                    })
                return out
        except Exception as error:
            logger.error(f"Error during find_project_for_invoice: {error}")
            raise map_database_error(error)

    def update_by_id(self, project: Project) -> Optional[Project]:
        """
        Update a project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateProjectById",
                    params={
                        "Id": project.id,
                        "RowVersion": project.row_version_bytes,
                        "Name": project.name,
                        "Description": project.description,
                        "Status": project.status,
                        "CustomerId": project.customer_id,
                        "Abbreviation": project.abbreviation,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update project by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Project]:
        """
        Delete a project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteProjectById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete project by ID: {error}")
            raise map_database_error(error)
