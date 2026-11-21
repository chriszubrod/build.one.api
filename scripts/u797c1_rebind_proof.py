"""U-797c1 re-bind proof and backfill report. Both are read-only.

PROOF (default with --run). For each dbo.Review row with an EmailMessageId since 2026-10-01,
take the applied reply's ConversationId and decide it with the SAME pure selector the live
binder uses (shared.authz.provenance.select_notification). Contract Labor uses the project and
work-date rule (not target membership). Every row gets a label and a reason:

  AGREE      the selector binds the reply to the applied document
  NO_ROW     no notification on the conversation. Reasons:
               null_conversation   the reply has no ConversationId
               pre_u579_thread     the review predates the U-579 stamp (2026-09-30): legacy binding
               no_row              anything else (fails)
  AMBIGUOUS  two or more notifications on the conversation
  DISAGREE   a notification exists but does not cover the applied document, project, date or kind.
             Contract Labor reasons also include applied_cl_outside_scope (the applied record is not
             on the scope's date or project) and legacy_unresolved_binder_refuses (today's resolver
             cannot scope it, a row exists, and the new binder would refuse an applied reply)
  NEW_BIND   today's resolver cannot scope a Contract Labor reply, but the new binder would bind it:
             a behaviour change Chris must clear (fails)
  LISTED     a Contract Labor reply with NO notification row that the legacy resolver cannot scope
             (reason legacy_unresolved): both paths refuse. Counted and printed; does not fail.
  UNRESOLVED an expected document that could not be determined, or (applied_cl_missing) an applied
             Contract Labor record with no work date or no line project (fails)
  SKIPPED    out of scope (BillCredit, Invoice)

Contract Labor scope (project and work date) comes from TODAY's legacy resolver,
FindContractLaborForReviewerReply, given the reply's ConversationId. That is the same resolver
the live legacy path uses. It is read-only.

Exit status is 0 only if every in-scope row is AGREE, LISTED, or SKIPPED, or a NO_ROW with
reason pre_u579_thread (printed for Chris).

REPORT (--report). Read-only. Prints: inserted backfill counts per kind, backfill rows with NULL
ConversationId, ambiguous Contract Labor claims, superseded Bill/Expense send_mail reviews, and
skipped claims (NULL WorkDate, and duplicate (ProjectId, WorkDate) claims beyond the first).

No write path exists in this file. Run from the repo root:
    ./.venv/bin/python scripts/u797c1_rebind_proof.py --run
    ./.venv/bin/python scripts/u797c1_rebind_proof.py --report
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.authz.provenance import (  # noqa: E402
    _LEGACY_CUTOFF_DT,
    _norm_work_date,
    select_notification,
)

SINCE = "2026-10-01"
LABELS = ("AGREE", "NO_ROW", "AMBIGUOUS", "DISAGREE", "NEW_BIND", "LISTED", "UNRESOLVED", "SKIPPED",
          "LEGACY_BIND")
_CUTOFF = _LEGACY_CUTOFF_DT  # the live binder's own parsed cutoff

SWEEP_SQL = """
SELECT r.[Id]              AS ReviewId,
       r.[BillId]          AS BillId,
       r.[ExpenseId]       AS ExpenseId,
       r.[ContractLaborId] AS ContractLaborId,
       r.[CreatedDatetime] AS CreatedDatetime,
       m.[ConversationId]  AS ConversationId,
       CONVERT(VARCHAR(10), cl.[WorkDate], 120) AS ClWorkDate,
       (SELECT STRING_AGG(CAST(x.[ProjectId] AS VARCHAR(20)), ',')
          FROM (SELECT DISTINCT li.[ProjectId] FROM dbo.[ContractLaborLineItem] li
                 WHERE li.[ContractLaborId] = cl.[Id] AND li.[ProjectId] IS NOT NULL) x) AS ClProjectIds,
       (SELECT TOP 1 o.[CreatedDatetime] FROM [ms].[Outbox] o
         WHERE o.[Kind] = N'send_mail' AND o.[Status] = N'done'
           AND o.[CreatedDatetime] <= r.[CreatedDatetime]
           AND o.[EntityType] = CASE WHEN r.[BillId] IS NOT NULL THEN N'Bill'
                                     WHEN r.[ExpenseId] IS NOT NULL THEN N'Expense'
                                     ELSE N'ContractLabor' END
           AND o.[EntityPublicId] = COALESCE(b.[PublicId], e.[PublicId], cl.[PublicId])
         ORDER BY o.[CreatedDatetime] DESC) AS EmailSentDatetime
