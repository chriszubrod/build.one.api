from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

from shared.database import get_db_connection
from shared.response import PersistenceResponse

@dataclass
class IntuitVendor:
    """Intuit Vendor data class."""
    vendor_guid: Optional[str] = ""
    realm_id: Optional[str] = ""
    tax_identifier: Optional[str] = ""
    vendor_id: Optional[str] = ""
    sync_token: Optional[str] = ""
    created_datetime: Optional[datetime] = None
    last_update_datetime: Optional[datetime] = None
    company_name: Optional[str] = ""
    display_name: Optional[str] = ""
    print_on_check_name: Optional[str] = ""
    active: Optional[bool] = False
    v4id_pseudonym: Optional[str] = ""
    primary_email_address: Optional[str] = ""

    @classmethod
    def from_db_row(cls, row):
        """Create an IntuitVendor object from a database row."""
        return cls(
            vendor_guid=getattr(row, 'VendorGUID'),
            realm_id=getattr(row, 'RealmId'),
            tax_identifier=getattr(row, 'TaxId'),
            vendor_id=getattr(row, 'Id'),
            sync_token=getattr(row, 'SyncToken'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            last_update_datetime=getattr(row, 'LastUpdatedDatetime'),
            company_name=getattr(row, 'CompanyName'),
            display_name=getattr(row, 'DisplayName'),
            print_on_check_name=getattr(row, 'PrintOnCheckName'),
            active=getattr(row, 'IsActive'),
            v4id_pseudonym=getattr(row, 'V4IDPseudonym'),
            primary_email_address=getattr(row, 'PrimaryEmailAddress')
        )


def create_intuit_vendor(realm_id, intuit_vendor):
    """Create vendor."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateIntuitVendor(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    realm_id,
                    intuit_vendor.tax_identifier,
                    intuit_vendor.vendor_id,
                    intuit_vendor.sync_token,
                    intuit_vendor.created_datetime,
                    intuit_vendor.last_update_datetime,
                    intuit_vendor.company_name,
                    intuit_vendor.display_name,
                    intuit_vendor.print_on_check_name,
                    intuit_vendor.active,
                    intuit_vendor.v4id_pseudonym,
                    intuit_vendor.primary_email_address
                ).rowcount
                if row_count == 1:
                    return SuccessResponse(
                        message="Intuit Vendor has been successfully created.",
                        data=row_count,
                        status_code=201
                    )
                else:
                    return BusinessResponse(
                        message="Intuit Vendor has NOT been successfully created.",
                        status_code=501
                    )
        except pyodbc.DatabaseError as err:
            raise DatabaseError(f"Failed to create Intuit Vendor: {str(err)}") from err


def read_intuit_vendor_by_id(vendor_id) -> PersistenceResponse:
    """Read vendor by id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadIntuitVendorById(?)}"
                row = cursor.execute(sql, vendor_id).fetchone()
                if row:
                    return PersistenceResponse( 
                        message="Intuit Vendor found",
                        data=IntuitVendor.from_db_row(row),
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        message="Intuit Vendor not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now(),
                        data=None
                    )
        except pyodbc.DatabaseError as err:
            return PersistenceResponse(
                message=f"Failed to read Intuit Vendor: {str(err)}",
                status_code=500,
                success=False,
                timestamp=datetime.now(),
                data=None
            )


def update_intuit_vendor_by_realm_id_and_vendor_id(realm_id, intuit_vendor):
    """Update vendor by realm id and vendor id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateIntuitVendorByRealmIdAndVendorId(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    realm_id,
                    intuit_vendor.tax_identifier,
                    intuit_vendor.vendor_id,
                    intuit_vendor.sync_token,
                    intuit_vendor.created_datetime,
                    intuit_vendor.last_update_datetime,
                    intuit_vendor.company_name,
                    intuit_vendor.display_name,
                    intuit_vendor.print_on_check_name,
                    intuit_vendor.active,
                    intuit_vendor.v4id_pseudonym,
                    intuit_vendor.primary_email_address
                ).rowcount
                if row_count == 1:
                    return SuccessResponse(
                        message="Intuit Vendor has been successfully updated.",
                        data=row_count,
                        status_code=201
                    )
                else:
                    return BusinessResponse(
                        message="Intuit Vendor has NOT been successfully updated.",
                        status_code=404
                    )
        except pyodbc.DatabaseError as err:
            raise DatabaseError(f"Failed to update Intuit Vendor: {str(err)}") from err
