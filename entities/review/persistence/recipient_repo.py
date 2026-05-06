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
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ResolveReviewRecipientsByBillId",
                    params={
                        "BillId": bill_id,
                        "ExcludeUserId": exclude_user_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(r) for r in rows if r]
        except Exception as error:
            logger.error(
                f"Error resolving review recipients for bill {bill_id}: {error}"
            )
            raise map_database_error(error)