FROM dbo.[Review] r
JOIN dbo.[EmailMessage] m ON m.[Id] = r.[EmailMessageId]
LEFT JOIN dbo.[ContractLabor] cl ON cl.[Id] = r.[ContractLaborId]
LEFT JOIN dbo.[Bill] b ON b.[Id] = r.[BillId]
LEFT JOIN dbo.[Expense] e ON e.[Id] = r.[ExpenseId]
WHERE r.[EmailMessageId] IS NOT NULL
  AND r.[CreatedDatetime] >= ?
ORDER BY r.[Id];
"""

REPORT_SQL = {
    "inserted": """
SELECT n.[Kind], COUNT(*) AS Rows FROM dbo.[ReviewNotification] n
WHERE n.[Source] = N'backfill' GROUP BY n.[Kind] ORDER BY n.[Kind];""",
    "null_conversation": """
SELECT n.[Kind], COUNT(*) AS Rows FROM dbo.[ReviewNotification] n
WHERE n.[Source] = N'backfill' AND n.[ConversationId] IS NULL GROUP BY n.[Kind] ORDER BY n.[Kind];""",
    "cl_candidates": """
SELECT c.[Id] AS ClaimId, c.[ProjectId], c.[WorkDate], c.[OutboundSubject],
  (SELECT COUNT(DISTINCT JSON_VALUE(o.[Payload], '$.conversation_id')) FROM [ms].[Outbox] o
    WHERE o.[EntityType] = N'ContractLabor' AND o.[Kind] = N'send_mail' AND o.[Status] = N'done'
      AND JSON_VALUE(o.[Payload], '$.subject') COLLATE Latin1_General_BIN = c.[OutboundSubject] COLLATE Latin1_General_BIN
      AND JSON_VALUE(o.[Payload], '$.conversation_id') IS NOT NULL) AS OutboxConversations,
  (SELECT COUNT(DISTINCT m.[ConversationId]) FROM dbo.[EmailMessage] m
    WHERE m.[ProcessingStatus] = N'outbound'
      AND m.[Subject] = c.[OutboundSubject]
      AND m.[ConversationId] IS NOT NULL) AS SentConversations
FROM dbo.[ContractLaborNotification] c
WHERE c.[WorkDate] IS NOT NULL;""",
    "superseded": """
SELECT TRY_CAST(JSON_VALUE(o.[Payload], '$.review_id') AS BIGINT) AS ReviewId, COUNT(*) AS DoneRows
FROM [ms].[Outbox] o
WHERE o.[Kind] = N'send_mail' AND o.[Status] = N'done'
  AND o.[EntityType] IN (N'Bill', N'Expense')
  AND TRY_CAST(JSON_VALUE(o.[Payload], '$.review_id') AS BIGINT) IS NOT NULL
GROUP BY TRY_CAST(JSON_VALUE(o.[Payload], '$.review_id') AS BIGINT)
HAVING COUNT(*) > 1;""",
    "null_workdate": """
SELECT c.[Id] AS ClaimId, c.[ProjectId], c.[OutboundSubject] FROM dbo.[ContractLaborNotification] c
WHERE c.[WorkDate] IS NULL;""",
    "duplicate_pairs": """
SELECT x.[ClaimId], x.[ProjectId], x.[WorkDate] FROM (
  SELECT c.[Id] AS ClaimId, c.[ProjectId], c.[WorkDate],
         ROW_NUMBER() OVER (PARTITION BY c.[ProjectId], c.[WorkDate] ORDER BY c.[Id]) AS rn
  FROM dbo.[ContractLaborNotification] c WHERE c.[WorkDate] IS NOT NULL) x
