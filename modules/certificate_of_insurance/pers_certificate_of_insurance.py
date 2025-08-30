"""
Module for certificate of insurance persistence.
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
class CertificateOfInsurance:
    """Data class to represent a certificate of insurance"""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    transaction_id: Optional[int] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['CertificateOfInsurance']:
        """Creates a CertificateOfInsurance instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            transaction_id=getattr(row, 'TransactionId', None),
        )


def create_certificate_of_insurance(
        certificate_of_insurance: CertificateOfInsurance
    ) -> PersistenceResponse:
    """
    Creates a new certificate of insurance record in the database.

    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCertificateOfInsurance(?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    certificate_of_insurance.created_datetime,
                    certificate_of_insurance.modified_datetime,
                    certificate_of_insurance.transaction_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Certificate of insurance creation failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in create certificate of insurance: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
