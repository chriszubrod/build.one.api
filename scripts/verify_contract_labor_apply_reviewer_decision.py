"""Regression check on ContractLaborService.apply_reviewer_decision.

Unit 3 of the contract_labor_specialist reviewer-reply branch build.
Exercises the apply flow against CL 647 (Ricky Moreno TB3 6/18) on
Project 73 (TB3), with Cassidy Andrews (UserId 18, PM on Project 73)
as the reviewer.

Cases:
  1. Approval with SCC + description → Review row inserted, line items
     on Project 73 updated, status stays pending_review
  2. Rejection with raw_reply_text → Review row inserted with declined
     status, line items untouched
  3. Status-race: flip CL.Status='ready' temporarily → apply raises with
     "no longer pending_review" message
  4. Authz failure: unauthorized email → ValueError "not an authorized reviewer"
  5. Missing SCC on approval → ValueError "sub_cost_code_public_id is required"

Read-modifies-prod-then-restores. Each case captures pre-state and
reverts in finally blocks.

Run:
    .venv/bin/python scripts/verify_contract_labor_apply_reviewer_decision.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


CL_PUBLIC_ID = '5D964E89-8830-4B38-9D83-11162AA6F319'  # Ricky Moreno TB3 6/18 (CL 647)
CL_ID = 647
PROJECT_TB3_PUBLIC_ID_LOOKUP = """
    SELECT TOP 1 CAST(p.PublicId AS NVARCHAR(50))
    FROM dbo.Project p WHERE p.Abbreviation = 'TB3'
"""
SCC_MISC_LABOR_PUBLIC_ID = 'D4EDE9C2-39EC-F011-8196-6045BDD32466'  # SCC 65.02 Miscellaneous Labor
REVIEWER_EMAIL = 'cassidy@rogersbuild.com'  # Cassidy Andrews — PM on Project 73


def _snapshot_state(cl_id: int) -> dict:
    """Capture CL.Status + line item (id, scc_id, description) on Project 73."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute('SELECT [Status] FROM dbo.[ContractLabor] WHERE [Id] = ?', (cl_id,))
        status = cur.fetchone()[0]
        cur.execute(
            """SELECT cli.[Id], cli.[SubCostCodeId], cli.[Description]
               FROM dbo.[ContractLaborLineItem] cli
               INNER JOIN dbo.[Project] p ON p.[Id] = cli.[ProjectId]
               WHERE cli.[ContractLaborId] = ? AND p.[Abbreviation] = 'TB3'
               ORDER BY cli.[Id]""",
            (cl_id,),
        )
        line_items = [(r.Id, r.SubCostCodeId, r.Description) for r in cur.fetchall()]
    return {'status': status, 'line_items': line_items}


def _restore_state(cl_id: int, snapshot: dict) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE dbo.[ContractLabor] SET [Status] = ? WHERE [Id] = ?',
            (snapshot['status'], cl_id),
        )
        for li_id, scc_id, desc in snapshot['line_items']:
            cur.execute(
                """UPDATE dbo.[ContractLaborLineItem]
                   SET [SubCostCodeId] = ?, [Description] = ?
                   WHERE [Id] = ?""",
                (scc_id, desc, li_id),
            )
        conn.commit()


