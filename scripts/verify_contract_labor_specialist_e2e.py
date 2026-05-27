"""End-to-end tool-path test of the contract_labor_specialist flow.

Phase 7 of the contract_labor_specialist agent build (TODO.md line 204,
item 5b). Exercises the full chain the live agent would run, but via
direct service calls — no Claude API, no agent loop, no production
side-effects beyond the single ContractLabor row created and deleted
within this script.

Fixture: EmailMessage Id=769 (JR Scruggs `Work Hours 5/11`):
  From:    jrscruggs07@gmail.com (John Randall Scruggs)
  Subject: Work Hours 5/11
  Body:    206 Haverford Ave
           Clock in: 3:55
           Clock out: 5:00
           Installed door hardware.
           JR Scruggs

The mapped agent flow:
  1. find_contract_labor_vendor_by_email(sender)         → Vendor 1175
  2. ProjectService.find_for_invoice(address)            → Project 128
  3. ContractLaborService.create(...)                    → new row
  4. read_by_public_id                                   → validate fields
  5. delete_by_public_id                                 → cleanup

This script does NOT spawn the live agent runner. The actual agent
loop / Claude API / delegation primitives are covered by
`dry_run_cache_markers.py contract_labor_specialist` (which validates
registration + prompt caching) and will be covered in production by
the next real timesheet email that hits invoice@ (which routes via
the unpaused email_specialist).

Run:
    .venv/bin/python scripts/verify_contract_labor_specialist_e2e.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context


# ── Fixture (Email 769 — JR Scruggs Work Hours 5/11) ──────────────────
SENDER_EMAIL          = "jrscruggs07@gmail.com"
EXPECTED_VENDOR_ID    = 1175
EXPECTED_VENDOR_NAME  = "John Randall Scruggs"
ADDRESS_HINT          = "206 Haverford Ave"
EXPECTED_PROJECT_ID   = 128
EXPECTED_PROJECT_NAME = "HA - 206 Haverford Ave"

WORK_DATE     = "2026-05-11"       # subject `Work Hours 5/11` + received year 2026
TIME_IN       = "15:55"            # body `Clock in: 3:55` + PM default
TIME_OUT      = "17:00"            # body `Clock out: 5:00` + PM default
TOTAL_HOURS   = Decimal("1.08")    # 17:00 - 15:55 = 65 min ≈ 1.08h
EMPLOYEE_NAME = "John Randall Scruggs"
DESCRIPTION   = "Installed door hardware."


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.vendor.business.service import VendorService
    from entities.project.business.service import ProjectService
    from entities.contract_labor.business.service import ContractLaborService

    print("=== contract_labor_specialist end-to-end (tool-path) ===")
    print(f"  fixture: Email 769 (JR Scruggs Work Hours 5/11)")
    print()

    failures: list[str] = []
    created_public_id: str | None = None
    resolved_project_public_id: str | None = None

    try:
        # ── Step 1: Sender → Vendor ───────────────────────────────────
        vendor = VendorService().find_contract_labor_by_email(SENDER_EMAIL)
        if vendor is None:
            failures.append(f"step 1: vendor lookup returned None for {SENDER_EMAIL}")
        else:
            print(f"  step 1 (vendor lookup)        : Vendor {vendor.id} "
                  f"({vendor.name}), IsContractLabor={vendor.is_contract_labor}")
            if vendor.id != EXPECTED_VENDOR_ID:
                failures.append(f"step 1: vendor.id={vendor.id}, expected {EXPECTED_VENDOR_ID}")
            if vendor.name != EXPECTED_VENDOR_NAME:
                failures.append(f"step 1: vendor.name={vendor.name!r}, expected {EXPECTED_VENDOR_NAME!r}")
            if not vendor.is_contract_labor:
                failures.append("step 1: vendor.is_contract_labor=False (expected True)")
        if failures:
            return _report(failures)
        vendor_public_id = vendor.public_id

        # ── Step 2: Address → Project ─────────────────────────────────
        candidates = ProjectService().find_for_invoice(address_hint=ADDRESS_HINT)
        print(f"  step 2 (project resolution)   : {len(candidates)} candidate(s) "
              f"for address_hint={ADDRESS_HINT!r}")
        if not candidates:
            failures.append(f"step 2: no Project candidates for {ADDRESS_HINT!r}")
            return _report(failures)
        top = candidates[0]
        project = top.get("project", {})
        print(f"                                  → top match: Project {project.get('id')} "
              f"({project.get('name')}), strategy={top.get('strategy')}, "
              f"confidence={top.get('confidence')}")
        if project.get("id") != EXPECTED_PROJECT_ID:
            failures.append(
                f"step 2: top project.id={project.get('id')}, "
                f"expected {EXPECTED_PROJECT_ID}"
            )
        if project.get("name") != EXPECTED_PROJECT_NAME:
            failures.append(
                f"step 2: top project.name={project.get('name')!r}, "
                f"expected {EXPECTED_PROJECT_NAME!r}"
            )
        resolved_project_public_id = project.get("public_id")
        if not resolved_project_public_id:
            failures.append("step 2: top project.public_id is empty")
        if failures:
            return _report(failures)

        # ── Step 3: Create ContractLabor row ──────────────────────────
        row = ContractLaborService().create(
            vendor_public_id=vendor_public_id,
            project_public_id=resolved_project_public_id,
            employee_name=EMPLOYEE_NAME,
            work_date=WORK_DATE,
            total_hours=TOTAL_HOURS,
            time_in=TIME_IN,
            time_out=TIME_OUT,
            job_name=ADDRESS_HINT,
            description=DESCRIPTION,
            status="pending_review",
        )
        created_public_id = row.public_id
        print(f"  step 3 (create row)           : public_id={created_public_id}, id={row.id}")
        print(f"                                  vendor_id={row.vendor_id}, "
              f"project_id={row.project_id}, status={row.status}")
        print(f"                                  work_date={row.work_date}, "
              f"total_hours={row.total_hours}, "
              f"billing_period_start={row.billing_period_start}")

        if row.vendor_id != EXPECTED_VENDOR_ID:
            failures.append(f"step 3: vendor_id={row.vendor_id}, expected {EXPECTED_VENDOR_ID}")
        if row.project_id != EXPECTED_PROJECT_ID:
            failures.append(f"step 3: project_id={row.project_id}, expected {EXPECTED_PROJECT_ID}")
        if row.status != "pending_review":
            failures.append(f"step 3: status={row.status!r}, expected 'pending_review'")
        if row.job_name != ADDRESS_HINT:
            failures.append(f"step 3: job_name={row.job_name!r}, expected {ADDRESS_HINT!r}")
        if row.description != DESCRIPTION:
            failures.append(f"step 3: description={row.description!r}, expected {DESCRIPTION!r}")
        if row.hourly_rate is not None:
            failures.append(
                f"step 3: hourly_rate={row.hourly_rate}, expected None "
                f"(MVP leaves rate for human review)"
            )
        if row.markup is not None:
            failures.append(f"step 3: markup={row.markup}, expected None")
        if row.sub_cost_code_id is not None:
            failures.append(f"step 3: sub_cost_code_id={row.sub_cost_code_id}, expected None")

        # ── Step 4: Read-back ─────────────────────────────────────────
        readback = ContractLaborService().read_by_public_id(public_id=created_public_id)
        if readback is None:
            failures.append("step 4: read_by_public_id returned None after create")
        else:
            print(f"  step 4 (read-back)            : public_id={readback.public_id}, "
                  f"vendor_id={readback.vendor_id}, project_id={readback.project_id}")
            if readback.id != row.id:
                failures.append(f"step 4: read-back id={readback.id}, expected {row.id}")

    finally:
        # ── Step 5: Cleanup ───────────────────────────────────────────
        if created_public_id is not None:
            deleted = ContractLaborService().delete_by_public_id(public_id=created_public_id)
            if deleted is None:
                failures.append(f"step 5: delete returned None for {created_public_id}")
            else:
                print(f"  step 5 (cleanup delete)       : public_id={created_public_id}")
            after = ContractLaborService().read_by_public_id(public_id=created_public_id)
            if after is not None:
                failures.append(
                    f"step 5: row {created_public_id} still readable after delete"
                )
            else:
                print(f"                                  read_by_public_id is None — clean")

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        "\nPASS — full agent tool-path works: sender binds to JR Scruggs "
        "Vendor, '206 Haverford Ave' resolves to Project 128, "
        "ContractLabor row created with status=pending_review, rate/markup/SCC "
        "left null, round-trips through read + delete cleanly."
    )
    return 0


if __name__ == "__main__":
    sys.exit(verify())
