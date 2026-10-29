#!/usr/bin/env python3
"""Read-only bill intake prediction accuracy report (U-676m) + live coding scorecard (U-744).

Usage (requires DB credentials — not for CI):
  ./.venv/bin/python scripts/u676m_prediction_accuracy.py /path/to/report.md
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.bill_intake_prediction.business.accuracy import (
    aggregate_prediction_accuracy,
    format_accuracy_markdown,
)
from entities.bill_intake_prediction.business.coding_scorecard import (
    build_coding_scorecard,
    format_coding_scorecard_markdown,
    scorecard_row_from_db,
)
from entities.bill_intake_prediction.persistence.repo import BillIntakePredictionRepository


def _row_to_dict(row) -> dict:
    return {
        "pred_vendor_id": getattr(row, "PredVendorId", None),
        "pred_bill_number": getattr(row, "PredBillNumber", None),
        "pred_bill_date": getattr(row, "PredBillDate", None),
        "pred_total_amount": getattr(row, "PredTotalAmount", None),
        "pred_sub_cost_code_id": getattr(row, "PredSubCostCodeId", None),
        "pred_project_id": getattr(row, "PredProjectId", None),
        "final_vendor_id": getattr(row, "FinalVendorId", None),
        "final_bill_number": getattr(row, "FinalBillNumber", None),
        "final_bill_date": getattr(row, "FinalBillDate", None),
        "final_total_amount": getattr(row, "FinalTotalAmount", None),
        "final_sub_cost_code_id": getattr(row, "FinalSubCostCodeId", None),
        "final_project_id": getattr(row, "FinalProjectId", None),
        "cost_usd": getattr(row, "CostUsd", None),
        "latency_ms_total": getattr(row, "LatencyMsTotal", None),
        "vendor_name": getattr(row, "VendorName", None),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bill intake prediction accuracy (read-only)")
    parser.add_argument("output", type=Path, help="Markdown output path")
    args = parser.parse_args(argv)

    repo = BillIntakePredictionRepository()
    db_rows = repo.read_for_accuracy()
    rows = [_row_to_dict(r) for r in db_rows]
    report = aggregate_prediction_accuracy(rows)
    scorecard = build_coding_scorecard([scorecard_row_from_db(r) for r in db_rows])
    markdown = format_accuracy_markdown(report) + "\n" + format_coding_scorecard_markdown(scorecard)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
