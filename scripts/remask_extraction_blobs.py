"""
One-time operator script: re-mask attachment extraction JSON blobs written before U-535g.

Rewrites each blob in place (same blob name/URL; no dbo.Attachment updates). Dry-run by default.

Usage (from repo root):
  ./.venv/bin/python -m scripts.remask_extraction_blobs
  ./.venv/bin/python -m scripts.remask_extraction_blobs --apply
  ./.venv/bin/python -m scripts.remask_extraction_blobs --apply --stamp-all --limit 100
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entities.attachment.business.extraction_service import (
    EXTRACTION_BLOB_KEYS,
    EXTRACTION_KVP_KEYS,
    REDACTION_VERSION,
    extraction_payload_is_current,
)
from entities.attachment.business.tin_redaction import mask_tins, mask_tins_in_kvps
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection
from shared.storage import AzureBlobStorage

_MAX_CHANGED_IDS_IN_SUMMARY = 40
_MAX_CONSECUTIVE_FAILURES = 5
_DOWNLOAD_ATTEMPTS = 3

_FETCH_SQL = """
SELECT
    a.[Id],
    a.[ExtractedTextBlobUrl],
    CASE WHEN EXISTS (
        SELECT 1 FROM dbo.[TaxpayerAttachment] ta
        WHERE ta.[AttachmentId] = a.[Id]
    ) THEN 1 ELSE 0 END AS [IsTaxpayerDocument]
FROM dbo.[Attachment] a
WHERE a.[ExtractionStatus] = N'completed'
  AND a.[ExtractedTextBlobUrl] IS NOT NULL
