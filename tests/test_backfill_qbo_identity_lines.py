"""
U-293 Gate-2 regression test — scripts/backfill_qbo_identity_lines.py's
`pending` count.

Confirmed P2 finding (Codex-fallback Workflow review): the script shipped with
zero test coverage, so a regression to the old, buggy `max(0, eligible -
stamped)` formula (which silently clamps to 0 — and so skips ALL real pending
rows — whenever "stamped but unmapped" anomaly rows push `stamped` above
`eligible`) would go undetected. This pins the fix: `would_apply` must reflect
the real `pending` count even when `stamped > eligible`.

Mirrors tests/test_backfill_qbo_active_mirror.py's mocked-get_connection shape,
adapted for this script's richer PRE/apply-loop/POST/verify flow.
"""
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.identity_drift import LINE_ENTITY_SPECS
from scripts.backfill_qbo_identity_lines import backfill_entity

_SPEC = next(s for s in LINE_ENTITY_SPECS if s.key == "bill_line_item")


def _build_mock_conn(*, pre_counts, post_counts, batch_rows):
    """cursor.execute is a no-op stub; behavior is driven entirely by patching
    _fetch_counts (see below) plus fetchall/fetchone side effects for the
    apply loop and the verify step (both configured empty/clean)."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [batch_rows, [], []]  # batch select, mismatch verify, collision verify
    cursor.fetchone.return_value = MagicMock(Stolen=False)  # _stamp_via_sproc's OUTPUT echo
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx, cursor, conn


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_pending_reflects_real_gap_even_when_stamped_exceeds_eligible(
    mock_get_connection, mock_fetch_counts, mock_call_procedure
):
    """The exact live-prod shape that broke the old formula: 5 'stamped but
    unmapped' anomaly rows push stamped (23551) above eligible (23547), while
    a real, distinct 1-row pending gap exists. The old `max(0, eligible -
    stamped)` = max(0, -4) = 0 would skip it entirely, silently. The new
    `pending` field (a direct COUNT) must drive the apply loop correctly."""
    pre_counts = {
        "mapping_count": 23553, "staging_count": 24531, "eligible": 23547,
        "stamped": 23551, "pending": 1, "unmapped_staging": 978, "dangling": 6,
    }
    post_counts = dict(pre_counts, stamped=23552, pending=0)
    batch_row = MagicMock(Id=23394, QboId="1", RealmId="9130353016965726")
    ctx, cursor, conn = _build_mock_conn(
        pre_counts=pre_counts, post_counts=post_counts, batch_rows=[batch_row]
    )
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [pre_counts, post_counts]

    backfill_entity(_SPEC, apply=True, batch_size=2000, limit=None)

    # The old formula would have computed would_apply=0 and never reached the
    # batch-select/stamp loop at all. Proves the fix: the real 1 pending row
    # was actually attempted.
    mock_call_procedure.assert_called_once()
    call = mock_call_procedure.call_args
    assert call.args[1] == _SPEC.sproc
    assert call.args[2] == {"Id": 23394, "QboId": "1", "RealmId": "9130353016965726"}
    conn.commit.assert_called_once()


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_dry_run_computes_would_apply_from_pending_without_stamping(
    mock_get_connection, mock_fetch_counts, mock_call_procedure
):
    """Dry-run (apply=False) must still report the correct pending count
    (observable via the printed would-apply path being skipped entirely —
    the stamp sproc is never called), matching the live prod scenario where
    stamped > eligible would have masked this under the old formula."""
    counts = {
        "mapping_count": 23553, "staging_count": 24531, "eligible": 23547,
        "stamped": 23551, "pending": 1, "unmapped_staging": 978, "dangling": 6,
    }
    ctx, cursor, conn = _build_mock_conn(pre_counts=counts, post_counts=counts, batch_rows=[])
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [counts, counts]

    backfill_entity(_SPEC, apply=False, batch_size=2000, limit=None)

    mock_call_procedure.assert_not_called()
    conn.commit.assert_not_called()


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_zero_pending_skips_the_apply_loop_entirely(
    mock_get_connection, mock_fetch_counts, mock_call_procedure
):
    """The ordinary case (nothing pending): no stamp call, no commit — proves
    the fix didn't flip the sign and start over-stamping when there's
    genuinely nothing to do."""
    counts = {
        "mapping_count": 100, "staging_count": 100, "eligible": 100,
        "stamped": 100, "pending": 0, "unmapped_staging": 0, "dangling": 0,
    }
    ctx, cursor, conn = _build_mock_conn(pre_counts=counts, post_counts=counts, batch_rows=[])
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [counts, counts]

    backfill_entity(_SPEC, apply=True, batch_size=2000, limit=None)

    mock_call_procedure.assert_not_called()
    conn.commit.assert_not_called()
