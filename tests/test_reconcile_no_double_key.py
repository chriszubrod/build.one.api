"""The col-Z backfill must never stamp one public_id onto two Excel rows.

`check_excel_to_db` matches an unkeyed Excel row to a DB line item on five
fields — date, vendor, reference number, description, amount — NOT on the key.
So when a charge appears twice on a sheet (once hand-entered and keyless, once
synced and keyed), the matcher can legitimately match BOTH rows to the same line
item and stamp its public_id onto the keyless one too.

That end state is worse than leaving the row unkeyed: both rows then look
synced, the DB->Excel direction skips the line item forever because its key is
"present", and the duplicate becomes permanent and invisible to every check.

Live case, 2026-09-28: EVR SharePoint r1113 and r1116 were the same $3,150
Comfort Engineered Systems charge (invoice 251223203) recorded twice. Only a
description mismatch — 'HVAC' on the legacy row vs 'HVAC Costs' in dbo — kept
the backfill off r1113. Tidying that description to silence the reconcile
warning would have made `--write` key both rows to
FA19EB61-083B-47AC-B8E9-2ADDD5DCA5F1.
"""

import inspect

from scripts.reconcile_project import check_excel_to_db


def test_the_backfill_accepts_the_sheets_existing_keys():
    """The guard can only work if the caller's col-Z set reaches the function —
    it was collected at the call site but never passed in."""
    params = inspect.signature(check_excel_to_db).parameters
    assert "existing_public_ids" in params
    assert params["existing_public_ids"].default is None, (
        "must default to None so existing callers keep working"
    )


def test_the_call_site_passes_the_existing_keys():
    """Guards against the parameter being added and then left unwired — which is
    exactly the state this fix found it in."""
    import scripts.reconcile_project as mod

    src = inspect.getsource(mod)
    call = src[src.index("= check_excel_to_db("):]
    call = call[: call.index(")")]
    assert "existing_public_ids=excel_public_ids" in call, (
        "reconcile_project must pass the sheet's col-Z set into check_excel_to_db"
    )


def test_the_guard_refuses_a_key_already_on_the_sheet():
    """The refusal must be reachable from the matched-line-item branch, and must
    `continue` rather than fall through to the write."""
    src = inspect.getsource(check_excel_to_db)
    assert "already_on_sheet" in src
    guard = src[src.index("if public_id_str in already_on_sheet"):]
    guard = guard[: guard.index("continue") + len("continue")]
    assert "backfilled_public_ids" in guard, (
        "must also refuse a key stamped earlier in this same run"
    )
    assert "issues.append" in guard, "the refusal must be reported, not silent"
    # and it must sit BEFORE the write
    assert src.index("if public_id_str in already_on_sheet") < src.index(
        "DRY RUN: would write public_id"
    ), "the guard must precede both the dry-run and the write branches"
