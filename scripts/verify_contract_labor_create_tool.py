"""Regression check on ContractLaborService.create — the service-layer
contract that backs the agent's `create_contract_labor` tool.

Phase 3 of the contract_labor_specialist agent build (TODO.md line 204,
item 5b). Exercises the create flow with the JR Scruggs vendor fixture
and the prompt's MVP scope (vendor + work_date + time_in/out +
total_hours + job_name + description; rate/markup/SCC left unset).

This script writes a real ContractLabor row, validates the field
shape, then deletes it — delete-after-create per the prompt's
no-prod-pollution rule.

Run:
    .venv/bin/python scripts/verify_contract_labor_create_tool.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context


JR_SCRUGGS_PUBLIC_ID = "CE24DDAF-3805-468D-AE9D-868C29168CDF"
JR_SCRUGGS_VENDOR_ID = 1175
WORK_DATE = "2026-05-11"           # matches the prompt's Email 769 fixture
TIME_IN = "15:55"                  # 3:55 PM
TIME_OUT = "17:00"                 # 5:00 PM
TOTAL_HOURS = Decimal("1.08")      # 65 min ≈ 1.083h, rounded to 2dp
JOB_NAME = "verify-create-tool: 206 Haverford Ave"
DESCRIPTION = "Verify-script row — DELETE on cleanup"


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.contract_labor.business.service import ContractLaborService

    service = ContractLaborService()

    print("=== ContractLaborService.create contract check ===")
    failures: list[str] = []
    created_public_id: str | None = None

    try:
        # ── Create ────────────────────────────────────────────────────
        row = service.create(
            vendor_public_id=JR_SCRUGGS_PUBLIC_ID,
            employee_name="John Randall Scruggs",
            work_date=WORK_DATE,
            total_hours=TOTAL_HOURS,
            project_public_id=None,   # MVP: no project resolution yet
            time_in=TIME_IN,
            time_out=TIME_OUT,
            job_name=JOB_NAME,
            description=DESCRIPTION,
            status="pending_review",
        )
        created_public_id = row.public_id
        print(f"  created                : public_id={created_public_id}, id={row.id}")
        print(f"                          vendor_id={row.vendor_id}, "
              f"work_date={row.work_date}, total_hours={row.total_hours}")
        print(f"                          time_in={row.time_in}, "
              f"time_out={row.time_out}, status={row.status}")
        print(f"                          job_name={row.job_name!r}, "
              f"billing_period_start={row.billing_period_start}")

        # ── Assert returned-row shape ─────────────────────────────────
        if row.vendor_id != JR_SCRUGGS_VENDOR_ID:
            failures.append(f"vendor_id={row.vendor_id}, expected {JR_SCRUGGS_VENDOR_ID}")
        if row.status != "pending_review":
            failures.append(f"status={row.status!r}, expected 'pending_review'")
        if row.work_date != WORK_DATE:
            failures.append(f"work_date={row.work_date!r}, expected {WORK_DATE!r}")
        if row.total_hours != TOTAL_HOURS:
            failures.append(f"total_hours={row.total_hours}, expected {TOTAL_HOURS}")
        if row.time_in != TIME_IN:
            failures.append(f"time_in={row.time_in!r}, expected {TIME_IN!r}")
        if row.time_out != TIME_OUT:
            failures.append(f"time_out={row.time_out!r}, expected {TIME_OUT!r}")
        if row.job_name != JOB_NAME:
            failures.append(f"job_name={row.job_name!r}, expected {JOB_NAME!r}")
        if row.description != DESCRIPTION:
            failures.append(f"description={row.description!r}, expected {DESCRIPTION!r}")
        if row.project_id is not None:
            failures.append(f"project_id={row.project_id}, expected None (no project passed)")
        if row.hourly_rate is not None:
            failures.append(
                f"hourly_rate={row.hourly_rate}, expected None "
                f"(MVP leaves rate for human reviewer)"
            )
        if row.markup is not None:
            failures.append(
                f"markup={row.markup}, expected None "
                f"(MVP leaves markup for human reviewer)"
            )
        if row.sub_cost_code_id is not None:
            failures.append(
                f"sub_cost_code_id={row.sub_cost_code_id}, expected None "
                f"(MVP leaves SCC for human reviewer)"
            )
        if row.total_amount is not None:
            failures.append(
                f"total_amount={row.total_amount}, expected None "
                f"(should stay None when no rate)"
            )
        if not row.billing_period_start:
            failures.append(
                f"billing_period_start={row.billing_period_start!r}, "
                f"expected a YYYY-MM-DD string (computed from work_date)"
            )

        # ── Read-back ─────────────────────────────────────────────────
        readback = service.read_by_public_id(public_id=created_public_id)
        if readback is None:
            failures.append("read_by_public_id returned None after create")
        else:
            if readback.id != row.id:
                failures.append(f"read-back id={readback.id}, expected {row.id}")
            if readback.job_name != JOB_NAME:
                failures.append(
                    f"read-back job_name={readback.job_name!r}, "
                    f"expected {JOB_NAME!r}"
                )
            print(f"  read-back              : public_id={readback.public_id}, "
                  f"job_name={readback.job_name!r}")

    finally:
        # ── Delete (cleanup) ──────────────────────────────────────────
        if created_public_id is not None:
            deleted = service.delete_by_public_id(public_id=created_public_id)
            if deleted is None:
                failures.append(f"delete_by_public_id returned None for {created_public_id}")
            else:
                print(f"  deleted                : public_id={created_public_id}")
            # Confirm gone
            after = service.read_by_public_id(public_id=created_public_id)
            if after is not None:
                failures.append(
                    f"row {created_public_id} still readable after delete — "
                    f"cleanup leaked"
                )
            else:
                print(f"  cleanup confirmed      : read_by_public_id is now None")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        "\nPASS — create accepts MVP scope (vendor + work_date + times + "
        "total_hours + job_name + description), leaves rate/markup/SCC "
        "null, stamps status=pending_review, and round-trips through "
        "read + delete cleanly."
    )
    return 0


if __name__ == "__main__":
    sys.exit(verify())
