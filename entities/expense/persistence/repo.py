# Python Standard Library Imports
import base64
import logging
from types import SimpleNamespace
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.expense.business.model import Expense
from shared.database import (
    call_procedure,
    conn_ctx,
    get_connection,
    map_database_error,
)
from shared.lifecycle.terminal_lock import reraise_if_sproc_status_locked

logger = logging.getLogger(__name__)


def _bit(flag):
    """SQL Server BIT params take 0/1, not Python bool."""
    if flag is None:
        return None
    return 1 if flag else 0


class ExpenseRepository:
    """
    Repository for Expense persistence operations.
    """

    def __init__(self):
        """Initialize the ExpenseRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Expense]:
        """
        Convert a database row into an Expense dataclass.
        """
        if not row:
            return None

        try:
            return Expense(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=getattr(row, "VendorId", None),
                expense_date=getattr(row, "ExpenseDate", None),
                reference_number=getattr(row, "ReferenceNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
                is_credit=bool(getattr(row, "IsCredit", False)) if getattr(row, "IsCredit", None) is not None else None,
                status=getattr(row, "Status", None),
                status_datetime=getattr(row, "StatusDatetime", None),
                status_origin=getattr(row, "StatusOrigin", None),
                status_source_ref=getattr(row, "StatusSourceRef", None),
                # getattr-guarded: read sprocs that don't yet project this
                # column simply yield None (no AttributeError).
                source_email_message_id=getattr(row, "SourceEmailMessageId", None),
                qbo_id=getattr(row, "QboId", None),
                realm_id=getattr(row, "RealmId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during expense mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during expense mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, vendor_id: Optional[int] = None, expense_date: Optional[str] = None, reference_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True, is_credit: bool = False, source_email_message_id: Optional[int] = None, created_by_user_id: Optional[int] = None, status: Optional[str] = None, status_origin: Optional[str] = None, status_source_ref: Optional[str] = None) -> Expense:
        """
        Create a new expense.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            vendor_id: Vendor ID
            expense_date: Expense date
            reference_number: Reference number
            total_amount: Total amount
            memo: Memo
            is_draft: Whether expense is in draft state
            source_email_message_id: dbo.EmailMessage.Id when this expense was
                created from a receipt email (receipt-intake pipeline); NULL otherwise.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateExpense",
                    params={
                        "VendorId": vendor_id,
                        "ExpenseDate": expense_date,
                        "ReferenceNumber": reference_number,
                        "TotalAmount": Decimal(str(total_amount)) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                        "IsCredit": 1 if is_credit else 0,
                        "SourceEmailMessageId": source_email_message_id,
                        "CreatedByUserId": created_by_user_id,
                        "Status": status,
                        "StatusOrigin": status_origin,
                        "StatusSourceRef": status_source_ref,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateExpense did not return a row.")
                    raise map_database_error(Exception("CreateExpense failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create expense: {error}")
            raise map_database_error(error)

    def read_all(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[Expense]:
        """Read expenses, scoped by UserProject for non-admin actors."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenses",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all expenses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Expense]:
        """
        Read an expense by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Expense]:
        """
        Read an expense by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by public ID: {error}")
            raise map_database_error(error)

    def read_by_reference_number_and_vendor_id(self, reference_number: str, vendor_id: int) -> Optional[Expense]:
        """
        Read an expense by reference number and vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseByReferenceNumberAndVendorId",
                    params={
                        "ReferenceNumber": reference_number,
                        "VendorId": vendor_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by reference number and vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, expense: Expense, *, allow_terminal_parent: bool = True) -> Optional[Expense]:
        """
        Update an expense by ID.

        `allow_terminal_parent` is the sproc-side half of the U-468 terminal
        lock. Always pass it explicitly. The REPO default is True, so an
        omitted kwarg sends 1 and bypasses both the Python guard and the
        sproc's fail-closed default. The sproc itself defaults to 0; the trap
        is this Python default, not the sproc's.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": expense.id,
                    "RowVersion": expense.row_version_bytes,
                    "VendorId": expense.vendor_id,
                    "ExpenseDate": expense.expense_date,
                    "ReferenceNumber": expense.reference_number,
                    "TotalAmount": Decimal(str(expense.total_amount)) if expense.total_amount is not None else None,
                    "Memo": expense.memo,
                }
                params["AllowTerminalParent"] = 1 if allow_terminal_parent else 0
                # Only include IsDraft/IsCredit if explicitly set (not None) — sproc uses CASE WHEN guard
                if expense.is_draft is not None:
                    params["IsDraft"] = 1 if expense.is_draft else 0
                if expense.is_credit is not None:
                    params["IsCredit"] = 1 if expense.is_credit else 0
                
                call_procedure(
                    cursor=cursor,
                    name="UpdateExpenseById",
                    params=params,
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateExpenseById returned no row (id=%s); possible row-version conflict or record not found.",
                        expense.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the expense may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            reraise_if_sproc_status_locked(error, what="its header cannot be changed")
            logger.error(f"Error during update expense by ID: {error}")
            raise map_database_error(error)

    def finalize_by_id(self, id: int) -> Optional[Expense]:
        """Idempotently flip Status to completed. The only sanctioned finalize path.

        Returns the Expense when it exists — whether this call flipped it or it
        was already finalized — and None when no such Expense exists. That
        asymmetry is the point: `FinalizeExpenseById`'s UPDATE matches 0 rows
        in BOTH the already-finalized and the missing case, so the sproc
        re-SELECTs unconditionally and the caller reads presence, not rowcount.

        Unlike `update_by_id` this carries NO RowVersion and does NOT raise on a
        no-op. Finalization is a state transition; an unrelated concurrent field
        edit must not be able to fail it. See the sproc's header (U-467).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FinalizeExpenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during finalize expense by ID {id}: {error}")
            raise map_database_error(error)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        status: Optional[str] = None,
        sort_by: str = "ExpenseDate",
        sort_direction: str = "DESC",
        conn: Optional[pyodbc.Connection] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> tuple[list[Expense], int]:
        """Read one page of expenses AND the matching total, scoped by UserProject.

        Returns `(rows, total)` (U-447, ported U-467). The total used to come
        from a second sproc on a second round trip, which put it in a different
        snapshot from the page. Both now come from one execution over one
        materialized set.
        """
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                params = {
                    "PageNumber": page_number,
                    "PageSize": page_size,
                    "SearchTerm": search_term,
                    "VendorId": vendor_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "Status": status,
                    "SortBy": sort_by,
                    "SortDirection": sort_direction,
                    "ActorUserId": actor_user_id,
                    "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                }
                call_procedure(
                    cursor=cursor,
                    name="ReadExpensesPaginated",
                    params=params,
                )
                rows = cursor.fetchall()
                expenses = [self._from_db(row) for row in rows if row]
                # Second result set: the total over the SAME materialized set.
                #
                # RAISES if it is absent (Codex P1). The obvious fallback —
                # `total = len(expenses)` — is silently wrong in exactly the
                # case that matters: page 2 of 120 matches would report
                # `count: 50`, and an out-of-range page `count: 0`, capping
                # every client's pagination at one page with nothing in the logs.
                if not cursor.nextset():
                    raise RuntimeError(
                        "ReadExpensesPaginated returned no total — the deployed sproc "
                        "predates U-447. Apply entities/expense/sql/dbo.expense.sql."
                    )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("ReadExpensesPaginated returned an empty total result set.")
                return expenses, int(row[0])
        except Exception as error:
            logger.error(f"Error during read paginated expenses: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        status: Optional[str] = None,
        conn: Optional[pyodbc.Connection] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> int:
        """Count expenses matching filter criteria, scoped by UserProject."""
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                params = {
                    "SearchTerm": search_term,
                    "VendorId": vendor_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "Status": status,
                    "ActorUserId": actor_user_id,
                    "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                }
                call_procedure(
                    cursor=cursor,
                    name="CountExpenses",
                    params=params,
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count expenses: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int, *, allow_terminal_parent: bool = True) -> Optional[Expense]:
        """
        Delete an expense by ID.

        See `update_by_id` for why the flag is always passed explicitly.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteExpenseById",
                    params={
                        "Id": id,
                        "AllowTerminalParent": 1 if allow_terminal_parent else 0,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            reraise_if_sproc_status_locked(error, what="it cannot be deleted")
            logger.error(f"Error during delete expense by ID: {error}")
            raise map_database_error(error)

    def delete_cascade_by_id(self, id: int, *, allow_terminal_parent: bool) -> Optional[Expense]:
        """U-468: delete an Expense and every row that FKs to it, in ONE transaction.

        Replaces the multi-transaction Python cascade. The sproc locks the
        header for the whole thing, so a completion landing mid-cascade either
        loses the race entirely or is refused with nothing destroyed — where the
        old shape refused at whichever step it reached and left the earlier ones
        committed. An invoiced citation is refused the same way, inside the
        lock, rather than by a lock-free pre-check on its own connection.

        Returns the deleted Expense, or None when no such Expense exists.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteExpenseCascadeById",
                    params={
                        "Id": id,
                        "AllowTerminalParent": 1 if allow_terminal_parent else 0,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            reraise_if_sproc_status_locked(error, what="it cannot be deleted")
            text = str(error)
            marker = "Cannot delete this expense:"
            idx = text.find(marker)
            if idx != -1:
                msg = text[idx:]
                cut = msg.find(" (")
                if cut != -1:
                    msg = msg[:cut]
                raise ValueError(msg) from error
            logger.error(f"Error during cascade delete of expense: {error}")
            raise map_database_error(error)

    def read_by_qbo_identity(
        self,
        qbo_id: str,
        realm_id: Optional[str] = None,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> Optional[Expense]:
        """
        Read an expense directly by its dbo-native QBO identity (U-283b),
        bypassing the qbo.Purchase / qbo.PurchaseExpense staging/mapping
        tables entirely. RBAC-scoped like every other Expense read.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseByQboIdAndRealmId",
                    params={
                        "QboId": qbo_id,
                        "RealmId": realm_id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by QBO identity: {error}")
            raise map_database_error(error)

    def read_qbo_identity_rows_by_realm_id(
        self,
        realm_id: str,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list:
        """
        Bulk dbo-native identity read (U-298, [Id] added U-301a): every
        (Id, QboId) pair already stamped on dbo.Expense for a realm, as
        SimpleNamespace(id=..., qbo_id=...) rows. RBAC-scoped like every
        other Expense read. Feeds both read_qbo_ids_by_realm_id (bare set,
        below) and the reconciliation void detector's local_rows contract,
        which needs a real per-row identity, not just a set membership test.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseQboIdsByRealmId",
                    params={
                        "RealmId": realm_id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                rows = cursor.fetchall()
                return [
                    SimpleNamespace(id=row.Id, qbo_id=row.QboId)
                    for row in rows
                    if row and row.QboId
                ]
        except Exception as error:
            logger.error(f"Error during read expense qbo identity rows by realm ID: {error}")
            raise map_database_error(error)

    def read_qbo_ids_by_realm_id(
        self,
        realm_id: str,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> set:
        """
        Bulk dbo-native identity read (U-298): the set of QboIds already stamped
        on dbo.Expense for a realm. RBAC-scoped like every other Expense read.
        """
        return {
            row.qbo_id
            for row in self.read_qbo_identity_rows_by_realm_id(
                realm_id,
                actor_user_id=actor_user_id,
                actor_is_system_admin=actor_is_system_admin,
            )
        }

    def set_qbo_identity(
        self,
        *,
        id: int,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        sync_token: Optional[str] = None,
    ) -> None:
        """Stamp dbo-native QBO identity columns (idempotent-safe via CASE WHEN sproc)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetExpenseQboIdentity",
                    params={
                        "Id": id,
                        "QboId": qbo_id,
                        "RealmId": realm_id,
                        "SyncToken": sync_token,
                    },
                )
                row = cursor.fetchone()
                if row and getattr(row, "Stolen", False):
                    logger.warning(
                        "Expense %s stole QBO identity (qbo_id=%s realm_id=%s) from a different "
                        "Expense row — a stale duplicate identity existed before this stamp",
                        id, qbo_id, realm_id,
                    )
        except Exception as error:
            logger.error(
                "Error stamping Expense QBO identity (expense_id=%s qbo_id=%s realm_id=%s): %s",
                id,
                qbo_id,
                realm_id,
                error,
            )
            raise map_database_error(error)

    def read_uncoded_completed_candidates(self) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUncodedCompletedExpenseCandidates",
                    params={},
                )
                return [
                    {
                        "id": getattr(row, "Id", None),
                        "public_id": str(row.PublicId) if getattr(row, "PublicId", None) else None,
                        "status": getattr(row, "Status", None),
                        "expense_date": getattr(row, "ExpenseDate", None),
                        "total_amount": getattr(row, "TotalAmount", None),
                        "reference_number": getattr(row, "ReferenceNumber", None),
                        "qbo_purchase_line_id": getattr(row, "QboPurchaseLineId", None),
                        "coding_item_public_id": (
                            str(row.CodingItemPublicId)
                            if getattr(row, "CodingItemPublicId", None)
                            else None
                        ),
                    }
                    for row in cursor.fetchall()
                ]
        except Exception as error:
            logger.error("Error reading uncoded completed expense candidates: %s", error)
            raise map_database_error(error)

    def mark_draft_for_coding(
        self,
        expense_id: int,
        *,
        status_source_ref: Optional[str] = None,
    ) -> Optional[Expense]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkExpenseDraftForCoding",
                    params={
                        "ExpenseId": expense_id,
                        "StatusSourceRef": status_source_ref,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error marking expense %s draft for coding: %s",
                expense_id,
                error,
            )
            raise map_database_error(error)

