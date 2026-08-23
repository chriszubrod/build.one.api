# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.base.identity_consistency import IdentityCheckResult
from integrations.intuit.qbo.customer.connector.project.business.model import CustomerProject
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CustomerProjectRepository:
    """
    Repository for CustomerProject persistence operations.
    """

    def __init__(self):
        """Initialize the CustomerProjectRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[CustomerProject]:
        """
        Convert a database row into a CustomerProject dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return CustomerProject(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                project_id=getattr(row, "ProjectId", None),
                qbo_customer_id=getattr(row, "QboCustomerId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during customer project mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during customer project mapping: {error}")
            raise map_database_error(error)

    def create(self, *, project_id: int, qbo_customer_id: int) -> CustomerProject:
        """
        Create a new CustomerProject mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateCustomerProject",
                        params={
                            "ProjectId": project_id,
                            "QboCustomerId": qbo_customer_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateCustomerProject did not return a row.")
                        raise map_database_error(Exception("CreateCustomerProject failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create customer project: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[CustomerProject]:
        """
        Read a CustomerProject mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerProjectById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer project by ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> Optional[CustomerProject]:
        """
        Read a CustomerProject mapping record by Project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerProjectByProjectId",
                        params={"ProjectId": project_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer project by project ID: {error}")
            raise map_database_error(error)

    def read_identity_check(self, *, local_id: int, qbo_id: str) -> IdentityCheckResult:
        """
        Single JOIN'd read backing `verify_project_qbo_identity` (U-306):
        replaces the old `read_by_project_id` + `qbo_customer_repo.read_by_id`
        2-read path with one round trip, and additionally resolves whether
        the mapping table already binds `qbo_id` to a DIFFERENT ProjectId
        (the reverse check that closes U-297's H1).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerProjectIdentityCheckByProjectId",
                        params={"ProjectId": local_id, "QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return IdentityCheckResult(
                        mapping_id=getattr(row, "MappingId", None),
                        forward_external_qbo_id=getattr(row, "ForwardExternalQboId", None),
                        reverse_mapped_local_id=getattr(row, "ReverseMappedLocalId", None),
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer project identity check: {error}")
            raise map_database_error(error)

    def read_by_qbo_customer_id(self, qbo_customer_id: int) -> Optional[CustomerProject]:
        """
        Read a CustomerProject mapping record by QboCustomer ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerProjectByQboCustomerId",
                        params={"QboCustomerId": qbo_customer_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer project by QBO customer ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, customer_project: CustomerProject) -> Optional[CustomerProject]:
        """
        Update a CustomerProject mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateCustomerProjectById",
                        params={
                            "Id": customer_project.id,
                            "RowVersion": customer_project.row_version_bytes,
                            "ProjectId": customer_project.project_id,
                            "QboCustomerId": customer_project.qbo_customer_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateCustomerProjectById did not return a row.")
                        raise map_database_error(Exception("UpdateCustomerProjectById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update customer project by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[CustomerProject]:
        """
        Delete a CustomerProject mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteCustomerProjectById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete customer project by ID: {error}")
            raise map_database_error(error)