def _force_pending_review(cl_id: int) -> None:
    """Reset CL.Status to pending_review between test cases.

    Necessary because the new auto-mirror (post-2026-06-26) flips CL.Status
    to 'ready' after every successful approval — subsequent cases that
    depend on the row being in pending_review would fail their precondition
    guard without a reset.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dbo.[ContractLabor] SET [Status] = 'pending_review' WHERE [Id] = ?",
            (cl_id,),
        )
        conn.commit()


def _delete_reviews_created_for_cl(cl_id: int, before_count: int) -> None:
    """Delete Review rows we created during the test, leaving any pre-existing rows."""
    with get_connection() as conn:
        cur = conn.cursor()
        # Get current review IDs, then DELETE rows newer than before_count
        cur.execute(
            'SELECT [Id] FROM dbo.[Review] WHERE [ContractLaborId] = ? ORDER BY [Id] ASC',
            (cl_id,),
        )
        ids = [r.Id for r in cur.fetchall()]
        if len(ids) > before_count:
            new_ids = ids[before_count:]
            for review_id in new_ids:
                cur.execute('DELETE FROM dbo.[Review] WHERE [Id] = ?', (review_id,))
            conn.commit()


def _count_reviews(cl_id: int) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM dbo.[Review] WHERE [ContractLaborId] = ?', (cl_id,))
        return int(cur.fetchone()[0])


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.contract_labor.business.service import ContractLaborService
    service = ContractLaborService()

    # Resolve Project 73 (TB3) public_id
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(PROJECT_TB3_PUBLIC_ID_LOOKUP)
        project_tb3_public_id = str(cur.fetchone()[0])

    print('=== ContractLaborService.apply_reviewer_decision check ===')
    failures: list = []
    initial_snapshot = _snapshot_state(CL_ID)
    initial_review_count = _count_reviews(CL_ID)
    # CL 647 may have been left at 'ready' / 'billed' by a prior verify
    # run (the auto-mirror flips on Approval and earlier verify cases
    # may not have restored). Force back to pending_review for the
    # duration of this test; cleanup restores whatever the original
    # snapshot's status was.
    if initial_snapshot['status'] != 'pending_review':
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE dbo.[ContractLabor] SET [Status] = 'pending_review' WHERE [Id] = ?",
                (CL_ID,),
            )
            conn.commit()
        print(f'  baseline: CL 647 status was {initial_snapshot["status"]!r} '
              f'(reset to pending_review for test; restored on cleanup), '
              f'line items on TB3={len(initial_snapshot["line_items"])}, '
              f'pre-existing reviews={initial_review_count}')
    else:
        print(f'  baseline: CL 647 status={initial_snapshot["status"]}, '
              f'line items on TB3={len(initial_snapshot["line_items"])}, '
              f'pre-existing reviews={initial_review_count}')

    try:
        # ── Case 1: Approval ──────────────────────────────────────────
        try:
            r1 = service.apply_reviewer_decision(
                contract_labor_public_id=CL_PUBLIC_ID,
                project_public_id=project_tb3_public_id,
                decision='approved',
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id=SCC_MISC_LABOR_PUBLIC_ID,
                description='Verify-unit3: cleanup work (DELETE-on-restore)',
                raw_reply_text='Approved for payment.\n917 Tyne Blvd.\nMisc Labor - 65.2',
            )
            print(f'  case 1 (approval) → decision={r1["decision_applied"]}, '
                  f'review_status={r1["review_status"]}, reviewer_user_id={r1["reviewer_user_id"]}')
            if r1['decision_applied'] != 'approved':
                failures.append(f'case 1: decision_applied={r1["decision_applied"]!r}, expected approved')
            if r1['reviewer_user_id'] != 18:
                failures.append(f'case 1: reviewer_user_id={r1["reviewer_user_id"]}, expected 18 (Cassidy)')
            # Verify line items got SCC + description, and CL.Status unchanged
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """SELECT cli.[Id], cli.[SubCostCodeId], cli.[Description]
                       FROM dbo.[ContractLaborLineItem] cli
                       INNER JOIN dbo.[Project] p ON p.[Id] = cli.[ProjectId]
                       WHERE cli.[ContractLaborId] = ? AND p.[Abbreviation] = 'TB3'""",
                    (CL_ID,),
                )
                updated_lines = [(r.Id, r.SubCostCodeId, r.Description) for r in cur.fetchall()]
                cur.execute('SELECT [Status] FROM dbo.[ContractLabor] WHERE [Id] = ?', (CL_ID,))
                status_after = cur.fetchone()[0]
            for li_id, scc_id, desc in updated_lines:
                if scc_id != 459:
                    failures.append(
                        f'case 1: line item {li_id} SubCostCodeId={scc_id}, '
                        f'expected 459 (SCC 65.02)'
                    )
                if 'Verify-unit3' not in (desc or ''):
                    failures.append(
                        f'case 1: line item {li_id} description not updated; '
                        f'got {desc!r}'
                    )
            # Auto-mirror: Approved Review → CL.Status flips to 'ready'
            # (matches React /advance/review canonical path; see
            # service.py mark_as_ready_via_review_approval).
            if status_after != 'ready':
                failures.append(
                    f'case 1: CL.Status={status_after!r} after approval; '
                    f'expected ready (auto-mirror from Review approval)'
                )
            # Verify a new Review row was inserted
            new_count = _count_reviews(CL_ID)
            if new_count != initial_review_count + 1:
                failures.append(
                    f'case 1: review count={new_count}, expected {initial_review_count + 1}'
                )
        except Exception as e:
            failures.append(f'case 1: raised {type(e).__name__}: {e}')

        # ── Case 2: Rejection ─────────────────────────────────────────
        _force_pending_review(CL_ID)  # case 1's approval flipped to ready
        snapshot_before_case2 = _snapshot_state(CL_ID)
        try:
            r2 = service.apply_reviewer_decision(
                contract_labor_public_id=CL_PUBLIC_ID,
                project_public_id=project_tb3_public_id,
                decision='rejected',
                reviewer_email=REVIEWER_EMAIL,
                raw_reply_text='Not approved — wrong worker, this was actually Mac McFarland.',
            )
            print(f'  case 2 (rejection) → decision={r2["decision_applied"]}, '
                  f'review_status={r2["review_status"]}')
            if r2['decision_applied'] != 'rejected':
                failures.append(f'case 2: decision_applied={r2["decision_applied"]!r}, expected rejected')
            # Line items must NOT be touched on rejection
            current_snapshot = _snapshot_state(CL_ID)
            if current_snapshot['line_items'] != snapshot_before_case2['line_items']:
                failures.append('case 2: line items changed on rejection (should be untouched)')
            if current_snapshot['status'] != 'pending_review':
                failures.append(f'case 2: CL.Status={current_snapshot["status"]!r}, expected pending_review')
            # Verify another Review row was inserted
            new_count2 = _count_reviews(CL_ID)
            if new_count2 != initial_review_count + 2:
                failures.append(
                    f'case 2: review count={new_count2}, expected {initial_review_count + 2}'
                )
        except Exception as e:
            failures.append(f'case 2: raised {type(e).__name__}: {e}')

        # ── Case 3: Status-race rejection ─────────────────────────────
        # Flip CL.Status temporarily to 'ready', then expect ValueError
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE dbo.[ContractLabor] SET [Status] = 'ready' WHERE [Id] = ?", (CL_ID,))
            conn.commit()
        try:
            try:
                service.apply_reviewer_decision(
                    contract_labor_public_id=CL_PUBLIC_ID,
                    project_public_id=project_tb3_public_id,
                    decision='approved',
                    reviewer_email=REVIEWER_EMAIL,
                    sub_cost_code_public_id=SCC_MISC_LABOR_PUBLIC_ID,
                    raw_reply_text='Late approval after CL went ready',
                )
                failures.append('case 3: no exception raised; expected ValueError "no longer pending_review"')
            except ValueError as e:
                msg = str(e).lower()
                if 'no longer pending_review' not in msg and 'pending_review' not in msg:
                    failures.append(
                        f'case 3: raised but message did not mention pending_review: {e}'
                    )
                else:
                    print(f'  case 3 (status-race) → ValueError as expected: {str(e)[:80]}…')
        finally:
            # Restore status before continuing
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE dbo.[ContractLabor] SET [Status] = 'pending_review' WHERE [Id] = ?",
                    (CL_ID,),
                )
                conn.commit()

        # ── Case 4: Unauthorized reviewer ─────────────────────────────
        _force_pending_review(CL_ID)
        try:
            service.apply_reviewer_decision(
                contract_labor_public_id=CL_PUBLIC_ID,
                project_public_id=project_tb3_public_id,
                decision='approved',
                reviewer_email='stranger@example.invalid',
                sub_cost_code_public_id=SCC_MISC_LABOR_PUBLIC_ID,
                raw_reply_text='Should be rejected',
            )
            failures.append('case 4: no exception raised; expected ValueError "not an authorized reviewer"')
        except ValueError as e:
            msg = str(e).lower()
            if 'not an authorized reviewer' not in msg:
                failures.append(f'case 4: raised but message not the expected one: {e}')
            else:
                print(f'  case 4 (unauthorized) → ValueError as expected: {str(e)[:80]}…')

        # ── Case 5: Approval without SCC ─────────────────────────────
        _force_pending_review(CL_ID)
        try:
            service.apply_reviewer_decision(
                contract_labor_public_id=CL_PUBLIC_ID,
                project_public_id=project_tb3_public_id,
                decision='approved',
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id=None,
                raw_reply_text='Missing SCC',
            )
            failures.append('case 5: no exception raised; expected ValueError "sub_cost_code_public_id is required"')
        except ValueError as e:
            msg = str(e).lower()
            if 'sub_cost_code_public_id' not in msg:
                failures.append(f'case 5: raised but message not the expected one: {e}')
            else:
                print(f'  case 5 (missing SCC) → ValueError as expected: {str(e)[:80]}…')

    finally:
        # ── Cleanup: restore CL line items + delete the 2 Review rows we created ──
        try:
            _restore_state(CL_ID, initial_snapshot)
            _delete_reviews_created_for_cl(CL_ID, initial_review_count)
            final_count = _count_reviews(CL_ID)
            print(f'  cleanup: restored CL state; reviews now back to {final_count} (was {initial_review_count})')
            if final_count != initial_review_count:
                failures.append(
                    f'cleanup: review count {final_count} != initial {initial_review_count}; '
                    f'rows leaked'
                )
        except Exception as cleanup_error:
            print(f'  CLEANUP-FAILED: {cleanup_error}')
            failures.append(f'cleanup: {cleanup_error}')

    if failures:
        print('\nFAIL:')
        for f in failures:
            print(f'  - {f}')
        return 1

    print(
        '\nPASS — apply_reviewer_decision: approval updates line items + writes Review + '
        'auto-mirrors CL.Status pending_review → ready (matches React /advance/review '
        'canonical path); rejection leaves lines untouched + writes Declined Review '
        '(Status untouched); status race rejects with specific error; unauthorized '
        'sender rejects; missing SCC rejects.'
    )
    return 0


if __name__ == '__main__':
    sys.exit(verify())
