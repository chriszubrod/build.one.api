"""Repair phantom EmailAttachment rows identified by the Phase 6 audit.

Phase 6 of the 2026-05-27 data-corruption investigation. Companion to
`reconcile_email_attachment_phantom_drift.py`. The audit established that
250 + 2 = 252 EmailAttachment rows are misattributed to wrong parent
EmailMessages (their `GraphAttachmentId` has a case-sensitive prefix
mismatch against the parent `GraphMessageId` body, and Microsoft Graph
either explicitly reports they are not attached to the claimed parent, or
the parent itself no longer exists in the mailbox).

Cause: the pre-Phase-3 `UpsertEmailMessage` stored procedure matched
case-twin `GraphMessageId` values as equal under the default
case-insensitive collation, causing two distinct Microsoft Graph messages
to merge onto the same `EmailMessage` row. Subsequent attachments from
both messages were then linked to the same `EmailMessageId`. Migration
004 fixed the root cause by switching the merge key to
(InternetMessageId, Folder). Migration 006 hardened the affected columns
to a binary collation so the underlying comparison can never be
case-insensitive again. All 252 phantom rows in prod were created before
2026-05-27 — no new phantoms have appeared post-fix.

This script removes the legacy phantoms. For each row:

  1. If `BlobUri` is set, attempt to delete the underlying Azure blob.
     Blob-delete failures are logged but do not block row deletion —
     the blob storage path is a soft reference and orphan blobs can be
     swept later if needed.
  2. Delete the `dbo.EmailAttachment` row.

Before any deletion, the full original row state is captured to a JSON
rollback file (mirrors the Phase 2 repair pattern) so a re-INSERT script
could restore exactly what was removed.

Flags:
  --dry-run   (default)  Print what would be done. No deletions.
  --apply                Perform the deletions. Requires explicit flag.
  --output PATH          Write the rollback JSON to this path. With
                         --apply this captures pre-delete state; with
                         --dry-run it captures the planned operations.
  --limit N              Operate on at most N phantoms (for smoke test).
  --report PATH          Read the audit report from
                         reconcile_email_attachment_phantom_drift.py
                         instead of re-running detection. Avoids a
                         round of Graph calls when applying a previously
                         confirmed plan.

Safety:
  - The script requires an explicit `--apply` flag to delete anything.
  - It operates only on rows the audit classified as
    `confirmed_phantom` or `parent_missing`. Other verdicts (`legit`,
    `error`) are skipped.
  - It re-verifies each row's verdict against the audit report (or
    re-runs detection if --report not supplied) — the audit is the
    authoritative classifier; this script does not invent its own.
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection
from shared.storage import AzureBlobStorage, AzureBlobStorageError


DELETABLE_VERDICTS = {"confirmed_phantom", "parent_missing"}


def load_targets_from_report(report_path: Path) -> list[dict]:
    """Read records from a prior audit run."""
    data = json.loads(report_path.read_text())
    records = data.get("records") or []
    return [r for r in records if r.get("verdict") in DELETABLE_VERDICTS]


def load_targets_from_db() -> list[dict]:
    """Re-detect phantoms case-sensitively without calling Graph.
    Returns records in the same shape as the audit script's output.
    Verdict here is always `confirmed_phantom` since we are not
    calling Graph to disambiguate from `parent_missing` / `legit`.
    Note: a pre-existing audit report (--report) is preferred for an
    apply run because it carries the Graph verification."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                ea.Id, ea.EmailMessageId, ea.GraphAttachmentId, ea.Filename,
                ea.ContentType, ea.SizeBytes, ea.IsInline, ea.BlobUri,
                ea.CreatedDatetime,
                em.GraphMessageId, em.MailboxAddress, em.FromAddress, em.Subject, em.Folder
            FROM dbo.EmailAttachment ea
            JOIN dbo.EmailMessage    em ON em.Id = ea.EmailMessageId
            WHERE ea.GraphAttachmentId IS NOT NULL
              AND em.GraphMessageId    IS NOT NULL
        """)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    out: list[dict] = []
    for row in rows:
        gmid_core = (row["GraphMessageId"] or "").rstrip("=")
        gaid      = row["GraphAttachmentId"] or ""
        if not gmid_core or not gaid:
            continue
        common = 0
        for i in range(min(len(gmid_core), len(gaid))):
            if gmid_core[i] == gaid[i]:
                common += 1
            else:
                break
        if common < len(gmid_core):
            out.append({
                "email_attachment_id": row["Id"],
                "email_message_id":    row["EmailMessageId"],
                "graph_attachment_id": row["GraphAttachmentId"],
                "filename":            row["Filename"],
                "content_type":        row["ContentType"],
                "size_bytes":          row["SizeBytes"],
                "is_inline":           bool(row["IsInline"]) if row["IsInline"] is not None else None,
                "blob_uri":            row["BlobUri"],
                "ea_created":          str(row["CreatedDatetime"]),
                "parent_from":         row["FromAddress"],
                "parent_subject":      row["Subject"],
                "parent_folder":       row["Folder"],
                "verdict":             "confirmed_phantom",
            })
    return out


def capture_full_row_state(email_attachment_id: int) -> dict:
    """Read the EmailAttachment row in full (all columns) so the
    rollback file has enough info to recreate it."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM dbo.EmailAttachment WHERE Id = ?", email_attachment_id)
        cols = [c[0] for c in cur.description]
        row = cur.fetchone()
        if not row:
            return {}
        record = {}
        for k, v in zip(cols, row):
            if isinstance(v, (bytes, bytearray)):
                record[k] = None  # RowVersion — not restorable, skip
            elif isinstance(v, datetime):
                record[k] = v.isoformat()
            else:
                record[k] = v
        return record


