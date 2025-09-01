"""
Module for certificate of insurance persistence.
"""

# python standard library imports
from dataclasses import dataclass
# types
from datetime import datetime, date
from typing import Optional

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class Certificate:
    """Represents a certificate."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    certificate_type_id: Optional[int] = None
    policy_number: Optional[str] = None
    policy_eff_date: Optional[date] = None
    policy_exp_date: Optional[date] = None
    certificate_attachment_id: Optional[int] = None
    vendor_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Certificate']:
        """Creates a Certificate instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            certificate_type_id=getattr(row, 'CertificateTypeId', None),
            policy_number=getattr(row, 'PolicyNumber', None),
            policy_eff_date=getattr(row, 'PolicyEffDate', None),
            policy_exp_date=getattr(row, 'PolicyExpDate', None),
            certificate_attachment_id=getattr(row, 'CertificateAttachmentId', None),
            vendor_id=getattr(row, 'VendorId', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_certificate(certificate: Certificate) -> PersistenceResponse:
    """Creates a new certificate record in the database."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCertificate(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    certificate.certificate_type_id,
                    certificate.policy_number,
                    certificate.policy_eff_date,
                    certificate.policy_exp_date,
                    certificate.certificate_attachment_id,
                    certificate.vendor_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error creating certificate: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificates() -> PersistenceResponse:
    """Reads all certificates."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificates}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Certificate.from_db_row(r) for r in rows],
                        message="Certificates found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No certificates found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error reading certificates: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_by_id(id: int) -> PersistenceResponse:
    """Reads a certificate by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateById (?)}"
                row = cursor.execute(sql, id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Certificate.from_db_row(row),
                        message="Certificate found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Certificate not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error reading certificate by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_by_guid(guid: str) -> PersistenceResponse:
    """Reads a certificate by GUID."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateByGUID (?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Certificate.from_db_row(row),
                        message="Certificate found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Certificate not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error reading certificate by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_certificate_by_id(certificate: Certificate) -> PersistenceResponse:
    """Updates a certificate by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateCertificateById (?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    certificate.id,
                    certificate.certificate_type_id,
                    certificate.policy_number,
                    certificate.policy_eff_date,
                    certificate.policy_exp_date,
                    certificate.certificate_attachment_id,
                    certificate.vendor_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error updating certificate: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_certificate_by_id(id: int) -> PersistenceResponse:
    """Deletes a certificate by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteCertificate (?)}"
                rowcount = cursor.execute(sql, id).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate not deleted",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error deleting certificate: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
