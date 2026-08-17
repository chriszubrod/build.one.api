"""U-242 RC fingerprint measurement helpers (scripts/analyze_rc_source_fingerprint.py).

MatchOutcome (unmatched/unique/ambiguous) and the A/B/C tier letters are this
measurement unit's internal vocabulary only. The production reconciliation path
(entities/invoice/business/reconciliation.py) does NOT import or call this
module; it uses its own status vocabulary (no_match/linkable/ambiguous/
already_linked/etc.) plus the numeric Tier scale (0-3) defined in
ProposeInvoiceSourceLinks.
"""

# Python Standard Library Imports
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Optional, TypeAlias

# Third-party Imports

# Local Imports
from shared.api.money import round_money

Tier = Literal["A", "B", "C"]
MatchOutcome = Literal["unmatched", "unique", "ambiguous"]
LineKind = Literal["base", "derivative", "skip"]

_AMOUNT_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class RcBaseLine:
    """One billable ReimburseCharge line eligible for fingerprint matching."""

    rc_id: str
    customer_ref_value: Optional[str]
    amount: Decimal
    txn_date: str
    description: Optional[str]
    item_ref_value: Optional[str]
    has_been_invoiced: Optional[bool]


@dataclass(frozen=True)
class SourceCandidate:
    """One staged qbo BillLine or PurchaseLine candidate for matching."""

    source_type: str
    customer_ref_value: Optional[str]
    amount: Decimal
    description: str
    txn_date: str
    item_ref_value: Optional[str]
    doc_number: Optional[str]
    vendor_or_entity_name: Optional[str]
    qbo_line_id: int
    mapped_dbo_id: Optional[int]


TierAKey: TypeAlias = tuple[Optional[str], str, str]
CandidateIndex: TypeAlias = dict[TierAKey, list[SourceCandidate]]


def tier_a_key(
    customer_ref_value: Optional[str],
    amount: Decimal,
    txn_date: str,
) -> TierAKey:
    """Hashable Tier-A lookup key (customer, amount@2dp, txn_date)."""
    return (
        customer_ref_value,
        str(round_money(amount)),
        txn_date,
    )


def tier_a_key_for_rc_line(rc_line: RcBaseLine) -> TierAKey:
    return tier_a_key(rc_line.customer_ref_value, rc_line.amount, rc_line.txn_date)


def tier_a_key_for_candidate(candidate: SourceCandidate) -> TierAKey:
    return tier_a_key(
        candidate.customer_ref_value,
        candidate.amount,
        (candidate.txn_date or "")[:10],
    )


def normalize_linked_txn(linked: Any) -> list[dict]:
    """Normalize QBO LinkedTxn (bare dict or list) into a list of dicts."""
    if linked is None:
        return []
    if isinstance(linked, dict):
        return [linked]
    if isinstance(linked, list):
        return [lt for lt in linked if isinstance(lt, dict)]
    return []


def collect_linked_txn_type_counts(
    raw_records: list[dict],
) -> tuple[Counter[str], Counter[str]]:
    """
    Count LinkedTxn TxnType at header and line level, split by HasBeenInvoiced.

    Returns (invoiced_counter, uninvoiced_counter).
    """
    invoiced: Counter[str] = Counter()
    uninvoiced: Counter[str] = Counter()

    for rc in raw_records:
        rc = rc or {}
        has_been_invoiced = bool(rc.get("HasBeenInvoiced"))
        bucket = invoiced if has_been_invoiced else uninvoiced

        for lt in normalize_linked_txn(rc.get("LinkedTxn")):
            txn_type = lt.get("TxnType")
            if txn_type is not None:
                bucket[str(txn_type)] += 1

        for line in rc.get("Line") or []:
            if not isinstance(line, dict):
                continue
            for lt in normalize_linked_txn(line.get("LinkedTxn")):
                txn_type = lt.get("TxnType")
                if txn_type is not None:
                    bucket[str(txn_type)] += 1

    return invoiced, uninvoiced


def classify_reimburse_line(line: dict) -> LineKind:
    """
    Classify one RC line for fingerprint scope.

    - base: ReimburseLineDetail with ItemRef (real billable charge)
    - derivative: ReimburseLineDetail without ItemRef (markup synthesis)
    - skip: any other DetailType
    """
    line = line or {}
    detail_type = line.get("DetailType")
    if detail_type != "ReimburseLineDetail":
        return "skip"

    detail = line.get("ReimburseLineDetail") or {}
    if not isinstance(detail, dict):
        return "skip"

    if detail.get("ItemRef") is not None:
        return "base"
    return "derivative"


