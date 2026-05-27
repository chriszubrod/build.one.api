"""Reconcile EmailMessage rows against MS Graph for GraphMessageId-recycling drift.

Phase 2 of the 2026-05-27 data-corruption investigation. Root cause established
in Phase 1: MS Graph reuses `GraphMessageId` values in the
`invoice@rogersbuild.com` shared mailbox when emails are deleted-then-restored.
Our `UpsertEmailMessage` MERGE is keyed only on `GraphMessageId`, so when a
recycled id arrives via a new email, the MERGE matches the previously-stored
row and overwrites the metadata — but leaves `CreatedDatetime` untouched
(INSERT-only). That produces the `CreatedDatetime < ReceivedDatetime`
time-inversion signature on 162 rows in prod.

This script is **read-only**. It walks every time-inverted EmailMessage,
queries Graph at the stored GraphMessageId, and classifies each as:

  - `match`     — Graph's internetMessageId matches our DB's. Row is legit;
                  the time-inversion has another explanation (most likely
                  benign — see Notes).
  - `drift`     — Graph reports a different internetMessageId than our DB
                  stores. Our row was overwritten by ID recycling. The
                  metadata in our DB belongs to the LATER email; the email
                  Graph currently reports at the same GraphMessageId is the
                  ORIGINAL.
  - `missing`   — Graph returns 404 for the stored GraphMessageId. The email
                  was deleted (and not restored). Our row contains data for
                  an email that no longer exists in the mailbox.
  - `error`     — Graph returned a non-200 / non-404 response we can't
                  classify.

Output: JSON report to stdout + summary table. No mutations.

Run:
    .venv/bin/python scripts/reconcile_email_message_graph_id_drift.py
    .venv/bin/python scripts/reconcile_email_message_graph_id_drift.py --output drift_report.json

Notes on `match` rows: a `match` with time-inversion can occur if the email
was scheduled-delivered, quarantine-released, or otherwise had its
`receivedDateTime` advanced after our initial poll. These are not corruption
— they're a separate residual gap on the polling primitive.
"""
import sys
import json
import argparse
from pathlib import Path
from collections import Counter
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


