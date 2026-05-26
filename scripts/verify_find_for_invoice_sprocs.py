"""Regression check on FindVendorForInvoice + FindProjectForInvoice.

Surfaced 2026-05-19 manual walk: both sprocs were reported to return rows
with populated `Strategy` + `Confidence` but NULL `Name` / `PublicId`.
2026-05-26 follow-up investigation could not reproduce on the same repro
case (Puente's Drywall / 4005 Franklin Pike); both sprocs returned fully-
populated rows. Best guess: transient state during the prior agent walk.

This script locks the contract so the bug, if it ever recurs, fails loudly
here instead of silently burning Claude tokens during agent runs:

  - Any returned row with non-null Strategy MUST have non-null
    {VendorName, VendorPublicId, Confidence} (vendor sproc)
  - Any returned row with non-null Strategy MUST have non-null
    {ProjectName, ProjectPublicId, Confidence} (project sproc)

Run:
    .venv/bin/python scripts/verify_find_for_invoice_sprocs.py

Exits 0 on PASS, 1 on FAIL. Read-only — no mutations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection


VENDOR_CASES = [
    # (vendor_name, sender_domain, label) — vendors known to exist in prod.
    ("Puente's Drywall", None, "prefix_name on possessive vendor"),
    ("WALKER LUMBER", None, "uppercase prefix"),
    ("Kenny & Company", "kennypipe.com", "exact name + domain"),
    ("Nonexistent Vendor XYZ", None, "no-match input (expect empty)"),
]

PROJECT_CASES = [
    # (address_hint, project_name_hint, label)
    ("4005 Franklin Pike", None, "address with multiple candidate projects"),
    ("1577 Moran", None, "address with several MR2-* sub-projects"),
    ("99999 Nowhere Rd", None, "no-match address (expect empty)"),
]


def _check_vendor_row(row: dict, case_label: str) -> list[str]:
    failures: list[str] = []
    if row["Strategy"] is None:
        return failures  # nothing to check
    for col in ("VendorName", "VendorPublicId", "Confidence"):
        if row.get(col) is None:
            failures.append(
                f"  {case_label}: Strategy={row['Strategy']!r} but {col}=NULL "
                f"(VendorId={row.get('VendorId')!r})"
            )
    return failures


def _check_project_row(row: dict, case_label: str) -> list[str]:
    failures: list[str] = []
    if row["Strategy"] is None:
        return failures
    for col in ("ProjectName", "ProjectPublicId", "Confidence"):
        if row.get(col) is None:
            failures.append(
                f"  {case_label}: Strategy={row['Strategy']!r} but {col}=NULL "
                f"(ProjectId={row.get('ProjectId')!r})"
            )
    return failures


def verify() -> int:
    all_failures: list[str] = []
    total_rows = 0

    with get_connection() as conn:
        cur = conn.cursor()

        for vendor_name, sender_domain, label in VENDOR_CASES:
            cur.execute(
                "EXEC dbo.FindVendorForInvoice @VendorName = ?, @SenderDomain = ?",
                vendor_name, sender_domain,
            )
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            total_rows += len(rows)
            for row in rows:
                all_failures.extend(_check_vendor_row(row, f"FindVendor[{label}]"))

        for address_hint, name_hint, label in PROJECT_CASES:
            cur.execute(
                "EXEC dbo.FindProjectForInvoice @AddressHint = ?, @ProjectNameHint = ?",
                address_hint, name_hint,
            )
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            total_rows += len(rows)
            for row in rows:
                all_failures.extend(_check_project_row(row, f"FindProject[{label}]"))

    print(f"=== Find{{Vendor,Project}}ForInvoice contract check ===")
    print(f"  vendor cases : {len(VENDOR_CASES)}")
    print(f"  project cases: {len(PROJECT_CASES)}")
    print(f"  rows checked : {total_rows}")

    if all_failures:
        print("\nFAIL — phantom-row regression:")
        for f in all_failures:
            print(f)
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
