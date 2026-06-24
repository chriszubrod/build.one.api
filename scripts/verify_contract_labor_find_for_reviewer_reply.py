"""Regression check on FindContractLaborForReviewerReply (join-table redesign).

Unit 2 of the contract_labor_specialist reviewer-reply branch build,
post-/simplify redesign. The sproc binds an inbound reply's
ConversationId back to its (CL, Project) pair via the
ContractLaborNotification join table — no subject parsing. Fuzzy
fallback handles non-Outlook clients that lose ConversationId.

Cases:
  1. Email 2944 (Ricky Moreno TB3 6/18) — PRIMARY conv path via backfilled join row
  2. Email 2549 (Mac McFarland HA 5/26) — PRIMARY conv path via backfilled join row
  3. Email 2091 (Selvin Cordova TB3 5/11) — PRIMARY conv path via backfilled join row
  4. Bogus conv id, no hints → null
  5. Bogus conv id + full fuzzy hints (Ricky Moreno TB3 2026-06-18) → fuzzy match CL 647
  6. Bogus conv id + only partial hints → null
  7. Bogus conv id + hints with bad project_abbr → null
  8. Status-race regression: CL not in pending_review status STILL resolves
     (mirrors BillRepo.find_for_reviewer_reply — Unit 3 enforces)

Case 8 synthesizes a temporary status flip on CL 647 then restores it.
Read-only otherwise. Existing 3 real fixtures were backfilled by the
Unit 2 migration's one-time scan of outbound CL EmailMessages.

Run:
    .venv/bin/python scripts/verify_contract_labor_find_for_reviewer_reply.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context


# Real conversation_ids (verified via SELECT against prod)
CONV_RICKY_TB3 = 'AAQkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQAQAAFBz39q75RBohW6KLyL2v8='
CONV_MAC_HA    = 'AAQkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQAQAA32Xmq2z-pIi8K89ygeTXg='
CONV_SELVIN_TB3 = 'AAQkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQAQAAw2glBZe-5Bih5S1mrwVo8='

BOGUS_CONV = 'BOGUS-CONV-ID-VERIFY-UNIT2'


def _check(label: str, result: dict | None, *, expected_cl_id: int | None,
           expected_project_abbr: str | None, expected_match_kind: str | None,
           failures: list) -> None:
    print(f'  {label}')
    if expected_cl_id is None:
        if result is not None:
            failures.append(f'{label}: expected null, got {result!r}')
        else:
            print(f'    → null (as expected)')
        return
    if result is None:
        failures.append(f'{label}: returned null; expected CL {expected_cl_id} / {expected_project_abbr}')
        return
    print(f'    → CL={result["contract_labor_id"]} ({result["contract_labor_public_id"][:8]}…) '
          f'Project={result["project_id"]} ({result["project_abbreviation"]}) '
          f'match_kind={result["match_kind"]}')
    if result['contract_labor_id'] != expected_cl_id:
        failures.append(f'{label}: contract_labor_id={result["contract_labor_id"]}, expected {expected_cl_id}')
    if result['project_abbreviation'] != expected_project_abbr:
        failures.append(f'{label}: project_abbreviation={result["project_abbreviation"]!r}, expected {expected_project_abbr!r}')
    if result['match_kind'] != expected_match_kind:
        failures.append(f'{label}: match_kind={result["match_kind"]!r}, expected {expected_match_kind!r}')


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.contract_labor.business.service import ContractLaborService
    service = ContractLaborService()

    print('=== FindContractLaborForReviewerReply (join-table) contract check ===')
    failures: list = []

    # Cases 1-3: PRIMARY conv path via backfilled join rows
    r1 = service.find_for_reviewer_reply(conversation_id=CONV_RICKY_TB3)
    _check('case 1 (Ricky Moreno TB3, conversation)', r1,
           expected_cl_id=647, expected_project_abbr='TB3',
           expected_match_kind='conversation', failures=failures)

    r2 = service.find_for_reviewer_reply(conversation_id=CONV_MAC_HA)
    _check('case 2 (Mac McFarland HA, conversation)', r2,
           expected_cl_id=612, expected_project_abbr='HA',
           expected_match_kind='conversation', failures=failures)

    r3 = service.find_for_reviewer_reply(conversation_id=CONV_SELVIN_TB3)
    _check('case 3 (Selvin Cordova TB3, conversation)', r3,
           expected_cl_id=566, expected_project_abbr='TB3',
           expected_match_kind='conversation', failures=failures)

    # Case 4: bogus conv id, no hints
    r4 = service.find_for_reviewer_reply(conversation_id=BOGUS_CONV)
    _check('case 4 (bogus conv, no hints)', r4,
           expected_cl_id=None, expected_project_abbr=None,
           expected_match_kind=None, failures=failures)

    # Case 5: fuzzy fallback
    r5 = service.find_for_reviewer_reply(
        conversation_id=BOGUS_CONV,
        worker_hint='Ricky Moreno', project_hint='TB3', work_date_hint='2026-06-18',
    )
    _check('case 5 (bogus conv + full fuzzy hints)', r5,
           expected_cl_id=647, expected_project_abbr='TB3',
           expected_match_kind='fuzzy', failures=failures)

    # Case 6: partial hints
    r6 = service.find_for_reviewer_reply(
        conversation_id=BOGUS_CONV,
        worker_hint='Ricky Moreno', project_hint='TB3',
    )
    _check('case 6 (bogus conv + partial hints)', r6,
           expected_cl_id=None, expected_project_abbr=None,
           expected_match_kind=None, failures=failures)

    # Case 7: hints with bad project_abbr
    r7 = service.find_for_reviewer_reply(
        conversation_id=BOGUS_CONV,
        worker_hint='Ricky Moreno', project_hint='ZZZ-NOT-A-PROJECT', work_date_hint='2026-06-18',
    )
    _check('case 7 (bogus conv + hints, no project match)', r7,
           expected_cl_id=None, expected_project_abbr=None,
           expected_match_kind=None, failures=failures)

    # Case 8: status-race regression — CL not in pending_review STILL resolves
    from shared.database import get_connection
    original_status = None
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT [Status] FROM dbo.[ContractLabor] WHERE [Id] = 647')
            original_status = cur.fetchone()[0]
            cur.execute("UPDATE dbo.[ContractLabor] SET [Status] = 'ready' WHERE [Id] = 647")
            conn.commit()

        r8 = service.find_for_reviewer_reply(conversation_id=CONV_RICKY_TB3)
        print(f'  case 8 (status-race regression: CL Status=ready)')
        if r8 is None:
            failures.append(
                'case 8: returned null; expected CL 647 (sproc should NOT '
                'filter by Status — Unit 3 apply layer enforces).'
            )
        else:
            print(f'    → CL={r8["contract_labor_id"]} Project={r8["project_abbreviation"]} '
                  f'status={r8["contract_labor_status"]} match_kind={r8["match_kind"]}')
            if r8['contract_labor_id'] != 647:
                failures.append(f'case 8: contract_labor_id={r8["contract_labor_id"]}, expected 647')
            if r8['contract_labor_status'] != 'ready':
                failures.append(
                    f'case 8: contract_labor_status={r8["contract_labor_status"]!r}, '
                    f'expected ready'
                )
    finally:
        try:
            if original_status is not None:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        'UPDATE dbo.[ContractLabor] SET [Status] = ? WHERE [Id] = 647',
                        (original_status,),
                    )
                    conn.commit()
                print(f'  case 8 cleanup: CL 647 status restored to {original_status!r}')
        except Exception as cleanup_error:
            print(f'  CLEANUP-FAILED case 8: {cleanup_error}')
            failures.append(f'case 8 cleanup: {cleanup_error}')

    if failures:
        print('\nFAIL:')
        for f in failures:
            print(f'  - {f}')
        return 1

    print(
        '\nPASS — FindContractLaborForReviewerReply (join-table): 3 backfilled '
        'real conv-path matches, null on bogus conv, fuzzy fallback with all 3 '
        'hints, null on partial/bad hints, CL status race resolves (Unit 3 '
        'enforces). No subject parsing at lookup time.'
    )
    return 0


if __name__ == '__main__':
    sys.exit(verify())
