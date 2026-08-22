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
from scripts.backfill_qbo_identity_lines import (
    backfill_entity,
    _batch_select_sql,
    _pending_join_for_mode,
)

_SPEC = next(s for s in LINE_ENTITY_SPECS if s.key == "bill_line_item")


def _build_mock_conn(*, pre_counts, post_counts, batch_rows, stamp_results=None):
    """cursor.execute is a no-op stub; behavior is driven entirely by patching
    _fetch_counts (see below) plus fetchall/fetchone side effects for the
    apply loop and the verify step (both configured empty/clean).

    `stamp_results`, if given, is a list of (QboId, RealmId) tuples — one per
    row in `batch_rows`, in order — modeling the sproc's real post-call output
    row (echoing the ACTUAL stored state, not the input params, per the
    U-293-dw output-contract fix). Defaults to one success-shaped row per
    batch row (QboId="stamped-qbo-id", RealmId="stamped-realm-id", Stolen=False)
    so a test that doesn't care about the completeness signal isn't silently
    passing on MagicMock's auto-attribute leniency (any unset attribute reads
    back as a truthy MagicMock, not None)."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [batch_rows, [], []]  # batch select, mismatch verify, collision verify
    if stamp_results is None:
        stamp_results = [("stamped-qbo-id", "stamped-realm-id")] * len(batch_rows)
    cursor.fetchone.side_effect = [
        MagicMock(QboId=qbo_id, RealmId=realm_id, Stolen=False) for qbo_id, realm_id in stamp_results
    ]
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


# ---------------------------------------------------------------------------
# U-293-dw: realm-only mode (QboId already stamped, RealmId NULL)
# ---------------------------------------------------------------------------


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_realm_only_mode_stamps_qboid_set_realmid_null_rows(
    mock_get_connection, mock_fetch_counts, mock_call_procedure
):
    """Reproduces backfilling the exact 2 live rows found at U-293's Gate-2
    (dbo.BillLineItem Ids 24621/24668: QboId already set, RealmId NULL) — a
    distinct anomaly shape from the default 'missing' mode's QboId-NULL gap."""
    pre_counts = {
        "mapping_count": 23554, "staging_count": 24531, "eligible": 23552,
        "stamped": 23552, "pending": 2, "unmapped_staging": 978, "dangling": 6,
    }
    post_counts = dict(pre_counts, pending=0)
    batch_rows = [
        MagicMock(Id=24621, QboId="2", RealmId="9130353016965726"),
        MagicMock(Id=24668, QboId="1", RealmId="9130353016965726"),
    ]
    ctx, cursor, conn = _build_mock_conn(
        pre_counts=pre_counts, post_counts=post_counts, batch_rows=batch_rows
    )
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [pre_counts, post_counts]

    backfill_entity(_SPEC, apply=True, batch_size=2000, limit=None, mode="realm-only")

    assert mock_call_procedure.call_count == 2
    calls_by_id = {c.args[2]["Id"]: c.args[2] for c in mock_call_procedure.call_args_list}
    assert calls_by_id[24621] == {"Id": 24621, "QboId": "2", "RealmId": "9130353016965726"}
    assert calls_by_id[24668] == {"Id": 24668, "QboId": "1", "RealmId": "9130353016965726"}
    conn.commit.assert_called_once()


def test_realm_only_pending_sql_targets_opposite_predicate_from_missing_mode():
    """The realm-only join keys on QboId IS NOT NULL AND RealmId IS NULL — the
    opposite predicate from the default 'missing' mode's QboId IS NULL — so
    the two modes' eligible-row sets never overlap or double-count a row."""
    sql = _pending_join_for_mode(_SPEC, "realm-only")
    assert "t.[QboId] IS NOT NULL" in sql
    assert "t.[RealmId] IS NULL" in sql


def test_missing_pending_sql_keys_on_qboid_null():
    """The default 'missing' mode's own predicate, for symmetry with the
    realm-only test above."""
    sql = _pending_join_for_mode(_SPEC, "missing")
    assert "t.[QboId] IS NULL" in sql


