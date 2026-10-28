"""Domain model for dbo.BillIntakePrediction (U-676m)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class BillIntakePrediction:
    id: Optional[int]
    public_id: Optional[str]
    attachment_id: int
    extracted_datetime: Optional[datetime]
    bill_id: Optional[int]
    intake_source: str
    model_version: str
    status: str
    pred_vendor_id: Optional[int]
    pred_vendor_extracted_name: Optional[str]
    pred_bill_number: Optional[str]
    pred_bill_date: Optional[date]
    pred_total_amount: Optional[Decimal]
    pred_description: Optional[str]
    pred_sub_cost_code_id: Optional[int]
    pred_project_id: Optional[int]
    coding_mode: Optional[str]
    coding_chosen_project_id: Optional[int]
    header_confidence: Optional[Decimal]
    header_model: Optional[str]
    coding_confidence: Optional[Decimal]
    coding_model: Optional[str]
    latency_ms_total: Optional[int]
    timing_json: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cost_usd: Optional[Decimal]
    final_vendor_id: Optional[int] = None
    final_bill_number: Optional[str] = None
    final_bill_date: Optional[date] = None
    final_total_amount: Optional[Decimal] = None
    final_sub_cost_code_id: Optional[int] = None
    final_project_id: Optional[int] = None
    finalized_datetime: Optional[datetime] = None
    similar_pred_sub_cost_code_id: Optional[int] = None
    similar_confidence: Optional[Decimal] = None
    coding_source: Optional[str] = None
    coding_escalation: Optional[str] = None
    primary_model: Optional[str] = None
    primary_sub_cost_code_id: Optional[int] = None
    primary_project_id: Optional[int] = None
    primary_confidence: Optional[Decimal] = None
    primary_error: Optional[str] = None
    fallback_model: Optional[str] = None
    fallback_sub_cost_code_id: Optional[int] = None
    fallback_confidence: Optional[Decimal] = None
    similar_neighbour_count: Optional[int] = None