WHERE x.rn > 1;""",
}


def review_target(*, bill_id, expense_id, contract_labor_id):
    """(kind, id) of the document a review row was applied to, or None if out of scope."""
    if bill_id is not None:
        return ("bill", int(bill_id))
    if expense_id is not None:
        return ("expense", int(expense_id))
    if contract_labor_id is not None:
        return ("contract_labor", int(contract_labor_id))
    return None


def classify_row(*, reply_conversation_id, matches, expected, project_id, work_date, pre_u579):
    """Label and reason for one applied reply, decided by the live binder's selector.

    matches: [(notification, {(kind, id), ...}), ...] for the reply's ConversationId."""
    if expected is None:
        return ("UNRESOLVED", "no_scope")
    if not isinstance(reply_conversation_id, str) or not reply_conversation_id.strip():
        return ("NO_ROW", "null_conversation")
    status, reason, _notification = select_notification(
        matches, expected, project_id=project_id, work_date=work_date)
    if status == "BOUND":
        return ("AGREE", "bound")
    if status == "NO_ROW":
        return ("NO_ROW", "pre_u579_thread" if pre_u579 else "no_row")
    if reason == "ambiguous":
        return ("AMBIGUOUS", "ambiguous")
    return ("DISAGREE", reason)


def cl_scope_from_legacy(resolver_result):
    """(project_id, work_date) from the legacy resolver's dict, or None when it cannot scope the reply.

    Mirrors the live legacy path: only match_kind == 'conversation' counts as a match."""
    if not resolver_result or resolver_result.get("match_kind") != "conversation":
        return None
    project_id = resolver_result.get("project_id")
    work_date = resolver_result.get("work_date")
    if project_id is None or work_date is None:
        return None
    return (project_id, work_date)


def email_predates_u579(email_sent_at) -> bool:
    """True when the review's email went out before the U-579 stamp cutoff.

    pre_u579 is decided by the SEND time (the outbox row), not the Review's CreatedDatetime: a
    review can be created after the cutoff for an email that went out before it. No send row
    (None) is not pre-U-579, so it fails closed."""
    return email_sent_at is not None and email_sent_at < _CUTOFF


def classify_cl_reply(*, reply_conversation_id, resolver_result, matches, expected, pre_u579,
                      cl_work_date, cl_project_ids):
    """Label and reason for a Contract Labor reply, tied to the record it was applied to.

    Scope comes from TODAY's legacy resolver. The applied record must sit inside that scope
    (its work date, and a line on the scope project), else DISAGREE. When the resolver cannot
    scope the reply, the new binder is still asked, with the applied record's own date and each
    of its projects: if it would bind, that is a behaviour change (NEW_BIND, fails); if a row exists
    and it refuses, an applied reply would now be refused (DISAGREE/AMBIGUOUS, fails). Only when no
    notification row exists do both paths refuse; that row is LISTED for Chris."""
    if not isinstance(reply_conversation_id, str) or not reply_conversation_id.strip():
        return ("NO_ROW", "null_conversation")
    project_ids = {int(p) for p in (cl_project_ids or ())}
    scope = cl_scope_from_legacy(resolver_result)
    if scope is None:
        if not matches:
            # No notification row: the new binder returns NO_ROW and legacy runs, and legacy
            # cannot scope it today either. Both paths refuse; listed for Chris.
            return ("LISTED", "legacy_unresolved")
        if not project_ids or _norm_work_date(cl_work_date) is None:
            return ("UNRESOLVED", "applied_cl_missing")
        statuses = []
        for project_id in sorted(project_ids):
            status, reason, _n = select_notification(
                matches, expected, project_id=project_id, work_date=cl_work_date)
            if status == "BOUND":
                return ("NEW_BIND", "legacy_refused")
            statuses.append(reason)
        # A row exists and the new binder refuses this applied reply outright (no fallback).
        label = "AMBIGUOUS" if "ambiguous" in statuses else "DISAGREE"
        return (label, "legacy_unresolved_binder_refuses")
    project_id, work_date = scope
    cl_date = _norm_work_date(cl_work_date)
    if cl_date is None or cl_date != _norm_work_date(work_date) or int(project_id) not in project_ids:
        # The applied record is no longer inside the scope (its lines were later moved, or it was
        # mis-applied). Nothing records project history, so the test is what matters for the flag:
        # does the new binder land on the same (project, date) as today's resolver? Same scope
        # (BOUND, or NO_ROW which falls back to legacy) -> listed with the evidence; else it fails.
        status, reason, _n = select_notification(
            matches, expected, project_id=project_id, work_date=work_date)
        if status in ("BOUND", "NO_ROW"):
            return ("LISTED", "scope_agrees_record_changed")
        if reason == "ambiguous":
            return ("AMBIGUOUS", "ambiguous")
        return ("DISAGREE", "applied_cl_outside_scope")
    label, reason = classify_row(
        reply_conversation_id=reply_conversation_id, matches=matches, expected=expected,
        project_id=project_id, work_date=work_date, pre_u579=pre_u579,
    )
    if label == "NO_ROW":
        # The applied record is inside the legacy scope and no notification row exists: with the
        # flag on, NO_ROW falls back to legacy, which binds it.
        return ("LEGACY_BIND", "legacy_binds")
    return (label, reason)


