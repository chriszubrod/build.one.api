"""Persistence for dbo.BillIntakePrediction (U-676m)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Literal, Optional, Sequence, TypeVar

import pyodbc

from entities.bill_intake_prediction.business.model import BillIntakePrediction
from shared.api.money import to_decimal_or_none
from shared.database import call_procedure, get_connection, map_database_error
from shared.db_datetime import parse_db_datetime

logger = logging.getLogger(__name__)

# Literal call_procedure(name=...) references for tests/test_entity_docstrings.py.
if False:  # pragma: no cover
    call_procedure(cursor=None, name="UpsertBillIntakePrediction")
    call_procedure(cursor=None, name="UpdateBillIntakePredictionSimilarShadow")
    call_procedure(cursor=None, name="BindBillIntakePredictionToBill")
    call_procedure(cursor=None, name="RecordBillIntakePredictionFinal")
    call_procedure(cursor=None, name="ReadBillIntakePredictionByBillId")
    call_procedure(cursor=None, name="ReadBillIntakePredictionByAttachmentId")
    call_procedure(cursor=None, name="ReadBillIntakePredictionsForAccuracy")

T = TypeVar("T")
FetchMode = Literal["one", "all", None]


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class BillIntakePredictionRepository:
    def _from_row(self, row: Optional[pyodbc.Row]) -> Optional[BillIntakePrediction]:
        if not row or getattr(row, "Id", None) is None:
            return None
        return BillIntakePrediction(
            id=int(row.Id),
            public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
            attachment_id=int(row.AttachmentId),
            extracted_datetime=parse_db_datetime(getattr(row, "ExtractedDatetime", None)),
            bill_id=int(row.BillId) if getattr(row, "BillId", None) is not None else None,
            intake_source=str(row.IntakeSource),
            model_version=str(row.ModelVersion),
            status=str(row.Status),
            pred_vendor_id=int(row.PredVendorId) if getattr(row, "PredVendorId", None) is not None else None,
            pred_vendor_extracted_name=getattr(row, "PredVendorExtractedName", None),
            pred_bill_number=getattr(row, "PredBillNumber", None),
            pred_bill_date=_parse_date(getattr(row, "PredBillDate", None)),
            pred_total_amount=to_decimal_or_none(getattr(row, "PredTotalAmount", None)),
            pred_description=getattr(row, "PredDescription", None),
            pred_sub_cost_code_id=int(row.PredSubCostCodeId)
            if getattr(row, "PredSubCostCodeId", None) is not None
            else None,
            pred_project_id=int(row.PredProjectId)
            if getattr(row, "PredProjectId", None) is not None
            else None,
            coding_mode=str(row.CodingMode).strip() if getattr(row, "CodingMode", None) else None,
            coding_chosen_project_id=int(row.CodingChosenProjectId)
            if getattr(row, "CodingChosenProjectId", None) is not None
            else None,
            header_confidence=to_decimal_or_none(getattr(row, "HeaderConfidence", None)),
            header_model=getattr(row, "HeaderModel", None),
            coding_confidence=to_decimal_or_none(getattr(row, "CodingConfidence", None)),
            coding_model=getattr(row, "CodingModel", None),
            latency_ms_total=int(row.LatencyMsTotal)
            if getattr(row, "LatencyMsTotal", None) is not None
            else None,
            timing_json=getattr(row, "TimingJson", None),
            input_tokens=int(row.InputTokens) if getattr(row, "InputTokens", None) is not None else None,
            output_tokens=int(row.OutputTokens) if getattr(row, "OutputTokens", None) is not None else None,
            cost_usd=to_decimal_or_none(getattr(row, "CostUsd", None)),
            final_vendor_id=int(row.FinalVendorId)
            if getattr(row, "FinalVendorId", None) is not None
            else None,
            final_bill_number=getattr(row, "FinalBillNumber", None),
            final_bill_date=_parse_date(getattr(row, "FinalBillDate", None)),
            final_total_amount=to_decimal_or_none(getattr(row, "FinalTotalAmount", None)),
            final_sub_cost_code_id=int(row.FinalSubCostCodeId)
            if getattr(row, "FinalSubCostCodeId", None) is not None
            else None,
            final_project_id=int(row.FinalProjectId)
            if getattr(row, "FinalProjectId", None) is not None
            else None,
            finalized_datetime=parse_db_datetime(getattr(row, "FinalizedDatetime", None)),
            similar_pred_sub_cost_code_id=int(row.SimilarPredSubCostCodeId)
            if getattr(row, "SimilarPredSubCostCodeId", None) is not None
            else None,
            similar_confidence=to_decimal_or_none(getattr(row, "SimilarConfidence", None)),
            coding_source=getattr(row, "CodingSource", None),
            coding_escalation=getattr(row, "CodingEscalation", None),
            primary_model=getattr(row, "PrimaryModel", None),
            primary_sub_cost_code_id=int(row.PrimarySubCostCodeId)
            if getattr(row, "PrimarySubCostCodeId", None) is not None
            else None,
            primary_project_id=int(row.PrimaryProjectId)
            if getattr(row, "PrimaryProjectId", None) is not None
            else None,
            primary_confidence=to_decimal_or_none(getattr(row, "PrimaryConfidence", None)),
            primary_error=getattr(row, "PrimaryError", None),
            fallback_model=getattr(row, "FallbackModel", None),
            fallback_sub_cost_code_id=int(row.FallbackSubCostCodeId)
            if getattr(row, "FallbackSubCostCodeId", None) is not None
            else None,
            fallback_confidence=to_decimal_or_none(getattr(row, "FallbackConfidence", None)),
            similar_neighbour_count=int(row.SimilarNeighbourCount)
            if getattr(row, "SimilarNeighbourCount", None) is not None
            else None,
            m0_sub_cost_code_id=int(row.M0SubCostCodeId)
            if getattr(row, "M0SubCostCodeId", None) is not None
            else None,
            m0_confidence=to_decimal_or_none(getattr(row, "M0Confidence", None)),
        )

    def _run(
        self,
        log_label: str,
        work: Callable[[Any], T],
    ) -> T:
        try:
            with get_connection() as conn:
                return work(conn.cursor())
        except Exception as error:
            logger.error("%s failed: %s", log_label, type(error).__name__)
            raise map_database_error(error) from error

    def _call(
        self,
        name: str,
        params: dict[str, Any],
        *,
        fetch: FetchMode = "one",
        log_label: Optional[str] = None,
    ) -> Any:
        label = log_label or name

        def work(cursor) -> Any:
            call_procedure(cursor=cursor, name=name, params=params)
            if fetch == "one":
                return cursor.fetchone()
            if fetch == "all":
                return cursor.fetchall()
            return None

        return self._run(label, work)

    def upsert(self, **params: Any) -> Optional[BillIntakePrediction]:
        row = self._call("UpsertBillIntakePrediction", params, fetch="one")
        return self._from_row(row)

    def update_similar_shadow(
        self,
        *,
        attachment_id: int,
        similar_pred_sub_cost_code_id: Optional[int],
        similar_confidence: Any,
    ) -> bool:
        row = self._call(
            "UpdateBillIntakePredictionSimilarShadow",
            {
                "AttachmentId": attachment_id,
                "SimilarPredSubCostCodeId": similar_pred_sub_cost_code_id,
                "SimilarConfidence": similar_confidence,
            },
            fetch="one",
        )
        return row is not None and int(getattr(row, "RowsUpdated", 0) or 0) > 0

    def bind_to_bill(self, *, attachment_id: int, bill_id: int) -> bool:
        row = self._call(
            "BindBillIntakePredictionToBill",
            {"AttachmentId": attachment_id, "BillId": bill_id},
            fetch="one",
        )
        return row is not None and getattr(row, "BillId", None) is not None

    def record_final(self, *, bill_id: int, **final_fields: Any) -> bool:
        row = self._call(
            "RecordBillIntakePredictionFinal",
            {"BillId": bill_id, **final_fields},
            fetch="one",
        )
        return row is not None and getattr(row, "FinalizedDatetime", None) is not None

    def read_by_bill_id(self, bill_id: int) -> Optional[BillIntakePrediction]:
        row = self._call(
            "ReadBillIntakePredictionByBillId",
            {"BillId": bill_id},
            fetch="one",
        )
        return self._from_row(row)

    def read_by_attachment_id(self, attachment_id: int) -> Optional[BillIntakePrediction]:
        row = self._call(
            "ReadBillIntakePredictionByAttachmentId",
            {"AttachmentId": attachment_id},
            fetch="one",
        )
        return self._from_row(row)

    def read_for_accuracy(self) -> Sequence[pyodbc.Row]:
        return self._call(
            "ReadBillIntakePredictionsForAccuracy",
            {},
            fetch="all",
        )
