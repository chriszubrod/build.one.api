"""
Module for certificate of insurance persistence.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional, List

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class CertificateOfInsurance:
    """Represents a certificate of insurance."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    type_of_insurance_id: Optional[int] = None
    policy_number: Optional[str] = None
    policy_eff_date: Optional[date] = None
    policy_exp_date: Optional[date] = None
    certificate_of_insurance_attachment_id: Optional[int] = None
    vendor_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['CertificateOfInsurance']:
        """Creates a CertificateOfInsurance instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            type_of_insurance_id=getattr(row, 'TypeOfInsuranceId', None),
            policy_number=getattr(row, 'PolicyNumber', None),
            policy_eff_date=getattr(row, 'PolicyEffDate', None),
            policy_exp_date=getattr(row, 'PolicyExpDate', None),
            certificate_of_insurance_attachment_id=getattr(row, 'CertificateOfInsuranceAttachmentId', None),
            vendor_id=getattr(row, 'VendorId', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_certificate_of_insurance(coi: CertificateOfInsurance) -> PersistenceResponse:
    """
    Creates a new certificate of insurance record in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCertificateOfInsurance(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    coi.type_of_insurance_id,
                    coi.policy_number,
                    coi.policy_eff_date,
                    coi.policy_exp_date,
                    coi.certificate_of_insurance_attachment_id,
                    coi.vendor_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate of insurance created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error creating certificate of insurance: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_of_insurances() -> PersistenceResponse:
    """Reads all certificates of insurance."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateOfInsurances}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[CertificateOfInsurance.from_db_row(r) for r in rows],
                        message="Certificates of insurance found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No certificates of insurance found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error reading certificates of insurance: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_of_insurance_by_id(coi_id: int) -> PersistenceResponse:
    """Reads a certificate of insurance by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateOfInsuranceById (?)}"
                row = cursor.execute(sql, coi_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CertificateOfInsurance.from_db_row(row),
                        message="Certificate of insurance found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error reading certificate of insurance by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_certificate_of_insurance_by_guid(coi_guid: str) -> PersistenceResponse:
    """Reads a certificate of insurance by GUID."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCertificateOfInsuranceByGUID (?)}"
                row = cursor.execute(sql, coi_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CertificateOfInsurance.from_db_row(row),
                        message="Certificate of insurance found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error reading certificate of insurance by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_certificate_of_insurance_by_id(coi: CertificateOfInsurance) -> PersistenceResponse:
    """Updates a certificate of insurance by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateCertificateOfInsuranceById (?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    coi.id,
                    coi.type_of_insurance_id,
                    coi.policy_number,
                    coi.policy_eff_date,
                    coi.policy_exp_date,
                    coi.certificate_of_insurance_attachment_id,
                    coi.vendor_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate of insurance updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error updating certificate of insurance: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_certificate_of_insurance_by_id(coi_id: int) -> PersistenceResponse:
    """Deletes a certificate of insurance by Id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteCertificateOfInsurance (?)}"
                rowcount = cursor.execute(sql, coi_id).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Certificate of insurance deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance not deleted",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error deleting certificate of insurance: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
