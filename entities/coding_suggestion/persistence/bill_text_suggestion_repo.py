"""Persistence for dbo.BillTextCodingSuggestion (U-654b)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

import pyodbc

from shared.database import call_procedure, get_connection, map_database_error
from shared.db_datetime import parse_db_datetime as _parse_datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BillTextCodingSuggestionRow:
    bill_id: int
    attachment_id: int
    extracted_datetime: datetime
    status: str
    sub_cost_code_id: Optional[int]
    project_id: Optional[int]
    confidence: Optional[Decimal]
    mode: Optional[str]
    winning_model: Optional[str]
    model_version: str
    failure_reason: Optional[str]
    # U-701b3 per-rung columns; None on rows written before they existed.
    decision_source: Optional[str] = None
    primary_sub_cost_code_id: Optional[int] = None
    primary_confidence: Optional[Decimal] = None
    escalate_sub_cost_code_id: Optional[int] = None
    escalate_confidence: Optional[Decimal] = None
    escalate_error: Optional[str] = None
    # U-745 M0 shadow pick; None on rows written before it existed.
    m0_sub_cost_code_id: Optional[int] = None
    m0_confidence: Optional[Decimal] = None


@dataclass(frozen=True)
class BillCodingActor:
    """The bill's creator, company and total (V2 tick similar-bills scope)."""

    created_by_user_id: Optional[int]
    is_system_admin: bool
    company_id: Optional[int]
    total_amount: Optional[Decimal]


def _opt_int(row: Any, column: str) -> Optional[int]:
    value = getattr(row, column, None)
    return int(value) if value is not None else None


def _opt_decimal(row: Any, column: str) -> Optional[Decimal]:
    value = getattr(row, column, None)
    return Decimal(str(value)) if value is not None else None


def _opt_str(row: Any, column: str) -> Optional[str]:
    value = getattr(row, column, None)
    return str(value) if value is not None else None


@dataclass(frozen=True)
class BillLatestTextExtractionState:
    latest_completed_attachment_id: Optional[int]
    latest_completed_extracted_datetime: Optional[datetime]
    has_incomplete_extraction: bool


@dataclass(frozen=True)
class BillTextCodingClaim:
    bill_id: int
    attachment_id: int
    extracted_text_blob_url: str
    extracted_datetime: datetime
    vendor_id: int
    chosen_project_id: Optional[int]
    bill_date: Optional[datetime]
    created_datetime: datetime