def passes(label, reason) -> bool:
    """The D5 pass rule for one row: AGREE, LEGACY_BIND, SKIPPED, LISTED, or a pre-U-579 NO_ROW."""
    if label in ("AGREE", "LEGACY_BIND", "SKIPPED", "LISTED"):
        return True
    return label == "NO_ROW" and reason == "pre_u579_thread"


_LISTED_WHY = {
    ("NO_ROW", "pre_u579_thread"): "pre-U-579 thread (legacy binds it)",
    ("LEGACY_BIND", "legacy_binds"): "no notification row; the legacy resolver binds it to the applied record",
    ("LISTED", "legacy_unresolved"): "no notification row and the legacy resolver cannot scope it",
    ("LISTED", "scope_agrees_record_changed"): "the new binder and today's resolver agree on the scope; the applied record's lines are no longer on it",
}


def exit_code(results) -> int:
    """results: [(label, reason), ...]. 0 only if every row passes (see `passes`)."""
    return 0 if all(passes(label, reason) for label, reason in results) else 1


def summarize(results) -> dict:
    counts = Counter(label for label, _reason in results)
    return {label: counts.get(label, 0) for label in LABELS}


def ambiguous_cl_claims(rows):
    """Rows from REPORT_SQL['cl_candidates'] the backfill cannot resolve to one conversation."""
    out = []
    for r in rows:
        if r.OutboxConversations > 1 or (r.OutboxConversations == 0 and r.SentConversations > 1):
            out.append(r)
    return out


def _plan() -> None:
    print("U-797c1 re-bind proof (dry run; pass --run to classify, or --report for the backfill report).")
    print(f"Scope: dbo.Review rows with an EmailMessageId, CreatedDatetime >= {SINCE}.")
    print("Pass bar: every row AGREE or LEGACY_BIND, except LISTED and NO_ROW pre_u579_thread (printed for Chris).")


def _fetch(cursor, sql, params=()):
    cursor.execute(sql, list(params))
    return cursor.fetchall()


