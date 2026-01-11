# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.customer.business.model import QboCustomer
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboCustomerRepository:
    """
    Repository for QboCustomer persistence operations.
    """

    def __init__(self):
        """Initialize the QboCustomerRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboCustomer]:
        """
        Convert a database row into a QboCustomer dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboCustomer(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                display_name=getattr(row, "DisplayName", None),
                title=getattr(row, "Title", None),
                given_name=getattr(row, "GivenName", None),
                middle_name=getattr(row, "MiddleName", None),
                family_name=getattr(row, "FamilyName", None),
                suffix=getattr(row, "Suffix", None),
                company_name=getattr(row, "CompanyName", None),
                fully_qualified_name=getattr(row, "FullyQualifiedName", None),
                level=getattr(row, "Level", None),
                parent_ref_value=getattr(row, "ParentRefValue", None),
                parent_ref_name=getattr(row, "ParentRefName", None),
                job=getattr(row, "Job", None),
                active=getattr(row, "Active", None),
                primary_email_addr=getattr(row, "PrimaryEmailAddr", None),
                primary_phone=getattr(row, "PrimaryPhone", None),
                mobile=getattr(row, "Mobile", None),
                fax=getattr(row, "Fax", None),
                bill_addr_id=getattr(row, "BillAddrId", None),
                ship_addr_id=getattr(row, "ShipAddrId", None),
                balance=Decimal(str(getattr(row, "Balance"))) if getattr(row, "Balance", None) is not None else None,
                balance_with_jobs=Decimal(str(getattr(row, "BalanceWithJobs"))) if getattr(row, "BalanceWithJobs", None) is not None else None,
                taxable=getattr(row, "Taxable", None),
                notes=getattr(row, "Notes", None),
                print_on_check_name=getattr(row, "PrintOnCheckName", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo customer mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo customer mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        display_name: Optional[str],
        title: Optional[str],
        given_name: Optional[str],
        middle_name: Optional[str],
        family_name: Optional[str],
        suffix: Optional[str],
        company_name: Optional[str],
        fully_qualified_name: Optional[str],
        level: Optional[int],
        parent_ref_value: Optional[str],
        parent_ref_name: Optional[str],
        job: Optional[bool],
        active: Optional[bool],
        primary_email_addr: Optional[str],
        primary_phone: Optional[str],
        mobile: Optional[str],
        fax: Optional[str],
        bill_addr_id: Optional[int],
        ship_addr_id: Optional[int],
        balance: Optional[Decimal],
        balance_with_jobs: Optional[Decimal],
        taxable: Optional[bool],
        notes: Optional[str],
        print_on_check_name: Optional[str],
    ) -> QboCustomer:
        """
        Create a new QboCustomer.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboCustomer",
                        params={
                            "QboId": qbo_id,
                            "SyncToken": sync_token,
                            "RealmId": realm_id,
                            "DisplayName": display_name,
                            "Title": title,
                            "GivenName": given_name,
                            "MiddleName": middle_name,
                            "FamilyName": family_name,
                            "Suffix": suffix,
                            "CompanyName": company_name,
                            "FullyQualifiedName": fully_qualified_name,
                            "Level": level,
                            "ParentRefValue": parent_ref_value,
                            "ParentRefName": parent_ref_name,
                            "Job": job,
                            "Active": active,
                            "PrimaryEmailAddr": primary_email_addr,
                            "PrimaryPhone": primary_phone,
                            "Mobile": mobile,
                            "Fax": fax,
                            "BillAddrId": bill_addr_id,
                            "ShipAddrId": ship_addr_id,
                            "Balance": float(balance) if balance is not None else None,
                            "BalanceWithJobs": float(balance_with_jobs) if balance_with_jobs is not None else None,
                            "Taxable": taxable,
                            "Notes": notes,
                            "PrintOnCheckName": print_on_check_name,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo customer did not return a row.")
                        raise map_database_error(Exception("create qbo customer failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo customer: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboCustomer]:
        """
        Read all QboCustomers.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCustomers",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all qbo customers: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboCustomer]:
        """
        Read all QboCustomers by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCustomersByRealmId",
                        params={"RealmId": realm_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo customers by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboCustomer]:
        """
        Read a QboCustomer by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCustomerById",
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
            logger.error(f"Error during read qbo customer by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboCustomer]:
        """
        Read a QboCustomer by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCustomerByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo customer by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboCustomer]:
        """
        Read a QboCustomer by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCustomerByQboIdAndRealmId",
                        params={"QboId": qbo_id, "RealmId": realm_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo customer by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        display_name: Optional[str],
        title: Optional[str],
        given_name: Optional[str],
        middle_name: Optional[str],
        family_name: Optional[str],
        suffix: Optional[str],
        company_name: Optional[str],
        fully_qualified_name: Optional[str],
        level: Optional[int],
        parent_ref_value: Optional[str],
        parent_ref_name: Optional[str],
        job: Optional[bool],
        active: Optional[bool],
        primary_email_addr: Optional[str],
        primary_phone: Optional[str],
        mobile: Optional[str],
        fax: Optional[str],
        bill_addr_id: Optional[int],
        ship_addr_id: Optional[int],
        balance: Optional[Decimal],
        balance_with_jobs: Optional[Decimal],
        taxable: Optional[bool],
        notes: Optional[str],
        print_on_check_name: Optional[str],
    ) -> Optional[QboCustomer]:
        """
        Update a QboCustomer by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboCustomerByQboId",
                        params={
                            "QboId": qbo_id,
                            "RowVersion": row_version,
                            "SyncToken": sync_token,
                            "RealmId": realm_id,
                            "DisplayName": display_name,
                            "Title": title,
                            "GivenName": given_name,
                            "MiddleName": middle_name,
                            "FamilyName": family_name,
                            "Suffix": suffix,
                            "CompanyName": company_name,
                            "FullyQualifiedName": fully_qualified_name,
                            "Level": level,
                            "ParentRefValue": parent_ref_value,
                            "ParentRefName": parent_ref_name,
                            "Job": job,
                            "Active": active,
                            "PrimaryEmailAddr": primary_email_addr,
                            "PrimaryPhone": primary_phone,
                            "Mobile": mobile,
                            "Fax": fax,
                            "BillAddrId": bill_addr_id,
                            "ShipAddrId": ship_addr_id,
                            "Balance": float(balance) if balance is not None else None,
                            "BalanceWithJobs": float(balance_with_jobs) if balance_with_jobs is not None else None,
                            "Taxable": taxable,
                            "Notes": notes,
                            "PrintOnCheckName": print_on_check_name,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo customer did not return a row.")
                        raise map_database_error(Exception("update qbo customer by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo customer by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboCustomer]:
        """
        Delete a QboCustomer by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboCustomerByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Delete qbo customer did not return a row.")
                        raise map_database_error(Exception("delete qbo customer by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo customer by QBO ID: {error}")
            raise map_database_error(error)
