# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.review.business.model import Review
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ReviewRepository:
    """
    Repository for Review persistence operations.

    Reviews are insert-only audit records. There is no update or delete
    surface — once written, a Review row is part of the parent's history.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Review]:
        if not row:
            return None

        try:
            return Review(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                review_status_id=row.ReviewStatusId,
                user_id=row.UserId,
                comments=row.Comments,
                bill_id=row.BillId,
                expense_id=row.ExpenseId,
                bill_credit_id=row.BillCreditId,
                invoice_id=row.InvoiceId,
                contract_labor_id=getattr(row, "ContractLaborId", None),
                status_name=row.StatusName,
                status_sort_order=row.StatusSortOrder,
                status_is_final=bool(row.StatusIsFinal),
                status_is_declined=bool(row.StatusIsDeclined),
                status_color=row.StatusColor,
                user_firstname=row.UserFirstname,
                user_lastname=row.UserLastname,
                email_message_id=getattr(row, "EmailMessageId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during review mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during review mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        review_status_id: int,
        user_id: int,
        comments: Optional[str] = None,
        bill_id: Optional[int] = None,
        expense_id: Optional[int] = None,
        bill_credit_id: Optional[int] = None,
        invoice_id: Optional[int] = None,
        contract_labor_id: Optional[int] = None,
        email_message_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Review:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateReview",
                    params={
                        "ReviewStatusId": review_status_id,
                        "UserId": user_id,
                        "Comments": comments,
                        "BillId": bill_id,
                        "ExpenseId": expense_id,
                        "BillCreditId": bill_credit_id,
                        "InvoiceId": invoice_id,
                        "ContractLaborId": contract_labor_id,
                        "EmailMessageId": email_message_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateReview did not return a row.")
                    raise map_database_error(Exception("CreateReview failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create review: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Review]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read review by public ID: {error}")
            raise map_database_error(error)

    # ---------------------------------------------------------------------
    # Per-parent list reads
    # ---------------------------------------------------------------------

    def read_by_bill_id(self, bill_id: int) -> list[Review]:
        return self._read_list("ReadReviewsByBillId", {"BillId": bill_id})

    def delete_by_bill_id(self, bill_id: int) -> None:
        """Delete all Review (and legacy ReviewEntry) rows for a Bill.

        Reviews are otherwise insert-only audit records; this exists ONLY so
        the parent Bill can be hard-deleted without tripping FK_Review_Bill.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteReviewsByBillId",
                    params={"BillId": bill_id},
                )
        except Exception as error:
            logger.error(f"Error during delete reviews by bill id {bill_id}: {error}")
            raise map_database_error(error)

    def read_by_expense_id(self, expense_id: int) -> list[Review]:
        return self._read_list("ReadReviewsByExpenseId", {"ExpenseId": expense_id})

    def read_by_bill_credit_id(self, bill_credit_id: int) -> list[Review]:
        return self._read_list("ReadReviewsByBillCreditId", {"BillCreditId": bill_credit_id})

    def read_by_invoice_id(self, invoice_id: int) -> list[Review]:
        return self._read_list("ReadReviewsByInvoiceId", {"InvoiceId": invoice_id})

    def read_by_contract_labor_id(self, contract_labor_id: int) -> list[Review]:
        return self._read_list(
            "ReadReviewsByContractLaborId",
            {"ContractLaborId": contract_labor_id},
        )

    def delete_by_contract_labor_id(self, contract_labor_id: int) -> None:
        """Delete all Review rows for a ContractLabor parent.

        Insert-only audit otherwise; this exists so ContractLabor hard-delete
        doesn't trip FK_Review_ContractLabor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteReviewsByContractLaborId",
                    params={"ContractLaborId": contract_labor_id},
                )
        except Exception as error:
            logger.error(f"Error during delete reviews by contract_labor id {contract_labor_id}: {error}")
            raise map_database_error(error)

    # ---------------------------------------------------------------------
    # Per-parent current (latest) reads
    # ---------------------------------------------------------------------

    def read_current_by_bill_id(self, bill_id: int) -> Optional[Review]:
        return self._read_current("ReadCurrentReviewByBillId", {"BillId": bill_id})

    def read_current_by_expense_id(self, expense_id: int) -> Optional[Review]:
        return self._read_current("ReadCurrentReviewByExpenseId", {"ExpenseId": expense_id})

    def read_current_by_bill_credit_id(self, bill_credit_id: int) -> Optional[Review]:
        return self._read_current("ReadCurrentReviewByBillCreditId", {"BillCreditId": bill_credit_id})

    def read_current_by_invoice_id(self, invoice_id: int) -> Optional[Review]:
        return self._read_current("ReadCurrentReviewByInvoiceId", {"InvoiceId": invoice_id})

    def read_current_by_contract_labor_id(self, contract_labor_id: int) -> Optional[Review]:
        return self._read_current(
            "ReadCurrentReviewByContractLaborId",
            {"ContractLaborId": contract_labor_id},
        )

    def read_current_by_bill_ids(self, bill_ids: list[int]) -> dict[int, Review]:
        """Batch lookup of the latest Review per Bill. Returns
        {bill_id: Review}; bills with no Review row are absent from the
        dict. Empty input → empty dict (no DB call).
        """
        if not bill_ids:
            return {}
        csv = ",".join(str(int(b)) for b in bill_ids if b is not None)
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCurrentReviewsByBillIds",
                    params={"BillIds": csv},
                )
                out: dict[int, Review] = {}
                for row in cursor.fetchall():
                    review = self._from_db(row)
                    if review is None or review.bill_id is None:
                        continue
                    out[int(review.bill_id)] = review
                return out
        except Exception as error:
            logger.error(f"Error during ReadCurrentReviewsByBillIds: {error}")
            raise map_database_error(error)

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    def _read_list(self, sproc: str, params: dict) -> list[Review]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name=sproc, params=params)
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during {sproc}: {error}")
            raise map_database_error(error)

    def _read_current(self, sproc: str, params: dict) -> Optional[Review]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name=sproc, params=params)
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during {sproc}: {error}")
            raise map_database_error(error)