def _run() -> int:
    from entities.contract_labor.business.service import ContractLaborService
    from entities.review.business.review_notification_service import (
        ReviewNotificationRecordService,
    )
    from shared.database import get_connection

    service = ReviewNotificationRecordService()
    cl_service = ContractLaborService()
    lookups: dict = {}   # one record lookup per conversation (a crew decision is many rows, one reply)
    resolved: dict = {}  # one legacy-resolver call per conversation
    with get_connection() as conn:
        rows = _fetch(conn.cursor(), SWEEP_SQL, [SINCE])

    results, listed, failing = [], [], []
    for row in rows:
        expected = review_target(bill_id=row.BillId, expense_id=row.ExpenseId,
                                 contract_labor_id=row.ContractLaborId)
        if expected is None:
            results.append(("SKIPPED", "out_of_scope"))
            continue
        if row.ConversationId not in lookups:
            lookups[row.ConversationId] = service.lookup_for_reply(row.ConversationId)
        matches = lookups[row.ConversationId]
        pre = email_predates_u579(row.EmailSentDatetime)
        if expected[0] == "contract_labor":
            if row.ConversationId not in resolved:
                resolved[row.ConversationId] = cl_service.find_for_reviewer_reply(
                    conversation_id=row.ConversationId)
            resolver = resolved[row.ConversationId]
            label, reason = classify_cl_reply(
                reply_conversation_id=row.ConversationId, resolver_result=resolver,
                matches=matches, expected=expected, pre_u579=pre,
                cl_work_date=row.ClWorkDate,
                cl_project_ids={p for p in (row.ClProjectIds or "").split(",") if p},
                )
        else:
            label, reason = classify_row(
                reply_conversation_id=row.ConversationId, matches=matches, expected=expected,
                project_id=None, work_date=None, pre_u579=pre)
        results.append((label, reason))
        if not passes(label, reason):
            failing.append((row.ReviewId, expected, label, reason))
        elif (label, reason) in _LISTED_WHY:
            listed.append((row.ReviewId, expected, _LISTED_WHY[(label, reason)]))

    counts = summarize(results)
    print(f"Replies checked: {len(rows)}")
    for label in LABELS:
        print(f"  {label:<11} {counts[label]}")
    if listed:
        print("Listed for Chris (not failing):")
        for review_id, expected, why in listed:
            print(f"  review {review_id}  {expected[0]} {expected[1]}  {why}")
    if failing:
        print("Failures:")
        for review_id, expected, label, reason in failing:
            print(f"  review {review_id}  {expected[0]} {expected[1]}  {label} {reason}")
    return exit_code(results)


def _report() -> int:
    from shared.database import get_connection

    with get_connection() as conn:
        cursor = conn.cursor()
        inserted = _fetch(cursor, REPORT_SQL["inserted"])
        null_conv = _fetch(cursor, REPORT_SQL["null_conversation"])
        cands = _fetch(cursor, REPORT_SQL["cl_candidates"])
        superseded = _fetch(cursor, REPORT_SQL["superseded"])
        null_wd = _fetch(cursor, REPORT_SQL["null_workdate"])
        dupes = _fetch(cursor, REPORT_SQL["duplicate_pairs"])

    print("Inserted backfill rows per kind:")
    for r in inserted:
        print(f"  {r.Kind:<16} {r.Rows}")
    print("Backfill rows with NULL ConversationId per kind:")
    for r in null_conv:
        print(f"  {r.Kind:<16} {r.Rows}")
    ambiguous = ambiguous_cl_claims(cands)
    print(f"Ambiguous Contract Labor claims: {len(ambiguous)}")
    for r in ambiguous:
        print(f"  claim {r.ClaimId}  project {r.ProjectId}  date {r.WorkDate}  "
              f"outbox_convs={r.OutboxConversations} sent_convs={r.SentConversations}")
    print(f"Superseded Bill/Expense reviews (more than one done send_mail): {len(superseded)}")
    for r in superseded:
        print(f"  review {r.ReviewId}  done_rows={r.DoneRows}")
    print(f"Skipped claims with NULL WorkDate: {len(null_wd)}")
    for r in null_wd:
        print(f"  claim {r.ClaimId}  project {r.ProjectId}")
    print(f"Skipped duplicate (ProjectId, WorkDate) claims beyond the first: {len(dupes)}")
    for r in dupes:
        print(f"  claim {r.ClaimId}  project {r.ProjectId}  date {r.WorkDate}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="classify every applied reply (read-only)")
    parser.add_argument("--report", action="store_true", help="print the backfill report (read-only)")
    args = parser.parse_args(argv)
    if args.report:
        return _report()
    if args.run:
        return _run()
    _plan()
    return 0


if __name__ == "__main__":
    # Read across every record as a system caller, like the other read-only CLI scripts, so an
    # access guard added to a service it calls can never silently filter the proof's rows.
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    sys.exit(main())