class BillTextCodingSuggestionRepository:
    def count_calls_today(self) -> int:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="CountBillTextCodingCallsToday", params={})
                row = cursor.fetchone()
                return int(row.CallCount) if row else 0
        except Exception as error:
            logger.error("CountBillTextCodingCallsToday failed: %s", type(error).__name__)
            raise map_database_error(error)

    def claim_next(self) -> Optional[BillTextCodingClaim]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClaimNextBillForTextCodingSuggestion",
                    params={},
                )
                row = cursor.fetchone()
                if not row or getattr(row, "BillId", None) is None:
                    return None
                extracted = _parse_datetime(row.ExtractedDatetime)
                created = _parse_datetime(row.CreatedDatetime)
                bill_date = _parse_datetime(getattr(row, "BillDate", None))
                if extracted is None or created is None:
                    logger.error(
                        "ClaimNextBillForTextCodingSuggestion bad datetimes bill=%s",
                        getattr(row, "BillId", None),
                    )
                    return None
                return BillTextCodingClaim(
                    bill_id=int(row.BillId),
                    attachment_id=int(row.AttachmentId),
                    extracted_text_blob_url=str(row.ExtractedTextBlobUrl),
                    extracted_datetime=extracted,
                    vendor_id=int(row.VendorId),
                    chosen_project_id=(
                        int(row.ChosenProjectId)
                        if getattr(row, "ChosenProjectId", None) is not None
                        else None
                    ),
                    bill_date=bill_date,
                    created_datetime=created,
                )
        except Exception as error:
            logger.error("ClaimNextBillForTextCodingSuggestion failed: %s", type(error).__name__)
            raise map_database_error(error)

    def read_by_bill_id(self, bill_id: int) -> Optional[BillTextCodingSuggestionRow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillTextCodingSuggestionByBillId",
                    params={"BillId": bill_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                extracted = _parse_datetime(row.ExtractedDatetime)
                if extracted is None:
                    logger.error(
                        "ReadBillTextCodingSuggestionByBillId bad ExtractedDatetime bill=%s",
                        bill_id,
                    )
                    return None
                return BillTextCodingSuggestionRow(
                    bill_id=int(row.BillId),
                    attachment_id=int(row.AttachmentId),
                    extracted_datetime=extracted,
                    status=str(row.Status),
                    sub_cost_code_id=(
                        int(row.SubCostCodeId)
                        if getattr(row, "SubCostCodeId", None) is not None
                        else None
                    ),
                    project_id=(
                        int(row.ProjectId)
                        if getattr(row, "ProjectId", None) is not None
                        else None
                    ),
                    confidence=(
                        Decimal(str(row.Confidence))
                        if getattr(row, "Confidence", None) is not None
                        else None
                    ),
                    mode=(
                        str(row.Mode).strip()
                        if getattr(row, "Mode", None) is not None
                        else None
                    ),
                    winning_model=(
                        str(row.WinningModel)
                        if getattr(row, "WinningModel", None) is not None
                        else None
                    ),
                    model_version=str(row.ModelVersion),
                    failure_reason=(
                        str(row.FailureReason)
                        if getattr(row, "FailureReason", None) is not None
                        else None
                    ),
                    decision_source=_opt_str(row, "DecisionSource"),
                    primary_sub_cost_code_id=_opt_int(row, "PrimarySubCostCodeId"),
                    primary_confidence=_opt_decimal(row, "PrimaryConfidence"),
                    escalate_sub_cost_code_id=_opt_int(row, "EscalateSubCostCodeId"),
                    escalate_confidence=_opt_decimal(row, "EscalateConfidence"),
                    escalate_error=_opt_str(row, "EscalateError"),
                    m0_sub_cost_code_id=_opt_int(row, "M0SubCostCodeId"),
                    m0_confidence=_opt_decimal(row, "M0Confidence"),
                )
        except Exception as error:
            logger.error(
                "ReadBillTextCodingSuggestionByBillId failed bill=%s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def read_latest_text_extraction_state(
        self, bill_id: int
    ) -> BillLatestTextExtractionState:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillLatestTextExtractionForCoding",
                    params={"BillId": bill_id},
                )
                row = cursor.fetchone()
                if not row:
                    return BillLatestTextExtractionState(None, None, False)
                latest_att = getattr(row, "LatestCompletedAttachmentId", None)
                latest = _parse_datetime(
                    getattr(row, "LatestCompletedExtractedDatetime", None)
                )
                incomplete = bool(getattr(row, "HasIncompleteExtraction", 0))
                return BillLatestTextExtractionState(
                    int(latest_att) if latest_att is not None else None,
                    latest,
                    incomplete,
                )
        except Exception as error:
            logger.error(
                "ReadBillLatestTextExtractionForCoding failed bill=%s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def read_vendor_scc_counts(
        self,
        vendor_id: int,
        *,
        project_id: Optional[int] = None,
        as_of: Optional[datetime] = None,
        top: int = 5,
        exclude_bill_id: Optional[int] = None,
    ) -> Sequence[pyodbc.Row]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorSccCountsForBills",
                    params={
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "AsOf": as_of,
                        "Top": top,
                        "ExcludeBillId": exclude_bill_id,
                    },
                )
                return cursor.fetchall()
        except Exception as error:
            logger.error(
                "ReadVendorSccCountsForBills failed vendor=%s: %s",
                vendor_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def read_bill_coding_actor(self, bill_id: int) -> Optional[BillCodingActor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCodingActorByBillId",
                    params={"BillId": bill_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return BillCodingActor(
                    created_by_user_id=_opt_int(row, "CreatedByUserId"),
                    is_system_admin=bool(getattr(row, "IsSystemAdmin", 0)),
                    company_id=_opt_int(row, "CompanyId"),
                    total_amount=_opt_decimal(row, "TotalAmount"),
                )
        except Exception as error:
            logger.error(
                "ReadBillCodingActorByBillId failed bill=%s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def complete(
        self,
        *,
        bill_id: int,
        attachment_id: int,
        extracted_datetime: datetime,
        status: str,
        model_version: str,
        sub_cost_code_id: Optional[int] = None,
        project_id: Optional[int] = None,
        confidence: Optional[Decimal] = None,
        mode: Optional[str] = None,
        winning_model: Optional[str] = None,
        failure_reason: Optional[str] = None,
        record_call: bool = False,
        **extra: Any,
    ) -> Optional[pyodbc.Row]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params: dict[str, Any] = {
                    "BillId": bill_id,
                    "AttachmentId": attachment_id,
                    "ExtractedDatetime": extracted_datetime,
                    "Status": status,
                    "SubCostCodeId": sub_cost_code_id,
                    "ProjectId": project_id,
                    "Confidence": confidence,
                    "Mode": mode,
                    "WinningModel": winning_model,
                    "ModelVersion": model_version,
                    "FailureReason": failure_reason,
                    "RecordCall": 1 if record_call else 0,
                }
                params.update(extra)
                call_procedure(
                    cursor=cursor,
                    name="CompleteBillTextCodingSuggestion",
                    params=params,
                )
                return cursor.fetchone()
        except Exception as error:
            logger.error(
                "CompleteBillTextCodingSuggestion failed bill=%s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)
