"""Persistence for U-716a expense receipt match + attach."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from entities.expense.persistence.repo import _bit
from shared.api.money import to_decimal_or_none
from shared.authz import current_is_system_admin, current_user_id
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _str_or_none(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


class ExpenseReceiptMatchRepository:
    def read_candidates(
        self,
        *,
        total_amount: Decimal,
        expense_date: Optional[date],
        vendor_id: Optional[int],
    ) -> list[dict[str, Any]]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseReceiptCandidates",
                    params={
                        "TotalAmount": total_amount,
                        "ExpenseDate": expense_date,
                        "VendorId": vendor_id,
                        "ActorUserId": current_user_id.get(),
                        "ActorIsSystemAdmin": _bit(current_is_system_admin.get()),
                    },
                )
                rows = cursor.fetchall()
                return [self._candidate_row(row) for row in rows]
        except Exception as error:
            logger.error(
                "ReadExpenseReceiptCandidates failed err=%s",
                type(error).__name__,
            )
            raise map_database_error(error)

    def attach_first_receipt(
        self,
        *,
        expense_public_id: UUID,
        attachment_id: int,
        created_by_user_id: Optional[int],
    ) -> dict[str, Any]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="AttachFirstReceiptToExpense",
                    params={
                        "ExpensePublicId": expense_public_id,
                        "AttachmentId": attachment_id,
                        "CreatedByUserId": created_by_user_id,
                        "ActorUserId": current_user_id.get(),
                        "ActorIsSystemAdmin": _bit(current_is_system_admin.get()),
                    },
                )
                row = cursor.fetchone()
                if not row:
                    return {"result_code": "not_found"}
                return {
                    "result_code": getattr(row, "ResultCode", None) or "not_found",
                    "expense_public_id": _str_or_none(getattr(row, "ExpensePublicId", None)),
                    "expense_line_item_public_id": _str_or_none(
                        getattr(row, "ExpenseLineItemPublicId", None)
                    ),
                    "attachment_public_id": _str_or_none(
                        getattr(row, "AttachmentPublicId", None)
                    ),
                }
        except Exception as error:
            logger.error(
                "AttachFirstReceiptToExpense failed err=%s",
                type(error).__name__,
            )
            raise map_database_error(error)

    @staticmethod
    def _candidate_row(row: Any) -> dict[str, Any]:
        total = to_decimal_or_none(getattr(row, "TotalAmount", None))
        exp_date = getattr(row, "ExpenseDate", None)
        if exp_date is not None and hasattr(exp_date, "isoformat"):
            exp_date_str = exp_date.isoformat()[:10]
        else:
            exp_date_str = str(exp_date)[:10] if exp_date else None
        return {
            "public_id": str(row.PublicId),
            "vendor_name": getattr(row, "VendorName", None),
            "expense_date": exp_date_str,
            "total_amount": total,
            "reference_number": getattr(row, "ReferenceNumber", None),
            "is_draft": bool(getattr(row, "IsDraft", False)),
            "status": getattr(row, "Status", None),
            "vendor_matches": bool(getattr(row, "VendorMatches", False)),
            "days_apart": int(getattr(row, "DaysApart", 0) or 0),
        }
