# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.taxpayer.business.model import Taxpayer, TaxpayerClassification
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TaxpayerRepository:
    """
    Repository for Taxpayer persistence operations.
    """

    def __init__(self):
        """Initialize the TaxpayerRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Taxpayer]:
        """
        Convert a database row into a Taxpayer dataclass.
        """
        if not row:
            return None

        try:
            # Convert classification string to enum if it matches a valid value
            classification = None
            if row.Classification:
                try:
                    classification = TaxpayerClassification(row.Classification)
                except ValueError:
                    # If the value doesn't match any enum, keep as None or raise error
                    # For now, we'll set to None to handle legacy data
                    classification = None
            
            return Taxpayer(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                entity_name=row.EntityName,
                business_name=row.BusinessName,
                classification=classification,
                taxpayer_id_number=row.TaxpayerIdNumber,
                is_signed=row.IsSigned,
                signature_date=row.SignatureDate,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during taxpayer mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during taxpayer mapping: {error}")
            raise map_database_error(error)

    def create(self, *, entity_name: Optional[str], business_name: Optional[str], classification: Optional[TaxpayerClassification], taxpayer_id_number: Optional[str], is_signed: Optional[int] = 0, signature_date: Optional[str] = None) -> Taxpayer:
        """
        Create a new taxpayer.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateTaxpayer",
                        params={
                            "EntityName": entity_name,
                            "BusinessName": business_name,
                            "Classification": classification.value if classification else None,
                            "TaxpayerIdNumber": taxpayer_id_number,
                            "IsSigned": is_signed if is_signed is not None else 0,
                            "SignatureDate": signature_date,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateTaxpayer did not return a row.")
                        raise map_database_error(Exception("CreateTaxpayer failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create taxpayer: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Taxpayer]:
        """
        Read all taxpayers.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaxpayers",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all taxpayers: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Taxpayer]:
        """
        Read a taxpayer by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaxpayerById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read taxpayer by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaxpayerByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read taxpayer by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, entity_name: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by entity name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaxpayerByName",
                    params={"EntityName": entity_name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read taxpayer by entity name: {error}")
            raise map_database_error(error)

    def read_by_business_name(self, business_name: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by business name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaxpayerByBusinessName",
                    params={"BusinessName": business_name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read taxpayer by business name: {error}")
            raise map_database_error(error)

    def read_by_taxpayer_id_number(self, taxpayer_id_number: str) -> Optional[Taxpayer]:
        """
        Read a taxpayer by taxpayer ID number (encrypted value).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTaxpayerByTaxpayerIdNumber",
                    params={"TaxpayerIdNumber": taxpayer_id_number},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read taxpayer by taxpayer ID number: {error}")
            raise map_database_error(error)

    def update_by_id(self, taxpayer: Taxpayer) -> Optional[Taxpayer]:
        """
        Update a taxpayer by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateTaxpayerById",
                    params={
                        "Id": taxpayer.id,
                        "RowVersion": taxpayer.row_version_bytes,
                        "EntityName": taxpayer.entity_name,
                        "BusinessName": taxpayer.business_name,
                        "Classification": taxpayer.classification.value if taxpayer.classification else None,
                        "TaxpayerIdNumber": taxpayer.taxpayer_id_number,
                        "IsSigned": taxpayer.is_signed if taxpayer.is_signed is not None else 0,
                        "SignatureDate": taxpayer.signature_date,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update taxpayer by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Taxpayer]:
        """
        Delete a taxpayer by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteTaxpayerById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete taxpayer by ID: {error}")
            raise map_database_error(error)