def load_inverted_rows() -> list[dict]:
    """All EmailMessages where CreatedDatetime < ReceivedDatetime."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT Id, GraphMessageId, InternetMessageId, FromAddress, Subject,
                   CreatedDatetime, ReceivedDatetime, ProcessingStatus, Folder
            FROM dbo.EmailMessage
            WHERE CreatedDatetime < ReceivedDatetime
            ORDER BY CreatedDatetime ASC
        """)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_attachment_count(email_message_id: int) -> int:
    """How many EmailAttachments are linked to this EmailMessage."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dbo.EmailAttachment WHERE EmailMessageId = ?", email_message_id)
        return int(cur.fetchone()[0])


def reconcile_row(row: dict, mailbox: str) -> dict:
    """Query Graph for this row's GraphMessageId and classify."""
    from integrations.ms.mail.external import client as mail_client
    gm_id = row["GraphMessageId"]
    result = mail_client.get_message(message_id=gm_id, mailbox=mailbox)
    status = result.get("status_code")
    if status == 404:
        return {"verdict": "missing", "graph_internet_message_id": None,
                "graph_from": None, "graph_subject": None,
                "graph_received": None, "graph_error": None}
    if status != 200:
        return {"verdict": "error", "graph_internet_message_id": None,
                "graph_from": None, "graph_subject": None,
                "graph_received": None, "graph_error": result.get("message")}
    em = result.get("email") or {}
    graph_imid = em.get("internet_message_id")
    our_imid = row.get("InternetMessageId")
    if graph_imid and our_imid and graph_imid == our_imid:
        verdict = "match"
    else:
        verdict = "drift"
    return {
        "verdict": verdict,
        "graph_internet_message_id": graph_imid,
        "graph_from": em.get("from_email"),
        "graph_subject": em.get("subject"),
        "graph_received": em.get("received_datetime"),
        "graph_error": None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", help="Write full JSON report to this path")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit number of rows reconciled (for fast smoke test)")
    p.add_argument("--mailbox", default="invoice@rogersbuild.com",
                   help="Mailbox to query Graph against")
    args = p.parse_args()

    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    rows = load_inverted_rows()
    if args.limit:
        rows = rows[:args.limit]

    print(f"Reconciling {len(rows)} time-inverted EmailMessages against Graph...")
    print()

    results: list[dict] = []
    verdict_counter: Counter = Counter()
    drift_by_overwriter_domain: Counter = Counter()
    drift_by_original_domain: Counter = Counter()

    for i, row in enumerate(rows, start=1):
        em_id = row["Id"]
        if i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] Reconciling Emsg {em_id}...")
        rec = reconcile_row(row, args.mailbox)
        att_count = load_attachment_count(em_id)
        record = {
            "email_message_id": em_id,
            "graph_message_id": row["GraphMessageId"],
            "our_internet_message_id": row.get("InternetMessageId"),
            "our_from": row.get("FromAddress"),
            "our_subject": row.get("Subject"),
            "our_created": str(row.get("CreatedDatetime")),
            "our_received": str(row.get("ReceivedDatetime")),
            "processing_status": row.get("ProcessingStatus"),
            "folder": row.get("Folder"),
            "attachment_count": att_count,
            "graph_internet_message_id": rec["graph_internet_message_id"],
            "graph_from": rec["graph_from"],
            "graph_subject": rec["graph_subject"],
            "graph_received": rec["graph_received"],
            "graph_error": rec["graph_error"],
            "verdict": rec["verdict"],
        }
        results.append(record)
        verdict_counter[rec["verdict"]] += 1

        if rec["verdict"] == "drift":
            ours_domain = (row.get("FromAddress") or "").split("@", 1)[-1].lower()
            theirs_domain = (rec["graph_from"] or "").split("@", 1)[-1].lower()
            drift_by_overwriter_domain[ours_domain] += 1
            drift_by_original_domain[theirs_domain] += 1

    print()
    print("=== Verdict counts ===")
    for v, n in verdict_counter.most_common():
        print(f"  {v:<10} {n}")

    if drift_by_overwriter_domain:
        print()
        print("=== Drift cases: overwriter-vendor domain (the email currently in our DB) ===")
        for d, n in drift_by_overwriter_domain.most_common(20):
            print(f"  {d:<40} {n}")
        print()
        print("=== Drift cases: original-vendor domain (the email Graph currently shows) ===")
        for d, n in drift_by_original_domain.most_common(20):
            print(f"  {d:<40} {n}")

    # Quantify EA impact for drift rows
    drift_em_ids = [r["email_message_id"] for r in results if r["verdict"] == "drift"]
    if drift_em_ids:
        with get_connection() as conn:
            cur = conn.cursor()
            placeholders = ",".join(["?"] * len(drift_em_ids))
            cur.execute(f"""SELECT COUNT(*), SUM(CASE WHEN IsInline = 0 THEN 1 ELSE 0 END)
                            FROM dbo.EmailAttachment WHERE EmailMessageId IN ({placeholders})""",
                        *drift_em_ids)
            total_ea, non_inline_ea = cur.fetchone()
        print()
        print(f"EA rows attached to drift EmailMessages: total={total_ea}, non-inline={non_inline_ea}")
        print("  (these are likely 'phantoms' relative to the current metadata —")
        print("   they were captured under the original email's identity)")

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_reconciled": len(results),
            "verdict_counts": dict(verdict_counter),
            "drift_overwriter_domains": dict(drift_by_overwriter_domain),
            "drift_original_domains": dict(drift_by_original_domain),
            "rows": results,
        }, indent=2, default=str))
        print()
        print(f"Full JSON report written to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