"""


class PayloadValidationError(ValueError):
    """Extraction JSON shape is not safe to remask in place."""


class _StorageLike(Protocol):
    def download_file(self, blob_url: str) -> tuple[bytes, dict]: ...

    def upload_file(
        self,
        blob_name: str,
        file_content: bytes,
        content_type: str = "application/octet-stream",
        container_name: Optional[str] = None,
    ) -> str: ...

    def _parse_blob_url(self, blob_url: str) -> tuple[str, str]: ...


@dataclass(frozen=True)
class _AttachmentRow:
    id: int
    extracted_text_blob_url: str
    is_taxpayer_document: bool


@dataclass
class _RowOutcome:
    attachment_id: int
    bucket: str  # current | changed | stamp_only | failed


def parse_attachment_ids(ids_arg: Optional[str]) -> Optional[list[int]]:
    """Parse --ids. None when omitted; non-empty positive ints when supplied."""
    if ids_arg is None:
        return None
    tokens = [part.strip() for part in ids_arg.split(",") if part.strip()]
    if not tokens:
        raise ValueError(
            "--ids requires at least one positive attachment id (comma-separated dbo.Attachment.Id values)"
        )
    parsed: list[int] = []
    for token in tokens:
        try:
            value = int(token)
        except ValueError as exc:
            raise ValueError(
                f"--ids contains non-integer token {token!r}; "
                "expected comma-separated positive attachment ids"
            ) from exc
        if value <= 0:
            raise ValueError(
                f"--ids contains non-positive id {value}; attachment ids must be positive integers"
            )
        parsed.append(value)
    return parsed


def _validate_payload_shape(payload: dict) -> None:
    if "content" in payload and not isinstance(payload["content"], str):
        raise PayloadValidationError("content must be a string when present")
    if "key_value_pairs" in payload:
        kvps = payload["key_value_pairs"]
        if not isinstance(kvps, list):
            raise PayloadValidationError("key_value_pairs must be a list when present")
        for kvp in kvps:
            if not isinstance(kvp, dict):
                raise PayloadValidationError("each key_value_pair must be an object")
            for field in ("key", "value"):
                if field not in kvp:
                    continue
                val = kvp[field]
                if val is not None and not isinstance(val, str):
                    raise PayloadValidationError(
                        f"key_value_pair {field} must be a string or null when present"
                    )


def remask_payload(payload: dict, *, aggressive: bool) -> tuple[dict, bool]:
    """Return (new_payload, masking_changed). Does not short-circuit on redaction_version.

    The payload is rebuilt from the current writer's fixed key set: pre-U-535a blobs also
    carry pages / paragraphs / tables, which repeat the document text unmasked, and the
    stamp written here must be as true as the writer's (U-672). Dropping such a key, or
    an unknown field inside a key/value pair, counts as a masking change."""
    _validate_payload_shape(payload)
    new_payload = {k: v for k, v in payload.items() if k in EXTRACTION_BLOB_KEYS}
    masking_changed = len(new_payload) != len(payload)

    if "content" in payload:
        content = payload["content"]
        masked_content, _ = mask_tins(content, aggressive=aggressive)
        new_payload["content"] = masked_content
        if masked_content != content:
            masking_changed = True

    if "key_value_pairs" in payload:
        kvps = payload["key_value_pairs"]
        masked_kvps, _ = mask_tins_in_kvps(kvps, aggressive=aggressive)
        masked_kvps = [
            {k: v for k, v in kvp.items() if k in EXTRACTION_KVP_KEYS} for kvp in masked_kvps
        ]
        new_payload["key_value_pairs"] = masked_kvps
        if masked_kvps != kvps:
            masking_changed = True

    new_payload["redaction_version"] = REDACTION_VERSION
    return new_payload, masking_changed


def fetch_attachment_rows(
    *,
    limit: Optional[int] = None,
    attachment_ids: Optional[list[int]] = None,
) -> list[_AttachmentRow]:
    """Read-only candidate list; connection is rolled back before return."""
    if attachment_ids is not None and not attachment_ids:
        raise ValueError("attachment_ids filter must not be empty")

    sql = _FETCH_SQL
    params: list[Any] = []
    if attachment_ids is not None:
        placeholders = ", ".join("?" for _ in attachment_ids)
        sql += f" AND a.[Id] IN ({placeholders})"
        params.extend(attachment_ids)
    sql += " ORDER BY a.[Id]"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, *params)
        raw = list(cursor.fetchall())
        conn.rollback()

    rows = [
        _AttachmentRow(
            id=int(r.Id),
            extracted_text_blob_url=str(r.ExtractedTextBlobUrl),
            is_taxpayer_document=bool(int(r.IsTaxpayerDocument)),
        )
        for r in raw
    ]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _download_json_with_retries(storage: _StorageLike, blob_url: str) -> dict:
    last_exc: Optional[BaseException] = None
    for _ in range(_DOWNLOAD_ATTEMPTS):
        try:
            raw, _meta = storage.download_file(blob_url)
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise PayloadValidationError("extraction blob JSON must be an object")
            return parsed
        except Exception as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _process_row(
    row: _AttachmentRow,
    *,
    storage: _StorageLike,
    apply: bool,
    stamp_all: bool,
) -> _RowOutcome:
    try:
        payload = _download_json_with_retries(storage, row.extracted_text_blob_url)
        if extraction_payload_is_current(payload):
            return _RowOutcome(attachment_id=row.id, bucket="current")

        new_payload, masking_changed = remask_payload(payload, aggressive=row.is_taxpayer_document)
        bucket = "changed" if masking_changed else "stamp_only"
        if not apply or (bucket == "stamp_only" and not stamp_all):
            return _RowOutcome(attachment_id=row.id, bucket=bucket)

        container, blob_name = storage._parse_blob_url(row.extracted_text_blob_url)
        body = json.dumps(new_payload).encode("utf-8")
        storage.upload_file(
            blob_name=blob_name,
            file_content=body,
            content_type="application/json",
            container_name=container,
        )
        read_back = _download_json_with_retries(storage, row.extracted_text_blob_url)
        if read_back != new_payload:
            return _RowOutcome(attachment_id=row.id, bucket="failed")
    except Exception:
        return _RowOutcome(attachment_id=row.id, bucket="failed")

    return _RowOutcome(attachment_id=row.id, bucket=bucket)


def remask_extraction_blobs(
    *,
    fetch_rows: Callable[[], list[_AttachmentRow]],
    storage: _StorageLike,
    apply: bool = False,
    stamp_all: bool = False,
    workers: int = 4,
) -> dict[str, Any]:
    """I/O entry: fetch rows then process with injectable storage + fetch callable."""
    return run_remask(
        fetch_rows(),
        storage=storage,
        apply=apply,
        stamp_all=stamp_all,
        workers=workers,
    )


def run_remask(
    rows: list[_AttachmentRow],
    *,
    storage: _StorageLike,
    apply: bool = False,
    stamp_all: bool = False,
    workers: int = 4,
) -> dict[str, Any]:
    """Process rows with bounded parallelism. Returns count-only summary fields."""
    counts: dict[str, int] = {
        "current": 0,
        "changed": 0,
        "stamp_only": 0,
        "failed": 0,
        "verified": 0,
    }
    changed_ids: list[int] = []
    consecutive_failures = 0
    abort = False

    row_iter = iter(rows)
    pending: set[Future[_RowOutcome]] = set()

    def _drain_done(done: set[Future[_RowOutcome]]) -> None:
        nonlocal consecutive_failures, abort
        for fut in done:
            outcome = fut.result()
            counts[outcome.bucket] += 1
            if outcome.bucket == "changed":
                changed_ids.append(outcome.attachment_id)
            # A written row is only bucketed changed/stamp_only after its readback matched.
            if apply and (outcome.bucket == "changed" or (outcome.bucket == "stamp_only" and stamp_all)):
                counts["verified"] += 1
            if outcome.bucket == "failed":
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    abort = True
            else:
                consecutive_failures = 0

    pool_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=pool_workers) as executor:
        while True:
            while len(pending) < pool_workers and not abort:
                try:
                    row = next(row_iter)
                except StopIteration:
                    break
                pending.add(
                    executor.submit(
                        _process_row,
                        row,
                        storage=storage,
                        apply=apply,
                        stamp_all=stamp_all,
                    )
                )
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            _drain_done(done)

    return {
        "counts": counts,
        "changed_ids": changed_ids[:_MAX_CHANGED_IDS_IN_SUMMARY],
        "aborted": abort,
    }


def _print_summary(summary: dict[str, Any], *, apply: bool) -> None:
    counts = summary["counts"]
    changed_ids = summary.get("changed_ids") or []
    ids_suffix = ""
    if changed_ids:
        ids_suffix = " ids=" + ",".join(str(i) for i in changed_ids)
    print(
        f"  current={counts.get('current', 0)} "
        f"changed={counts.get('changed', 0)} "
        f"stamp_only={counts.get('stamp_only', 0)} "
        f"failed={counts.get('failed', 0)}"
        f"{ids_suffix}"
    )
    if apply:
        print(f"  verified={counts.get('verified', 0)}")
    if summary.get("aborted"):
        print("  aborted_after_consecutive_failures=yes")


def _cli_error(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-mask attachment extraction JSON blobs to U-535g rules (dry-run by default)."
    )
    ap.add_argument("--limit", type=int, default=None, help="Process at most N attachments.")
    ap.add_argument(
        "--ids",
        type=str,
        default=None,
        help="Comma-separated attachment ids (dbo.Attachment.Id).",
    )
    ap.add_argument("--workers", type=int, default=4, help="Parallel download/upload workers.")
    ap.add_argument("--apply", action="store_true", help="Upload remasked blobs (default: dry-run).")
    ap.add_argument(
        "--stamp-all",
        action="store_true",
        help="With --apply, also rewrite stamp_only rows (redaction_version only).",
    )
    args = ap.parse_args(argv)

    if args.workers < 1:
        return _cli_error("--workers must be >= 1")
    if args.limit is not None and args.limit < 0:
        return _cli_error("--limit must be >= 0 (0 means no rows)")

    try:
        attachment_ids = parse_attachment_ids(args.ids)
    except ValueError as exc:
        return _cli_error(str(exc))

    rows = fetch_attachment_rows(limit=args.limit, attachment_ids=attachment_ids)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: remask extraction blobs ===")
    print(f"rows: {len(rows)}")

    storage: _StorageLike = AzureBlobStorage()
    summary = remask_extraction_blobs(
        fetch_rows=lambda: rows,
        storage=storage,
        apply=args.apply,
        stamp_all=args.stamp_all,
        workers=args.workers,
    )
    _print_summary(summary, apply=args.apply)

    if not args.apply:
        print("DRY RUN: no blobs changed. Re-run with --apply to upload remasked JSON.")
    return 1 if summary["counts"].get("failed", 0) else 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
