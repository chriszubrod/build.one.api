# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboCompanyInfoRepository:
    """
    Repository for QboCompanyInfo persistence operations.
    """

    def __init__(self):
        """Initialize the QboCompanyInfoRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboCompanyInfo]:
        """
        Convert a database row into a QboCompanyInfo dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboCompanyInfo(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                company_name=getattr(row, "CompanyName", None),
                legal_name=getattr(row, "LegalName", None),
                company_addr_id=getattr(row, "CompanyAddrId", None),
                legal_addr_id=getattr(row, "LegalAddrId", None),
                customer_communication_addr_id=getattr(row, "CustomerCommunicationAddrId", None),
                tax_payer_id=getattr(row, "TaxPayerId", None),
                fiscal_year_start_month=getattr(row, "FiscalYearStartMonth", None),
                country=getattr(row, "Country", None),
                email=getattr(row, "Email", None),
                web_addr=getattr(row, "WebAddr", None),
                currency_ref=getattr(row, "CurrencyRef", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo company info mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo company info mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        company_name: Optional[str],
        legal_name: Optional[str],
        company_addr_id: Optional[int],
        legal_addr_id: Optional[int],
        customer_communication_addr_id: Optional[int],
        tax_payer_id: Optional[str],
        fiscal_year_start_month: Optional[int],
        country: Optional[str],
        email: Optional[str],
        web_addr: Optional[str],
        currency_ref: Optional[str],
    ) -> QboCompanyInfo:
        """
        Create a new QboCompanyInfo.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboCompanyInfo",
                        params={
                            "QboId": qbo_id,
                            "SyncToken": sync_token,
                            "RealmId": realm_id,
                            "CompanyName": company_name,
                            "LegalName": legal_name,
                            "CompanyAddrId": company_addr_id,
                            "LegalAddrId": legal_addr_id,
                            "CustomerCommunicationAddrId": customer_communication_addr_id,
                            "TaxPayerId": tax_payer_id,
                            "FiscalYearStartMonth": fiscal_year_start_month,
                            "Country": country,
                            "Email": email,
                            "WebAddr": web_addr,
                            "CurrencyRef": currency_ref,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo company info did not return a row.")
                        raise map_database_error(Exception("create qbo company info failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo company info: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[QboCompanyInfo]:
        """
        Read all QboCompanyInfos.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCompanyInfos",
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
            logger.error(f"Error during read all qbo company infos: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboCompanyInfo]:
        """
        Read a QboCompanyInfo by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCompanyInfoById",
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
            logger.error(f"Error during read qbo company info by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboCompanyInfo]:
        """
        Read a QboCompanyInfo by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCompanyInfoByQboId",
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
            logger.error(f"Error during read qbo company info by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> Optional[QboCompanyInfo]:
        """
        Read a QboCompanyInfo by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboCompanyInfoByRealmId",
                        params={"RealmId": realm_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo company info by realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        company_name: Optional[str],
        legal_name: Optional[str],
        company_addr_id: Optional[int],
        legal_addr_id: Optional[int],
        customer_communication_addr_id: Optional[int],
        tax_payer_id: Optional[str],
        fiscal_year_start_month: Optional[int],
        country: Optional[str],
        email: Optional[str],
        web_addr: Optional[str],
        currency_ref: Optional[str],
    ) -> Optional[QboCompanyInfo]:
        """
        Update a QboCompanyInfo by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboCompanyInfoByQboId",
                        params={
                            "QboId": qbo_id,
                            "RowVersion": row_version,
                            "SyncToken": sync_token,
                            "RealmId": realm_id,
                            "CompanyName": company_name,
                            "LegalName": legal_name,
                            "CompanyAddrId": company_addr_id,
                            "LegalAddrId": legal_addr_id,
                            "CustomerCommunicationAddrId": customer_communication_addr_id,
                            "TaxPayerId": tax_payer_id,
                            "FiscalYearStartMonth": fiscal_year_start_month,
                            "Country": country,
                            "Email": email,
                            "WebAddr": web_addr,
                            "CurrencyRef": currency_ref,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo company info did not return a row.")
                        raise map_database_error(Exception("update qbo company info by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo company info by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboCompanyInfo]:
        """
        Delete a QboCompanyInfo by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboCompanyInfoByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Delete qbo company info did not return a row.")
                        raise map_database_error(Exception("delete qbo company info by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo company info by QBO ID: {error}")
            raise map_database_error(error)

