"""
Persistence for Certificate Type.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class CertificateType:
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'CertificateType':
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_certificate_type(certificate_type: CertificateType) -> PersistenceResponse:
    """Creates a new CertificateType row."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCertificateType (?)}"
                rowcount = cursor.execute(sql, certificate_type.name).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate type created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Certificate type not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create certificate type: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_types() -> PersistenceResponse:
    """Reads all CertificateType rows."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateTypes}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[CertificateType.from_db_row(r) for r in rows],
                        message="Certificate types found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="No certificate types found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read certificate types: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_type_by_name(name: str) -> PersistenceResponse:
    """Reads a CertificateType by name."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateTypeByName (?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CertificateType.from_db_row(row),
                        message="Certificate type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="Certificate type not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read certificate type by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_type_by_id(id: int) -> PersistenceResponse:
    """Reads a CertificateType by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateTypeByID (?)}"
                row = cursor.execute(sql, id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CertificateType.from_db_row(row),
                        message="Certificate type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="Certificate type not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read certificate type by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_type_by_guid(guid: str) -> PersistenceResponse:
    """Reads a CertificateType by GUID."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateTypeByGUID (?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CertificateType.from_db_row(row),
                        message="Certificate type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="Certificate type not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read certificate type by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_certificate_type(certificate_type: CertificateType) -> PersistenceResponse:
    """Updates a CertificateType by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateCertificateType (?, ?)}"
                rowcount = cursor.execute(sql, certificate_type.id, certificate_type.name).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate type updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Certificate type not updated",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update certificate type: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_certificate_type(certificate_type: CertificateType) -> PersistenceResponse:
    """Deletes a CertificateType by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteCertificateType (?)}"
                rowcount = cursor.execute(sql, certificate_type.id).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate type deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Certificate type not deleted",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to delete certificate type: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

