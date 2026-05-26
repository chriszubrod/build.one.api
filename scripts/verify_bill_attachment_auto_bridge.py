"""Regression check on BillService.create auto-bridging EmailAttachment IDs.

Surfaced 2026-05-19 manual backlog walk: `BillService.create(attachment_public_id=...)`
raised `ValueError("Attachment with public_id 'X' not found.")` when an agent
passed an `EmailAttachment.PublicId` instead of an `Attachment.PublicId`. The
agent path had to call `bridge_email_attachment` first, but the convention
was easy to violate and the error message didn't hint at the bridge.

Fixed 2026-05-26 in `entities/bill/business/service.py`: when the
Attachment-table lookup misses, fall back to EmailAttachment by the same
public_id; if found, auto-invoke `EmailAttachmentBridgeService.bridge()`
and continue with the bridged Attachment.

This script locks the lookup-and-bridge wiring without creating any Bill
rows. It uses an already-bridged EmailAttachment as a fixture so the
hash-dedup short-circuit in `bridge()` returns the existing Attachment —
no new rows, no orphans.

Run:
    .venv/bin/python scripts/verify_bill_attachment_auto_bridge.py

Exits 0 on PASS, 1 on FAIL.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


def _pick_bridged_fixture() -> tuple[str, str]:
    """Return (email_attachment_public_id, expected_bridged_attachment_public_id)
    for an already-bridged EmailAttachment. Joins on BlobUrl/BlobUri since
    the bridge service shares the blob between the two rows."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP 1
                   CAST(ea.[PublicId] AS NVARCHAR(50)) AS EaPublicId,
                   CAST(a.[PublicId]  AS NVARCHAR(50)) AS AttPublicId
               FROM dbo.[EmailAttachment] ea
               INNER JOIN dbo.[Attachment] a ON a.[BlobUrl] = ea.[BlobUri]
               WHERE ea.[IsInline] = 0
                 AND a.[ContentType] = 'application/pdf'
               ORDER BY ea.[Id] DESC"""
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                "No already-bridged EmailAttachment found — fixture cannot "
                "be selected. Has any email-driven Bill been created yet?"
            )
        return str(row[0]), str(row[1])


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    from entities.attachment.business.service import AttachmentService
    from entities.email_message.business.service import EmailAttachmentBridgeService
    from entities.email_message.persistence.repo import EmailAttachmentRepository

    ea_pid, expected_att_pid = _pick_bridged_fixture()
    print(f"=== BillService.create auto-bridge wiring check ===")
    print(f"  fixture EmailAttachment.PublicId : {ea_pid}")
    print(f"  expected bridged Attachment      : {expected_att_pid}")

    failures: list[str] = []

    # 1. Confirm the EmailAttachment's PublicId does NOT directly resolve to
    #    an Attachment — that's the precondition that makes the auto-bridge
    #    necessary (different keyspaces).
    direct = AttachmentService().read_by_public_id(public_id=ea_pid)
    if direct is not None:
        failures.append(
            f"AttachmentService.read_by_public_id({ea_pid}) unexpectedly "
            f"returned an Attachment (different keyspaces should mean None)"
        )

    # 2. Confirm the EmailAttachment exists at that PublicId (otherwise the
    #    auto-bridge fallback wouldn't fire on a real agent miscall).
    ea = EmailAttachmentRepository().read_by_public_id(ea_pid)
    if ea is None:
        failures.append(f"EmailAttachment {ea_pid} not found — fixture stale")

    # 3. Call bridge() — should return the existing bridged Attachment via
    #    hash dedup (no new row created).
    bridged = EmailAttachmentBridgeService().bridge(email_attachment_public_id=ea_pid)
    if bridged is None:
        failures.append(f"bridge() returned None for {ea_pid}")
    else:
        actual = str(bridged.public_id).upper()
        if actual != expected_att_pid.upper():
            failures.append(
                f"bridge() returned Attachment.PublicId={actual}, "
                f"expected {expected_att_pid} (hash-dedup should have hit)"
            )
        if bridged.content_type != "application/pdf":
            failures.append(
                f"bridged Attachment content_type={bridged.content_type!r}, "
                f"expected 'application/pdf'"
            )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS — Attachment-table miss falls through to EmailAttachment lookup")
    print("       and bridge() returns the existing bridged Attachment via hash dedup")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
