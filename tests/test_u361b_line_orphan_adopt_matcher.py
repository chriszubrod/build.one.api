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


def test_mixed_type_ids_normalize_before_membership_check():
    """U-361c hardening: correctness must not depend on the caller keeping
    `qbo_id` and `live_qbo_line_ids` the same type. A clone family could build
    `live_qbo_line_ids` from int-typed staging ids while dbo `qbo_id` comes
    back str (or vice versa) - see feedback_qbo_dbo_id_keyspaces. A line whose
    str qbo_id canonically matches an int in the live set must still be
    treated as LIVE (never stolen); a line with no live counterpart of any
    type must still be eligible."""
    still_live = _line(1, "1", ("Materials", "500"))  # str "1" == int 1, canonically
    stale = _line(2, "3", ("Materials", "500"))  # "3" not live under any type
    result = find_stale_identity_orphan(
        existing_lines=[still_live, stale],
        live_qbo_line_ids=frozenset({1, 2}),  # int-typed
        fingerprint=_fingerprint,
        target=("Materials", "500"),
    )
    assert result is stale


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


# ---------------------------------------------------------------------------
# U-362c: optional position_key override
# ---------------------------------------------------------------------------


def test_position_key_overrides_default_id_ordering():
    """U-362c: a caller with a MORE precise position signal than dbo.Id (e.g.
    the source document's own LineNum) can override the ordering. Two
    candidates share a fingerprint; default `.id` order would pick the
    lower-id one, but a custom position_key can rank the OTHER first."""
    lower_id = _line(1, "1", ("Materials", "500"))
    higher_id = _line(2, "1", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[lower_id, higher_id],
        live_qbo_line_ids=frozenset({"9"}),
        fingerprint=_fingerprint,
        target=("Materials", "500"),
        position_key=lambda li: -li.id,  # reverses the default order
    )
    assert result is higher_id


def test_position_key_omitted_preserves_default_id_ordering():
    """Backward-compatibility: every existing caller (Manual fingerprint
    readopt, BillCreditLineItem) doesn't pass position_key and must keep the
    original `.id`-based selection unchanged."""
    later = _line(5, "1", ("Materials", "500"))
    earlier = _line(1, "1", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[later, earlier],  # deliberately out of id order
        live_qbo_line_ids=frozenset({"9"}),
        fingerprint=_fingerprint,
        target=("Materials", "500"),
    )
    assert result is earlier


def test_position_key_still_respects_the_content_filter_and_theft_guard():
    """A precise position_key does not bypass the OTHER two rules: a
    non-matching fingerprint or a still-live qbo_id must still exclude a
    candidate, even if position_key would otherwise rank it first."""
    content_mismatch = _line(1, "1", ("Labor", "999"))
    still_live = _line(2, "3", ("Materials", "500"))
    result = find_stale_identity_orphan(
        existing_lines=[content_mismatch, still_live],
        live_qbo_line_ids=frozenset({"3"}),  # "3" IS live
        fingerprint=_fingerprint,
        target=("Materials", "500"),
        position_key=lambda li: li.id,
    )
    assert result is None
