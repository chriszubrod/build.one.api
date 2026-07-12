# Python Standard Library Imports
import base64
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.bill.business.model import Bill
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


@contextmanager
def _conn_ctx(conn: Optional[pyodbc.Connection]):
    """Yield `conn` if provided (no lifecycle management); else open a fresh one."""
    if conn is not None:
        yield conn
    else:
        with get_connection() as c:
            yield c


class BillRepository:
    """
    Repository for Bill persistence operations.
    """

    def __init__(self):
        """Initialize the BillRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Bill]:
        """
        Convert a database row into a Bill dataclass.
        """
        if not row:
            return None

        try:
            return Bill(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=getattr(row, "VendorId", None),
                payment_term_id=getattr(row, "PaymentTermId", None),
                bill_date=getattr(row, "BillDate", None),
                due_date=getattr(row, "DueDate", None),
                bill_number=getattr(row, "BillNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
                intake_source=getattr(row, "IntakeSource", None),
                intake_source_detail=getattr(row, "IntakeSourceDetail", None),
                source_email_message_id=getattr(row, "SourceEmailMessageId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, vendor_id: Optional[int] = None, payment_term_id: Optional[int] = None, bill_date: Optional[str] = None, due_date: Optional[str] = None, bill_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True, intake_source: Optional[str] = None, intake_source_detail: Optional[str] = None, source_email_message_id: Optional[int] = None, created_by_user_id: Optional[int] = None) -> Bill:
        """
        Create a new bill.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            vendor_id: Vendor ID
            payment_term_id: Payment term ID
            bill_date: Bill date
            due_date: Due date
            bill_number: Bill number
            total_amount: Total amount
            memo: Memo
            is_draft: Whether bill is in draft state
            intake_source: How this bill arrived ('manual' | 'agent' | 'script'). Set-once.
            intake_source_detail: Specific actor — username, agent name, or script name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                call_procedure(
                    cursor=cursor,
                    name="CreateBill",
                    params={
                        "VendorId": vendor_id,
                        "PaymentTermId": payment_term_id,
                        "BillDate": bill_date,
                        "DueDate": due_date,
                        "BillNumber": bill_number,
                        "TotalAmount": Decimal(str(total_amount)) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                        "IntakeSource": intake_source,
                        "IntakeSourceDetail": intake_source_detail,
                        "SourceEmailMessageId": source_email_message_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateBill did not return a row.")
                    raise map_database_error(Exception("CreateBill failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create bill: {error}")
            raise map_database_error(error)

    def read_all(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[Bill]:
        """
        Read bills, scoped by UserProject membership for non-admin actors.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBills",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all bills: {error}")
            raise map_database_error(error)

    def read_first_line_item_projects(self, bill_ids: list[int], *, conn: Optional[pyodbc.Connection] = None) -> dict[int, Optional[int]]:
        """Return a mapping of BillId → ProjectId from the first line item of each bill."""
        if not bill_ids:
            return {}
        try:
            with _conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillFirstLineItemProjects",
                    params={"BillIds": ",".join(str(bid) for bid in bill_ids)},
                )
                rows = cursor.fetchall()
                return {row.BillId: row.ProjectId for row in rows}
        except Exception as error:
            logger.error(f"Error reading first line item projects: {error}")
            return {}

    def read_by_id(self, id: int) -> Optional[Bill]:
        """
        Read a bill by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Read a bill by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by public ID: {error}")
            raise map_database_error(error)

    def read_qbo_link_info(self, bill_id: int) -> Optional[dict]:
        """
        Read the Intuit (QboId, RealmId) for the bill's first QBO-synced
        line item — used by the service to build a deep link to the bill
        in the QuickBooks Online web app.

        Returns None when no line item is mapped to QBO yet (i.e. the
        bill has been drafted locally but not pushed). Powered by
        `dbo.ReadBillQboLinkInfo`.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillQboLinkInfo",
                    params={"BillId": bill_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                qbo_id = getattr(row, "QboId", None)
                realm_id = getattr(row, "QboRealmId", None)
                if not qbo_id or not realm_id:
                    return None
                return {"qbo_id": qbo_id, "qbo_realm_id": realm_id}
        except Exception as error:
            logger.error(f"Error during read bill QBO link info: {error}")
            raise map_database_error(error)

    def read_by_bill_number(self, bill_number: str) -> Optional[Bill]:
        """
        Read a bill by bill number.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByBillNumber",
                    params={"BillNumber": bill_number},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by bill number: {error}")
            raise map_database_error(error)

    def read_by_bill_number_and_vendor_id(self, bill_number: str, vendor_id: int, bill_date: str = None) -> Optional[Bill]:
        """
        Read a bill by bill number, vendor ID, and optionally bill date.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByBillNumberAndVendorId",
                    params={
                        "BillNumber": bill_number,
                        "VendorId": vendor_id,
                        "BillDate": bill_date,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by bill number and vendor ID: {error}")
            raise map_database_error(error)

    def read_slim_by_conversation_id(self, conversation_id: str) -> Optional[dict]:
        """Find the Bill linked to an email conversation. The PM's reply
        carries the same MS Graph ConversationId as the original vendor
        email; the agent uses this to identify which Bill it's reviewing.
        Returns None if no Bill is linked or the conversation is unknown.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByConversationId",
                    params={"ConversationId": conversation_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "id": row.Id,
                    "public_id": str(row.PublicId),
                    "bill_number": row.BillNumber,
                    "total_amount": float(row.TotalAmount) if row.TotalAmount is not None else None,
                    "is_draft": bool(row.IsDraft),
                    "created_datetime": row.CreatedDatetime,
                    "vendor_name": row.VendorName,
                    "source_email_message_id": row.SourceEmailMessageId,
                    "conversation_id": row.ConversationId,
                }
        except Exception as error:
            logger.error(f"Error reading slim Bill by conversation_id: {error}")
            raise map_database_error(error)

    def find_for_reviewer_reply(
        self,
        conversation_id: Optional[str] = None,
        bill_number_hint: Optional[str] = None,
        project_hint: Optional[str] = None,
    ) -> Optional[dict]:
        """Find the Bill that a PM/Owner reply is reviewing — strict
        ConversationId match first, with a fuzzy fallback on
        (BillNumber exact, Project name contains hint) when conversation_id
        misses AND both hints are supplied.

        Returns the slim dict shape used by `read_slim_by_conversation_id`
        plus a `match_kind` field (`'conversation'` | `'fuzzy'`) for
        telemetry. Returns None on 0 or 2+ fuzzy candidates — ambiguous
        cases stay in `flagged_needs_review`.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FindBillForReviewerReply",
                    params={
                        "ConversationId": conversation_id,
                        "BillNumberHint": bill_number_hint,
                        "ProjectHint": project_hint,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "id": row.Id,
                    "public_id": str(row.PublicId),
                    "bill_number": row.BillNumber,
                    "total_amount": float(row.TotalAmount) if row.TotalAmount is not None else None,
                    "is_draft": bool(row.IsDraft),
                    "created_datetime": row.CreatedDatetime,
                    "vendor_name": row.VendorName,
                    "source_email_message_id": row.SourceEmailMessageId,
                    "conversation_id": row.ConversationId,
                    "match_kind": row.MatchKind,
                }
        except Exception as error:
            logger.error(f"Error in find_for_reviewer_reply: {error}")
            raise map_database_error(error)

    def read_slim_by_source_email_message_id(self, source_email_message_id: int) -> Optional[dict]:
        """Slim Bill lookup keyed on SourceEmailMessageId. Returns a dict
        (not a Bill model) carrying just the fields the React Email-message
        detail view needs to render the "Linked Bill" panel — joined with
        Vendor for the denormalized vendor name. None when no Bill is
        linked. Returns at most one row (most recently created if
        somehow multiple exist).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillSlimBySourceEmailMessageId",
                    params={"SourceEmailMessageId": source_email_message_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "id": row.Id,
                    "public_id": str(row.PublicId),
                    "bill_number": row.BillNumber,
                    "total_amount": float(row.TotalAmount) if row.TotalAmount is not None else None,
                    "is_draft": bool(row.IsDraft),
                    "created_datetime": row.CreatedDatetime,
                    "vendor_name": row.VendorName,
                }
        except Exception as error:
            logger.error(f"Error reading slim Bill by source_email_message_id: {error}")
            raise map_database_error(error)

    def link_source_email_message(self, *, bill_id: int, source_email_message_id: int) -> bool:
        """Idempotent backfill of Bill.SourceEmailMessageId. Used by
        BillService.create() when a duplicate is detected: if the
        existing Bill came in via a non-email path (e.g. bill_folder)
        and has no source email linked, this stamps the link so the
        email-driven dedup audit trail is preserved.

        Returns True when the update happened, False when the existing
        Bill already had a source linked (or the Bill doesn't exist) —
        the underlying sproc filters on SourceEmailMessageId IS NULL,
        so it never overwrites a non-NULL link.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="LinkBillSourceEmailMessage",
                    params={
                        "Id": bill_id,
                        "SourceEmailMessageId": source_email_message_id,
                    },
                )
                row = cursor.fetchone()
                return row is not None
        except Exception as error:
            logger.error(f"Error linking Bill.SourceEmailMessageId: {error}")
            raise map_database_error(error)

    def update_by_id(self, bill: Bill) -> Optional[Bill]:
        """
        Update a bill by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": bill.id,
                    "RowVersion": bill.row_version_bytes,
                    "VendorId": bill.vendor_id,
                    "PaymentTermId": bill.payment_term_id,
                    "BillDate": bill.bill_date,
                    "DueDate": bill.due_date,
                    "BillNumber": bill.bill_number,
                    "TotalAmount": Decimal(str(bill.total_amount)) if bill.total_amount is not None else None,
                    "Memo": bill.memo,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if bill.is_draft is not None:
                    params["IsDraft"] = 1 if bill.is_draft else 0
                
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillById",
                    params=params,
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateBillById returned no row (id=%s); possible row-version conflict or record not found.",
                        bill.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the bill may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update bill by ID: {error}")
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
        sort_by: str = "BillDate",
        sort_direction: str = "DESC",
        conn: Optional[pyodbc.Connection] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[Bill]:
        """
        Read bills with pagination and filtering, scoped by UserProject.
        """
        try:
            with _conn_ctx(conn) as c:
                cursor = c.cursor()
                params = {
                    "PageNumber": page_number,
                    "PageSize": page_size,
                    "SearchTerm": search_term,
                    "VendorId": vendor_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "SortBy": sort_by,
                    "SortDirection": sort_direction,
                    "ActorUserId": actor_user_id,
                    "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                }
                call_procedure(
                    cursor=cursor,
                    name="ReadBillsPaginated",
                    params=params,
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read paginated bills: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        conn: Optional[pyodbc.Connection] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> int:
        """
        Count bills matching the filter criteria, scoped by UserProject.
        """
        try:
            with _conn_ctx(conn) as c:
                cursor = c.cursor()
                params = {
                    "SearchTerm": search_term,
                    "VendorId": vendor_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "ActorUserId": actor_user_id,
                    "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                }
                call_procedure(
                    cursor=cursor,
                    name="CountBills",
                    params=params,
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count bills: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Bill]:
        """
        Delete a bill by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteBillById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete bill by ID: {error}")
            raise map_database_error(error)

    def set_completion_result(self, public_id: str, result: dict[str, Any]) -> None:
        """Store completion result for a bill (permanent record)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertBillCompletionResult",
                    params={
                        "BillPublicId": public_id,
                        "ResultJson": json.dumps(result, default=str),
                    },
                )
        except Exception as error:
            logger.error(f"Error storing bill completion result: {error}")
            raise map_database_error(error)

    def get_completion_result(self, public_id: str) -> Optional[dict[str, Any]]:
        """Return completion result for a bill."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="GetBillCompletionResult",
                    params={"BillPublicId": public_id},
                )
                row = cursor.fetchone()
                if not row or not getattr(row, "ResultJson", None):
                    return None
                return json.loads(row.ResultJson)
        except Exception as error:
            logger.error(f"Error reading bill completion result: {error}")
            raise map_database_error(error)


def _bit(flag):
    """SQL Server BIT params take 0/1, not Python bool."""
    if flag is None:
        return None
    return 1 if flag else 0

