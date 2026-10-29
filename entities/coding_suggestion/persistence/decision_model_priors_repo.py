"""Persistence for the U-745 M0 decision-model reads (dbo.decision_model_priors.sql).

Read-only. Rows map to the backtest's own ``PriorBill`` (training's build_index shape).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from entities.coding_suggestion.business.decision_model.features import PriorBill
from shared.database import call_procedure, get_connection, map_database_error
from shared.db_datetime import parse_db_datetime

logger = logging.getLogger(__name__)


def prior_bill_from_row(row: Any, *, vendor_id: int) -> PriorBill:
    """One ReadVendorPriorBillsForDecisionModel row -> PriorBill."""
    created = parse_db_datetime(row.CreatedDatetime)
    if created is None:
        raise ValueError("prior bill without CreatedDatetime")
    project = getattr(row, "DominantProjectId", None)
    amount = getattr(row, "TotalAbsAmount", None)
    return PriorBill(
        bill_id=int(row.BillId),
        vendor_id=int(vendor_id),
        created=created,
        label_scc_id=int(row.DominantSubCostCodeId),
        project_id=int(project) if project is not None else None,
        amount=float(amount) if amount is not None else 0.0,
        has_pdf=bool(getattr(row, "HasPdf", 0)),
    )


class DecisionModelPriorsRepository:
    def read_vendor_prior_bills(
        self,
        *,
        company_id: int,
        vendor_id: int,
        as_of: datetime,
        exclude_bill_id: Optional[int],
        tokenizer_version: Optional[str],
    ) -> list[PriorBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorPriorBillsForDecisionModel",
                    params={
                        "CompanyId": company_id,
                        "VendorId": vendor_id,
                        "AsOf": as_of,
                        "ExcludeBillId": exclude_bill_id,
                        "TokenizerVersion": tokenizer_version,
                    },
                )
                rows = cursor.fetchall()
        except Exception as error:
            logger.error(
                "ReadVendorPriorBillsForDecisionModel failed: %s", type(error).__name__
            )
            raise map_database_error(error)
        return [prior_bill_from_row(r, vendor_id=vendor_id) for r in rows]

    def read_label_counts(self, company_id: int) -> dict[int, int]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeLabelCountsForDecisionModel",
                    params={"CompanyId": company_id},
                )
                rows = cursor.fetchall()
        except Exception as error:
            logger.error(
                "ReadSubCostCodeLabelCountsForDecisionModel failed: %s", type(error).__name__
            )
            raise map_database_error(error)
        return {int(r.SubCostCodeId): int(r.BillCount) for r in rows}
