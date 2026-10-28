"""Entity contract — BillIntakePrediction.   (maintained with the code; checked by tests/test_entity_docstrings.py)

Owns
  dbo.BillIntakePrediction — AI read snapshot on bill-create attachment read, bill bind, and complete final.
Invariants
  One row per AttachmentId (re-read overwrites only while BillId and FinalizedDatetime are null).
  BillId set once via bind. Final* written once on complete.
  No FK constraints on the table (telemetry): orphan rows after parent delete are acceptable.
  PredDescription and PredVendorExtractedName hold extracted text from the PDF; no full bill text or TIN.
External writes
  None (read path upsert is failure-isolated in read_attachment_service; bind/final are failure-isolated in BillService).
Sprocs
  BindBillIntakePredictionToBill
  ReadBillIntakePredictionByAttachmentId
  ReadBillIntakePredictionByBillId
  ReadBillIntakePredictionsForAccuracy
  RecordBillIntakePredictionFinal
  UpdateBillIntakePredictionSimilarShadow
  UpsertBillIntakePrediction
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, Sequence, get_args

from entities.bill.business.model import Bill
from entities.bill_intake_prediction.business.model import BillIntakePrediction
from entities.bill_intake_prediction.persistence.repo import BillIntakePredictionRepository
from entities.bill_line_item.business.model import BillLineItem
from entities.bill_line_item_attachment.business.model import BillLineItemAttachment
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.business.bill_text_suggestion_service import (
    MODEL_VERSION,
    MODEL_VERSION_V2,
)
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.vendor.business.service import VendorService
from intelligence.cascade.core import CascadeResult
from intelligence.observability.cascade_telemetry import cascade_usage_totals
from shared.authz import current_user_id

if TYPE_CHECKING:
    from entities.coding_suggestion.business.bill_coding_decision import BillCodingDecision

logger = logging.getLogger(__name__)

IntakeSource = Literal["pdf_drop", "pdf_upload", "list_drop", "reread"]
INTAKE_SOURCES = frozenset(get_args(IntakeSource))

TEXT_MODEL_VERSION = MODEL_VERSION


def sum_cascade_cost_and_tokens(
    *cascades: Optional[CascadeResult],
) -> tuple[int, int, Optional[Decimal]]:
    """Sum input/output tokens and USD cost across cascades (None if any attempt price unknown)."""
    input_tokens = 0
    output_tokens = 0
    total_cost: Optional[Decimal] = Decimal("0")
    for cascade in cascades:
        if cascade is None:
            continue
        inp, out, cost = cascade_usage_totals(cascade)
        input_tokens += inp
        output_tokens += out
        if cost is None:
            total_cost = None
        elif total_cost is not None:
            total_cost += cost
    return input_tokens, output_tokens, total_cost


def text_model_version_compatible(
    stored: str,
    *,
    ladder_v2_enabled: bool = False,
) -> bool:
    if stored == MODEL_VERSION or stored == TEXT_MODEL_VERSION:
        return True
    if ladder_v2_enabled and stored == MODEL_VERSION_V2:
        return True
    return False


def _intake_reuse_checks_pass(
    prediction: BillIntakePrediction,
    *,
    extracted_datetime: datetime,
    chosen_project_id: Optional[int],
    vendor_id: int,
) -> bool:
    if prediction.status not in ("ok", "partial"):
        return False
    if prediction.pred_sub_cost_code_id is None:
        return False
    if prediction.pred_vendor_id != vendor_id:
        return False
    if prediction.extracted_datetime != extracted_datetime:
        return False
    tick_mode = "A" if chosen_project_id is not None else "B"
    if (prediction.coding_mode or "").upper() != tick_mode:
        return False
    if tick_mode == "A":
        if prediction.coding_chosen_project_id != chosen_project_id:
            return False
    else:
        if prediction.coding_chosen_project_id is not None:
            return False
        if prediction.pred_project_id is None:
            return False
    return True


def tick_can_reuse_prediction(
    prediction: BillIntakePrediction,
    *,
    extracted_datetime: datetime,
    chosen_project_id: Optional[int],
    vendor_id: int,
) -> bool:
    if not text_model_version_compatible(prediction.model_version):
        return False
    return _intake_reuse_checks_pass(
        prediction,
        extracted_datetime=extracted_datetime,
        chosen_project_id=chosen_project_id,
        vendor_id=vendor_id,
    )


def tick_decision_for_intake(
    prediction: BillIntakePrediction,
    *,
    extracted_datetime: datetime,
    chosen_project_id: Optional[int],
    vendor_id: int,
    ladder_v2_enabled: bool,
) -> Literal["reuse", "escalate", "full"]:
    if not _intake_reuse_checks_pass(
        prediction,
        extracted_datetime=extracted_datetime,
        chosen_project_id=chosen_project_id,
        vendor_id=vendor_id,
    ):
        return "full"
    if not text_model_version_compatible(
        prediction.model_version,
        ladder_v2_enabled=ladder_v2_enabled,
    ):
        return "full"
    if not ladder_v2_enabled:
        return "reuse"
    if prediction.model_version != MODEL_VERSION_V2:
        # A legacy (u654b-text-v1) intake row has no per-rung answer: run the V2 ladder.
        return "full"
    if (
        prediction.coding_escalation == "low_confidence"
        and prediction.primary_sub_cost_code_id is not None
    ):
        return "escalate"
    if prediction.coding_escalation is None:
        return "reuse"
    return "full"


def _intake_decision_params(decision: "BillCodingDecision") -> dict[str, Any]:
    """Per-rung intake columns from a V2 decision (U-701b3); the similar columns hold
    the decision's FILTERED vote, never the raw one."""
    primary, fallback = decision.primary, decision.fallback
    return {
        "CodingSource": decision.source,
        "CodingEscalation": decision.escalation,
        "PrimaryModel": primary.model if primary else None,
        "PrimarySubCostCodeId": primary.sub_cost_code_id if primary else None,
        "PrimaryProjectId": primary.project_id if primary else None,
        "PrimaryConfidence": primary.confidence if primary else None,
        "PrimaryError": primary.error if primary else None,
        "FallbackModel": fallback.model if fallback else None,
        "FallbackSubCostCodeId": fallback.sub_cost_code_id if fallback else None,
        "FallbackConfidence": fallback.confidence if fallback else None,
        "SimilarPredSubCostCodeId": decision.similar_vote_scc_id,
        "SimilarConfidence": decision.similar_vote_confidence,
        "SimilarNeighbourCount": decision.similar_neighbour_count,
    }


