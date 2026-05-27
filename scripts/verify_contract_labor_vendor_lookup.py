"""Regression check on VendorService.find_contract_labor_by_email.

Phase 1 of the contract_labor_specialist agent build (TODO.md line 204,
item 5b). The new sproc `FindContractLaborVendorByEmail` binds a
sender email back to the contract-labor Vendor row via
`Vendor INNER JOIN Contact ON Contact.VendorId = Vendor.Id` with
`Vendor.IsContractLabor=1 AND LOWER(Contact.Email)=LOWER(@email)`.

This script locks the contract by exercising three cases via the
service layer (which routes through the new sproc):

  1. PASS — known JR Scruggs fixture (Vendor 1175): returns the Vendor.
  2. PASS — bogus email: returns None.
  3. PASS — a synthetic Contact (inserted on a non-CL Vendor, deleted
     in the same script) with a unique email: returns None (the
     IsContractLabor filter must hold even when the email binding
     exists).

Case 3 uses a delete-after-create pattern because today there's only
one Vendor-linked Contact in prod (JR Scruggs / Contact 19), so we
synthesize a non-CL Contact to exercise the filter. The Contact row
is removed before the script exits — no permanent state change.

Run:
    .venv/bin/python scripts/verify_contract_labor_vendor_lookup.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


JR_SCRUGGS_EMAIL = "jrscruggs07@gmail.com"
JR_SCRUGGS_VENDOR_ID = 1175
JR_SCRUGGS_VENDOR_NAME = "John Randall Scruggs"
BOGUS_EMAIL = "definitely-not-a-real-vendor-email-xyzzy@example.invalid"


def _pick_non_cl_vendor() -> tuple[int, str]:
    """Return (vendor_id, vendor_name) for any non-CL non-deleted Vendor.
    Used as the parent for the synthetic Contact in case 3."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP 1 [Id], [Name]
               FROM dbo.[Vendor]
               WHERE [IsContractLabor] = 0
                 AND [IsDeleted]       = 0
               ORDER BY [Id]"""
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                "No non-CL Vendor found — cannot pick a parent for the "
                "synthetic Contact fixture."
            )
        return int(row.Id), str(row.Name)


def _create_synthetic_contact(vendor_id: int, email: str) -> int:
    """Insert a Contact row bound to `vendor_id` with the given email.
    Returns the new Contact.Id so we can delete it after the test."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
               INSERT INTO dbo.[Contact]
                   ([CreatedDatetime], [ModifiedDatetime], [Email], [VendorId])
               OUTPUT INSERTED.[Id]
               VALUES (@Now, @Now, ?, ?);""",
            (email, vendor_id),
        )
        row = cur.fetchone()
        contact_id = int(row[0])
        conn.commit()
        return contact_id


def _delete_synthetic_contact(contact_id: int) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.[Contact] WHERE [Id] = ?", (contact_id,))
        conn.commit()


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.vendor.business.service import VendorService

    print("=== VendorService.find_contract_labor_by_email contract check ===")
    failures: list[str] = []
    service = VendorService()

    # 1. Positive case — JR Scruggs (locked fixture).
    v = service.find_contract_labor_by_email(JR_SCRUGGS_EMAIL)
    print(f"  case 1 (JR Scruggs)             : email={JR_SCRUGGS_EMAIL}")
    if v is None:
        failures.append(
            f"case 1: returned None — expected Vendor {JR_SCRUGGS_VENDOR_ID} "
            f"({JR_SCRUGGS_VENDOR_NAME})"
        )
    else:
        if v.id != JR_SCRUGGS_VENDOR_ID:
            failures.append(
                f"case 1: vendor.id={v.id}, expected {JR_SCRUGGS_VENDOR_ID}"
            )
        if v.name != JR_SCRUGGS_VENDOR_NAME:
            failures.append(
                f"case 1: vendor.name={v.name!r}, expected {JR_SCRUGGS_VENDOR_NAME!r}"
            )
        if not v.is_contract_labor:
            failures.append(
                f"case 1: vendor.is_contract_labor={v.is_contract_labor!r}, "
                f"expected True"
            )
        print(f"           → Vendor {v.id} ({v.name}), IsContractLabor={v.is_contract_labor}")

    # 2. Negative case — bogus email.
    v = service.find_contract_labor_by_email(BOGUS_EMAIL)
    print(f"  case 2 (bogus email)            : email={BOGUS_EMAIL}")
    if v is not None:
        failures.append(
            f"case 2: returned Vendor {v.id} ({v.name}) — expected None"
        )
    else:
        print(f"           → None (as expected)")

    # 3. Negative case — synthetic Contact on a non-CL Vendor. Insert,
    #    test, delete. Verifies the IsContractLabor=1 filter rejects the
    #    lookup even when the email binding exists.
    non_cl_vendor_id, non_cl_vendor_name = _pick_non_cl_vendor()
    synthetic_email = f"verify-cl-filter-{non_cl_vendor_id}@example.invalid"
    contact_id = _create_synthetic_contact(non_cl_vendor_id, synthetic_email)
    try:
        v = service.find_contract_labor_by_email(synthetic_email)
        print(
            f"  case 3 (synthetic non-CL email) : email={synthetic_email}  "
            f"(parent Vendor {non_cl_vendor_id} '{non_cl_vendor_name}', "
            f"IsContractLabor=0)"
        )
        if v is not None:
            failures.append(
                f"case 3: returned Vendor {v.id} ({v.name}) for a non-CL "
                f"Contact email — expected None (IsContractLabor filter "
                f"should have rejected it)"
            )
        else:
            print(f"           → None (IsContractLabor filter held)")
    finally:
        _delete_synthetic_contact(contact_id)
        print(f"           cleaned up synthetic Contact {contact_id}")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(
        "\nPASS — sproc + repo + service correctly: returns the matching "
        "CL Vendor, returns None on bogus email, and excludes non-CL vendors."
    )
    return 0


if __name__ == "__main__":
    sys.exit(verify())
