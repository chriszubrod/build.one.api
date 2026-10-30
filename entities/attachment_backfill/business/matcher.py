"""U-535b: tier-A matcher — Box invoice files to unlinked bills (pure, no I/O).

Tier A = normalized invoice # equal AND amount equal (Decimal, exact, SAME SIGN) AND the bill's
line projects include the filename-prefix project OR the folder's project. Only tier A is
eligible (DESIGN §3.1). Credit files (negative amounts) are excluded: bill credits are a later
source. Copies of one invoice (same projects, invoice, amount and byte size) collapse to the
lowest Box id before claims are counted. A candidate must be mutually unique: exactly one bill
for the file, and that bill claimed by exactly one file (every tier-A hit is a claim). Anything
else is counted by reason and dropped.

Tier A uses the primary normalized invoice # of the filename's third segment ONLY. The round-1
alternate tiers (next segment as invoice, alpha-prefix alternate key) were removed in fix round 2:
they turned description / PO segments into unique tier-A candidates.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from entities.attachment_backfill.business.filename import (
    ParsedFilename,
    norm_invoice,
    parse_filename,
)
from entities.attachment_backfill.business.project_map import ProjectMap

TIER_A = "A"

EXCLUSION_REASONS = (
    "duplicate_listing",
    "unparsed",
    "pushed",
    "credit",
    "no_project",
    "duplicate_copy",
    "no_match",
    "ambiguous_file",
    "contested_bill",
)


@dataclass(frozen=True)
class InventoryFile:
    file_id: str
    name: str
    folder_id: Optional[str]
    project_folder_name: str
    size: Optional[int] = None


@dataclass(frozen=True)
class BillRow:
    id: int
    bill_number: Optional[str]
    total_amount: Optional[Decimal]
    project_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Candidate:
    bill_id: int
    file_id: str
    file_name: str
    folder_id: Optional[str]
    tier: str = TIER_A
    size: Optional[int] = None


@dataclass
class MatchResult:
    candidates: list[Candidate] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)
    files_seen: int = 0

    def counts(self) -> dict:
        out = {"files_seen": self.files_seen, "candidates": len(self.candidates)}
        for reason in EXCLUSION_REASONS:
            out[reason] = int(self.excluded.get(reason, 0))
        return out


Key = tuple[str, Decimal]
Index = dict[Key, list[BillRow]]


def _bill_index(bills: Iterable[BillRow]) -> Index:
    index: Index = {}
    for bill in bills:
        inv = norm_invoice(bill.bill_number)
        if not inv or bill.total_amount is None:
            continue
        index.setdefault((inv, Decimal(str(bill.total_amount))), []).append(bill)
    return index


def _box_order(file_id: str) -> tuple[int, int, str]:
    return (0, int(file_id), "") if file_id.isdigit() else (1, 0, file_id)


def _lookup(index: Index, key: str, amount: Decimal, projects: frozenset[int]) -> list[BillRow]:
    if not key:
        return []
    return [bill for bill in index.get((key, amount), []) if bill.project_ids & projects]


def match_tier_a(
    inventory: Iterable[InventoryFile],
    bills: Iterable[BillRow],
    project_map: ProjectMap,
) -> MatchResult:
    result = MatchResult()
    index = _bill_index(bills)
    seen_ids: set[str] = set()
    records: list[tuple[InventoryFile, ParsedFilename, frozenset[int]]] = []

    for item in inventory:
        result.files_seen += 1
        if item.file_id in seen_ids:
            result.excluded["duplicate_listing"] += 1
            continue
        seen_ids.add(item.file_id)

        parsed = parse_filename(item.name)
        if parsed is None:
            result.excluded["unparsed"] += 1
            continue
        if parsed.pushed:
            result.excluded["pushed"] += 1
            continue
        if parsed.amount < 0:
            result.excluded["credit"] += 1
            continue

        projects = frozenset(
            p for p in (project_map.resolve_prefix(parsed.project), project_map.map_folder(item.project_folder_name)) if p
        )
        if not projects:
            result.excluded["no_project"] += 1
            continue
        records.append((item, parsed, projects))

    # Collapse byte-size-identical copies of one invoice to the lowest Box id.
    records.sort(key=lambda r: _box_order(r[0].file_id))
    kept: list[tuple[InventoryFile, ParsedFilename, frozenset[int]]] = []
    copy_keys: set[tuple] = set()
    for item, parsed, projects in records:
        if item.size is not None:
            copy_key = (projects, parsed.invoice_norm, parsed.amount, item.size)
            if copy_key in copy_keys:
                result.excluded["duplicate_copy"] += 1
                continue
            copy_keys.add(copy_key)
        kept.append((item, parsed, projects))

    # Every tier-A hit is a claim, including an ambiguous file's: a bill some other file also
    # matches at tier A is contested even when that other file was itself dropped.
    claims: Counter = Counter()
    tentative: list[tuple[InventoryFile, BillRow]] = []
    for item, parsed, projects in kept:
        hits = _lookup(index, parsed.invoice_norm, parsed.amount, projects)
        if not hits:
            result.excluded["no_match"] += 1
            continue
        claims.update(bill.id for bill in hits)
        if len(hits) > 1:
            result.excluded["ambiguous_file"] += 1
            continue
        tentative.append((item, hits[0]))

    for item, bill in tentative:
        if claims[bill.id] > 1:
            result.excluded["contested_bill"] += 1
            continue
        result.candidates.append(
            Candidate(
                bill_id=bill.id,
                file_id=item.file_id,
                file_name=item.name,
                folder_id=item.folder_id,
                tier=TIER_A,
                size=item.size,
            )
        )
    return result
