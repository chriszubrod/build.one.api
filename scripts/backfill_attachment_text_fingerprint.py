"""
U-723: backfill AttachmentTextFingerprint for bill-linked PDF attachments.

Dry-run by default (counts only). --apply performs upserts.

Usage (repo root):
  ./.venv/bin/python scripts/backfill_attachment_text_fingerprint.py
  ./.venv/bin/python scripts/backfill_attachment_text_fingerprint.py --apply

DO NOT run against production without an explicit ops runbook step.
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from entities.attachment.business.text_fingerprint import FINGERPRINT_VERSION
from entities.attachment.business.text_fingerprint_service import AttachmentTextFingerprintService
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

DEFAULT_WORKERS = 4
READ_TIMEOUT_SECONDS = 120.0
_PROGRESS_EVERY = 100


def _list_candidates(conn, *, version: str) -> list[tuple[int, str, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT a.[Id], a.[BlobUrl], a.[ContentType]
        FROM dbo.[Attachment] a
        INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[AttachmentId] = a.[Id]
        WHERE LOWER(ISNULL(a.[ContentType], '')) = 'application/pdf'
          AND a.[BlobUrl] IS NOT NULL
          AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
          AND NOT EXISTS (
                SELECT 1 FROM dbo.[AttachmentTextFingerprint] f
                WHERE f.[AttachmentId] = a.[Id]
                  AND f.[Version] = ?
          )
        ORDER BY a.[Id]
        """,
        (version,),
    )
    return [(int(r.Id), str(r.BlobUrl), str(r.ContentType or "application/pdf")) for r in cur.fetchall()]


def _download_with_timeout(blob_url: str, timeout_seconds: float) -> tuple[Optional[bytes], str]:
    holder: list[Any] = [None, "read_error"]

    def work() -> None:
        try:
            from shared.storage import AzureBlobStorage

            storage = AzureBlobStorage()
            content, _meta = storage.download_file(blob_url)
            holder[0] = content
            holder[1] = "ok"
        except Exception:
            holder[0] = None
            holder[1] = "read_error"

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        return None, "read_timeout"
    return holder[0], str(holder[1])


def _process_one(
    attachment_id: int,
    blob_url: str,
    content_type: str,
    *,
    svc: AttachmentTextFingerprintService,
) -> str:
    raw, read_outcome = _download_with_timeout(blob_url, READ_TIMEOUT_SECONDS)
    if raw is None:
        return read_outcome
    try:
        svc.upsert_from_pdf_bytes(
            attachment_id=attachment_id,
            pdf_bytes=raw,
            content_type=content_type,
        )
        return "upserted"
    except Exception:
        return "upsert_error"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform upserts (default: dry-run counts only)",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)

    assert_cli_system_admin()
    version = FINGERPRINT_VERSION
    with get_connection() as conn:
        candidates = _list_candidates(conn, version=version)

    print(len(candidates))

    if not args.apply or not candidates:
        return 0

    svc = AttachmentTextFingerprintService()
    counts: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(_process_one, att_id, url, ct, svc=svc): att_id
            for att_id, url, ct in candidates
        }
        done = 0
        for fut in as_completed(futures, timeout=None):
            try:
                outcome = fut.result()
            except Exception:
                outcome = "read_error"
            counts[outcome] = counts.get(outcome, 0) + 1
            done += 1
            if done % _PROGRESS_EVERY == 0:
                print(done, file=sys.stderr)

    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
