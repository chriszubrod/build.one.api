"""
Orchestrator: run all three QBO identity drift detectors (U-238a headers, U-238b
lines, U-238c reference) and print one aggregated PASS/FAIL summary.

Usage:
  PYTHONPATH=. python scripts/check_qbo_identity_drift_all.py
"""
from __future__ import annotations

import logging
import sys

from scripts.check_qbo_identity_drift_headers import main as run_headers
from scripts.check_qbo_identity_drift_lines import main as run_lines
from scripts.check_qbo_identity_drift_reference import main as run_reference

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("check_qbo_identity_drift_all")


def main() -> int:
    print("\n========== QBO identity drift — ALL (17 mapping topologies) ==========")

    print("\n>>> Headers (5 entities)")
    header_code = run_headers()

    print("\n>>> Line items (4 entities)")
    line_code = run_lines()

    print("\n>>> Reference entities (8 entities)")
    ref_code = run_reference()

    passed = header_code == 0 and line_code == 0 and ref_code == 0
    print("\n========== AGGREGATED SUMMARY ==========")
    if passed:
        logger.info("PASS — all 17 mapping topologies clean (including fan-out checks)")
        return 0

    logger.error(
        "FAIL — one or more drift detectors reported issues "
        "(headers=%s lines=%s reference=%s); see sections above",
        header_code,
        line_code,
        ref_code,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
