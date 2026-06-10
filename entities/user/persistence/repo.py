# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user.business.model import User
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserRepository:
    """
    Repository for User persistence operations.
    """

    def __init__(self):
        """Initialize the UserRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[User]:
        """
        Convert a database row into a User dataclass.
        """
        if not row:
            return None

        try:
            # New access-control discriminators are tolerated as missing for
            # any sproc that hasn't been re-issued with the new SELECT shape.
            is_system_admin = getattr(row, "IsSystemAdmin", None)
            is_agent = getattr(row, "IsAgent", None)
            last_company_id = getattr(row, "LastCompanyId", None)
            created_by_user_id = getattr(row, "CreatedByUserId", None)
            modified_by_user_id = getattr(row, "ModifiedByUserId", None)
            employee_id = getattr(row, "EmployeeId", None)
            vendor_id = getattr(row, "VendorId", None)
            return User(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                firstname=row.Firstname,
                lastname=row.Lastname,
                is_system_admin=bool(is_system_admin) if is_system_admin is not None else None,
                is_agent=bool(is_agent) if is_agent is not None else None,
                last_company_id=last_company_id,
                created_by_user_id=created_by_user_id,
                modified_by_user_id=modified_by_user_id,
                employee_id=employee_id,
                vendor_id=vendor_id,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        firstname: str,
        lastname: str,
        created_by_user_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> User:
        """
        Create a new user, stamping CreatedByUserId / ModifiedByUserId.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUser",
                    params={
                        "Firstname": firstname,
                        "Lastname": lastname,
                        "CreatedByUserId": created_by_user_id,
                        "ModifiedByUserId": modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUser did not return a row.")
                    raise map_database_error(Exception("CreateUser failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user: {error}")
            raise map_database_error(error)

    def read_workers(self) -> list[User]:
        """
        Read users eligible to be picked as a worker on a TimeEntry.

        Excludes LLM agents and persona test accounts; includes users
        with an Employee/Vendor FK linkage OR a 'Field Crew' / 'Intern'
        role. Same column shape as ReadUsers so _from_db hydrates
        without changes.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadWorkers", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read workers: {error}")
            raise map_database_error(error)

    def read_all(self, *, include_agents: bool = False) -> list[User]:
        """
        Read users. By default agent users (IsAgent=1) are hidden;
        pass include_agents=True to surface them (e.g. for an admin
        Agents tab).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUsers",
                    params={"IncludeAgents": 1 if include_agents else 0},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all users: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[User]:
        """
        Read a user by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[User]:
        """
        Read a user by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by public ID: {error}")
            raise map_database_error(error)

    def read_by_firstname(self, firstname: str) -> Optional[User]:
        """
        Read a user by firstname.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserByFirstname",
                    params={"Firstname": firstname},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by firstname: {error}")
            raise map_database_error(error)

    def read_by_lastname(self, lastname: str) -> Optional[User]:
        """
        Read a user by lastname.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserByLastname",
                    params={"Lastname": lastname},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by lastname: {error}")
            raise map_database_error(error)

    def update_by_id(self, user: User) -> Optional[User]:
        """
        Update a user by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserById",
                    params={
                        "Id": user.id,
                        "RowVersion": user.row_version_bytes,
                        "Firstname": user.firstname,
                        "Lastname": user.lastname,
                        "ModifiedByUserId": user.modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user by ID: {error}")
            raise map_database_error(error)

    def set_last_company_id(self, *, user_id: int, last_company_id: int) -> None:
        """
        Phase 0 — remember the active Company a user last switched to so
        the next login can default `cid` to it. Does not return a row.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetUserLastCompanyId",
                    params={
                        "UserId": user_id,
                        "LastCompanyId": last_company_id,
                    },
                )
        except Exception as error:
            logger.error(f"Error during set last company id: {error}")
            raise map_database_error(error)

    def update_worker_link(
        self,
        *,
        id: int,
        row_version_bytes: bytes,
        employee_id: Optional[int],
        vendor_id: Optional[int],
    ) -> Optional[User]:
        """Set the User's EmployeeId / VendorId worker linkage.

        XOR is enforced inside the sproc — passing both as non-NULL raises.
        Pass both as NULL to clear the link.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserWorkerLink",
                    params={
                        "Id": id,
                        "RowVersion": row_version_bytes,
                        "EmployeeId": employee_id,
                        "VendorId": vendor_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user worker link: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[User]:
        """
        Delete a user by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user by ID: {error}")
            raise map_database_error(error)
