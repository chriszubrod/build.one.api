# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.company.business.model import Company
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CompanyRepository:
    """
    Repository for Company persistence operations.
    """

    def __init__(self):
        """Initialize the CompanyRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Company]:
        if not row:
            return None

        try:
            return Company(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                website=row.Website,
                organization_id=getattr(row, "OrganizationId", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                modified_by_user_id=getattr(row, "ModifiedByUserId", None),
                qbo_id=getattr(row, "QboId", None),
                realm_id=getattr(row, "RealmId", None),
                ap_account_qbo_id=getattr(row, "APAccountQboId", None),
                ap_account_name=getattr(row, "APAccountName", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during company mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        name: str,
        website: str,
        organization_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Company:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateCompany",
                    params={
                        "Name": name,
                        "Website": website,
                        "OrganizationId": organization_id,
                        "CreatedByUserId": created_by_user_id,
                        "ModifiedByUserId": modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateCompany did not return a row.")
                    raise map_database_error(Exception("CreateCompany failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create company: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Company]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanies",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all companies: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Company]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_identity(self, qbo_id: str, realm_id: Optional[str] = None) -> Optional[Company]:
        """
        Read a company directly by its dbo-native QBO identity (U-277), bypassing
        the qbo.CompanyInfo / qbo.CompanyInfoCompany staging/mapping tables entirely.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyByQboIdAndRealmId",
                    params={"QboId": qbo_id, "RealmId": realm_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by QBO identity: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> Optional[Company]:
        """
        Read a company directly by its dbo-native QBO RealmId (U-281) — the
        seam BillBillConnector._get_ap_account_ref uses to resolve the cached
        AP-account fact from a bare realm_id, with no Company id in hand.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyByRealmId",
                    params={"RealmId": realm_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by realm ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Company]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Company]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, company: Company) -> Optional[Company]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateCompanyById",
                    params={
                        "Id": company.id,
                        "RowVersion": company.row_version_bytes,
                        "Name": company.name,
                        "Website": company.website,
                        "OrganizationId": company.organization_id,
                        "ModifiedByUserId": company.modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update company by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Company]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete company by ID: {error}")
            raise map_database_error(error)

    def set_qbo_identity(
        self,
        *,
        id: int,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> None:
        """Stamp dbo-native QBO identity columns (idempotent-safe via CASE WHEN sproc)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetCompanyQboIdentity",
                    params={
                        "Id": id,
                        "QboId": qbo_id,
                        "RealmId": realm_id,
                    },
                )
                row = cursor.fetchone()
                if row and getattr(row, "Stolen", False):
                    logger.warning(
                        "Company %s stole QBO identity (qbo_id=%s realm_id=%s) from a different "
                        "Company row — a stale duplicate identity existed before this stamp",
                        id, qbo_id, realm_id,
                    )
        except Exception as error:
            logger.error(
                "Error stamping Company QBO identity (company_id=%s qbo_id=%s realm_id=%s): %s",
                id,
                qbo_id,
                realm_id,
                error,
            )
            raise map_database_error(error)

    def set_ap_account(
        self,
        *,
        realm_id: str,
        ap_account_qbo_id: Optional[str],
        ap_account_name: Optional[str],
    ) -> None:
        """
        Stamp the cached AP-account fields (U-281) for the Company matching
        realm_id. Narrow OUTPUT (Id/RealmId/APAccountQboId/APAccountName
        only, mirrors set_qbo_identity's own narrow OUTPUT) — not a full
        Company row, so this does not route through _from_db.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetCompanyApAccount",
                    params={
                        "RealmId": realm_id,
                        "APAccountQboId": ap_account_qbo_id,
                        "APAccountName": ap_account_name,
                    },
                )
                row = cursor.fetchone()
                if row is None:
                    logger.warning(
                        "SetCompanyApAccount matched no Company row for realm_id=%s "
                        "(AP account cache not updated)",
                        realm_id,
                    )
        except Exception as error:
            logger.error(
                "Error stamping Company AP account (realm_id=%s): %s",
                realm_id,
                error,
            )
            raise map_database_error(error)
