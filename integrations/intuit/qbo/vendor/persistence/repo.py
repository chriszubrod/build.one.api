# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.vendor.business.model import QboVendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboVendorRepository:
    """
    Repository for QboVendor persistence operations.
    """

    def __init__(self):
        """Initialize the QboVendorRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboVendor]:
        """
        Convert a database row into a QboVendor dataclass.
        """
        if not row:
            return None

        try:
            return QboVendor(
                id=row.Id,
                sync_token=row.SyncToken,
                display_name=row.DisplayName,
                vendor_1099=row.Vendor1099,
                company_name=row.CompanyName,
                tax_identifier=row.TaxIdentifier,
                print_on_check_name=row.PrintOnCheckName,
                bill_addr_id=row.BillAddrId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo vendor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo vendor mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        id: Optional[str],
        sync_token: Optional[str],
        display_name: Optional[str],
        vendor_1099: Optional[int],
        company_name: Optional[str],
        tax_identifier: Optional[str],
        print_on_check_name: Optional[str],
        bill_addr_id: Optional[str],
    ) -> QboVendor:
        """
        Create a new QboVendor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateQboVendor",
                    params={
                        "Id": id,
                        "SyncToken": sync_token,
                        "DisplayName": display_name,
                        "Vendor1099": vendor_1099,
                        "CompanyName": company_name,
                        "TaxIdentifier": tax_identifier,
                        "PrintOnCheckName": print_on_check_name,
                        "BillAddrId": bill_addr_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create qbo vendor did not return a row.")
                    raise map_database_error(Exception("create qbo vendor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create qbo vendor: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[QboVendor]:
        """
        Read all QboVendors.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboVendors",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all qbo vendors: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo vendor by ID: {error}")
            raise map_database_error(error)

    def read_by_sync_token(self, sync_token: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by sync token.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboVendorBySyncToken",
                    params={"SyncToken": sync_token},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo vendor by sync token: {error}")
            raise map_database_error(error)

    def read_by_display_name(self, display_name: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by display name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboVendorByDisplayName",
                    params={"DisplayName": display_name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo vendor by display name: {error}")
            raise map_database_error(error)

    def read_by_company_name(self, company_name: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by company name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboVendorByCompanyName",
                    params={"CompanyName": company_name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo vendor by company name: {error}")
            raise map_database_error(error)

    def read_by_tax_identifier(self, tax_identifier: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by tax identifier.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboVendorByTaxIdentifier",
                    params={"TaxIdentifier": tax_identifier},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo vendor by tax identifier: {error}")
            raise map_database_error(error)

    def update_by_id(self, id: str, sync_token: str, display_name: str, vendor_1099: int, company_name: str, tax_identifier: str, print_on_check_name: str, bill_addr_id: str) -> Optional[QboVendor]:
        """
        Update a QboVendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateQboVendorById",
                    params={
                        "Id": id,
                        "SyncToken": sync_token,
                        "DisplayName": display_name,
                        "Vendor1099": vendor_1099,
                        "CompanyName": company_name,
                        "TaxIdentifier": tax_identifier,
                        "PrintOnCheckName": print_on_check_name,
                        "BillAddrId": bill_addr_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update qbo vendor did not return a row.")
                    raise map_database_error(Exception("update qbo vendor by ID failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update qbo vendor by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[QboVendor]:
        """
        Delete a QboVendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteQboVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete qbo vendor did not return a row.")
                    raise map_database_error(Exception("delete qbo vendor by ID failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during delete qbo vendor by ID: {error}")
            raise map_database_error(error)
