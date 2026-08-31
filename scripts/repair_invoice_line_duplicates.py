"""
Delete duplicate unmapped Manual dbo.InvoiceLineItem rows left by the U-247
fingerprint-adoption bug (U-264).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY (SELECTs
only) and prints fleet-wide counts, per-invoice breakdown, and the full repair-set
listing. --apply deletes via InvoiceLineItemService.delete_by_public_id (never raw
SQL DELETE) one invoice at a time, re-verifying each row immediately before delete.

Usage:
  PYTHONPATH=. python scripts/repair_invoice_line_duplicates.py
  PYTHONPATH=. python scripts/repair_invoice_line_duplicates.py --manifest-out repair_manifest.json
  # --expected-count must match the manifest line count from dry-run (repair_set=N on PRE line).
  PYTHONPATH=. python scripts/repair_invoice_line_duplicates.py \\
    --apply --manifest repair_manifest.json --expected-count N
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Optional, Sequence

from entities.invoice_line_item.business.service import InvoiceLineItemService
from integrations.intuit.qbo.base.locking import qbo_entity_sync_lock_resource, qbo_sync_lock
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("repair_invoice_line_duplicates")

QBO_INVOICE_SYNC_LOCK = qbo_entity_sync_lock_resource("invoice")
QBO_INVOICE_SYNC_LOCK_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class MappedQboLineContent:
    description: Optional[str]
    amount: Optional[Decimal]


@dataclass(frozen=True)
class LocalInvoiceLineRow:
    id: int
    public_id: str
    invoice_id: int
    invoice_number: str
    source_type: str
    description: Optional[str]
    amount: Optional[Decimal]
    is_mapped: bool
    mapped_qbo_line: Optional[MappedQboLineContent] = None


@dataclass(frozen=True)
class QboInvoiceLineRow:
    description: Optional[str]
    amount: Optional[Decimal]


@dataclass(frozen=True)
class RepairEntry:
    row_id: int
    public_id: str
    invoice_id: int
    invoice_number: str
    description: Optional[str]
    amount: Optional[Decimal]
    mapped_sibling_id: int
    qbo_line_count: int
    local_mapped_count: int


@dataclass(frozen=True)
class HeldBackEntry:
    row_id: int
    public_id: str
    invoice_id: int
    invoice_number: str
    description: Optional[str]
    amount: Optional[Decimal]
    reason: str


@dataclass(frozen=True)
class ClassificationResult:
    repair: tuple[RepairEntry, ...]
    held_back: tuple[HeldBackEntry, ...]
    total_unmapped_manual: int


def normalize_fingerprint(description: Optional[str], amount) -> tuple[str, str]:
    desc = (description or "").strip()
    amt = "" if amount is None else format(Decimal(str(amount)).normalize(), "f")
    return (desc, amt)


def _mapped_qbo_fingerprint(row: LocalInvoiceLineRow) -> Optional[tuple[str, str]]:
    if row.mapped_qbo_line is None:
        return None
    return normalize_fingerprint(row.mapped_qbo_line.description, row.mapped_qbo_line.amount)


def classify_invoice_line_items(
    local_rows: Sequence[LocalInvoiceLineRow],
    qbo_lines_by_invoice: Mapping[int, Sequence[QboInvoiceLineRow]],
) -> ClassificationResult:
    """Pure predicate: partition unmapped Manual rows into repair vs held-back."""
    by_invoice: dict[int, list[LocalInvoiceLineRow]] = defaultdict(list)
    total_unmapped_manual = 0
    for row in local_rows:
        by_invoice[row.invoice_id].append(row)
        if row.source_type == "Manual" and not row.is_mapped:
            total_unmapped_manual += 1

    repair: list[RepairEntry] = []
    held_back: list[HeldBackEntry] = []

    for invoice_id, invoice_rows in by_invoice.items():
        qbo_lines = qbo_lines_by_invoice.get(invoice_id, ())
        qbo_fp_counts: dict[tuple[str, str], int] = defaultdict(int)
        for ql in qbo_lines:
            qbo_fp_counts[normalize_fingerprint(ql.description, ql.amount)] += 1

        verified_mapped_by_fp: dict[tuple[str, str], list[LocalInvoiceLineRow]] = defaultdict(list)
        for sibling in invoice_rows:
            if sibling.source_type != "Manual" or not sibling.is_mapped:
                continue
            fp_local = normalize_fingerprint(sibling.description, sibling.amount)
            qbo_fp = _mapped_qbo_fingerprint(sibling)
            if qbo_fp is not None and qbo_fp == fp_local:
                verified_mapped_by_fp[fp_local].append(sibling)

        for row in invoice_rows:
            if row.source_type != "Manual" or row.is_mapped:
                continue

            def _held_back(reason: str) -> HeldBackEntry:
                return HeldBackEntry(
                    row_id=row.id,
                    public_id=row.public_id,
                    invoice_id=row.invoice_id,
                    invoice_number=row.invoice_number,
                    description=row.description,
                    amount=row.amount,
                    reason=reason,
                )

            fp = normalize_fingerprint(row.description, row.amount)
            mapped_siblings = verified_mapped_by_fp.get(fp, ())
            if not mapped_siblings:
                held_back.append(_held_back("no_sibling"))
                continue

            local_mapped_count = len(mapped_siblings)
            qbo_line_count = qbo_fp_counts.get(fp, 0)

            if qbo_line_count > local_mapped_count:
                held_back.append(_held_back("unclaimed_qbo_line"))
                continue

            repair.append(
                RepairEntry(
                    row_id=row.id,
                    public_id=row.public_id,
                    invoice_id=row.invoice_id,
                    invoice_number=row.invoice_number,
                    description=row.description,
                    amount=row.amount,
                    mapped_sibling_id=min(s.id for s in mapped_siblings),
                    qbo_line_count=qbo_line_count,
                    local_mapped_count=local_mapped_count,
                )
            )

    repair.sort(key=lambda e: (e.invoice_id, e.row_id))
    held_back.sort(key=lambda e: (e.invoice_id, e.row_id))
    return ClassificationResult(
        repair=tuple(repair),
        held_back=tuple(held_back),
        total_unmapped_manual=total_unmapped_manual,
    )


def repair_entry_for_row(
    local_rows: Sequence[LocalInvoiceLineRow],
    qbo_lines_by_invoice: Mapping[int, Sequence[QboInvoiceLineRow]],
    row_id: int,
) -> Optional[RepairEntry]:
    """Return the repair entry for row_id if it still satisfies the full predicate."""
    target = next((r for r in local_rows if r.id == row_id), None)
    if not target:
        return None
    result = classify_invoice_line_items(local_rows, qbo_lines_by_invoice)
    for entry in result.repair:
        if entry.row_id == row_id:
            return entry
    return None


def _str_public_id(value) -> str:
    return str(value) if value is not None else ""


def _local_rows_query(*, invoice_id: Optional[int] = None) -> str:
    where = "WHERE ili.[InvoiceId] = ?" if invoice_id is not None else ""
    return f"""
    SELECT
        ili.[Id],
        ili.[PublicId],
        ili.[InvoiceId],
        inv.[InvoiceNumber],
        ili.[SourceType],
        ili.[Description],
        ili.[Amount],
        CASE WHEN map.[Id] IS NOT NULL THEN 1 ELSE 0 END AS IsMapped,
        mapped_ql.[Description] AS MappedQboDescription,
        mapped_ql.[Amount] AS MappedQboAmount
    FROM dbo.[InvoiceLineItem] ili
    INNER JOIN dbo.[Invoice] inv ON inv.[Id] = ili.[InvoiceId]
    LEFT JOIN qbo.[InvoiceLineItemInvoiceLine] map ON map.[InvoiceLineItemId] = ili.[Id]
    LEFT JOIN qbo.[InvoiceLine] mapped_ql ON mapped_ql.[Id] = map.[QboInvoiceLineId]
    {where}
    ORDER BY ili.[InvoiceId], ili.[Id]
    """


def _qbo_lines_query(*, invoice_id: Optional[int] = None) -> str:
    where = "WHERE i.[Id] = ?" if invoice_id is not None else ""
    return f"""
    SELECT
        i.[Id] AS InvoiceId,
        ql.[Description],
        ql.[Amount]
    FROM dbo.[Invoice] i
    INNER JOIN qbo.[InvoiceInvoice] ii ON ii.[InvoiceId] = i.[Id]
    INNER JOIN qbo.[InvoiceLine] ql ON ql.[QboInvoiceId] = ii.[QboInvoiceId]
    {where}
    ORDER BY i.[Id], ql.[Id]
    """


def _row_from_db(raw) -> LocalInvoiceLineRow:
    mapped_qbo_line: Optional[MappedQboLineContent] = None
    if bool(raw.IsMapped):
        mapped_qbo_line = MappedQboLineContent(
            description=raw.MappedQboDescription,
            amount=raw.MappedQboAmount
            if raw.MappedQboAmount is None
            else Decimal(str(raw.MappedQboAmount)),
        )
    return LocalInvoiceLineRow(
        id=int(raw.Id),
        public_id=_str_public_id(raw.PublicId),
        invoice_id=int(raw.InvoiceId),
        invoice_number=str(raw.InvoiceNumber or ""),
        source_type=str(raw.SourceType or ""),
        description=raw.Description,
        amount=raw.Amount if raw.Amount is None else Decimal(str(raw.Amount)),
        is_mapped=bool(raw.IsMapped),
        mapped_qbo_line=mapped_qbo_line,
    )


def _execute_scoped(cursor, query_fn, invoice_id: Optional[int]) -> None:
    if invoice_id is not None:
        cursor.execute(query_fn(invoice_id=invoice_id), (invoice_id,))
    else:
        cursor.execute(query_fn())


def fetch_local_rows(cursor, *, invoice_id: Optional[int] = None) -> list[LocalInvoiceLineRow]:
    _execute_scoped(cursor, _local_rows_query, invoice_id)
    return [_row_from_db(r) for r in cursor.fetchall()]


def fetch_qbo_lines_by_invoice(cursor, *, invoice_id: Optional[int] = None) -> dict[int, list[QboInvoiceLineRow]]:
    _execute_scoped(cursor, _qbo_lines_query, invoice_id)
    out: dict[int, list[QboInvoiceLineRow]] = defaultdict(list)
    for raw in cursor.fetchall():
        inv_id = int(raw.InvoiceId)
        out[inv_id].append(
            QboInvoiceLineRow(
                description=raw.Description,
                amount=raw.Amount if raw.Amount is None else Decimal(str(raw.Amount)),
            )
        )
    return dict(out)


def load_classification(cursor, *, invoice_id: Optional[int] = None) -> ClassificationResult:
    local_rows = fetch_local_rows(cursor, invoice_id=invoice_id)
    qbo_lines = fetch_qbo_lines_by_invoice(cursor, invoice_id=invoice_id)
    return classify_invoice_line_items(local_rows, qbo_lines)


def _invoice_breakdown(
    result: ClassificationResult,
) -> dict[int, dict[str, int | str]]:
    breakdown: dict[int, dict[str, int | str]] = defaultdict(
        lambda: {
            "invoice_number": "",
            "repair": 0,
            "held_back": 0,
            "held_back_no_sibling": 0,
            "held_back_unclaimed_qbo_line": 0,
        }
    )
    for entry in result.repair:
        slot = breakdown[entry.invoice_id]
        slot["invoice_number"] = entry.invoice_number
        slot["repair"] = int(slot["repair"]) + 1
    for entry in result.held_back:
        slot = breakdown[entry.invoice_id]
        slot["invoice_number"] = entry.invoice_number
        slot["held_back"] = int(slot["held_back"]) + 1
        if entry.reason == "no_sibling":
            slot["held_back_no_sibling"] = int(slot["held_back_no_sibling"]) + 1
        elif entry.reason == "unclaimed_qbo_line":
            slot["held_back_unclaimed_qbo_line"] = int(slot["held_back_unclaimed_qbo_line"]) + 1
    return dict(breakdown)


def _print_pre_counts(result: ClassificationResult) -> None:
    held_no_sibling = sum(1 for h in result.held_back if h.reason == "no_sibling")
    held_unclaimed = sum(1 for h in result.held_back if h.reason == "unclaimed_qbo_line")

    print(
        f"\nPRE fleet-wide: unmapped_manual={result.total_unmapped_manual} "
        f"repair_set={len(result.repair)} held_back={len(result.held_back)} "
        f"(no_sibling={held_no_sibling} unclaimed_qbo_line={held_unclaimed})"
    )

    breakdown = _invoice_breakdown(result)
    if not breakdown:
        print("  (no unmapped Manual candidates with per-invoice activity)")
        return

    print("\nPer-invoice breakdown (invoices with repair and/or held-back rows):")
    for inv_id in sorted(breakdown):
        slot = breakdown[inv_id]
        print(
            f"  Invoice {slot['invoice_number']} (id={inv_id}): "
            f"repair={slot['repair']} held_back={slot['held_back']} "
            f"(no_sibling={slot['held_back_no_sibling']} "
            f"unclaimed_qbo_line={slot['held_back_unclaimed_qbo_line']})"
        )


def _print_repair_listing(result: ClassificationResult) -> None:
    print(f"\nRepair set ({len(result.repair)} row(s)):")
    if not result.repair:
        print("  (empty)")
        return
    for entry in result.repair:
        print(
            f"  Id={entry.row_id} PublicId={entry.public_id} "
            f"InvoiceNumber={entry.invoice_number} "
            f"Description={entry.description!r} Amount={entry.amount} "
            f"MappedSiblingId={entry.mapped_sibling_id} "
            f"qbo_line_count={entry.qbo_line_count} "
            f"local_mapped_count={entry.local_mapped_count}"
        )


def _print_held_back_listing(result: ClassificationResult) -> None:
    if not result.held_back:
        return
    print(f"\nHeld back ({len(result.held_back)} row(s)) — will NOT be deleted:")
    for entry in result.held_back:
        print(
            f"  Id={entry.row_id} PublicId={entry.public_id} "
            f"InvoiceNumber={entry.invoice_number} reason={entry.reason} "
            f"Description={entry.description!r} Amount={entry.amount}"
        )


def _print_invoice_snapshot(label: str, invoice_id: int, result: ClassificationResult) -> None:
    repair_n = sum(1 for e in result.repair if e.invoice_id == invoice_id)
    held_n = sum(1 for e in result.held_back if e.invoice_id == invoice_id)
    inv_num = next(
        (e.invoice_number for e in result.repair if e.invoice_id == invoice_id),
        "",
    ) or next(
        (e.invoice_number for e in result.held_back if e.invoice_id == invoice_id),
        "",
    )
    print(
        f"  {label} Invoice {inv_num or '?'} (id={invoice_id}): "
        f"repair_set={repair_n} held_back={held_n}"
    )


def _check_expected_count(
    actual: int,
    expected: Optional[int],
    *,
    apply: bool,
) -> bool:
    if expected is None:
        return True
    if actual == expected:
        return True
    msg = (
        f"repair_set size {actual} does NOT match --expected-count {expected} "
        f"— refuse to proceed; re-run dry-run and re-review live counts"
    )
    if apply:
        logger.error(msg)
        return False
    logger.warning("*** %s (dry-run continues — read-only) ***", msg)
    return True


def write_manifest(path: Path, repair_entries: Sequence[RepairEntry]) -> None:
    public_ids = [entry.public_id for entry in repair_entries]
    path.write_text(json.dumps(public_ids, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote manifest with %s PublicId(s) to %s", len(public_ids), path)


def read_manifest(path: Path) -> tuple[str, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"manifest must be a JSON list of PublicId strings: {path}")
    return tuple(raw)


def filter_repair_entries_by_manifest(
    repair_entries: Sequence[RepairEntry],
    manifest_public_ids: Sequence[str],
) -> tuple[RepairEntry, ...]:
    allowed = frozenset(manifest_public_ids)
    return tuple(entry for entry in repair_entries if entry.public_id in allowed)


def _print_apply_partial_summary(
    *,
    deleted: int,
    skipped: int,
    planned: int,
    not_yet_attempted: int,
) -> None:
    print(
        f"\nAPPLY partial summary (interrupted): deleted={deleted} skipped={skipped} "
        f"planned={planned} not_yet_attempted={not_yet_attempted}"
    )


def apply_repairs(
    cursor,
    repair_entries: Sequence[RepairEntry],
    *,
    batch_size: int,
    manifest_public_ids: Optional[frozenset[str]] = None,
) -> tuple[int, int, bool]:
    """Delete repair-set rows invoice-by-invoice. Returns (deleted, skipped, all_ok)."""
    service = InvoiceLineItemService()
    by_invoice: dict[int, list[RepairEntry]] = defaultdict(list)
    for entry in repair_entries:
        if manifest_public_ids is not None and entry.public_id not in manifest_public_ids:
            continue
        by_invoice[entry.invoice_id].append(entry)

    deleted = 0
    skipped = 0
    planned_total = sum(len(rows) for rows in by_invoice.values())
    attempted = 0

    for invoice_id in sorted(by_invoice):
        planned = by_invoice[invoice_id]
        inv_num = planned[0].invoice_number if planned else "?"
        print(f"\n--- APPLY invoice {inv_num} (id={invoice_id}) — {len(planned)} planned row(s) ---")

        before = load_classification(cursor, invoice_id=invoice_id)
        _print_invoice_snapshot("BEFORE", invoice_id, before)

        remaining = list(planned)
        while remaining:
            batch = remaining[:batch_size]
            remaining = remaining[batch_size:]

            for entry in batch:
                attempted += 1
                try:
                    fresh_local = fetch_local_rows(cursor, invoice_id=invoice_id)
                    fresh_qbo = fetch_qbo_lines_by_invoice(cursor, invoice_id=invoice_id)
                    still = repair_entry_for_row(fresh_local, fresh_qbo, entry.row_id)
                    if not still:
                        logger.warning(
                            "SKIP Id=%s PublicId=%s — row no longer satisfies repair predicate "
                            "(likely mapped by a concurrent QBO pull); not deleting",
                            entry.row_id,
                            entry.public_id,
                        )
                        skipped += 1
                        continue

                    logger.info(
                        "Deleting Id=%s PublicId=%s InvoiceNumber=%s",
                        entry.row_id,
                        entry.public_id,
                        entry.invoice_number,
                    )
                    service.delete_by_public_id(public_id=entry.public_id)
                    deleted += 1
                except Exception:
                    logger.exception(
                        "DELETE FAILED Id=%s PublicId=%s InvoiceNumber=%s",
                        entry.row_id,
                        entry.public_id,
                        entry.invoice_number,
                    )
                    _print_apply_partial_summary(
                        deleted=deleted,
                        skipped=skipped,
                        planned=planned_total,
                        not_yet_attempted=planned_total - attempted,
                    )
                    raise

            mid = load_classification(cursor, invoice_id=invoice_id)
            _print_invoice_snapshot("MID-BATCH", invoice_id, mid)

        after = load_classification(cursor, invoice_id=invoice_id)
        _print_invoice_snapshot("AFTER", invoice_id, after)
        repair_remaining = sum(1 for e in after.repair if e.invoice_id == invoice_id)
        if repair_remaining != 0:
            logger.error(
                "Post-invoice verification FAILED for invoice id=%s — "
                "repair_set count=%s (expected 0)",
                invoice_id,
                repair_remaining,
            )
            return deleted, skipped, False

    return deleted, skipped, True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Repair duplicate unmapped Manual InvoiceLineItem rows (dry-run by default)."
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Delete repair-set rows via InvoiceLineItemService.delete_by_public_id.",
    )
    ap.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Required with --apply: exact repair-set size guard before any delete.",
    )
    ap.add_argument(
        "--manifest-out",
        type=Path,
        default=None,
        help="Dry-run only: write the reviewed repair-set PublicIds to this JSON file.",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Required with --apply: JSON manifest of PublicIds from an earlier dry-run.",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Sub-batch size within each invoice during --apply (default 20).",
    )
    args = ap.parse_args()

    if args.apply and args.expected_count is None:
        logger.error("--expected-count N is REQUIRED when --apply is passed")
        return 1
    if args.apply and args.manifest is None:
        logger.error("--manifest PATH is REQUIRED when --apply is passed")
        return 1
    if args.manifest_out is not None and args.apply:
        logger.error("--manifest-out is dry-run only — omit when --apply is passed")
        return 1
    if args.batch_size < 1:
        logger.error("--batch-size must be >= 1")
        return 1

    assert_cli_system_admin()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: repair duplicate unmapped Manual InvoiceLineItem rows (U-264) ===")

    with get_connection() as conn:
        cur = conn.cursor()
        result = load_classification(cur)

        _print_pre_counts(result)
        _print_repair_listing(result)
        _print_held_back_listing(result)

        if args.manifest_out is not None:
            write_manifest(args.manifest_out, result.repair)

        if not args.apply:
            if not _check_expected_count(len(result.repair), args.expected_count, apply=False):
                return 1
            print(
                "\nDry-run complete — no mutations. Re-run with "
                "--apply --manifest PATH --expected-count N after review."
            )
            return 0

        try:
            manifest_public_ids = read_manifest(args.manifest)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Failed to read --manifest %s: %s", args.manifest, exc)
            return 1

        if not _check_expected_count(len(manifest_public_ids), args.expected_count, apply=True):
            return 1

        manifest_filtered = filter_repair_entries_by_manifest(result.repair, manifest_public_ids)
        if len(manifest_filtered) != len(manifest_public_ids):
            in_manifest_not_repairable = len(manifest_public_ids) - len(manifest_filtered)
            logger.warning(
                "%s manifest PublicId(s) no longer in the live repair set — will skip at apply time",
                in_manifest_not_repairable,
            )

        lock_ctx = qbo_sync_lock("invoice", timeout_ms=QBO_INVOICE_SYNC_LOCK_TIMEOUT_MS)
        with lock_ctx as got_lock:
            if not got_lock:
                logger.error(
                    "Could not acquire %s applock within %sms — refuse to apply "
                    "(concurrent QBO invoice sync may be running); zero mutations",
                    QBO_INVOICE_SYNC_LOCK,
                    QBO_INVOICE_SYNC_LOCK_TIMEOUT_MS,
                )
                return 1

            deleted, skipped, all_ok = apply_repairs(
                cur,
                result.repair,
                batch_size=args.batch_size,
                manifest_public_ids=frozenset(manifest_public_ids),
            )
        print(
            f"\nAPPLY summary: deleted={deleted} skipped={skipped} "
            f"planned={len(manifest_filtered)} manifest={len(manifest_public_ids)}"
        )
        if skipped:
            logger.warning(
                "%s row(s) skipped because predicate no longer held at delete time — "
                "re-run dry-run to confirm remaining repair-set size",
                skipped,
            )
        if not all_ok:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
