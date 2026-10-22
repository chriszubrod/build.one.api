"""
U-701a: backfill AttachmentTextTokenHash for bill-linked attachments with current extractions.

Dry-run by default (counts only). --run upserts missing rows.

Usage (repo root):
  ./.venv/bin/python -m scripts.u701a_backfill_token_hashes
  ./.venv/bin/python -m scripts.u701a_backfill_token_hashes --run
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from entities.attachment.business.text_token_hash_service import AttachmentTextTokenHashService
from entities.coding_suggestion.business.bill_text_token_hash import (
    current_tokenizer_version,
    feature_enabled,
)
from entities.coding_suggestion.business.similar_bills import _pdf_text_from_raw_blob
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

DEFAULT_WORKERS = 4
READ_TIMEOUT_SECONDS = 120.0
_PROGRESS_EVERY = 100


def _list_candidates(conn, *, tokenizer_version: str) -> list[tuple[int, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT a.[Id], a.[ExtractedTextBlobUrl]
        FROM dbo.[Attachment] a
        INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[AttachmentId] = a.[Id]
        WHERE a.[ExtractionStatus] = 'completed'
          AND a.[ExtractedTextBlobUrl] IS NOT NULL
          AND NOT EXISTS (
                SELECT 1 FROM dbo.[TaxpayerAttachment] t WHERE t.[AttachmentId] = a.[Id]
          )
          AND NOT EXISTS (  -- missing, or hashed under another key/tokenizer version
                SELECT 1 FROM dbo.[AttachmentTextTokenHash] h
                WHERE h.[AttachmentId] = a.[Id]
                  AND h.[TokenizerVersion] = ?
          )
        ORDER BY a.[Id]
        """,
        (tokenizer_version,),
    )
    return [(int(r.Id), str(r.ExtractedTextBlobUrl)) for r in cur.fetchall()]


def _read_blob_text_with_timeout(blob_url: str, timeout_seconds: float) -> tuple[Optional[str], str]:
    holder: list[Any] = [None, "read_error"]

    def work() -> None:
        try:
            from shared.storage import AzureBlobStorage

            storage = AzureBlobStorage()
            content, _meta = storage.download_file(blob_url)
            holder[0] = content.decode("utf-8")
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
    *,
    svc: AttachmentTextTokenHashService,
) -> str:
    raw, read_outcome = _read_blob_text_with_timeout(blob_url, READ_TIMEOUT_SECONDS)
    if raw is None:
        return read_outcome
    _text, outcome = _pdf_text_from_raw_blob(raw)  # the same envelope gate the matcher uses
    if outcome != "ok":
        return "empty_content" if outcome == "empty" else outcome
    try:
        svc.upsert_from_extraction_envelope(attachment_id=attachment_id, envelope=json.loads(raw))
        return "upserted"
    except Exception:
        return "upsert_error"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Perform upserts (default: dry-run counts)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)

    if not feature_enabled():
        print("0")
        return 0

    assert_cli_system_admin()
    version = current_tokenizer_version()
    if not version:
        print("0")
        return 0
    with get_connection() as conn:
        candidates = _list_candidates(conn, tokenizer_version=version)

    print(len(candidates))

    if not args.run or not candidates:
        return 0

    svc = AttachmentTextTokenHashService()
    counts: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(_process_one, att_id, url, svc=svc): att_id
            for att_id, url in candidates
        }
        done = 0
        for fut in as_completed(futures, timeout=None):
            try:
                outcome = fut.result()
            except Exception:
                outcome = "read_error"
            counts[outcome] = counts.get(outcome, 0) + 1  # main thread only
            done += 1
            if done % _PROGRESS_EVERY == 0:
                print(done, file=sys.stderr)

    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
