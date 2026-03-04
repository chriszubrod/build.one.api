#!/usr/bin/env python3
"""
Find and clean up orphaned BillLineItemAttachment and Attachment records.

Orphans come from previous Generate Bills runs where old BillLineItems were
deleted but their BillLineItemAttachment/Attachment records were not cleaned up.

Usage:
    python scripts/cleanup_orphan_attachments.py [--dry-run]
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import get_connection


def main():
    parser = argparse.ArgumentParser(description="Clean up orphaned attachment records")
    parser.add_argument("--dry-run", action="store_true", help="Show orphans without deleting")
    args = parser.parse_args()

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. Find orphan BillLineItemAttachments (BillLineItemId points to deleted BillLineItem)
        cursor.execute("""
            SELECT blia.Id, blia.BillLineItemId, blia.AttachmentId
            FROM dbo.BillLineItemAttachment blia
            LEFT JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
            WHERE bli.Id IS NULL
        """)
        orphan_links = cursor.fetchall()
        print(f"Orphan BillLineItemAttachments: {len(orphan_links)}")

        orphan_attachment_ids = set()
        for row in orphan_links:
            orphan_attachment_ids.add(row.AttachmentId)
            if args.dry_run:
                print(f"  [DRY RUN] BillLineItemAttachment {row.Id} -> BillLineItemId={row.BillLineItemId} (missing), AttachmentId={row.AttachmentId}")

        # 2. Find Attachments that are ONLY referenced by orphan links (not by any live link)
        cursor.execute("""
            SELECT a.Id, a.Filename, a.BlobUrl, a.Category
            FROM dbo.Attachment a
            WHERE a.Id IN (
                SELECT blia.AttachmentId
                FROM dbo.BillLineItemAttachment blia
                LEFT JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
                WHERE bli.Id IS NULL
            )
            AND a.Id NOT IN (
                SELECT blia2.AttachmentId
                FROM dbo.BillLineItemAttachment blia2
                INNER JOIN dbo.BillLineItem bli2 ON bli2.Id = blia2.BillLineItemId
            )
        """)
        orphan_attachments = cursor.fetchall()
        print(f"Orphan Attachments (no live links): {len(orphan_attachments)}")
        for row in orphan_attachments:
            print(f"  Attachment {row.Id} | {row.Filename} | {row.Category} | {row.BlobUrl}")

        if args.dry_run:
            print(f"\nDry run complete. Re-run without --dry-run to delete.")
            return

        # 3. Delete orphan BillLineItemAttachments
        if orphan_links:
            orphan_link_ids = [row.Id for row in orphan_links]
            placeholders = ",".join(str(i) for i in orphan_link_ids)
            cursor.execute(f"DELETE FROM dbo.BillLineItemAttachment WHERE Id IN ({placeholders})")
            conn.commit()
            print(f"  Deleted {len(orphan_link_ids)} orphan BillLineItemAttachment records")

        # 4. Delete orphan Attachments
        if orphan_attachments:
            orphan_att_ids = [row.Id for row in orphan_attachments]
            placeholders = ",".join(str(i) for i in orphan_att_ids)
            cursor.execute(f"DELETE FROM dbo.Attachment WHERE Id IN ({placeholders})")
            conn.commit()
            print(f"  Deleted {len(orphan_att_ids)} orphan Attachment records")

        # 5. Also clean up old blobs in invoices/ folder (now unused)
        print(f"\nNote: Old blobs under invoices/ in Azure can be manually deleted if desired.")

    print("\nDone.")


if __name__ == "__main__":
    main()
