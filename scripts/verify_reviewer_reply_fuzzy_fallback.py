"""Regression check on FindBillForReviewerReply sproc + repo wiring.

Surfaced 2026-05-19 manual walk on EmailMessage Id=750 — Tanner Baker
replied `Approved / 7550 Buffalo / 95.0...` but the inbound reply's
ConversationId didn't match any tracked Bill, so the parsed approval
fell on the floor as `internal_reply` + `flagged_needs_review`. Wave 3
Phase 1 fix shipped 2026-05-26: `FindBillForReviewerReply` sproc adds a
fuzzy fallback on (BillNumber exact match) AND (Project name contains
hint substring), single-result only.

This script picks the most-recent draft Bill with a non-null BillNumber
and at least one line item on a Project, then exercises three paths:

  (a) bogus conv_id + correct hints → Bill returned with match_kind='fuzzy'
  (b) bogus conv_id + no hints       → null (preserves old contract)
  (c) bogus conv_id + wrong hints    → null (no false positive)

Read-only — exercises only the lookup path, no mutations.

Run:
    .venv/bin/python scripts/verify_reviewer_reply_fuzzy_fallback.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


def _pick_fixture() -> tuple[int, str, str, str]:
    """Return (bill_id, bill_number, project_name, project_hint_substring)
    for the most-recent draft Bill with a non-null BillNumber and at
    least one line-item Project."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP 1
                   b.[Id], b.[BillNumber], p.[Name]
               FROM dbo.[Bill] b
               INNER JOIN dbo.[BillLineItem] bli ON bli.[BillId] = b.[Id]
               INNER JOIN dbo.[Project] p ON p.[Id] = bli.[ProjectId]
               WHERE b.[IsDraft] = 1
                 AND b.[BillNumber] IS NOT NULL
                 AND LEN(b.[BillNumber]) >= 3
               ORDER BY b.[Id] DESC"""
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                "No draft Bill with a BillNumber and line-item Project — "
                "fixture cannot be selected."
            )
        bill_id = int(row[0])
        bill_number = str(row[1])
        project_name = str(row[2])
        # Pick a substring from the Project.Name that's specific enough.
        # Project names look like "WVA - 424 Westview Avenue"; strip the
        # leading code prefix if present and use a meaningful word.
        if " - " in project_name:
            hint = project_name.split(" - ", 1)[1].strip()
        else:
            hint = project_name.strip()
        # Cap to ~25 chars so the LIKE pattern isn't unnecessarily long.
        return bill_id, bill_number, project_name, hint[:25]


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.bill.persistence.repo import BillRepository

    bill_id, bill_number, project_name, project_hint = _pick_fixture()
    print(f"=== FindBillForReviewerReply contract check ===")
    print(f"  fixture bill_id      : {bill_id}")
    print(f"  fixture bill_number  : {bill_number!r}")
    print(f"  fixture project_name : {project_name!r}")
    print(f"  fixture project_hint : {project_hint!r}")

    repo = BillRepository()
    bogus_conv = "BOGUS-CONVERSATION-ID-FOR-REGRESSION-TEST"
    failures: list[str] = []

    # (a) Fuzzy path — conv miss + correct hints → returns the fixture Bill.
    res_a = repo.find_for_reviewer_reply(
        conversation_id=bogus_conv,
        bill_number_hint=bill_number,
        project_hint=project_hint,
    )
    if res_a is None:
        failures.append(
            f"(a) fuzzy match returned None — expected Bill Id={bill_id}"
        )
    else:
        if res_a.get("id") != bill_id:
            failures.append(
                f"(a) returned wrong bill: got Id={res_a.get('id')}, "
                f"expected {bill_id}"
            )
        if res_a.get("match_kind") != "fuzzy":
            failures.append(
                f"(a) match_kind={res_a.get('match_kind')!r}, expected 'fuzzy'"
            )

    # (b) No hints → null. Preserves the original contract.
    res_b = repo.find_for_reviewer_reply(
        conversation_id=bogus_conv,
        bill_number_hint=None,
        project_hint=None,
    )
    if res_b is not None:
        failures.append(
            f"(b) expected None on conv miss + no hints, got {res_b!r}"
        )

    # (c) Wrong hints → null. No false positive.
    res_c = repo.find_for_reviewer_reply(
        conversation_id=bogus_conv,
        bill_number_hint="DEFINITELY_NOT_A_REAL_BILL_NUMBER_XYZ",
        project_hint="Nonexistent Project Name 99999",
    )
    if res_c is not None:
        failures.append(
            f"(c) expected None on bogus hints, got {res_c!r}"
        )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS — fuzzy path matches, no-hint path nulls, bogus-hint path nulls")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