class BillIntakePredictionService:
    def __init__(self, repo: Optional[BillIntakePredictionRepository] = None):
        self.repo = repo or BillIntakePredictionRepository()

    @staticmethod
    def _shadow_similar_prediction(
        *,
        attachment_id: Optional[int] = None,
        shadow_extraction_content: Optional[str] = None,
        shadow_vendor_id: Optional[int] = None,
        shadow_amount: Any = None,
        shadow_as_of: Optional[datetime] = None,
    ) -> tuple[Optional[int], Optional[Decimal]]:
        from entities.coding_suggestion.business.similar_bills_service import (
            SimilarBillsService,
            log_similar_bills_shadow_none,
        )

        if not shadow_extraction_content:
            log_similar_bills_shadow_none("no_text", attachment_id=attachment_id)
            return None, None
        if shadow_as_of is None:
            log_similar_bills_shadow_none("no_as_of", attachment_id=attachment_id)
            return None, None
        amount: Optional[Decimal] = None
        if shadow_amount is not None:
            try:
                amount = abs(Decimal(str(shadow_amount)))
            except (InvalidOperation, TypeError, ValueError):
                amount = None
        if amount is None:
            log_similar_bills_shadow_none("no_amount", attachment_id=attachment_id)
            return None, None

        vendor_id = int(shadow_vendor_id) if shadow_vendor_id is not None else None
        return SimilarBillsService.shared().shadow_predict_with_budget(
            extraction_content=shadow_extraction_content,
            amount=amount,
            vendor_id=vendor_id,
            as_of=shadow_as_of,
            attachment_id=attachment_id,
        )

    def record_read(
        self,
        *,
        coding_decision: Optional["BillCodingDecision"] = None,
        **build_kwargs: Any,
    ) -> None:
        """Build upsert params and persist; never raises (failure-isolated).

        With a V2 coding_decision the filtered similar vote goes in the same upsert and
        the separate U-701a shadow write is skipped."""
        shadow_kwargs = {
            k: build_kwargs.pop(k)
            for k in (
                "shadow_extraction_content",
                "shadow_vendor_id",
                "shadow_amount",
                "shadow_as_of",
            )
            if k in build_kwargs
        }
        try:
            params = self.build_upsert_params_from_read(
                coding_decision=coding_decision,
                **build_kwargs,
            )
            self.repo.upsert(**params)
        except Exception as error:
            logger.warning(
                "bill_intake_prediction.persist_failed err=%s",
                type(error).__name__,
            )
            return
        if coding_decision is not None:
            return
        from entities.coding_suggestion.business.bill_text_token_hash import feature_enabled

        if not feature_enabled():
            logger.debug(
                "similar_bills.shadow_none reason=disabled attachment_id=%s",
                int(params["AttachmentId"]),
            )
            return
        try:
            similar_scc, similar_conf = self._shadow_similar_prediction(
                attachment_id=int(params["AttachmentId"]),
                **shadow_kwargs,
            )
            self.repo.update_similar_shadow(
                attachment_id=int(params["AttachmentId"]),
                similar_pred_sub_cost_code_id=similar_scc,
                similar_confidence=similar_conf,
            )
        except Exception as error:
            logger.warning(
                "bill_intake_prediction.similar_shadow_failed err=%s",
                type(error).__name__,
            )

    def bind_attachment_to_bill_failure_isolated(
        self,
        *,
        attachment_id: int,
        bill_id: int,
    ) -> None:
        try:
            self.repo.bind_to_bill(attachment_id=attachment_id, bill_id=bill_id)
        except Exception as error:
            logger.warning(
                "bill_intake_prediction.bind_failed attachment_id=%s err=%s",
                attachment_id,
                type(error).__name__,
            )

    def record_final_on_complete_failure_isolated(
        self,
        *,
        bill: Bill,
        line: Optional[BillLineItem],
    ) -> None:
        try:
            final_scc = line.sub_cost_code_id if line else None
            final_project = line.project_id if line else None
            bill_date: Optional[date] = None
            if bill.bill_date:
                if isinstance(bill.bill_date, date):
                    bill_date = bill.bill_date
                else:
                    try:
                        bill_date = datetime.strptime(str(bill.bill_date)[:10], "%Y-%m-%d").date()
                    except ValueError:
                        bill_date = None
            total: Optional[Decimal] = None
            if bill.total_amount is not None:
                total = Decimal(str(bill.total_amount))
            self.repo.record_final(
                bill_id=int(bill.id),
                FinalVendorId=bill.vendor_id,
                FinalBillNumber=bill.bill_number,
                FinalBillDate=bill_date,
                FinalTotalAmount=total,
                FinalSubCostCodeId=final_scc,
                FinalProjectId=final_project,
            )
        except Exception as error:
            logger.warning(
                "bill_intake_prediction.record_final_failed bill_id=%s err=%s",
                getattr(bill, "id", None),
                type(error).__name__,
            )

    def read_by_bill_id(self, bill_id: int) -> Optional[BillIntakePrediction]:
        return self.repo.read_by_bill_id(bill_id)

    def read_by_attachment_id(self, attachment_id: int) -> Optional[BillIntakePrediction]:
        return self.repo.read_by_attachment_id(attachment_id)

    def build_upsert_params_from_read(
        self,
        *,
        attachment_id: int,
        intake_source: str,
        status: str,
        timing_ms: dict[str, int],
        fields: dict[str, Any],
        suggestion: Optional[BillSuggestion],
        chosen_project_id: Optional[int],
        header_cascade: Optional[CascadeResult],
        coding_cascade: Optional[CascadeResult],
        header_model: Optional[str],
        vendor_id: Optional[int],
        vendor_extracted_name: Optional[str],
        coding_decision: Optional["BillCodingDecision"] = None,
    ) -> dict[str, Any]:
        if coding_cascade is None and coding_decision is not None and coding_decision.cascades:
            coding_cascade = coding_decision.cascades[0]
        input_tokens, output_tokens, cost_usd = sum_cascade_cost_and_tokens(
            header_cascade,
            coding_cascade,
        )
        header_conf: Optional[Decimal] = None
        if header_cascade and header_cascade.confidence is not None:
            try:
                header_conf = Decimal(str(header_cascade.confidence))
            except (InvalidOperation, TypeError, ValueError):
                header_conf = None

        pred_bn = _field_value(fields, "bill_number")
        pred_date = _parse_pred_date(_field_value(fields, "bill_date"))
        pred_total = _parse_pred_total(_field_value(fields, "total_amount"))
        pred_desc = _field_value(fields, "description")

        coding_conf = suggestion.confidence if suggestion else None
        coding_model = suggestion.text_model if suggestion else None
        pred_scc = suggestion.sub_cost_code_id if suggestion else None
        pred_project = suggestion.project_id if suggestion else None
        coding_mode = suggestion.text_mode if suggestion else None
        if coding_mode:
            coding_mode = coding_mode.upper()[0]

        if coding_decision is not None:
            coding_conf = coding_decision.confidence
            coding_model = coding_decision.model
            pred_scc = coding_decision.sub_cost_code_id
            pred_project = coding_decision.project_id
            coding_mode = coding_decision.mode

        timing_json = json.dumps(timing_ms, separators=(",", ":"))

        params = {
            "AttachmentId": attachment_id,
            "IntakeSource": intake_source,
            "ModelVersion": TEXT_MODEL_VERSION if coding_decision is None else MODEL_VERSION_V2,
            "Status": status,
            "PredVendorId": vendor_id,
            "PredVendorExtractedName": vendor_extracted_name,
            "PredBillNumber": pred_bn,
            "PredBillDate": pred_date,
            "PredTotalAmount": pred_total,
            "PredDescription": pred_desc,
            "PredSubCostCodeId": pred_scc,
            "PredProjectId": pred_project,
            "CodingMode": coding_mode,
            "CodingChosenProjectId": chosen_project_id if coding_mode == "A" else None,
            "HeaderConfidence": header_conf,
            "HeaderModel": header_model,
            "CodingConfidence": coding_conf,
            "CodingModel": coding_model,
            "LatencyMsTotal": timing_ms.get("total"),
            "TimingJson": timing_json,
            "InputTokens": input_tokens,
            "OutputTokens": output_tokens,
            "CostUsd": cost_usd,
            "CreatedByUserId": current_user_id.get(),
        }
        if coding_decision is not None:
            params.update(_intake_decision_params(coding_decision))
        return params

    def to_api_payload(self, prediction: BillIntakePrediction) -> dict[str, Any]:
        vendor_payload = None
        if prediction.pred_vendor_id is not None:
            vendor = VendorService().read_by_id(id=prediction.pred_vendor_id)
            if vendor and vendor.public_id:
                vendor_payload = {
                    "public_id": str(vendor.public_id),
                    "name": vendor.name,
                }
        elif prediction.pred_vendor_extracted_name:
            vendor_payload = {
                "public_id": None,
                "name": prediction.pred_vendor_extracted_name,
            }

        scc_payload = None
        if prediction.pred_sub_cost_code_id is not None:
            scc = SubCostCodeService().read_by_id(id=prediction.pred_sub_cost_code_id)
            if scc and scc.public_id:
                scc_payload = {
                    "public_id": str(scc.public_id),
                    "number": scc.number,
                }

        project_payload = None
        if prediction.pred_project_id is not None:
            project = ProjectService().read_by_id(id=prediction.pred_project_id)
            if project and project.public_id:
                project_payload = {
                    "public_id": str(project.public_id),
                    "abbreviation": project.abbreviation,
                }

        return {
            "status": prediction.status,
            "intake_source": prediction.intake_source,
            "vendor": vendor_payload,
            "bill_number": prediction.pred_bill_number,
            "bill_date": prediction.pred_bill_date.isoformat()
            if prediction.pred_bill_date
            else None,
            "total_amount": str(prediction.pred_total_amount)
            if prediction.pred_total_amount is not None
            else None,
            "description": prediction.pred_description,
            "sub_cost_code": scc_payload,
            "project": project_payload,
            "header_confidence": str(prediction.header_confidence)
            if prediction.header_confidence is not None
            else None,
            "coding_confidence": str(prediction.coding_confidence)
            if prediction.coding_confidence is not None
            else None,
        }


def _field_value(fields: dict[str, Any], key: str) -> Optional[str]:
    block = fields.get(key) or {}
    val = block.get("value")
    if val is None:
        return None
    return str(val)


def _parse_pred_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_pred_total(value: Optional[str]) -> Optional[Decimal]:
    if not value:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def resolve_intake_final_line(
    line_items: list[BillLineItem],
    *,
    target_attachment_id: Optional[int],
    read_attachment_links_for_line_items: Callable[[Sequence[str]], list[BillLineItemAttachment]],
) -> Optional[BillLineItem]:
    """Pick the line carrying the bill PDF, else the first line."""
    if target_attachment_id is not None and line_items:
        public_ids = [str(line.public_id) for line in line_items if line.public_id]
        if public_ids:
            links = read_attachment_links_for_line_items(list(public_ids))
            line_by_id = {int(line.id): line for line in line_items if line.id is not None}
            for link in links:
                if link.attachment_id != target_attachment_id:
                    continue
                line_id = link.bill_line_item_id
                if line_id is not None and int(line_id) in line_by_id:
                    return line_by_id[int(line_id)]
    return line_items[0] if line_items else None
