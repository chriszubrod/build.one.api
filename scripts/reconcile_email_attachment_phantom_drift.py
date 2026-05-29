"""Reconcile EmailAttachment rows against Microsoft Graph for phantom drift.

Phase 6 of the 2026-05-27 data-corruption investigation. Root cause for this
phase: the EmailAttachment upsert path appears to compare Graph identifiers
under a case-insensitive collation (SQL Server default `Latin1_General_CI_AS`).
When two messages have GraphMessageId values that differ only in case at a
single position — which happens routinely in base64-encoded message
identifiers — the upsert merges the new attachment onto the wrong parent
EmailMessage row.

Symptom: an EmailAttachment row whose `GraphAttachmentId` does NOT
case-sensitively share the full GraphMessageId body of its parent
EmailMessage. Confirmed empirically against 3 sample EmailMessages (751,
753, 113): every flagged EmailAttachment was missing from Microsoft Graph's
own attachment list for that message, with zero false positives.

This script is **read-only**. It walks every suspect EmailAttachment,
queries Graph once per parent EmailMessage (batched), and classifies each
suspect as:

  - `confirmed_phantom`  — Graph's attachment list for the parent message
                            does NOT include this GraphAttachmentId.
                            The EmailAttachment is misattributed.
  - `legit`              — Graph DOES list this GraphAttachmentId on the
                            parent message. False positive of the
                            detection heuristic — should not happen but
                            worth surfacing.
  - `parent_missing`     — Graph returns 404 for the parent message id.
                            The EmailMessage was deleted from the mailbox.
                            The EmailAttachments grafted onto it are
                            necessarily phantoms but cannot be re-linked
                            since the real parent is gone.
  - `error`              — Graph returned a non-200 / non-404 status we
                            cannot classify.

Output: JSON report to stdout plus a summary table. No mutations.

Run:
    .venv/bin/python scripts/reconcile_email_attachment_phantom_drift.py
    .venv/bin/python scripts/reconcile_email_attachment_phantom_drift.py --output phantom_ea_report.json
    .venv/bin/python scripts/reconcile_email_attachment_phantom_drift.py --limit 25

Notes:
  - The script groups suspect EmailAttachments by parent EmailMessageId so
    that each parent is queried against Graph exactly once, regardless of
    how many suspect EmailAttachments are linked to it.
  - The parent's `MailboxAddress` is honored when issuing the Graph call,
    so emails from non-default mailboxes (if any exist) reconcile against
    the correct mailbox.
  - A confirmed_phantom record also reports whether the EmailAttachment's
    blob storage URI is still set — useful for the Phase 6 cleanup pass
    that follows this audit.
"""
import sys
import json
import argparse
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


DEFAULT_MAILBOX = "invoice@rogersbuild.com"


def load_suspect_attachments() -> list[dict]:
    """All EmailAttachments whose case-sensitive GraphAttachmentId prefix
    does not match the parent EmailMessage's GraphMessageId body.

    Detection uses Latin1_General_BIN collation to force case-sensitivity
    on a comparison that the default `_CI_AS` collation would consider
    equal.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                ea.Id              AS EmailAttachmentId,
                ea.EmailMessageId  AS EmailMessageId,
                ea.GraphAttachmentId,
                ea.Filename,
                ea.ContentType,
                ea.SizeBytes,
                ea.IsInline,
                ea.BlobUri,
                ea.CreatedDatetime,
                em.GraphMessageId,
                em.MailboxAddress,
                em.FromAddress,
                em.Subject,
                em.Folder
            FROM dbo.EmailAttachment ea
            JOIN dbo.EmailMessage    em ON em.Id = ea.EmailMessageId
            WHERE ea.GraphAttachmentId IS NOT NULL
              AND em.GraphMessageId    IS NOT NULL
        """)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    suspects: list[dict] = []
    for row in rows:
        gmid_core = (row["GraphMessageId"] or "").rstrip("=")
        gaid      = row["GraphAttachmentId"] or ""
        if not gmid_core or not gaid:
            continue
        # Case-sensitive longest-common-prefix
        common = 0
        for i in range(min(len(gmid_core), len(gaid))):
            if gmid_core[i] == gaid[i]:
                common += 1
            else:
                break
        if common < len(gmid_core):
            row["common_prefix_len"] = common
            row["gmid_core_len"]     = len(gmid_core)
            suspects.append(row)
    return suspects