def test_realm_only_batch_select_reads_dbo_qboid_not_staging():
    """realm-only mode must select t.[QboId] (the dbo row's own value) — never
    s.[QboLineId] (staging). A row can only enter this mode's eligible set
    with a real QboId already stamped; re-selecting the staging value instead
    would silently overwrite a genuine QboId/staging mismatch (a distinct drift
    _mismatch_sql exists to surface) as a side effect of what's meant to be a
    realm-only backfill."""
    sql = _batch_select_sql(_SPEC, limit=100, mode="realm-only")
    assert "t.[QboId] AS QboId" in sql
    assert "s.[QboLineId]" not in sql


def test_missing_mode_batch_select_still_reads_staging_qboid():
    """The default 'missing' mode is unchanged: it must still resolve QboId
    from staging (the dbo row has none yet by definition of this mode)."""
    sql = _batch_select_sql(_SPEC, limit=100, mode="missing")
    assert "s.[QboLineId] AS QboId" in sql


# ---------------------------------------------------------------------------
# U-293-dw round-2: the sproc's own atomic-pair guard can silently no-op a
# stamp attempt (neither this call's realm_id nor the row's existing one
# resolves) — the script must detect and report this, not misreport it as a
# success just because the sproc call didn't raise.
# ---------------------------------------------------------------------------


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_guard_blocked_row_is_not_counted_as_completed(
    mock_get_connection, mock_fetch_counts, mock_call_procedure, caplog
):
    """Reproduces the sproc's atomic-pair guard silently no-op'ing a stamp:
    the row's staging RealmId is unresolvable, so the sproc leaves QboId
    unchanged (NULL, since this is 'missing' mode) despite the call
    succeeding without error. The script must recognize this via the sproc's
    now-accurate output (not an echo of the input) and NOT count it as a
    completed backfill."""
    pre_counts = {
        "mapping_count": 100, "staging_count": 100, "eligible": 100,
        "stamped": 0, "pending": 1, "unmapped_staging": 0, "dangling": 0,
    }
    post_counts = dict(pre_counts)  # unchanged — the row never actually got stamped
    batch_row = MagicMock(Id=999, QboId="unresolvable-line-id", RealmId=None)
    ctx, cursor, conn = _build_mock_conn(
        pre_counts=pre_counts,
        post_counts=post_counts,
        batch_rows=[batch_row],
        stamp_results=[(None, None)],  # sproc's real post-call state: still fully unstamped
    )
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [pre_counts, post_counts]

    import logging
    with caplog.at_level(logging.WARNING):
        backfill_entity(_SPEC, apply=True, batch_size=2000, limit=None)

    mock_call_procedure.assert_called_once()  # the attempt happened
    conn.commit.assert_called_once()  # still commits (nothing to roll back — no-op, not an error)
    assert any("not stamped" in r.message for r in caplog.records), (
        "expected a warning naming the guard-blocked row, not a silent success"
    )
    assert any("QboId and RealmId" in r.message for r in caplog.records), (
        "expected the message to name the ACTUAL missing column(s), not a generic realm_id claim"
    )


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_incomplete_diagnostic_names_the_actual_missing_column_not_always_realm(
    mock_get_connection, mock_fetch_counts, mock_call_procedure, caplog
):
    """A row can end up incomplete for a reason OTHER than an unresolvable
    realm_id — e.g. staging itself had no QboLineId to offer, while the
    realm resolved fine. The diagnostic must name QboId as what's missing in
    that case, not blame realm_id (round-2 review finding: the old fixed
    'realm_id unresolvable' message text was inaccurate for this shape)."""
    pre_counts = {
        "mapping_count": 100, "staging_count": 100, "eligible": 100,
        "stamped": 0, "pending": 1, "unmapped_staging": 0, "dangling": 0,
    }
    post_counts = dict(pre_counts)
    batch_row = MagicMock(Id=42, QboId=None, RealmId="realm-1")
    ctx, cursor, conn = _build_mock_conn(
        pre_counts=pre_counts,
        post_counts=post_counts,
        batch_rows=[batch_row],
        # RealmId writes independently of the QboId guard (its own CASE WHEN
        # isn't gated on @RealmComplete) — so RealmId lands, QboId stays NULL.
        stamp_results=[(None, "realm-1")],
    )
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [pre_counts, post_counts]

    import logging
    with caplog.at_level(logging.WARNING):
        backfill_entity(_SPEC, apply=True, batch_size=2000, limit=None)

    messages = [r.message for r in caplog.records if "still unresolved" in r.message]
    assert len(messages) == 1
    assert "QboId still unresolved" in messages[0]
    assert "RealmId" not in messages[0]


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_chronically_blocked_row_does_not_starve_later_batches(
    mock_get_connection, mock_fetch_counts, mock_call_procedure
):
    """A row the sproc's atomic-pair guard can never complete this run must
    NOT be re-selected on the next batch fetch (it never leaves the live
    'pending' WHERE clause on its own) — the Id-cursor pagination must
    advance past it so a later, genuinely-resolvable row still gets its
    attempt within the same run's budget, instead of the blocked row being
    re-fetched and re-counted toward `processed` forever."""
    pre_counts = {
        "mapping_count": 100, "staging_count": 100, "eligible": 100,
        "stamped": 0, "pending": 2, "unmapped_staging": 0, "dangling": 0,
    }
    post_counts = dict(pre_counts, stamped=1, pending=1)
    blocked_row = MagicMock(Id=1, QboId="q1", RealmId=None)
    resolvable_row = MagicMock(Id=2, QboId="q2", RealmId="r2")

    cursor = MagicMock()
    # 2 batch-select fetches (batch_size=1 forces one row per batch) + 2 empty
    # verify-step fetches (mismatch, collision).
    cursor.fetchall.side_effect = [[blocked_row], [resolvable_row], [], []]
    cursor.fetchone.side_effect = [
        MagicMock(QboId=None, RealmId=None, Stolen=False),  # blocked_row: guard no-ops it
        MagicMock(QboId="q2", RealmId="r2", Stolen=False),  # resolvable_row: genuinely completes
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [pre_counts, post_counts]

    backfill_entity(_SPEC, apply=True, batch_size=1, limit=None)

    assert mock_call_procedure.call_count == 2  # both rows were attempted, not just the first
    # The second batch-select must carry the cursor bound past the blocked row's Id.
    batch_select_calls = [c.args[0] for c in cursor.execute.call_args_list if "SELECT TOP" in c.args[0]]
    assert len(batch_select_calls) == 2
    assert "t.[Id] > 1" in batch_select_calls[1]


@patch("scripts.backfill_qbo_identity_lines.call_procedure")
@patch("scripts.backfill_qbo_identity_lines._fetch_counts")
@patch("scripts.backfill_qbo_identity_lines.get_connection")
def test_completed_row_is_not_flagged_as_guard_blocked(
    mock_get_connection, mock_fetch_counts, mock_call_procedure, caplog
):
    """The normal-success counterpart to the guard-blocked test above — a row
    that genuinely ends up with both QboId and RealmId must NOT trigger the
    guard-blocked warning."""
    pre_counts = {
        "mapping_count": 100, "staging_count": 100, "eligible": 100,
        "stamped": 0, "pending": 1, "unmapped_staging": 0, "dangling": 0,
    }
    post_counts = dict(pre_counts, stamped=1, pending=0)
    batch_row = MagicMock(Id=1000, QboId="line-1", RealmId="realm-1")
    ctx, cursor, conn = _build_mock_conn(
        pre_counts=pre_counts,
        post_counts=post_counts,
        batch_rows=[batch_row],
        stamp_results=[("line-1", "realm-1")],
    )
    mock_get_connection.return_value = ctx
    mock_fetch_counts.side_effect = [pre_counts, post_counts]

    import logging
    with caplog.at_level(logging.WARNING):
        backfill_entity(_SPEC, apply=True, batch_size=2000, limit=None)

    assert not any("not stamped" in r.message for r in caplog.records)