def parse_rc_base_lines(raw_rc: dict) -> tuple[list[RcBaseLine], int, int]:
    """
    Parse one raw QBO ReimburseCharge into base fingerprint lines.

    Returns (base_lines, derivative_count, skipped_other_detail_type_count).
    """
    raw_rc = raw_rc or {}
    rc_id = str(raw_rc.get("Id") or "")
    customer_ref = raw_rc.get("CustomerRef") or {}
    if not isinstance(customer_ref, dict):
        customer_ref = {}

    has_been_invoiced = raw_rc.get("HasBeenInvoiced")
    if has_been_invoiced is not None:
        has_been_invoiced = bool(has_been_invoiced)

    txn_date_raw = raw_rc.get("TxnDate")
    txn_date = str(txn_date_raw)[:10] if txn_date_raw else ""

    base_lines: list[RcBaseLine] = []
    derivative_count = 0
    skipped_count = 0

    for line in raw_rc.get("Line") or []:
        if not isinstance(line, dict):
            continue

        kind = classify_reimburse_line(line)
        if kind == "derivative":
            derivative_count += 1
            continue
        if kind == "skip":
            skipped_count += 1
            continue

        detail = line.get("ReimburseLineDetail") or {}
        item_ref = detail.get("ItemRef") or {}
        if not isinstance(item_ref, dict):
            item_ref = {}

        base_lines.append(
            RcBaseLine(
                rc_id=rc_id,
                customer_ref_value=str(customer_ref.get("value")) if customer_ref.get("value") is not None else None,
                amount=round_money(Decimal(str(line["Amount"]))),
                txn_date=txn_date,
                description=line.get("Description"),
                item_ref_value=str(item_ref.get("value")) if item_ref.get("value") is not None else None,
                has_been_invoiced=has_been_invoiced,
            )
        )

    return base_lines, derivative_count, skipped_count


def _tier_a_match(rc_line: RcBaseLine, candidate: SourceCandidate) -> bool:
    if rc_line.customer_ref_value != candidate.customer_ref_value:
        return False
    if abs(rc_line.amount - candidate.amount) >= _AMOUNT_TOLERANCE:
        return False
    candidate_date = (candidate.txn_date or "")[:10]
    if rc_line.txn_date != candidate_date:
        return False
    return True


def _tier_b_match(rc_line: RcBaseLine, candidate: SourceCandidate) -> bool:
    if not _tier_a_match(rc_line, candidate):
        return False
    rc_desc = rc_line.description or ""
    return rc_desc == candidate.description


def _tier_c_match(rc_line: RcBaseLine, candidate: SourceCandidate) -> bool:
    if not _tier_b_match(rc_line, candidate):
        return False
    return rc_line.item_ref_value == candidate.item_ref_value


_MATCHERS = {"A": _tier_a_match, "B": _tier_b_match, "C": _tier_c_match}


def tier_match(
    rc_line: RcBaseLine,
    candidates: list[SourceCandidate],
    tier: Tier,
) -> list[SourceCandidate]:
    """Return all candidates matching the RC line at the requested tier."""
    matcher = _MATCHERS[tier]
    return [c for c in candidates if matcher(rc_line, c)]


def build_candidate_index(candidates: list[SourceCandidate]) -> CandidateIndex:
    """Bucket candidates by Tier-A key for indexed matching."""
    index: CandidateIndex = {}
    for candidate in candidates:
        key = tier_a_key_for_candidate(candidate)
        index.setdefault(key, []).append(candidate)
    return index


def tier_match_indexed(
    rc_line: RcBaseLine,
    index: CandidateIndex,
    tier: Tier,
) -> list[SourceCandidate]:
    """Return tier matches using a prebuilt Tier-A index (same results as tier_match)."""
    bucket = index.get(tier_a_key_for_rc_line(rc_line), [])
    return tier_match(rc_line, bucket, tier)


def match_outcome(matches: list[SourceCandidate]) -> MatchOutcome:
    """Classify a tier's candidate list as unmatched, unique, or ambiguous."""
    if len(matches) == 0:
        return "unmatched"
    if len(matches) == 1:
        return "unique"
    return "ambiguous"
