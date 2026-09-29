"""U-573 — retire follow-up rows whose Ramp transaction has vanished, so the
straggler refetch stops growing forever.

`_process_chaser_window` issues one GET per still-open row that is not in the 90-day
window, every sweep, forever. Two row-classes never leave that set and ONLY ONE is a
target:

  (A) AGED OUT OF THE WINDOW, still resolvable. ⛔ MUST keep being refetched — the
      refetch is the only way we learn the cardholder finally added the memo/receipt.
      An age-cap was considered and rejected for exactly this reason. Covered by
      `test_u573_aged_out_but_resolvable_item_keeps_being_refetched_and_resolves`
      (and by the reset half of the consecutive-miss tests).

  (B) 404s FROM RAMP — `get_transaction` returns falsy. Ramp cannot ever report it
      complete, so the GET is pure waste. This is the target. Retired after
      GONE_FROM_RAMP_MISS_THRESHOLD CONSECUTIVE misses; any successful fetch zeroes
      the run.

Retiring is NOT resolving: a retired row stays an open follow-up item and must still
reach the cardholder's digest. Only the *refetch* stops.

Doubles are reused from tests/test_u549_ramp_chaser.py rather than re-declared.
"""

# Python Standard Library Imports
import logging
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

# Local Imports
from entities.ramp_transaction_follow_up.business.model import (
    GONE_FROM_RAMP_MISS_THRESHOLD,
    RampTransactionFollowUp,
)
from entities.ramp_transaction_follow_up.persistence.repo import (
    RampTransactionFollowUpRepository,
)
from integrations.ramp.transaction.business.service import (
    RampChaserSweepStats,
    RampTransactionService,
)
from integrations.ramp.user.business.service import RampUserService
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments
from tests.test_u549_ramp_chaser import (
    _ACTIVE_USER,
    _FakeFollowUpRepo,
    _FakeTxClient,
    _FakeUserClient,
    _seed_open,
    _settings,
    _txn,
)

FOLLOW_UP_SQL = (
    REPO_ROOT / "entities/ramp_transaction_follow_up/sql/dbo.ramp_transaction_follow_up.sql"
)

REPO_MODULE = "entities.ramp_transaction_follow_up.persistence.repo"