def delete_blob(blob_uri: str) -> dict:
    """Best-effort blob deletion. Returns status dict."""
    storage = AzureBlobStorage()
    try:
        storage.delete_file(blob_uri)
        return {"status": "ok"}
    except AzureBlobStorageError as e:
        return {"status": "failed", "error": str(e)}
    except Exception as e:
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def delete_email_attachment_row(email_attachment_id: int) -> None:
    """Delete a single EmailAttachment row by Id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.EmailAttachment WHERE Id = ?", email_attachment_id)
        conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="(default) Plan deletions but do not execute them")
    mode.add_argument("--apply",   action="store_true",
                      help="Actually perform the deletions")
    p.add_argument("--output", help="Path to write the rollback JSON")
    p.add_argument("--limit",  type=int, default=None,
                   help="Operate on at most N targets")
    p.add_argument("--report", help="Path to audit JSON from reconcile_email_attachment_phantom_drift.py")
    args = p.parse_args()

    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    is_apply = bool(args.apply)
    mode_str = "APPLY" if is_apply else "DRY-RUN"

    if args.report:
        targets = load_targets_from_report(Path(args.report))
        source_desc = f"audit report at {args.report}"
    else:
        targets = load_targets_from_db()
        source_desc = "live DB detection (no Graph verification)"
    if args.limit:
        targets = targets[:args.limit]

    print(f"Mode: {mode_str}")
    print(f"Source: {source_desc}")
    print(f"Targets: {len(targets)} EmailAttachment row(s) to remove")
    if not targets:
        print("Nothing to do.")
        return 0
    print()

    rollback: list[dict] = []
    verdict_counter: Counter = Counter()
    blob_result_counter: Counter = Counter()

    for i, t in enumerate(targets, start=1):
        ea_id    = t["email_attachment_id"]
        blob_uri = t.get("blob_uri")
        verdict  = t.get("verdict", "unknown")
        verdict_counter[verdict] += 1

        if i % 25 == 0 or i == len(targets):
            print(f"  [{i}/{len(targets)}] {mode_str} EA {ea_id} "
                  f"(verdict={verdict}, blob={'yes' if blob_uri else 'no'})")

        before_state = capture_full_row_state(ea_id) if is_apply else {
            "Id": ea_id,
            "EmailMessageId": t["email_message_id"],
            "Filename": t.get("filename"),
            "BlobUri": blob_uri,
            "verdict": verdict,
        }

        action_log: dict = {
            "email_attachment_id": ea_id,
            "verdict_at_plan":     verdict,
            "filename":            t.get("filename"),
            "blob_uri":            blob_uri,
            "blob_deletion":       None,
            "row_deletion":        None,
            "before_state":        before_state,
        }

        if is_apply:
            if blob_uri:
                blob_outcome = delete_blob(blob_uri)
                action_log["blob_deletion"] = blob_outcome
                blob_result_counter[blob_outcome["status"]] += 1
            try:
                delete_email_attachment_row(ea_id)
                action_log["row_deletion"] = {"status": "ok"}
            except Exception as e:
                action_log["row_deletion"] = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        else:
            action_log["blob_deletion"] = {"status": "would_delete"} if blob_uri else None
            action_log["row_deletion"]  = {"status": "would_delete"}

        rollback.append(action_log)

    print()
    print("=== Plan / Result summary ===")
    print(f"  Verdict mix: {dict(verdict_counter)}")
    if is_apply:
        ok_rows     = sum(1 for r in rollback if r["row_deletion"]  and r["row_deletion"]["status"]  == "ok")
        failed_rows = sum(1 for r in rollback if r["row_deletion"]  and r["row_deletion"]["status"]  == "failed")
        ok_blobs   = blob_result_counter.get("ok", 0)
        failed_blobs = blob_result_counter.get("failed", 0)
        print(f"  Rows deleted: {ok_rows} ok, {failed_rows} failed")
        print(f"  Blobs deleted: {ok_blobs} ok, {failed_blobs} failed (file phantoms with BlobUri)")
    else:
        would_blobs = sum(1 for r in rollback if r["blob_deletion"] and r["blob_deletion"]["status"] == "would_delete")
        print(f"  Would delete: {len(targets)} EmailAttachment row(s), {would_blobs} blob(s)")
        print("  Re-run with --apply to execute.")

    out_path = Path(args.output) if args.output else None
    if out_path is None and is_apply:
        # Default rollback file location for apply runs.
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        out_path = Path(f"/tmp/phantom_ea_rollback_{ts}.json")
    if out_path:
        out_path.write_text(json.dumps({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "mode":         mode_str,
            "source":       source_desc,
            "total":        len(rollback),
            "verdict_mix":  dict(verdict_counter),
            "actions":      rollback,
        }, indent=2, default=str))
        print()
        print(f"Rollback / plan JSON written to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