def reconcile_parent_message(graph_message_id: str, mailbox: str) -> dict:
    """Ask Graph for the attachment list of one EmailMessage. Returns
    `{"status": "ok", "attachment_ids": {...}}` on success, or
    `{"status": "parent_missing"}` on 404, or `{"status": "error", "error": ...}`."""
    from integrations.ms.mail.external import client as mail_client
    result = mail_client.list_message_attachments(message_id=graph_message_id, mailbox=mailbox)
    status_code = result.get("status_code")
    if status_code == 404:
        return {"status": "parent_missing", "attachment_ids": set()}
    if status_code != 200:
        return {"status": "error", "attachment_ids": set(), "error": result.get("message")}
    return {
        "status": "ok",
        "attachment_ids": {att.get("attachment_id") for att in (result.get("attachments") or [])},
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", help="Write the full JSON report to this path")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit the number of suspect EmailAttachments reconciled (for fast smoke test)")
    p.add_argument("--mailbox", default=DEFAULT_MAILBOX,
                   help="Fallback mailbox used when an EmailMessage has no MailboxAddress set")
    args = p.parse_args()

    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    suspects = load_suspect_attachments()
    if args.limit:
        suspects = suspects[:args.limit]

    print(f"Loaded {len(suspects)} suspect EmailAttachment(s) "
          f"(case-sensitive GraphAttachmentId prefix mismatch).")
    print()

    # Batch by parent EmailMessageId so we hit Graph once per parent.
    by_parent: dict[int, list[dict]] = defaultdict(list)
    for s in suspects:
        by_parent[s["EmailMessageId"]].append(s)
    print(f"Spans {len(by_parent)} distinct parent EmailMessage(s).")
    print()

    results: list[dict] = []
    verdict_counter: Counter = Counter()

    parent_count = len(by_parent)
    for i, (em_id, group) in enumerate(sorted(by_parent.items()), start=1):
        first = group[0]
        mailbox = first.get("MailboxAddress") or args.mailbox
        graph_message_id = first["GraphMessageId"]
        if i % 25 == 0 or i == parent_count:
            print(f"  [{i}/{parent_count}] Reconciling parent EmailMessage {em_id} "
                  f"({len(group)} suspect EA(s)) against {mailbox}...")
        rec = reconcile_parent_message(graph_message_id=graph_message_id, mailbox=mailbox)
        parent_status = rec["status"]
        graph_attachment_ids = rec["attachment_ids"]
        for s in group:
            gaid = s["GraphAttachmentId"]
            if parent_status == "parent_missing":
                verdict = "parent_missing"
            elif parent_status == "error":
                verdict = "error"
            elif gaid in graph_attachment_ids:
                verdict = "legit"
            else:
                verdict = "confirmed_phantom"
            record = {
                "email_attachment_id": s["EmailAttachmentId"],
                "email_message_id":    s["EmailMessageId"],
                "graph_attachment_id": gaid,
                "parent_graph_message_id": graph_message_id,
                "mailbox":            mailbox,
                "parent_from":        s["FromAddress"],
                "parent_subject":     s["Subject"],
                "parent_folder":      s["Folder"],
                "filename":           s["Filename"],
                "content_type":       s["ContentType"],
                "size_bytes":         s["SizeBytes"],
                "is_inline":          bool(s["IsInline"]) if s["IsInline"] is not None else None,
                "blob_uri":           s["BlobUri"],
                "ea_created":         str(s["CreatedDatetime"]),
                "common_prefix_len":  s["common_prefix_len"],
                "gmid_core_len":      s["gmid_core_len"],
                "graph_parent_status": parent_status,
                "graph_error":        rec.get("error"),
                "verdict":            verdict,
            }
            results.append(record)
            verdict_counter[verdict] += 1

    print()
    print("=== Verdict counts ===")
    for v, n in verdict_counter.most_common():
        print(f"  {v:<20} {n}")

    # Per-parent rollup
    parents_by_verdict: dict[str, set[int]] = defaultdict(set)
    for r in results:
        parents_by_verdict[r["verdict"]].add(r["email_message_id"])
    print()
    print("=== Distinct parent EmailMessages by verdict ===")
    for v, parent_ids in parents_by_verdict.items():
        print(f"  {v:<20} {len(parent_ids)} parent EmailMessage(s)")

    # Blob-uri presence on confirmed phantoms (informs cleanup pass)
    phantom_with_blob = sum(1 for r in results
                            if r["verdict"] == "confirmed_phantom" and r["blob_uri"])
    phantom_total = verdict_counter.get("confirmed_phantom", 0)
    if phantom_total:
        print()
        print(f"Of {phantom_total} confirmed_phantom EmailAttachment(s), "
              f"{phantom_with_blob} still have BlobUri set (need blob deletion in cleanup).")

    # Top inline-vs-attached split for confirmed_phantom
    if phantom_total:
        phantom_inline = sum(1 for r in results
                             if r["verdict"] == "confirmed_phantom" and r["is_inline"])
        print(f"Of {phantom_total} confirmed_phantom EmailAttachment(s), "
              f"{phantom_inline} are inline (signature images, embedded screenshots) "
              f"and {phantom_total - phantom_inline} are file attachments.")

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_suspect_attachments": len(suspects),
            "total_distinct_parents":   len(by_parent),
            "verdict_counts":           dict(verdict_counter),
            "parents_by_verdict":       {v: sorted(list(ids)) for v, ids in parents_by_verdict.items()},
            "records":                  results,
        }, indent=2, default=str))
        print()
        print(f"Full JSON report written to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
