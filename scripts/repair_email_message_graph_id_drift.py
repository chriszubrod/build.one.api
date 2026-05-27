"""Repair the 160 drift + 2 missing EmailMessages identified by
reconcile_email_message_graph_id_drift.py.

Phase 2 of the 2026-05-27 data-corruption investigation.

For each drift row (where Graph's `internetMessageId` at the stored
`GraphMessageId` differs from our DB's stored value):

  1. Fetch the original email's full metadata from Graph at the same
     `GraphMessageId`.
  2. Save the current DB values into a rollback JSON file.
  3. UPDATE the row in place — replacing the overwriter's metadata with
     the original email's. Direct SQL UPDATE (not via UpsertEmailMessage)
     to avoid the very MERGE bug we're repairing.
  4. Reset `ProcessingStatus` to 'pending' (overwriter's classification is
     wrong for the original email) and NULL out agent fields.
  5. EmailAttachment rows stay attached — they were captured under the
     original email's identity, so they belong here.

For each missing row (Graph returns 404 — the email was deleted from
the mailbox between when we polled and now):

  1. Save current DB values to rollback.
  2. Set `ProcessingStatus = 'failed'` + `LastError = 'deleted_from_mailbox_during_corruption_window'`.
  3. Keep the row for audit. Don't delete.

Trade-off: the "overwriter" emails (AMP / mobmatnash / HomeDepot / etc.)
are NOT preserved as separate rows after this repair. They're still in
the mailbox — when polling resumes after Phase 3 (with the
InternetMessageId-keyed MERGE), they'll be re-polled under their currently
-stable GraphMessageIds and re-created as fresh rows.

Run:
    .venv/bin/python scripts/repair_email_message_graph_id_drift.py --dry-run
    .venv/bin/python scripts/repair_email_message_graph_id_drift.py --apply

Default is --dry-run. --apply commits the mutations.
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


def load_inverted_rows() -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT Id, GraphMessageId, InternetMessageId, ConversationId,
                   MailboxAddress, FromAddress, FromName, ToRecipients, CcRecipients,
                   Subject, BodyPreview, BodyContent, BodyContentType,
                   ReceivedDatetime, WebLink, HasAttachments,
                   AgentClassification, AgentClassificationReason,
                   AgentDecidedAction, AgentClassificationConfidence,
                   AgentSessionId, ProcessingStatus, LastError, Folder
            FROM dbo.EmailMessage
            WHERE CreatedDatetime < ReceivedDatetime
            ORDER BY Id ASC
        """)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _normalize_iso_to_sql(iso: str | None) -> str | None:
    """Graph returns ISO 8601 like '2026-05-12T20:05:03Z'.
    DATETIME2(3) wants '2026-05-12 20:05:03.000' (no TZ, no Z)."""
    if not iso:
        return None
    s = iso.replace("Z", "").replace("T", " ")
    # Strip TZ offset if present
    if "+" in s[10:]:
        s = s.rsplit("+", 1)[0]
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true",
                     help="Report what would change without mutating.")
    grp.add_argument("--apply", action="store_true",
                     help="Apply the repair mutations.")
    ap.add_argument("--rollback-file", default="/tmp/drift_repair_rollback.json",
                    help="Path to write the per-row rollback JSON")
    ap.add_argument("--mailbox", default="invoice@rogersbuild.com")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    set_authz_context(user_id=17, company_id=1, is_system_admin=True)
    from integrations.ms.mail.external import client as mail_client

    rows = load_inverted_rows()
    if args.limit:
        rows = rows[:args.limit]

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"=== {mode} — repairing {len(rows)} inverted EmailMessages ===")
    print()

    rollback_records: list[dict] = []
    drift_repaired = 0
    missing_marked = 0
    matched_skipped = 0  # legit re-polls; leave alone
    errors = 0

    for i, row in enumerate(rows, start=1):
        em_id = row["Id"]
        if i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] Processing Emsg {em_id}...")

        # Re-fetch Graph state
        result = mail_client.get_message(message_id=row["GraphMessageId"], mailbox=args.mailbox)
        status = result.get("status_code")

        # Snapshot current DB state for rollback
        snap = {k: (str(v) if isinstance(v, datetime) else v) for k, v in row.items()}

        if status == 404:
            # Missing case
            new_status = "failed"
            new_error = "deleted_from_mailbox_during_corruption_window"
            rollback_records.append({
                "email_message_id": em_id,
                "verdict": "missing",
                "before": snap,
                "applied_change": {
                    "ProcessingStatus": new_status,
                    "LastError": new_error,
                },
            })
            if args.apply:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""UPDATE dbo.EmailMessage
                                   SET ProcessingStatus = ?, LastError = ?,
                                       ModifiedDatetime = SYSUTCDATETIME()
                                   WHERE Id = ?""",
                                new_status, new_error, em_id)
                    conn.commit()
            missing_marked += 1
            continue

        if status != 200:
            errors += 1
            print(f"  WARN Emsg {em_id}: Graph error {status} — {result.get('message')}")
            continue

        em = result.get("email") or {}
        graph_imid = em.get("internet_message_id")
        our_imid = row.get("InternetMessageId")

        if graph_imid and our_imid and graph_imid == our_imid:
            # Legit re-poll (verdict=match); leave alone
            matched_skipped += 1
            continue

        # Drift case — repair
        to_recipients_json = json.dumps(em.get("to_recipients") or []) if em.get("to_recipients") else None
        cc_recipients_json = json.dumps(em.get("cc_recipients") or []) if em.get("cc_recipients") else None
        repaired = {
            "InternetMessageId": graph_imid,
            "ConversationId": em.get("conversation_id"),
            "FromAddress": em.get("from_email"),
            "FromName": em.get("from_name"),
            "ToRecipients": to_recipients_json,
            "CcRecipients": cc_recipients_json,
            "Subject": em.get("subject"),
            "BodyPreview": em.get("body_preview"),
            "BodyContent": em.get("body_content"),
            "BodyContentType": em.get("body_content_type"),
            "ReceivedDatetime": _normalize_iso_to_sql(em.get("received_datetime")),
            "WebLink": em.get("web_link"),
            "HasAttachments": bool(em.get("has_attachments", False)),
            "ProcessingStatus": "pending",
            "AgentClassification": None,
            "AgentClassificationReason": None,
            "AgentDecidedAction": None,
            "AgentClassificationConfidence": None,
            "AgentSessionId": None,
            "LastError": None,
        }
        rollback_records.append({
            "email_message_id": em_id,
            "verdict": "drift",
            "before": snap,
            "applied_change": repaired,
        })

        if args.apply:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE dbo.EmailMessage
                    SET InternetMessageId = ?, ConversationId = ?,
                        FromAddress = ?, FromName = ?,
                        ToRecipients = ?, CcRecipients = ?,
                        Subject = ?, BodyPreview = ?, BodyContent = ?,
                        BodyContentType = ?, ReceivedDatetime = ?,
                        WebLink = ?, HasAttachments = ?,
                        ProcessingStatus = ?, AgentClassification = NULL,
                        AgentClassificationReason = NULL, AgentDecidedAction = NULL,
                        AgentClassificationConfidence = NULL, AgentSessionId = NULL,
                        LastError = NULL,
                        ModifiedDatetime = SYSUTCDATETIME()
                    WHERE Id = ?
                """,
                    repaired["InternetMessageId"], repaired["ConversationId"],
                    repaired["FromAddress"], repaired["FromName"],
                    repaired["ToRecipients"], repaired["CcRecipients"],
                    repaired["Subject"], repaired["BodyPreview"], repaired["BodyContent"],
                    repaired["BodyContentType"], repaired["ReceivedDatetime"],
                    repaired["WebLink"], repaired["HasAttachments"],
                    repaired["ProcessingStatus"], em_id)
                conn.commit()
        drift_repaired += 1

    # Write the rollback file
    out = Path(args.rollback_file)
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "drift_repaired": drift_repaired,
        "missing_marked": missing_marked,
        "matched_skipped": matched_skipped,
        "errors": errors,
        "records": rollback_records,
    }, indent=2, default=str))

    print()
    print(f"=== {mode} summary ===")
    print(f"  drift_repaired : {drift_repaired}")
    print(f"  missing_marked : {missing_marked}")
    print(f"  matched_skipped: {matched_skipped}")
    print(f"  errors         : {errors}")
    print(f"  rollback file  : {out}")
    if args.dry_run:
        print()
        print("DRY-RUN — no DB mutations applied. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