def _sweep(
    repo: _FakeFollowUpRepo,
    *,
    overrides: Optional[Dict[str, Any]] = None,
    window: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[RampChaserSweepStats, _FakeTxClient]:
    """Run ONE sweep against a fresh service, carrying state only in `repo`.

    Production shape: the sweep is a scheduled job, so nothing survives between runs
    except the persisted row. A fresh `_FakeTxClient` per sweep also makes
    `get_calls` a per-sweep count.
    """
    tx_client = _FakeTxClient(list(window or []), get_overrides=overrides)
    service = RampTransactionService(
        settings=_settings(),
        transaction_client=tx_client,  # type: ignore[arg-type]
        user_service=RampUserService(_FakeUserClient([_ACTIVE_USER])),  # type: ignore[arg-type]
    )
    return service.run_chaser_sweep(follow_up_repo=repo), tx_client


def _where_clause(sproc_name: str) -> str:
    """The WHERE predicate of a read sproc, comments stripped, whitespace collapsed."""
    body = strip_sql_comments(sproc_body(FOLLOW_UP_SQL, sproc_name))
    match = re.search(r"\bWHERE\b(.*?)(?:\bORDER\s+BY\b|;)", body, re.IGNORECASE | re.DOTALL)
    assert match is not None, f"{sproc_name} must have a WHERE clause"
    return re.sub(r"\s+", " ", match.group(1)).strip()


# --- class (B): the 404 row, retired after N CONSECUTIVE misses ------------------


def test_u573_one_miss_does_not_retire_and_row_is_refetched_again():
    """Class (B), first miss. A single absence proves nothing."""
    repo = _FakeFollowUpRepo()
    _seed_open(repo, "vanished-1")

    stats, _ = _sweep(repo, overrides={"vanished-1": None})

    assert stats.stragglers_refetched == 1
    assert stats.stragglers_gone_from_ramp == 1
    assert stats.stragglers_retired == 0
    assert repo.rows["vanished-1"]["gone_from_ramp_count"] == 1
    assert not repo.rows["vanished-1"].get("gone_from_ramp_at")
    assert "vanished-1" in repo.read_unresolved_ramp_transaction_ids()

    # still in the refetch set: the NEXT sweep asks Ramp about it again
    stats2, tx2 = _sweep(repo, overrides={"vanished-1": None})
    assert tx2.get_calls == 1
    assert stats2.stragglers_retired == 0
    assert repo.rows["vanished-1"]["gone_from_ramp_count"] == 2


def test_u573_three_consecutive_misses_retire_and_stamp(caplog):
    """Class (B), the crossing. N misses IN A ROW stamp GoneFromRampAt exactly once."""
    repo = _FakeFollowUpRepo()
    _seed_open(repo, "vanished-2")

    with caplog.at_level(logging.INFO):
        for attempt in range(1, GONE_FROM_RAMP_MISS_THRESHOLD + 1):
            stats, _ = _sweep(repo, overrides={"vanished-2": None})
            crossing = attempt == GONE_FROM_RAMP_MISS_THRESHOLD
            assert stats.stragglers_retired == (1 if crossing else 0)

    row = repo.rows["vanished-2"]
    assert row["gone_from_ramp_count"] == GONE_FROM_RAMP_MISS_THRESHOLD
    assert row["gone_from_ramp_at"]

    # retiring a row is a decision an operator may need to audit: the line names it
    retire_records = [
        r
        for r in caplog.records
        if r.message == "ramp.chaser.straggler.retired"
        or getattr(r, "event_name", "") == "ramp.chaser.straggler.retired"
    ]
    assert len(retire_records) == 1
    assert getattr(retire_records[0], "ramp_transaction_id", None) == "vanished-2"


def test_u573_successful_fetch_between_misses_resets_the_run(caplog):
    """THE load-bearing test. miss, miss, SUCCESS, miss, miss -> NOT retired.

    Class (B) and class (A) are indistinguishable on any single sweep: a transient
    Ramp 404 on a live row looks exactly like a vanished one. The counter reset is
    the only thing keeping a blip from retiring a row someone still owes work on.
    """
    repo = _FakeFollowUpRepo()
    _seed_open(repo, "blip-1")

    _sweep(repo, overrides={"blip-1": None})
    _sweep(repo, overrides={"blip-1": None})
    assert repo.rows["blip-1"]["gone_from_ramp_count"] == 2

    # Ramp answers this sweep -> the run is broken
    with caplog.at_level(logging.INFO):
        stats_ok, _ = _sweep(repo, overrides={"blip-1": _txn(txn_id="blip-1")})
    assert stats_ok.stragglers_gone_from_ramp == 0
    assert stats_ok.stragglers_retired == 0
    assert "blip-1" in repo.reset_calls
    assert repo.rows["blip-1"]["gone_from_ramp_count"] == 0

    stats_a, _ = _sweep(repo, overrides={"blip-1": None})
    stats_b, _ = _sweep(repo, overrides={"blip-1": None})

    assert stats_a.stragglers_retired == 0
    assert stats_b.stragglers_retired == 0
    assert repo.rows["blip-1"]["gone_from_ramp_count"] == 2
    assert not repo.rows["blip-1"].get("gone_from_ramp_at")
    assert "blip-1" in repo.read_unresolved_ramp_transaction_ids()
    assert not any(
        r.message == "ramp.chaser.straggler.retired"
        or getattr(r, "event_name", "") == "ramp.chaser.straggler.retired"
        for r in caplog.records
    )


def test_u573_retired_row_is_never_refetched_again():
    """Class (B), the payoff: the refetch set stops growing."""
    repo = _FakeFollowUpRepo()
    _seed_open(repo, "vanished-3")
    for _ in range(GONE_FROM_RAMP_MISS_THRESHOLD):
        _sweep(repo, overrides={"vanished-3": None})

    assert "vanished-3" not in repo.read_unresolved_ramp_transaction_ids()
    calls_before = list(repo.gone_calls)

    stats, tx_client = _sweep(repo, overrides={"vanished-3": None})

    assert tx_client.get_calls == 0
    assert stats.stragglers_refetched == 0
    assert stats.stragglers_gone_from_ramp == 0
    assert stats.stragglers_retired == 0
    assert repo.gone_calls == calls_before


def test_u573_retired_row_still_appears_in_the_full_row_read_for_the_digest():
    """Retiring is NOT resolving. The row is still an OPEN item someone owes work on.

    Filtering the full-row read on GoneFromRampAt would silently drop it from that
    cardholder's chaser email with no error anywhere.
    """
    repo = _FakeFollowUpRepo()
    _seed_open(repo, "vanished-4")
    for _ in range(GONE_FROM_RAMP_MISS_THRESHOLD):
        _sweep(repo, overrides={"vanished-4": None})

    assert "vanished-4" not in repo.read_unresolved_ramp_transaction_ids()
    digest_ids = [
        row["ramp_transaction_id"] for row in repo.read_unresolved()
    ]
    assert "vanished-4" in digest_ids
    assert repo.rows["vanished-4"]["resolved_at"] is None
    assert "vanished-4" not in repo.resolve_calls


# --- class (A): aged out of the window, still resolvable -------------------------


def test_u573_aged_out_but_resolvable_item_keeps_being_refetched_and_resolves():
    """Class (A) regression guard against the REJECTED age-cap approach.

    This row is out of the window on every sweep, so the window query never sees it.
    The straggler refetch is the ONLY channel through which we can learn the
    cardholder finally did the work. Bound the refetch by age and we chase this
    person forever for something they already completed.
    """
    repo = _FakeFollowUpRepo()
    _seed_open(repo, "aged-1")

    still_open = _txn(txn_id="aged-1", complete=False, memo="", receipts=[])
    for sweep_no in range(1, 6):
        stats, tx_client = _sweep(repo, overrides={"aged-1": still_open})
        assert tx_client.get_calls == 1, f"sweep {sweep_no} stopped refetching"
        assert stats.stragglers_refetched == 1
        assert stats.stragglers_gone_from_ramp == 0
        assert stats.stragglers_retired == 0
        assert not repo.rows["aged-1"].get("gone_from_ramp_at")
        assert "aged-1" in repo.read_unresolved_ramp_transaction_ids()

    # the cardholder finally adds the memo + receipt; Ramp flips the flag
    completed = _txn(txn_id="aged-1", complete=True, memo="done", receipts=[{"id": "r"}])
    stats, tx_client = _sweep(repo, overrides={"aged-1": completed})

    assert tx_client.get_calls == 1
    assert stats.resolved == 1
    assert repo.rows["aged-1"]["resolved_at"] is not None
    assert "aged-1" not in repo.read_unresolved_ramp_transaction_ids()


# --- SQL pins: the fakes above mirror these sprocs; these prove the sprocs match --


def test_u573_ids_sproc_drops_retired_rows_and_changes_nothing_else():
    where = _where_clause("ReadUnresolvedRampTransactionFollowUpIds")
    assert re.search(r"\[ResolvedAt\]\s+IS\s+NULL", where, re.IGNORECASE)
    assert re.search(r"\[GoneFromRampAt\]\s+IS\s+NULL", where, re.IGNORECASE)
    # no age predicate smuggled in: the rejected approach would need one of these
    assert not re.search(r"DATEADD|GETUTCDATE|SYSUTCDATETIME|FirstSeenAt", where, re.IGNORECASE)


def test_u573_full_row_read_sproc_is_not_filtered_on_gone_from_ramp():
    """The other half of the pin above — same column, deliberately NOT applied here."""
    where = _where_clause("ReadUnresolvedRampTransactionFollowUps")
    assert re.search(r"\[ResolvedAt\]\s+IS\s+NULL", where, re.IGNORECASE)
    assert "GoneFromRamp" not in where

    body = strip_sql_comments(sproc_body(FOLLOW_UP_SQL, "ReadUnresolvedRampTransactionFollowUps"))
    assert "r.[GoneFromRampCount]" in body
    assert "CONVERT(VARCHAR(30), r.[GoneFromRampAt], 126) AS [GoneFromRampAt]" in body


def test_u573_repo_sends_the_named_threshold_constant_not_a_literal():
    repo = RampTransactionFollowUpRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch(f"{REPO_MODULE}.conn_ctx") as mock_ctx, patch(
        f"{REPO_MODULE}.call_procedure"
    ) as mock_call:
        mock_ctx.return_value.__enter__.return_value = conn
        repo.record_gone_from_ramp(ramp_transaction_id="tx-9")

    assert mock_call.call_args.kwargs["name"] == "RecordRampTransactionFollowUpGoneFromRamp"
    params = mock_call.call_args.kwargs["params"]
    assert params["RampTransactionId"] == "tx-9"
    assert params["Threshold"] == GONE_FROM_RAMP_MISS_THRESHOLD
    assert GONE_FROM_RAMP_MISS_THRESHOLD == 3


def test_u573_from_db_reads_both_new_columns_off_the_row():
    """A column in the table but absent from `_from_db` yields None with no error."""
    repo = RampTransactionFollowUpRepository()
    row = SimpleNamespace(
        Id=1,
        RampTransactionId="tx-9",
        GoneFromRampCount=2,
        GoneFromRampAt="2026-09-29T00:00:00",
    )

    model = repo._from_db(row)

    assert isinstance(model, RampTransactionFollowUp)
    assert model.gone_from_ramp_count == 2
    assert model.gone_from_ramp_at == "2026-09-29T00:00:00"


def test_u573_record_sproc_retires_ON_the_nth_miss_not_after_it():
    """The threshold comparison must be `>=`, never `>`.

    ⚠️ STRUCTURAL, and deliberately so. Every behavioural test in this file drives
    `_FakeFollowUpRepo`, whose own docstring says it MIRRORS this sproc — so the
    real T-SQL is executed by nothing in the suite. A mutation changing `>=` to
    `>` here (retire on the 4th miss instead of the 3rd) left the whole suite
    GREEN, because the fake carries its own copy of the comparison.

    That is the mirror hazard this codebase has been bitten by before, and a
    structural pin on the SQL text is the only guard available without a live DB.
    """
    body = strip_sql_comments(
        sproc_body(FOLLOW_UP_SQL, "RecordRampTransactionFollowUpGoneFromRamp")
    )
    assert "[GoneFromRampCount] + 1 >= @Threshold" in body, (
        "the crossing test must be >= @Threshold — `>` silently retires one sweep late"
    )
    assert "+ 1 > @Threshold" not in body.replace("+ 1 >= @Threshold", "")


def test_u573_record_sproc_stamps_the_retirement_exactly_once():
    """`[GoneFromRampAt] IS NULL` in the WHERE is what makes the stamp fire once.

    Without it an already-retired row would be re-stamped on every later call,
    moving its retirement timestamp forward forever and making "when did we stop
    asking Ramp about this?" unanswerable. Structural for the same reason as the
    test above: the fake mirrors this predicate rather than executing it.
    """
    body = strip_sql_comments(
        sproc_body(FOLLOW_UP_SQL, "RecordRampTransactionFollowUpGoneFromRamp")
    )
    where = body[body.rindex("WHERE"):]
    assert "[GoneFromRampAt] IS NULL" in where, (
        "the record sproc must skip already-retired rows, or the stamp moves forever"
    )
    assert "[ResolvedAt] IS NULL" in where
