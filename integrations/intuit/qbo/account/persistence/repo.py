# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.account.business.model import QboAccount
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboAccountRepository:
    """
    Repository for QboAccount persistence operations.
    """

    def __init__(self):
        """Initialize the QboAccountRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboAccount]:
        """
        Convert a database row into a QboAccount dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboAccount(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                name=getattr(row, "Name", None),
                acct_num=getattr(row, "AcctNum", None),
                description=getattr(row, "Description", None),
                active=getattr(row, "Active", None),
                classification=getattr(row, "Classification", None),
                account_type=getattr(row, "AccountType", None),
                account_sub_type=getattr(row, "AccountSubType", None),
                fully_qualified_name=getattr(row, "FullyQualifiedName", None),
                sub_account=getattr(row, "SubAccount", None),
                parent_ref_value=getattr(row, "ParentRefValue", None),
                parent_ref_name=getattr(row, "ParentRefName", None),
                current_balance=Decimal(str(getattr(row, "CurrentBalance"))) if getattr(row, "CurrentBalance", None) is not None else None,
                current_balance_with_sub_accounts=Decimal(str(getattr(row, "CurrentBalanceWithSubAccounts"))) if getattr(row, "CurrentBalanceWithSubAccounts", None) is not None else None,
                currency_ref_value=getattr(row, "CurrencyRefValue", None),
                currency_ref_name=getattr(row, "CurrencyRefName", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo account mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo account mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        name: Optional[str],
        acct_num: Optional[str],
        description: Optional[str],
        active: Optional[bool],
        classification: Optional[str],
        account_type: Optional[str],
        account_sub_type: Optional[str],
        fully_qualified_name: Optional[str],
        sub_account: Optional[bool],
        parent_ref_value: Optional[str],
        parent_ref_name: Optional[str],
        current_balance: Optional[Decimal],
        current_balance_with_sub_accounts: Optional[Decimal],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
    ) -> QboAccount:
        """
        Create a new QboAccount.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "Name": name,
                        "AcctNum": acct_num,
                        "Description": description,
                        "Active": 1 if active is True else (0 if active is False else None),
                        "Classification": classification,
                        "AccountType": account_type,
                        "AccountSubType": account_sub_type,
                        "FullyQualifiedName": fully_qualified_name,
                        "SubAccount": 1 if sub_account is True else (0 if sub_account is False else None),
                        "ParentRefValue": parent_ref_value,
                        "ParentRefName": parent_ref_name,
                        "CurrentBalance": float(current_balance) if current_balance is not None else None,
                        "CurrentBalanceWithSubAccounts": float(current_balance_with_sub_accounts) if current_balance_with_sub_accounts is not None else None,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                    }
                    logger.debug(f"Calling CreateQboAccount with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboAccount",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo account did not return a row.")
                        raise map_database_error(Exception("create qbo account failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo account: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboAccount]:
        """
        Read all QboAccounts.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAccounts",
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
            logger.error(f"Error during read all qbo accounts: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboAccount]:
        """
        Read all QboAccounts by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAccountsByRealmId",
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
            logger.error(f"Error during read qbo accounts by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboAccount]:
        """
        Read a QboAccount by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAccountById",
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
            logger.error(f"Error during read qbo account by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboAccount]:
        """
        Read a QboAccount by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAccountByQboId",
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
            logger.error(f"Error during read qbo account by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboAccount]:
        """
        Read a QboAccount by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAccountByQboIdAndRealmId",
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
            logger.error(f"Error during read qbo account by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        name: Optional[str],
        acct_num: Optional[str],
        description: Optional[str],
        active: Optional[bool],
        classification: Optional[str],
        account_type: Optional[str],
        account_sub_type: Optional[str],
        fully_qualified_name: Optional[str],
        sub_account: Optional[bool],
        parent_ref_value: Optional[str],
        parent_ref_name: Optional[str],
        current_balance: Optional[Decimal],
        current_balance_with_sub_accounts: Optional[Decimal],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
    ) -> Optional[QboAccount]:
        """
        Update a QboAccount by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "RowVersion": row_version,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "Name": name,
                        "AcctNum": acct_num,
                        "Description": description,
                        "Active": 1 if active is True else (0 if active is False else None),
                        "Classification": classification,
                        "AccountType": account_type,
                        "AccountSubType": account_sub_type,
                        "FullyQualifiedName": fully_qualified_name,
                        "SubAccount": 1 if sub_account is True else (0 if sub_account is False else None),
                        "ParentRefValue": parent_ref_value,
                        "ParentRefName": parent_ref_name,
                        "CurrentBalance": float(current_balance) if current_balance is not None else None,
                        "CurrentBalanceWithSubAccounts": float(current_balance_with_sub_accounts) if current_balance_with_sub_accounts is not None else None,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                    }
                    logger.debug(f"Calling UpdateQboAccountByQboId with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboAccountByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo account did not return a row.")
                        raise map_database_error(Exception("update qbo account by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo account by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboAccount]:
        """
        Delete a QboAccount by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboAccountByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo account by QBO ID: {error}")
            raise map_database_error(error)
