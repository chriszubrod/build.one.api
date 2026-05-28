# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.review.business.recipient_model import ResolvedRecipient
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class ReviewRecipientRepository:
    """
    Repository for resolving review-notification recipients via the
    ResolveReviewRecipientsByBillId sproc.
    """

    def _from_db(self, row: pyodbc.Row) -> ResolvedRecipient:
        return ResolvedRecipient(
            user_id=row.UserId,
            firstname=row.Firstname,
            lastname=row.Lastname,
            email=row.Email,
            role_name=row.RoleName,
            project_id=row.ProjectId,
        )

    def resolve_for_bill(
        self,
        *,
        bill_id: int,
        exclude_user_id: Optional[int] = None,
    ) -> list[ResolvedRecipient]:
        return self._resolve(
            sproc="ResolveReviewRecipientsByBillId",
            id_key="BillId",
            parent_id=bill_id,
            exclude_user_id=exclude_user_id,
        )

    def resolve_for_contract_labor(
        self,
        *,
        contract_labor_id: int,
        exclude_user_id: Optional[int] = None,
    ) -> list[ResolvedRecipient]:
        return self._resolve(
            sproc="ResolveReviewRecipientsByContractLaborId",
            id_key="ContractLaborId",
            parent_id=contract_labor_id,
            exclude_user_id=exclude_user_id,
        )

    def _resolve(
        self,
        *,
        sproc: str,
        id_key: str,
        parent_id: int,
        exclude_user_id: Optional[int],
    ) -> list[ResolvedRecipient]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name=sproc,
                    params={
                        id_key: parent_id,
                        "ExcludeUserId": exclude_user_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(r) for r in rows if r]
        except Exception as error:
            logger.error(
                f"Error resolving review recipients via {sproc} for parent {parent_id}: {error}"
            )
            raise map_database_error(error)
