"""
U-361b — the shared stale-identity-orphan matcher
(base/line_orphan_adopt.py::find_stale_identity_orphan).

Pure-logic tests: no DB, no connector, no primitive — just the selection rule
every line family's `readopt_candidate` closure shares. Mirrors the pre-U-361
`_match_unmapped_by_fingerprint`'s own selection contract (position-aware,
first-match-wins), generalized off the retired mapping table's "unmapped"
signal onto the dbo-only "not in this pull's live QBO line-id set" signal.
"""
from types import SimpleNamespace

from integrations.intuit.qbo.base.line_orphan_adopt import find_stale_identity_orphan


def _line(id, qbo_id, fp):
    return SimpleNamespace(id=id, qbo_id=qbo_id, fp=fp)


def _fingerprint(line):
    return line.fp


def test_no_lines_returns_none():
    assert find_stale_identity_orphan(
        existing_lines=[], live_qbo_line_ids=frozenset(), fingerprint=_fingerprint, target=("a",),
    ) is None


def test_matches_a_line_whose_identity_is_not_in_the_live_set():
    stale = _line(55, "1", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[stale],
        live_qbo_line_ids=frozenset({"2"}),  # "1" is no longer live -- "2" replaced it
        fingerprint=_fingerprint,
        target=("Materials", "500"),
    )
    assert result is stale


def test_never_matches_a_line_still_bound_to_a_live_id_even_with_an_identical_fingerprint():
    """The identity-theft guard: a line correctly bound elsewhere in THIS SAME
    pull must never be stolen, even if its content happens to match."""
    still_live = _line(55, "2", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[still_live],
        live_qbo_line_ids=frozenset({"2", "3"}),  # "2" IS live
        fingerprint=_fingerprint,
        target=("Materials", "500"),
    )
    assert result is None


def test_no_fingerprint_match_returns_none():
    stale = _line(55, "1", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[stale],
        live_qbo_line_ids=frozenset({"2"}),
        fingerprint=_fingerprint,
        target=("Labor", "999"),  # different content
    )
    assert result is None


def test_multiple_matches_pick_the_first_by_stable_position_order():
    """A 50-50 split, repeated draws: several stale orphans share a fingerprint.
    Pick by id (approximating creation order), not iteration order, so the
    caller's per-line loop pairs them 1:1 with the incoming QBO lines
    consistently across re-pulls."""
    later = _line(99, "1", ("Materials", "500"))
    earlier = _line(55, "5", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[later, earlier],  # deliberately out of id order
        live_qbo_line_ids=frozenset({"9"}),  # neither "1" nor "5" is live
        fingerprint=_fingerprint,
        target=("Materials", "500"),
    )
    assert result is earlier


def test_a_line_with_no_current_qbo_id_is_still_eligible():
    """None is trivially 'not in live_qbo_line_ids' (a real QBO line id is
    never None) — a row from a prior interrupted create (QboId still NULL)
    is just as eligible for readopt as a recycled-id orphan."""
    never_stamped = _line(55, None, ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[never_stamped],
        live_qbo_line_ids=frozenset({"9"}),
        fingerprint=_fingerprint,
        target=("Materials", "500"),
    )
    assert result is never_stamped


def test_target_and_fingerprint_compare_as_tuples_not_by_identity():
    """A list vs. a tuple carrying the same values must still compare equal —
    callers may build either shape."""
    stale = _line(55, "1", ["Materials", "500"])  # list, not tuple
    result = find_stale_identity_orphan(
        existing_lines=[stale],
        live_qbo_line_ids=frozenset({"2"}),
        fingerprint=_fingerprint,
        target=("Materials", "500"),  # tuple
    )
    assert result is stale
