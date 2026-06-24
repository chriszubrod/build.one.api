"""End-to-end tool-path test of the contract_labor_specialist reviewer-reply branch.

Unit 4 of the contract_labor_specialist build (TODO.md line 204, item 5b).
Exercises the full chain the live agent would run, but via direct service
calls — no Claude API, no agent loop, no production side-effects beyond
the single ContractLabor row mutated + Review row created + cleanup.

Case 1: Email 2549 (Mac McFarland HA 2026-05-26, single-SCC approval)
  Cassidy's reply:
    "Approved for payment.\n\n206 Haverford Ave.\nMisc. Labor - 65.2"
  Expected flow:
    1. find_contract_labor_by_conversation_id(Email 2549's conv) → CL 612 / Project HA
    2. find_sub_cost_code_for_reply(hint='65.2')      → SCC 459 (65.02 Misc Labor)
    3. apply_contract_labor_reviewer_decision(approved) → Review row + line items updated
    4. Cleanup: restore line items, delete the Review row

Case 2: Email 2944 (Ricky Moreno TB3 2026-06-18, multi-SCC reply)
  Cassidy's reply:
    "Approved for payment.\n\n917 Tyne Blvd.\nCleaning - 62.0\n(...)\nTrim Labor - 44.0\n(...)"
  Expected flow:
    1. find_contract_labor_by_conversation_id(Email 2944's conv) → CL 647 / Project TB3
    2. Multi-SCC detection HAPPENS AT THE EMAIL_SPECIALIST PROMPT LAYER
       (Step 1bx step 3 — gates before delegation). This verify cannot
       exercise that gate without a live agent run; instead it confirms
       the underlying find_contract_labor_by_conversation_id resolves the
       fixture cleanly, leaving the multi-SCC gate as a prompt-level
       responsibility tested by manual prompt review.

Read/write — case 1 mutates CL 612 line items + writes a Review row,
both reverted in finally. Cases 2 is read-only.

Run:
    .venv/bin/python scripts/verify_contract_labor_reviewer_reply_e2e.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


# ── Case 1: Mac McFarland HA single-SCC fixture ─────────────────────
CASE1_INBOUND_PUBLIC_ID = 'C8ED5191'  # Email 2549 (Re: Contract Labor - Mac McFarland - HA - 2026-05-26)
CASE1_CONV_ID = 'AAQkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQAQAA32Xmq2z-pIi8K89ygeTXg='
CASE1_EXPECTED_CL_ID = 612
CASE1_EXPECTED_PROJECT_ABBR = 'HA'
CASE1_PM_EMAIL = 'cassidy@rogersbuild.com'
CASE1_SCC_HINT = '65.2'   # → SCC 65.02 Misc Labor
CASE1_RAW_REPLY = (
    "Approved for payment.\n\n206 Haverford Ave.\nMisc. Labor - 65.2\n"
)

# ── Case 2: Ricky Moreno TB3 multi-SCC fixture (read-only) ──────────
CASE2_CONV_ID = 'AAQkAGE2YjU2M2E5LWQ2MjQtNGRiNC04ODI2LWY4MDY3NjQ5MDExMQAQAAFBz39q75RBohW6KLyL2v8='
CASE2_EXPECTED_CL_ID = 647
CASE2_EXPECTED_PROJECT_ABBR = 'TB3'


def _resolve_inbound_email_public_id(prefix: str) -> str:
    """Find an inbound EmailMessage by PublicId prefix (8-char hex)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP 1 CAST(PublicId AS NVARCHAR(50))
               FROM dbo.[EmailMessage]
               WHERE CAST(PublicId AS NVARCHAR(50)) LIKE ?""",
            (f"{prefix}%",),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"No EmailMessage with PublicId prefix {prefix!r}")
        return str(row[0])


def _snapshot_state(cl_id: int, project_abbr: str) -> dict:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute('SELECT [Status] FROM dbo.[ContractLabor] WHERE [Id] = ?', (cl_id,))
        status = cur.fetchone()[0]
        cur.execute(
            """SELECT cli.[Id], cli.[SubCostCodeId], cli.[Description]
               FROM dbo.[ContractLaborLineItem] cli
               INNER JOIN dbo.[Project] p ON p.[Id] = cli.[ProjectId]
               WHERE cli.[ContractLaborId] = ? AND p.[Abbreviation] = ?
               ORDER BY cli.[Id]""",
            (cl_id, project_abbr),
        )
        lines = [(r.Id, r.SubCostCodeId, r.Description) for r in cur.fetchall()]
        cur.execute('SELECT COUNT(*) FROM dbo.[Review] WHERE [ContractLaborId] = ?', (cl_id,))
        review_count = int(cur.fetchone()[0])
    return {'status': status, 'lines': lines, 'review_count': review_count}


def _restore_state(cl_id: int, snapshot: dict) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE dbo.[ContractLabor] SET [Status] = ? WHERE [Id] = ?',
            (snapshot['status'], cl_id),
        )
        for li_id, scc_id, desc in snapshot['lines']:
            cur.execute(
                """UPDATE dbo.[ContractLaborLineItem]
                   SET [SubCostCodeId] = ?, [Description] = ?
                   WHERE [Id] = ?""",
                (scc_id, desc, li_id),
            )
        # Delete any Review rows we created during the test (rows added
        # after the snapshot's review_count).
        cur.execute(
            'SELECT [Id] FROM dbo.[Review] WHERE [ContractLaborId] = ? ORDER BY [Id] ASC',
            (cl_id,),
        )
        ids = [r.Id for r in cur.fetchall()]
        if len(ids) > snapshot['review_count']:
            for review_id in ids[snapshot['review_count']:]:
                cur.execute('DELETE FROM dbo.[Review] WHERE [Id] = ?', (review_id,))
        conn.commit()


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.contract_labor.business.service import ContractLaborService
    from entities.sub_cost_code.business.service import SubCostCodeService
    service = ContractLaborService()
    scc_service = SubCostCodeService()

    print('=== contract_labor_specialist reviewer-reply end-to-end ===')
    failures: list = []

    # ── Case 1: Single-SCC approval ─────────────────────────────────
    print('\n  case 1: Email 2549 — Mac McFarland HA single-SCC approval')

    # Step 1: find_contract_labor_by_conversation_id (PRIMARY path)
    found = service.find_for_reviewer_reply(conversation_id=CASE1_CONV_ID)
    if found is None:
        failures.append('case 1.find: no match for conv_id (expected CL 612 / HA)')
        return _report(failures)
    print(f'    step 1 (find): CL={found["contract_labor_id"]} '
          f'Project={found["project_abbreviation"]} match_kind={found["match_kind"]}')
    if found['contract_labor_id'] != CASE1_EXPECTED_CL_ID:
        failures.append(f'case 1.find: CL={found["contract_labor_id"]}, expected {CASE1_EXPECTED_CL_ID}')
    if found['project_abbreviation'] != CASE1_EXPECTED_PROJECT_ABBR:
        failures.append(f'case 1.find: project_abbr={found["project_abbreviation"]}, expected {CASE1_EXPECTED_PROJECT_ABBR}')

    # Step 2: find_sub_cost_code_for_reply
    scc_candidates = scc_service.find_for_reply(hint=CASE1_SCC_HINT)
    if not scc_candidates:
        failures.append(f'case 1.scc: no candidates for hint {CASE1_SCC_HINT!r}')
        return _report(failures)
    top_scc = scc_candidates[0]['sub_cost_code']
    print(f'    step 2 (SCC):  {top_scc["number"]} ({top_scc["name"]}), '
          f'confidence={scc_candidates[0]["confidence"]}')

    # Step 3: snapshot CL 612, apply
    snapshot = _snapshot_state(CASE1_EXPECTED_CL_ID, CASE1_EXPECTED_PROJECT_ABBR)
    print(f'    pre-apply baseline: status={snapshot["status"]}, '
          f'lines on HA={len(snapshot["lines"])}, reviews={snapshot["review_count"]}')

    try:
        inbound_em_public_id = _resolve_inbound_email_public_id(CASE1_INBOUND_PUBLIC_ID)
        result = service.apply_reviewer_decision(
            contract_labor_public_id=found['contract_labor_public_id'],
            project_public_id=found['project_public_id'],
            decision='approved',
            reviewer_email=CASE1_PM_EMAIL,
            sub_cost_code_public_id=top_scc['public_id'],
            description='Verify-unit4-e2e: applied via reviewer-reply (DELETE-on-restore)',
            raw_reply_text=CASE1_RAW_REPLY,
            reviewer_email_message_public_id=inbound_em_public_id,
        )
        print(f'    step 3 (apply): decision={result["decision_applied"]} '
              f'review_status={result["review_status"]} '
              f'reviewer_user_id={result["reviewer_user_id"]}')
        if result['decision_applied'] != 'approved':
            failures.append(f'case 1.apply: decision_applied={result["decision_applied"]!r}')

        # Verify line items updated + status unchanged + Review row added
        after = _snapshot_state(CASE1_EXPECTED_CL_ID, CASE1_EXPECTED_PROJECT_ABBR)
        if after['status'] != 'pending_review':
            failures.append(f'case 1.post: status={after["status"]!r}, expected pending_review')
        if after['review_count'] != snapshot['review_count'] + 1:
            failures.append(
                f'case 1.post: review_count={after["review_count"]}, '
                f'expected {snapshot["review_count"] + 1}'
            )
        # Every line must now have SCC 459 + the verify description
        for li_id, scc_id, desc in after['lines']:
            if scc_id != 459:
                failures.append(f'case 1.post: line {li_id} SCC={scc_id}, expected 459')
            if 'Verify-unit4-e2e' not in (desc or ''):
                failures.append(f'case 1.post: line {li_id} desc={desc!r}, not updated')

    finally:
        try:
            _restore_state(CASE1_EXPECTED_CL_ID, snapshot)
            after_restore = _snapshot_state(CASE1_EXPECTED_CL_ID, CASE1_EXPECTED_PROJECT_ABBR)
            print(f'    cleanup: lines + status + reviews restored to baseline '
                  f'(reviews now {after_restore["review_count"]})')
            if after_restore['review_count'] != snapshot['review_count']:
                failures.append('case 1.cleanup: review count not restored')
        except Exception as cleanup_error:
            print(f'    CLEANUP-FAILED case 1: {cleanup_error}')
            failures.append(f'case 1.cleanup: {cleanup_error}')

    # ── Case 2: Multi-SCC fixture (read-only conv lookup) ───────────
    # The multi-SCC gate is implemented in email_specialist's Step 1bx
    # prompt — testing it end-to-end requires a live agent run, which the
    # constraints forbid. This case just confirms that the underlying
    # find_contract_labor_by_conversation_id resolves Email 2944's
    # fixture cleanly so the prompt-layer multi-SCC gate would have
    # something to gate ON (rather than the lookup itself missing).
    print('\n  case 2: Email 2944 — Ricky Moreno TB3 multi-SCC fixture (read-only)')
    found_c2 = service.find_for_reviewer_reply(conversation_id=CASE2_CONV_ID)
    if found_c2 is None:
        failures.append('case 2.find: no match for conv_id (expected CL 647 / TB3)')
    else:
        print(f'    find: CL={found_c2["contract_labor_id"]} '
              f'Project={found_c2["project_abbreviation"]} match_kind={found_c2["match_kind"]}')
        if found_c2['contract_labor_id'] != CASE2_EXPECTED_CL_ID:
            failures.append(f'case 2.find: CL={found_c2["contract_labor_id"]}, expected {CASE2_EXPECTED_CL_ID}')
        if found_c2['project_abbreviation'] != CASE2_EXPECTED_PROJECT_ABBR:
            failures.append(f'case 2.find: project_abbr={found_c2["project_abbreviation"]}, expected {CASE2_EXPECTED_PROJECT_ABBR}')
        print('    multi-SCC gate is a PROMPT-LAYER responsibility (email_specialist '
              "Step 1bx step 3) — not exercised here; manual prompt review covers it.")

    return _report(failures)


def _report(failures: list) -> int:
    if failures:
        print('\nFAIL:')
        for f in failures:
            print(f'  - {f}')
        return 1
    print(
        '\nPASS — reviewer-reply end-to-end: case 1 (single-SCC) applied + reverted, '
        'case 2 (multi-SCC fixture) resolves via find_for_reviewer_reply '
        '(multi-SCC gate is prompt-layer, tested via prompt review).'
    )
    return 0


if __name__ == '__main__':
    sys.exit(verify())
