"""Persistence for dbo.ContractLaborCodingSuggestion (U-797a). Thin sproc wrappers only.

Logging names the exception class only; never note or reply text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from entities.contract_labor.business.coding_shadow import (
    CatalogEntry,
    PrecedentDay,
    ShadowInputs,
    rebuild_note_text,
)
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)

_COMPLETE_PARAMS = {
    "status": "Status",
    "decision_source": "DecisionSource",
    "sub_cost_code_id": "SubCostCodeId",
    "confidence": "Confidence",
    "may_split": "MaySplit",
    "model_proposal": "ModelProposal",
    "model_split_codes": "ModelSplitCodes",
    "history_top_sub_cost_code_id": "HistoryTopSubCostCodeId",
    "history_share": "HistoryShare",
    "history_prior_count": "HistoryPriorCount",
    "arm_a_code_count": "ArmACodeCount",
    "model": "Model",
    "failure_reason": "FailureReason",
}


@dataclass(frozen=True)
class ClaimedSuggestion:
    id: int
    contract_labor_id: int
    project_id: int
    work_date: date
    vendor_id: Optional[int]
    attempt_count: int = 0


def _decimal(value: Any) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None


class ContractLaborCodingSuggestionRepository:
    """Repository for the contract-labor coding shadow table."""

    def enqueue_for_contract_labor(self, contract_labor_id: int) -> int:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="EnqueueContractLaborCodingSuggestions",
                    params={"ContractLaborId": contract_labor_id},
                )
                row = cursor.fetchone()
                return int(row.InsertedCount) if row else 0
        except Exception as error:
            logger.error("EnqueueContractLaborCodingSuggestions failed: %s", type(error).__name__)
            raise map_database_error(error)

    def claim_next(self) -> Optional[ClaimedSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ClaimNextContractLaborCodingSuggestion", params={})
                row = cursor.fetchone()
                if not row or getattr(row, "Id", None) is None:
                    return None
                return ClaimedSuggestion(
                    id=int(row.Id),
                    contract_labor_id=int(row.ContractLaborId),
                    project_id=int(row.ProjectId),
                    work_date=row.WorkDate,
                    vendor_id=int(row.VendorId) if row.VendorId is not None else None,
                    attempt_count=int(row.AttemptCount or 0),
                )
        except Exception as error:
            logger.error("ClaimNextContractLaborCodingSuggestion failed: %s", type(error).__name__)
            raise map_database_error(error)

    def release(self, suggestion_id: int, count_attempt: bool = False) -> None:
        """Return the row to pending. count_attempt=True records a failed model attempt (AttemptCount +1)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReleaseContractLaborCodingSuggestion",
                    params={"Id": suggestion_id, "CountAttempt": 1 if count_attempt else 0},
                )
                cursor.fetchone()
        except Exception as error:
            logger.error("ReleaseContractLaborCodingSuggestion failed: %s", type(error).__name__)
            raise map_database_error(error)

    def read_inputs(self, contract_labor_id: int, project_id: int) -> Optional[ShadowInputs]:
        """Five result sets: header, day notes, precedent lines, precedent notes, catalog."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborCodingShadowInputs",
                    params={"ContractLaborId": contract_labor_id, "ProjectId": project_id},
                )
                header = cursor.fetchall()
                if not header:
                    return None
                cursor.nextset()
                day_rows = cursor.fetchall()
                cursor.nextset()
                line_rows = cursor.fetchall()
                cursor.nextset()
                note_rows = cursor.fetchall()
                cursor.nextset()
                catalog_rows = cursor.fetchall()
        except Exception as error:
            logger.error("ReadContractLaborCodingShadowInputs failed: %s", type(error).__name__)
            raise map_database_error(error)

        total = _decimal(header[0].TotalHours) or Decimal("0")
        day_note = rebuild_note_text((r.ClockIn, r.Note) for r in day_rows)

        notes_by_cl: dict[int, list[tuple[Any, Optional[str]]]] = {}
        for r in note_rows:
            notes_by_cl.setdefault(int(r.ContractLaborId), []).append((r.ClockIn, r.Note))

        lines_by_day: dict[tuple[int, date], list[tuple[int, Decimal]]] = {}
        for r in line_rows:
            key = (int(r.ContractLaborId), r.LineDate)
            lines_by_day.setdefault(key, []).append((int(r.SubCostCodeId), _decimal(r.Hours) or Decimal("0")))

        precedents = tuple(
            PrecedentDay(
                contract_labor_id=cl_id,
                line_date=line_date,
                lines=tuple(lines),
                note_text=rebuild_note_text(notes_by_cl.get(cl_id, [])),
            )
            for (cl_id, line_date), lines in sorted(lines_by_day.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        )
        catalog = tuple(
            CatalogEntry(id=int(r.Id), number=str(r.Number), name=str(r.Name)) for r in catalog_rows
        )
        return ShadowInputs(
            total_hours=total,
            day_note_text=day_note,
            precedent_days=precedents,
            catalog=catalog,
            own_status=getattr(header[0], "Status", None),
        )

    def read_idf_corpus(self) -> list[tuple[int, Any, Optional[str]]]:
        """(LineId, ClockIn, Note) rows for every labelled TimeLog-sourced line; Python rebuilds the documents."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadContractLaborCodingIdfCorpus", params={})
                rows = cursor.fetchall()
        except Exception as error:
            logger.error("ReadContractLaborCodingIdfCorpus failed: %s", type(error).__name__)
            raise map_database_error(error)
        return [(int(r.LineId), r.ClockIn, r.Note) for r in rows]

    def count_calls_today(self) -> int:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="CountContractLaborCodingCallsToday", params={})
                row = cursor.fetchone()
                return int(row.CallCount) if row else 0
        except Exception as error:
            logger.error("CountContractLaborCodingCallsToday failed: %s", type(error).__name__)
            raise map_database_error(error)

    def complete(self, suggestion_id: int, **fields: Any) -> None:
        params: dict[str, Any] = {"Id": suggestion_id}
        for key, column in _COMPLETE_PARAMS.items():
            params[column] = fields.get(key)
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="CompleteContractLaborCodingSuggestion", params=params)
                cursor.fetchone()
        except Exception as error:
            logger.error("CompleteContractLaborCodingSuggestion failed: %s", type(error).__name__)
            raise map_database_error(error)

    def read_scorecard_rows(self, since: Optional[date] = None) -> list[dict[str, Any]]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborCodingScorecardRows",
                    params={"Since": since},
                )
                rows = cursor.fetchall()
        except Exception as error:
            logger.error("ReadContractLaborCodingScorecardRows failed: %s", type(error).__name__)
            raise map_database_error(error)

        out: list[dict[str, Any]] = []
        for r in rows:
            raw = r.FinalCodeIds
            final = tuple(int(x) for x in str(raw).split(",") if x.strip()) if raw else None
            out.append(
                {
                    "work_date": r.WorkDate,
                    "decision_source": r.DecisionSource,
                    "sub_cost_code_id": int(r.SubCostCodeId) if r.SubCostCodeId is not None else None,
                    "confidence": _decimal(r.Confidence),
                    "may_split": bool(r.MaySplit) if r.MaySplit is not None else None,
                    "final_code_ids": final or None,
                    "failure_reason": r.FailureReason,
                }
            )
        return out
