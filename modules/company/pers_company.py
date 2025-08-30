"""
Module for company persistence.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


# third party imports
import pyodbc


# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class Company:
    """Represents a company in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Company']:
        """Creates a Company instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None)
        )


def create_company(company: Company) -> PersistenceResponse:
    """Creates a new company in the database."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCompany(?, ?, ?)}"
                row = cursor.execute(
                    sql,
                    company.created_datetime,
                    company.modified_datetime,
                    company.name
                ).fetchone()
                cnxn.commit()
                if row:
                    return PersistenceResponse(
                        data=Company.from_db_row(row),
                        message="Company created successfully",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Company creation failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create company: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_company() -> PersistenceResponse:
    """Reads a company from the database."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCompany()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Company.from_db_row(row) for row in rows],
                        message="Company read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Company not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read companies: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_company_by_guid(company_guid: str) -> PersistenceResponse:
    """Reads a company from the database by guid."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCompanyByGuid(?)}"
                row = cursor.execute(sql, company_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Company.from_db_row(row),
                        message="Company read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Company not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read companies: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_company_by_id(company: Company) -> PersistenceResponse:
    """Updates a company in the database by ID."""
    print(company)
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateCompanyById(?, ?, ?)}"
                count = cursor.execute(
                    sql,
                    company.id,
                    company.modified_datetime,
                    company.name
                ).rowcount
                cnxn.commit()
                if count:
                    return PersistenceResponse(
                        data=None,
                        message="Company updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Company not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update company by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_company_by_id(company_id: int) -> PersistenceResponse:
    """Deletes a company from the database by ID."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteCompanyById(?)}"
                row_count = cursor.execute(sql, company_id).fetchone()
                if not row_count:
                    return PersistenceResponse(
                        data=row_count,
                        message="Company deleted successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Company not deleted",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to delete company by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
