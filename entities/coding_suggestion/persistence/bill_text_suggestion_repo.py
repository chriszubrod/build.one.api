"""Persistence for dbo.BillTextCodingSuggestion (U-654b)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

import pyodbc

from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


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
    ) -> Optional[pyodbc.Row]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CompleteBillTextCodingSuggestion",
                    params={
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
                    },
                )
                return cursor.fetchone()
        except Exception as error:
            logger.error(
                "CompleteBillTextCodingSuggestion failed bill=%s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)
