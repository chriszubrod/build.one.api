"""U-778b Pass 2: pin the exact key list AND order of both counts dicts (captured from the pre-refactor code)."""
from integrations.intuit.qbo.billable_refresh.business.service import _EntityCounts, _ReverseCounts

_REVERSE_COMMON = [
    "candidate_lines", "headers_fetched", "headers_missing_live", "lines_missing_live", "reverse_still_billed",
    "reverse_charge_changed", "reverse_other_status", "dbo_flag_deduped", "dbo_already_unbilled", "dbo_unmapped",
    "dbo_unbill_errors", "flipped_to_billable", "cas_skipped", "cas_errors", "deferred_by_cap", "batch_errors",
]
_ENTITY_COMMON = [
    "staged_billable_lines", "headers_fetched", "headers_missing_live", "lines_missing_live",
    "flipped_to_has_been_billed", "flipped_to_not_billable", "cas_skipped", "dbo_heal_errors", "batch_errors",
    "unexpected_live_status",
]


def test_reverse_counts_key_order():
    assert list(_ReverseCounts().as_dict(dry_run=False)) == _REVERSE_COMMON + ["dbo_unbilled", "dbo_flagged"]
    assert list(_ReverseCounts().as_dict(dry_run=True)) == _REVERSE_COMMON + ["dbo_would_unbill", "dbo_would_flag"]


def test_entity_counts_key_order():
    assert list(_EntityCounts().as_dict(dry_run=False)) == _ENTITY_COMMON + ["dbo_healed"]
    assert list(_EntityCounts().as_dict(dry_run=True)) == _ENTITY_COMMON + ["dbo_would_heal"]
