"""Pure-logic tests for SyncOutcome + WatermarkRun (U-217 step 3).

Guards watermark hold/advance semantics, query_start-minus-overlap arithmetic,
commit precedence, row resolution, failed-write honesty, and fleet drift in
scripts/sync_qbo_*.py — no live DB or network.
"""
from __future__ import annotations

import ast
import contextlib
import functools
import importlib
import inspect
import sys
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from integrations.intuit.qbo.base.errors import (
    QboRateLimitError,
    QboServerError,
    QboTimeoutError,
    QboTransportError,
    QboValidationError,
    is_retryable_error,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.base import watermark as watermark_module
from integrations.intuit.qbo.base.watermark import (
    WatermarkRun,
    _QboSyncEntityMeta,
    _held_duration,
    _normalize_watermark_value,
    _resolve_staging_qbo_id,
    _watermark_hold_bound_seconds,
    _watermark_overlap_seconds,
)
from integrations.sync.business.model import Sync
from integrations.sync.business.service import SyncService
from integrations.sync.persistence.repo import SyncRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

PROVIDER = "qbo"
ENV = "prod"
ENTITY = "bill"

FIXED_QUERY_START = datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)
# 68 days before FIXED_QUERY_START — comfortably past the 7200s default hold bound.
PAST_BOUND_HOLD = (FIXED_QUERY_START - timedelta(days=68)).strftime("%Y-%m-%dT%H:%M:%S")


class FakeSyncService:
    """In-memory SyncService stand-in for WatermarkRun unit tests."""

    def __init__(self, rows: Optional[list[Sync]] = None):
        self.rows: list[Sync] = list(rows or [])
        self.updates: list[tuple[str, Sync]] = []
        self.create_calls: list[dict[str, Any]] = []
        self._create_raises: Optional[Exception] = None
        self._update_returns_none = False
        self._update_failures_remaining = 0
        self._advance_last_sync_on_failed_write: Optional[str] = None
        self._next_id = max((int(r.id) for r in self.rows if r.id and str(r.id).isdigit()), default=0)
        self._repo = SyncRepository()

    def configure_create_raises_once(self, exc: Exception) -> None:
        self._create_raises = exc

    def configure_update_returns_none(self, value: bool = True) -> None:
        self._update_returns_none = value

    def configure_update_failures(self, count: int, *, advance_last_sync_to: Optional[str] = None) -> None:
        """Return None from the next `count` updates; optionally simulate a concurrent watermark advance."""
        self._update_failures_remaining = count
        self._advance_last_sync_on_failed_write = advance_last_sync_to

    def read_all(self) -> list[Sync]:
        return list(self.rows)

    def read_candidates_for(
        self, provider: str, env: str, entity: str
    ) -> list[Sync]:
        return [
            sync
            for sync in self.read_all()
            if sync.provider == provider
            and sync.env == env
            and sync.entity == entity
        ]

    def pick_canonical(self, candidates: list[Sync]) -> Optional[Sync]:
        return self._repo.pick_canonical(candidates)

    def watermark_is_at_or_ahead(self, sync: Sync, iso_value: str) -> bool:
        return self._repo.watermark_is_at_or_ahead(sync, iso_value)

    def create(
        self,
        *,
        provider: str,
        env: str,
        entity: str,
        last_sync_datetime=None,
    ) -> Sync:
        self.create_calls.append(
            {
                "provider": provider,
                "env": env,
                "entity": entity,
                "last_sync_datetime": last_sync_datetime,
            }
        )
        if self._create_raises is not None:
            exc = self._create_raises
            self._create_raises = None
            raise exc
        self._next_id += 1
        row = _make_sync(
            id=str(self._next_id),
            public_id=f"pub-{self._next_id}",
            provider=provider,
            env=env,
            entity=entity,
            last_sync_datetime=last_sync_datetime,
        )
        self.rows.append(row)
        return row

    def update_by_public_id(self, public_id: str, sync: Sync) -> Optional[Sync]:
        self.updates.append((public_id, sync))
        if self._update_returns_none or self._update_failures_remaining > 0:
            if self._update_failures_remaining > 0:
                self._update_failures_remaining -= 1
            if self._advance_last_sync_on_failed_write:
                for idx, row in enumerate(self.rows):
                    if row.public_id == public_id:
                        self.rows[idx] = _make_sync(
                            id=row.id,
                            public_id=row.public_id,
                            row_version="concurrent-rv",
                            provider=row.provider,
                            env=row.env,
                            entity=row.entity,
                            last_sync_datetime=self._advance_last_sync_on_failed_write,
                            created_datetime=row.created_datetime,
                            modified_datetime=row.modified_datetime,
                            hold_started_datetime=row.hold_started_datetime,
                        )
            return None
        for idx, row in enumerate(self.rows):
            if row.public_id == public_id:
                persisted = _make_sync(
                    id=row.id,
                    public_id=row.public_id,
                    row_version="next-rv",
                    provider=row.provider,
                    env=row.env,
                    entity=row.entity,
                    last_sync_datetime=sync.last_sync_datetime,
                    hold_started_datetime=sync.hold_started_datetime,
                    created_datetime=row.created_datetime,
                    modified_datetime=row.modified_datetime,
                )
                self.rows[idx] = persisted
                return persisted
        return None


def _make_sync(
    *,
    id: str = "1",
    public_id: str = "pub-1",
    row_version: str = "rv1",
    created_datetime: Optional[str] = "2026-01-01T00:00:00",
    modified_datetime: Optional[str] = "2026-01-01T00:00:00",
    provider: str = PROVIDER,
    env: str = ENV,
    entity: str = ENTITY,
    last_sync_datetime: Optional[str] = None,
    hold_started_datetime: Optional[str] = None,
) -> Sync:
    return Sync(
        id=id,
        public_id=public_id,
        row_version=row_version,
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        provider=provider,
        env=env,
        entity=entity,
        last_sync_datetime=last_sync_datetime,
        hold_started_datetime=hold_started_datetime,
    )


def _opened_run(
    fake: FakeSyncService,
    *,
    entity: str = ENTITY,
    query_start: datetime = FIXED_QUERY_START,
) -> WatermarkRun:
    run = WatermarkRun(fake, PROVIDER, ENV, entity)
    run.open()
    run.query_start = query_start
    return run


# --------------------------------------------------------------------------- #
# A. SyncOutcome semantics
# --------------------------------------------------------------------------- #


def test_should_hold_true_when_only_staging_failure_recorded():
    """Staging upsert failures must hold the watermark so qbo.* gaps are re-pulled."""
    outcome = SyncOutcome()
    outcome.record_staging_failure("42")
    assert outcome.should_hold is True


def test_should_hold_true_when_only_projection_failure_recorded():
    """Module projection failures must hold the watermark so dbo.* gaps are re-pulled."""
    outcome = SyncOutcome()
    outcome.record_projection_failure("99")
    assert outcome.should_hold is True


def test_should_hold_false_when_only_skips_recorded():
    """Permanent data gaps (e.g. unmapped vendor) must not wedge the watermark forever."""
    outcome = SyncOutcome()
    outcome.record_staging_skip("7", reason="vendor not mapped")
    # Skips are intentional permanent data issues — they will never self-resolve on retry,
    # so holding the watermark on them would stall incremental sync indefinitely.
    assert outcome.should_hold is False


def test_failed_count_sums_both_tiers_and_excludes_skips():
    """Operator summaries must count real failures, not benign permanent skips."""
    outcome = SyncOutcome()
    outcome.record_staging_failure("1")
    outcome.record_projection_failure("2")
    outcome.record_staging_skip("3")
    assert outcome.failed_count == 2


def test_summary_carries_all_fields_and_list_copies():
    """Downstream logging must not mutate internal failure id lists via summary()."""
    outcome = SyncOutcome(fetched=5)
    for _ in range(4):
        outcome.record_synced(object())
    outcome.record_staging_failure("s1")
    outcome.record_projection_failure("p1")
    outcome.record_staging_skip("k1")
    summary = outcome.summary()
    assert summary["fetched"] == 5
    assert summary["synced"] == 4
    assert outcome.synced_count == 4
    assert summary["failed_count"] == 2
    assert summary["staging_failed_ids"] == ["s1"]
    assert summary["projection_failed_ids"] == ["p1"]
    assert summary["skipped_count"] == 1
    assert summary["skipped_ids"] == ["k1"]
    summary["staging_failed_ids"].append("mutated")
    assert outcome.staging_failed_ids == ["s1"]


def test_hold_reason_none_when_not_holding_and_names_both_tiers_when_both_failed():
    """Hold logs must explain every failure tier that blocked the watermark."""
    clean = SyncOutcome()
    assert clean.hold_reason() is None
    both = SyncOutcome()
    both.record_staging_failure("a")
    both.record_projection_failure("b")
    reason = both.hold_reason()
    assert reason is not None
    assert "staging failed: a (no reason provided)" in reason
    assert "projection failed: b (no reason provided)" in reason


def test_record_projection_error_plain_value_error_is_skip_without_hold():
    """Permanent connector data gaps must skip without wedging the watermark."""
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("42", ValueError("vendor not mapped"))
    assert kind == "skip"
    assert outcome.skipped_ids == ["42"]
    assert outcome.projection_failed_ids == []
    assert outcome.should_hold is False


def test_record_projection_error_value_error_with_transient_cause_holds_watermark():
    """ValueError(... from db_err) must not advance the watermark when the cause is transient."""
    db_err = Exception("40001")
    wrapped = ValueError("Failed to create PurchaseExpense mapping for QboPurchase 1")
    wrapped.__cause__ = db_err
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("99", wrapped)
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["99"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


@pytest.mark.parametrize(
    "error",
    [
        QboTimeoutError("read timed out"),
        QboTransportError("connection reset"),
        QboRateLimitError("too many requests", retry_after_seconds=30.0),
        QboServerError("internal error", http_status=500),
    ],
)
def test_record_projection_error_qbo_retryable_errors_hold_watermark(error):
    """QBO typed retryable failures must hold — round 1 wrongly skipped them via DB-only transience."""
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("attach-1", error)
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["attach-1"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


def test_record_projection_error_runtime_error_from_line_item_aggregation_holds():
    """BillBillConnector._sync_line_items aggregates per-line failures into RuntimeError('N line(s) failed')."""
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("bill-7", RuntimeError("2 line(s) failed"))
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["bill-7"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


def test_record_projection_error_value_error_with_qbo_retryable_cause_holds():
    """Rule 1 (retryable chain) must beat rule 2 (ValueError shape)."""
    cause = QboTimeoutError("attachable read timed out")
    wrapped = ValueError("attachment sync failed")
    wrapped.__cause__ = cause
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("vc-2", wrapped)
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["vc-2"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


def test_record_projection_error_retryable_logs_error_and_includes_label(caplog):
    """Projection failure logging is centralized on SyncOutcome (U-217 Pass-2 B1)."""
    import logging

    caplog.set_level(logging.INFO)
    outcome = SyncOutcome()
    outcome.record_projection_error(
        "bill-1",
        QboTimeoutError("timed out"),
        label="QboBill->Bill",
    )
    assert any(
        r.levelname == "ERROR" and "QboBill->Bill" in r.message and "bill-1" in r.message
        for r in caplog.records
    )


def test_record_projection_error_value_error_logs_info_and_includes_label(caplog):
    import logging

    caplog.set_level(logging.INFO)
    outcome = SyncOutcome()
    outcome.record_projection_error(
        "pur-9",
        ValueError("vendor not mapped"),
        label="QboPurchase->Expense",
    )
    assert any(
        r.levelname == "INFO"
        and "QboPurchase->Expense" in r.message
        and "pur-9" in r.message
        and "permanent data issue" in r.message
        for r in caplog.records
    )


def test_u217_regression_inverted_safe_default_unknown_exception_holds_not_skips():
    """
    Round 1 classified any non-DB-transient error as skip (including QboTimeoutError and RuntimeError).

    Skipping an unknown error advances the watermark permanently — the QBO record is not re-pulled
    until someone edits it in QBO again. Unknown exceptions must hold.
    """
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("unknown-1", LookupError("unexpected mapping gap"))
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["unknown-1"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


def test_is_retryable_error_follows_cause_chain_at_least_two_links():
    """Retryable signal on a nested __cause__ must be detected on the outer connector ValueError."""
    root = Exception("40001")
    middle = ValueError("create_mapping failed")
    middle.__cause__ = root
    outer = ValueError("Failed to create PurchaseExpense mapping")
    outer.__cause__ = middle
    assert is_retryable_error(outer) is True


def test_is_retryable_error_terminates_on_self_referential_cause_chain():
    """Cycle-safe chain walk must not hang on a self-linked __cause__."""
    err = ValueError("cyclic")
    err.__cause__ = err
    assert is_retryable_error(err) is False


def test_is_retryable_error_hyt00_query_timeout_pyodbc_shape():
    db_err = Exception(
        "HYT00",
        "[HYT00][Microsoft][ODBC Driver 17 for SQL Server]Query timeout expired (0) (SQLExecDirectW)",
    )
    assert is_retryable_error(db_err) is True


def test_record_projection_error_hyt00_query_timeout_from_purchase_mapping_path_holds():
    """
    PurchaseExpenseConnector rolls back a just-created Expense and re-raises mapping-create
    DB failures as ValueError(...) from e. HYT00 query timeouts are now in
    shared.database.is_transient_error (U-262 Part 2); this test still verifies
    is_retryable_error(...) is True for HYT00 via that consolidated path, and that
    record_projection_error classifies the wrapped failure as a hold (not a skip).
    """
    db_err = Exception(
        "HYT00",
        "[HYT00][Microsoft][ODBC Driver 17 for SQL Server]Query timeout expired (0) (SQLExecDirectW)",
    )
    wrapped = ValueError("Failed to create PurchaseExpense mapping for QboPurchase 1")
    wrapped.__cause__ = db_err
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("pur-hyt00", wrapped)
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["pur-hyt00"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


def test_record_projection_error_hyt01_connection_timeout_from_purchase_mapping_path_holds():
    """
    Same purchase-connector rollback/re-raise path as HYT00: a connection timeout must hold,
    not skip — otherwise the Purchase is lost to incremental sync until a QBO edit.
    """
    db_err = Exception(
        "HYT01",
        "[HYT01][Microsoft][ODBC Driver 17 for SQL Server]Connection timeout expired (0)",
    )
    assert is_retryable_error(db_err) is True
    wrapped = ValueError("Failed to create PurchaseExpense mapping for QboPurchase 2")
    wrapped.__cause__ = db_err
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("pur-hyt01", wrapped)
    assert kind == "failure"
    assert outcome.projection_failed_ids == ["pur-hyt01"]
    assert outcome.skipped_ids == []
    assert outcome.should_hold is True


def test_is_retryable_error_message_only_query_timeout_without_sqlstate_in_args():
    """Drivers that report only message text (no SQLSTATE in args[0]) must still hold the watermark."""
    db_err = Exception("SomeDriverError", "Query timeout expired while executing batch")
    assert is_retryable_error(db_err) is True
    wrapped = ValueError("mapping create failed")
    wrapped.__cause__ = db_err
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("pur-msg", wrapped)
    assert kind == "failure"
    assert outcome.should_hold is True


def test_record_projection_error_vendor_not_mapped_still_skips_after_extra_transient_vocab():
    """Widened timeout vocabulary must not reclassify permanent connector ValueErrors as retryable."""
    outcome = SyncOutcome()
    kind = outcome.record_projection_error("pur-perm", ValueError("vendor not mapped"))
    assert kind == "skip"
    assert outcome.skipped_ids == ["pur-perm"]
    assert outcome.projection_failed_ids == []
    assert outcome.should_hold is False


def test_record_projected_increments_projected_count_and_is_separate_from_synced():
    outcome = SyncOutcome()
    outcome.record_projected()
    outcome.record_projected()
    assert outcome.projected_count == 2
    assert outcome.synced_count == 0
    outcome.record_synced(object())
    assert outcome.synced_count == 1
    assert outcome.projected_count == 2


def test_summary_reports_projected_separately_from_synced():
    outcome = SyncOutcome()
    outcome.record_synced(object())
    outcome.record_synced(object())
    outcome.record_projected()
    outcome.record_projected()
    outcome.record_projected()
    summary = outcome.summary()
    assert summary["synced"] == 2
    assert summary["projected"] == 3


def test_record_projected_does_not_affect_should_hold():
    outcome = SyncOutcome()
    for _ in range(5):
        outcome.record_projected()
    assert outcome.should_hold is False


# --------------------------------------------------------------------------- #
# B. Watermark value arithmetic
# --------------------------------------------------------------------------- #


def test_watermark_value_is_query_start_minus_overlap_formatted_utc():
    """QBO incremental filters must use the captured query_start, not wall clock at commit."""
    run = WatermarkRun(FakeSyncService(), PROVIDER, ENV, ENTITY)
    run.query_start = FIXED_QUERY_START
    assert run.watermark_value == "2026-03-10T14:29:00Z"


def test_committed_watermark_is_query_start_minus_overlap_not_post_work_wall_clock():
    """Prevents P1: datetime.now() after minutes of attachment/Excel work skipping mid-run QBO edits."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    # Simulate a long run: query_start was captured at run open; commit happens "minutes later"
    # without advancing any clock the production code reads — watermark must still anchor to
    # query_start minus overlap, strictly before query_start, never a later wall-clock stamp.
    outcome = SyncOutcome.for_service_pull()
    run.commit(outcome)
    assert len(fake.updates) == 1
    committed = fake.updates[0][1].last_sync_datetime
    assert committed == "2026-03-10T14:29:00Z"
    committed_dt = datetime.strptime(committed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert committed_dt < run.query_start
    assert committed_dt == run.query_start - timedelta(seconds=60)


def test_watermark_overlap_seconds_default_and_env_validation(monkeypatch):
    """Overlap must be tunable via env without import-time caching."""
    monkeypatch.delenv("QBO_SYNC_WATERMARK_OVERLAP_SECONDS", raising=False)
    assert _watermark_overlap_seconds() == 60
    monkeypatch.setenv("QBO_SYNC_WATERMARK_OVERLAP_SECONDS", "300")
    assert _watermark_overlap_seconds() == 300
    monkeypatch.setenv("QBO_SYNC_WATERMARK_OVERLAP_SECONDS", "not-int")
    assert _watermark_overlap_seconds() == 60
    monkeypatch.setenv("QBO_SYNC_WATERMARK_OVERLAP_SECONDS", "-5")
    assert _watermark_overlap_seconds() == 60


def test_normalize_watermark_value_handles_datetime_and_string_shapes():
    """ReadSyncs returns datetime objects while UpdateSync OUTPUT returns strings — both must work."""
    naive = datetime(2026, 6, 1, 10, 15, 30)
    assert _normalize_watermark_value(naive) == "2026-06-01T10:15:30Z"
    chicago = datetime(2026, 6, 1, 10, 15, 30, tzinfo=ZoneInfo("America/Chicago"))
    assert _normalize_watermark_value(chicago) == "2026-06-01T15:15:30Z"
    assert _normalize_watermark_value("2026-06-01T10:15:30+00:00") == "2026-06-01T10:15:30Z"
    assert _normalize_watermark_value(None) is None


def test_clamp_historical_stamp_future_end_date_uses_watermark_value_not_end_of_day():
    """Future end_date must clamp to watermark_value, not poison the incremental cursor."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull(), end_date="2026-03-10")
    assert fake.updates[0][1].last_sync_datetime == run.watermark_value
    assert fake.updates[0][1].last_sync_datetime != "2026-03-10T23:59:59"


def test_clamp_historical_stamp_past_end_date_keeps_end_of_day_stamp_unchanged():
    """Past end_date still produces the naive end-of-day stamp for resumable historical batches."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull(), end_date="2024-07-04")
    assert fake.updates[0][1].last_sync_datetime == "2024-07-04T23:59:59"


# --------------------------------------------------------------------------- #
# C. WatermarkRun.commit precedence
# --------------------------------------------------------------------------- #


def test_commit_skip_true_writes_nothing_on_clean_outcome():
    """--skip-sync-update must not mutate dbo.Sync even when the pull succeeded."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull(), skip=True)
    assert fake.updates == []


def test_commit_skip_true_writes_nothing_even_with_end_date():
    """Skip must outrank historical end_date imports that would otherwise stamp the watermark."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull(), end_date="2020-01-01", skip=True)
    assert fake.updates == []


def test_commit_first_hold_of_fresh_streak_stamps_hold_started_datetime_only():
    """First hold in a streak writes once to stamp HoldStartedDatetime; LastSyncDatetime unchanged."""
    fake = FakeSyncService([_make_sync(hold_started_datetime=None)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("vc-1")
    before = run.sync_record.last_sync_datetime
    run.commit(outcome, end_date="2019-12-31")
    assert len(fake.updates) == 1
    assert fake.updates[0][1].last_sync_datetime == before
    assert fake.updates[0][1].hold_started_datetime == "2026-03-10T14:30:00Z"
    assert run.sync_record.hold_started_datetime == "2026-03-10T14:30:00Z"
    assert run.sync_record.last_sync_datetime == before


def test_commit_continuing_hold_streak_writes_nothing_even_when_end_date_supplied():
    """Second-or-later hold in an existing streak must not write — end_date must not advance watermark."""
    fake = FakeSyncService([_make_sync(hold_started_datetime="2026-03-10T14:25:00")])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("vc-1")
    before = run.sync_record.last_sync_datetime
    run.commit(outcome, end_date="2019-12-31")
    assert fake.updates == []
    assert run.sync_record.last_sync_datetime == before


def test_commit_staging_only_failure_fresh_streak_stamps_hold_start_once():
    """First staging-only hold in a fresh streak writes once to stamp HoldStartedDatetime."""
    fake = FakeSyncService([_make_sync(hold_started_datetime=None)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_staging_failure("staging-only")
    before = run.sync_record.last_sync_datetime
    run.commit(outcome)
    assert len(fake.updates) == 1
    assert fake.updates[0][1].last_sync_datetime == before
    assert fake.updates[0][1].hold_started_datetime == "2026-03-10T14:30:00Z"


def test_commit_staging_only_failure_holds_with_no_write():
    """Audit S-01: continuing hold streak must not write when staging failures block advance."""
    fake = FakeSyncService([_make_sync(hold_started_datetime="2026-03-10T14:25:00")])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_staging_failure("staging-only")
    run.commit(outcome)
    assert fake.updates == []


def test_first_hold_after_long_skip_gap_does_not_force_advance_even_with_ancient_modified_datetime():
    """
    Regression for Finding 1: stale modified_datetime after a long --skip-sync-update gap must
    not cause an immediate force-advance on the first real hold observation.
    """
    ancient = (FIXED_QUERY_START - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    fake = FakeSyncService([
        _make_sync(
            modified_datetime=ancient,
            created_datetime=ancient,
            hold_started_datetime=None,
        )
    ])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")
    for _ in range(3):
        run.commit(outcome, skip=True)
    assert fake.updates == []
    before = run.sync_record.last_sync_datetime
    run.commit(outcome)
    assert len(fake.updates) == 1
    assert fake.updates[0][1].hold_started_datetime == "2026-03-10T14:30:00Z"
    assert run.sync_record.last_sync_datetime == before


def test_hold_streak_second_evaluation_reuses_existing_hold_started_datetime_no_extra_write():
    """Continuing hold streak must not re-stamp hold_started_datetime."""
    hold_start = (FIXED_QUERY_START - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    fake = FakeSyncService([_make_sync(hold_started_datetime=hold_start)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")
    run.commit(outcome)
    assert fake.updates == []
    assert _held_duration(run.sync_record, FIXED_QUERY_START) == timedelta(minutes=30)


def test_hold_cleared_on_successful_advance_after_recovering():
    """Successful watermark advance must clear hold_started_datetime."""
    fake = FakeSyncService([_make_sync(hold_started_datetime="2026-03-10T12:00:00")])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull())
    assert fake.updates[-1][1].hold_started_datetime is None


def test_force_advance_with_future_end_date_also_clamps():
    """Bound-exceeded force-advance with a future end_date must clamp to watermark_value."""
    past_bound_hold = PAST_BOUND_HOLD
    fake = FakeSyncService([_make_sync(hold_started_datetime=past_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    with _patch_bound_forced_advance_deps():
        run.commit(outcome, end_date="2026-03-10")

    assert fake.updates[0][1].last_sync_datetime == run.watermark_value
    assert fake.updates[0][1].last_sync_datetime != "2026-03-10T23:59:59"


# --------------------------------------------------------------------------- #
# U-228: bound-exceeded hold force-advances instead of holding forever
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _patch_bound_forced_advance_deps(*, realm_id="realm-1", resolved_qbo_id=None):
    """
    Patch _record_bound_forced_advance's lazy-imported dependencies at their origin modules
    (function-local `from X import Y` re-resolves via sys.modules[X].Y at call time, so
    patching the origin is correct regardless of import order). Yields `recorded`, which
    accumulates every record_mapping_issue(**kwargs) call so assertions can inspect exactly
    what was written.
    """
    recorded = []

    def _fake_record_mapping_issue(_repo, **kwargs):
        recorded.append(kwargs)

    mock_auth_service = Mock(return_value=Mock(read_all=Mock(return_value=[SimpleNamespace(realm_id=realm_id)])))
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("integrations.intuit.qbo.auth.business.service.QboAuthService", mock_auth_service))
        stack.enter_context(patch("integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository", Mock()))
        stack.enter_context(patch("integrations.intuit.qbo.base.reconciliation_recorder.record_mapping_issue", side_effect=_fake_record_mapping_issue))
        stack.enter_context(
            patch(
                "integrations.intuit.qbo.base.watermark._resolve_staging_qbo_id",
                return_value=resolved_qbo_id,
            )
        )
        yield recorded


def test_commit_holding_outcome_past_bound_force_advances_and_records_issues():
    """
    Core U-228 behavior: once held longer than the bound, commit() must advance the watermark
    exactly like a clean success AND record a critical issue per blocking id.
    """
    # hold_started_datetime ~68 days before FIXED_QUERY_START — comfortably past the 7200s default bound.
    past_bound_hold = PAST_BOUND_HOLD
    fake = FakeSyncService([_make_sync(hold_started_datetime=past_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")   # internal staging PK
    outcome.record_staging_failure("QB-99")   # real QBO id

    with _patch_bound_forced_advance_deps(resolved_qbo_id=None) as recorded:
        result = run.commit(outcome)

    assert len(fake.updates) == 1
    assert result.last_sync_datetime == run.watermark_value
    assert len(recorded) == 2  # one critical issue per blocking id (1 staging + 1 projection)
    by_qbo_id = {kwargs["qbo_id"] for kwargs in recorded}
    assert "QB-99" in by_qbo_id            # staging_failed_ids recorded at face value
    assert None in by_qbo_id               # projection_failed_ids: resolver mocked to miss
    assert all(kwargs["severity"] == "critical" for kwargs in recorded)
    assert all(kwargs["drift_type"] == "watermark_hold_bound_exceeded" for kwargs in recorded)


def test_commit_holding_outcome_exactly_at_bound_force_advances():
    """
    Boundary case: held == bound exactly must still force-advance (the check is `>=`, not `>`)
    — the bound is a ceiling on how long a hold may persist, not a strictly-greater-than gate.
    """
    at_bound_hold = (FIXED_QUERY_START - timedelta(seconds=_watermark_hold_bound_seconds())).strftime("%Y-%m-%dT%H:%M:%S")
    fake = FakeSyncService([_make_sync(hold_started_datetime=at_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    with _patch_bound_forced_advance_deps():
        result = run.commit(outcome)

    assert len(fake.updates) == 1
    assert result.last_sync_datetime == run.watermark_value


def test_commit_holding_outcome_one_second_under_bound_does_not_force_advance():
    """Mirror of the boundary test above: one second under the bound must still be a plain hold."""
    under_bound_hold = (
        FIXED_QUERY_START - timedelta(seconds=_watermark_hold_bound_seconds() - 1)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    fake = FakeSyncService([_make_sync(hold_started_datetime=under_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    result = run.commit(outcome)

    assert fake.updates == []
    assert result is run.sync_record


def test_commit_holding_outcome_past_bound_resolves_real_qbo_id_for_projection_failure():
    """The staging-repo resolver, when it hits, must supply the real id — not just the PK."""
    past_bound_hold = PAST_BOUND_HOLD
    fake = FakeSyncService([_make_sync(hold_started_datetime=past_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    with _patch_bound_forced_advance_deps(resolved_qbo_id="QB-resolved-42") as recorded:
        run.commit(outcome)

    assert len(recorded) == 1
    assert recorded[0]["qbo_id"] == "QB-resolved-42"


def test_commit_holding_outcome_past_bound_item_projection_failure_uses_id_directly():
    """U-307c Codex P2 fix: item's projection_failed_ids already carry the real
    QBO id (no qbo.Item staging PK exists to resolve from) — must be recorded
    at face value, NOT run through _resolve_staging_qbo_id (which would always
    miss for item since its registry row carries no staging_repo, silently
    losing a QboId the caller already had in hand)."""
    past_bound_hold = PAST_BOUND_HOLD
    fake = FakeSyncService([_make_sync(hold_started_datetime=past_bound_hold, entity="item")])
    run = _opened_run(fake, entity="item")
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("QBO-I-42")  # already the real qbo_id, not a staging PK

    with _patch_bound_forced_advance_deps() as recorded, patch(
        "integrations.intuit.qbo.base.watermark._resolve_staging_qbo_id"
    ) as mock_resolve:
        run.commit(outcome)

    mock_resolve.assert_not_called()
    assert len(recorded) == 1
    assert recorded[0]["qbo_id"] == "QBO-I-42"
    assert "internal staging id" not in recorded[0]["details"]


def test_commit_holding_outcome_past_bound_with_end_date_writes_end_of_day_stamp():
    """Bound-exceeded must respect end_date exactly like the clean-success path does."""
    past_bound_hold = PAST_BOUND_HOLD
    fake = FakeSyncService([_make_sync(hold_started_datetime=past_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    with _patch_bound_forced_advance_deps():
        run.commit(outcome, end_date="2024-07-04")

    assert fake.updates[0][1].last_sync_datetime == "2024-07-04T23:59:59"


def test_commit_holding_outcome_unparseable_anchor_never_force_advances():
    """
    Safety net against the U-217-class inverted-default bug: when held_duration cannot be
    determined (unparseable/missing anchor), commit() must fall through to the ORDINARY hold —
    never force-advance on a None. A wrong hold costs a redundant re-pull; a wrong force-advance
    permanently loses a still-failing record.
    """
    fake = FakeSyncService([_make_sync(hold_started_datetime="not-a-real-datetime")])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    # No dependency patches: if this ever reached _record_bound_forced_advance it would try a
    # real DB call and fail loudly, making any accidental force-advance obvious.
    result = run.commit(outcome)

    assert fake.updates == []
    assert result is run.sync_record


def test_commit_holding_outcome_realm_resolution_failure_still_force_advances():
    """
    A failure resolving the QBO realm (e.g. no auth row) must not block the force-advance —
    the advance is the load-bearing guarantee; the reconciliation write is best-effort.
    """
    past_bound_hold = PAST_BOUND_HOLD
    fake = FakeSyncService([_make_sync(hold_started_datetime=past_bound_hold)])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")

    mock_auth_service = Mock(return_value=Mock(read_all=Mock(return_value=[])))  # no auth rows
    with patch("integrations.intuit.qbo.auth.business.service.QboAuthService", mock_auth_service):
        result = run.commit(outcome)

    assert len(fake.updates) == 1
    assert result.last_sync_datetime == run.watermark_value


def test_watermark_hold_bound_seconds_default_and_env_validation(monkeypatch):
    """Mirrors test_watermark_overlap_seconds_default_and_env_validation for the new bound env var."""
    monkeypatch.delenv("QBO_WATERMARK_HOLD_BOUND_SECONDS", raising=False)
    assert _watermark_hold_bound_seconds() == 7200
    monkeypatch.setenv("QBO_WATERMARK_HOLD_BOUND_SECONDS", "3600")
    assert _watermark_hold_bound_seconds() == 3600
    monkeypatch.setenv("QBO_WATERMARK_HOLD_BOUND_SECONDS", "not-a-number")
    assert _watermark_hold_bound_seconds() == 7200
    monkeypatch.setenv("QBO_WATERMARK_HOLD_BOUND_SECONDS", "-5")
    assert _watermark_hold_bound_seconds() == 7200


def test_held_duration_reads_hold_started_datetime():
    sync_record = _make_sync(hold_started_datetime="2026-03-10T12:30:00")
    now = datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)
    assert _held_duration(sync_record, now) == timedelta(hours=2)


def test_held_duration_none_when_anchor_unparseable():
    sync_record = _make_sync(hold_started_datetime="garbage")
    assert _held_duration(sync_record, datetime.now(timezone.utc)) is None


def test_resolve_staging_qbo_id_none_for_entity_with_no_staging_repo():
    """reimburse_charge has no read_by_id on its staging repo — must miss cleanly, not raise."""
    assert _resolve_staging_qbo_id("reimburse_charge", 42) is None


def test_resolve_staging_qbo_id_none_for_item_dbo_only_repoint():
    """U-307c: qbo.Item is transient (never persisted) — item's registry entry
    carries no staging_repo (same shape as reimburse_charge), so a projection
    failure's id (now the real qbo_id passed directly by the sync script, not
    a staging PK) resolves to no *additional* lookup rather than a wrong one."""
    assert _resolve_staging_qbo_id("item", "QBO-I-99") is None


def test_resolve_staging_qbo_id_resolves_via_the_entity_staging_repo(monkeypatch):
    # _QBO_SYNC_ENTITY_META holds a direct class reference captured at sync_helper's import
    # time, not a by-name lookup — patch the registry entry itself, not the origin module.
    fake_row = SimpleNamespace(qbo_id="QB-real-id")
    mock_repo_cls = Mock(return_value=Mock(read_by_id=Mock(return_value=fake_row)))
    monkeypatch.setitem(
        watermark_module._QBO_SYNC_ENTITY_META,
        "bill",
        _QboSyncEntityMeta(label="Bill", staging_repo=mock_repo_cls),
    )
    assert _resolve_staging_qbo_id("bill", 42) == "QB-real-id"


def test_resolve_staging_qbo_id_none_when_repo_lookup_raises(monkeypatch):
    mock_repo_cls = Mock(return_value=Mock(read_by_id=Mock(side_effect=RuntimeError("db down"))))
    monkeypatch.setitem(
        watermark_module._QBO_SYNC_ENTITY_META,
        "bill",
        _QboSyncEntityMeta(label="Bill", staging_repo=mock_repo_cls),
    )
    assert _resolve_staging_qbo_id("bill", 42) is None


def test_commit_skips_only_outcome_advances_watermark_normally():
    """Benign permanent skips must not block incremental sync from moving forward."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_staging_skip("perm")
    run.commit(outcome)
    assert len(fake.updates) == 1
    assert fake.updates[0][1].last_sync_datetime == run.watermark_value


def test_commit_clean_outcome_with_end_date_writes_end_of_day_stamp():
    """Historical TxnDate window imports must stamp the watermark to the batch end date."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull(), end_date="2024-07-04")
    assert fake.updates[0][1].last_sync_datetime == "2024-07-04T23:59:59"


def test_commit_clean_outcome_without_end_date_writes_watermark_value():
    """Incremental pulls must persist query_start-minus-overlap on success."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull())
    assert fake.updates[0][1].last_sync_datetime == run.watermark_value


def test_commit_refuses_an_unstamped_outcome():
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    with pytest.raises(RuntimeError, match="sync_from_qbo"):
        run.commit(SyncOutcome())
    assert fake.updates == []


def test_commit_push_writes_watermark_without_pull_outcome():
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit_push()
    assert len(fake.updates) == 1


def test_commit_push_skip_writes_nothing():
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit_push(skip=True)
    assert fake.updates == []


def test_commit_accepts_the_service_stamped_outcome():
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome.for_service_pull())
    assert len(fake.updates) == 1


def test_commit_refuses_unstamped_even_when_skipping():
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    with pytest.raises(RuntimeError, match="sync_from_qbo"):
        run.commit(SyncOutcome(), skip=True)
    assert fake.updates == []


def test_every_pull_service_stamps_its_outcome():
    qbo_root = REPO_ROOT / "integrations" / "intuit" / "qbo"
    unstamped: list[str] = []
    for entity in _QBO_SERVICE_MODULE_BY_ENTITY:
        service_path = qbo_root / entity / "business" / "service.py"
        if not _sync_from_qbo_outcome_stamps_from_service_pull(service_path):
            unstamped.append(str(service_path))
    assert not unstamped, (
        "an unstamped service outcome will raise at watermark commit: "
        + ", ".join(unstamped)
    )


def test_laundered_outcome_from_helper_is_refused_at_commit():
    def sync_qbo_to_local_laundering():
        return {}, SyncOutcome()

    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    _, outcome = sync_qbo_to_local_laundering()
    with pytest.raises(RuntimeError, match="sync_from_qbo"):
        run.commit(outcome)
    assert fake.updates == []


# --------------------------------------------------------------------------- #
# D. Row resolution (open)
# --------------------------------------------------------------------------- #


def test_open_picks_newest_last_sync_datetime_regardless_of_read_all_order():
    """Duplicate Sync rows must resolve to the freshest LastSyncDatetime, not list order."""
    rows = [
        _make_sync(id="1", public_id="p1", last_sync_datetime="2026-01-01T00:00:00Z"),
        _make_sync(id="2", public_id="p2", last_sync_datetime="2026-06-01T00:00:00Z"),
        _make_sync(id="3", public_id="p3", last_sync_datetime="2026-03-01T00:00:00Z"),
    ]
    fake = FakeSyncService(list(reversed(rows)))
    run = WatermarkRun(fake, PROVIDER, ENV, ENTITY).open()
    assert run.sync_record.public_id == "p2"


def test_open_breaks_last_sync_datetime_tie_with_higher_id():
    """Equal LastSyncDatetime duplicates must pick the higher Id per repo tie-breaker."""
    rows = [
        _make_sync(id="5", public_id="low", last_sync_datetime="2026-01-01T00:00:00Z"),
        _make_sync(id="9", public_id="high", last_sync_datetime="2026-01-01T00:00:00Z"),
    ]
    fake = FakeSyncService(rows)
    run = WatermarkRun(fake, PROVIDER, ENV, ENTITY).open()
    assert run.sync_record.public_id == "high"


def test_open_with_no_row_creates_exactly_once():
    """First pull for an entity must insert a Sync row instead of failing."""
    fake = FakeSyncService()
    WatermarkRun(fake, PROVIDER, ENV, ENTITY).open()
    assert len(fake.create_calls) == 1
    assert fake.create_calls[0]["entity"] == ENTITY


def test_open_create_race_resolves_existing_row_without_reraise():
    """Unique-index create races must re-read and bind the winner, not crash the script."""
    existing = _make_sync(id="77", public_id="race", entity=ENTITY)
    fake = FakeSyncService([existing])
    fake.configure_create_raises_once(RuntimeError("duplicate key"))
    run = WatermarkRun(fake, PROVIDER, ENV, ENTITY).open()
    assert run.sync_record.public_id == "race"


def test_open_create_race_with_no_row_after_failure_reraises():
    """A genuine create failure with no row must propagate — silent success would lie."""
    fake = FakeSyncService()
    fake.configure_create_raises_once(RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):
        WatermarkRun(fake, PROVIDER, ENV, ENTITY).open()


def test_open_ignores_rows_with_different_provider_env_or_entity():
    """Watermark keys are (provider, env, entity) — partial matches must not bind."""
    fake = FakeSyncService(
        [
            _make_sync(id="1", public_id="wrong-provider", provider="ms", entity=ENTITY),
            _make_sync(id="2", public_id="wrong-env", env="sandbox", entity=ENTITY),
            _make_sync(id="3", public_id="wrong-entity", entity="invoice"),
        ]
    )
    run = WatermarkRun(fake, PROVIDER, ENV, ENTITY).open()
    assert len(fake.create_calls) == 1
    assert run.sync_record.entity == ENTITY
    assert run.sync_record.public_id != "wrong-provider"
    assert run.sync_record.public_id != "wrong-env"
    assert run.sync_record.public_id != "wrong-entity"


# --------------------------------------------------------------------------- #
# E. Failed-write honesty
# --------------------------------------------------------------------------- #


def test_write_refuses_to_move_watermark_backward_on_primary_path():
    """
    Primary _write path must refuse backward watermark moves (must fail against pre-fix _write).
    """
    original = _make_sync(last_sync_datetime="2026-03-01T00:00:00Z")
    fake = FakeSyncService([original])
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert fake.updates == []
    assert persisted.last_sync_datetime == "2026-03-01T00:00:00Z"
    assert run.sync_record.last_sync_datetime == "2026-03-01T00:00:00Z"


def test_write_backward_guard_clears_stale_hold_marker():
    """
    When the monotonicity guard blocks a backward/equal write and a hold marker is set,
    _write must clear the stale marker. Must fail against pre-fix _write (early return
    left hold_started_datetime untouched).
    """
    stored = "2026-03-01T00:00:00Z"
    original = _make_sync(
        last_sync_datetime=stored,
        hold_started_datetime="2026-02-15T12:00:00Z",
    )
    fake = FakeSyncService([original])
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert len(fake.updates) == 1
    assert fake.updates[-1][1].hold_started_datetime is None
    assert fake.updates[-1][1].last_sync_datetime == stored
    assert persisted.last_sync_datetime == stored
    assert run.sync_record.hold_started_datetime is None
    assert run.sync_record.last_sync_datetime == stored


def test_write_backward_guard_no_write_when_no_hold_marker_to_clear():
    """Sibling negative control: blocked backward write with no hold marker must not write."""
    stored = "2026-03-01T00:00:00Z"
    original = _make_sync(last_sync_datetime=stored, hold_started_datetime=None)
    fake = FakeSyncService([original])
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert fake.updates == []
    assert persisted.last_sync_datetime == stored
    assert run.sync_record.last_sync_datetime == stored


def test_sync_service_update_by_public_id_preserves_hold_marker_for_generic_update_payload():
    """SyncUpdate HTTP path lacks hold_started_datetime — must preserve existing marker."""
    existing = _make_sync(
        public_id="pub-hold",
        hold_started_datetime="2026-01-01T00:00:00",
        last_sync_datetime="2026-01-15T00:00:00Z",
    )
    repo = Mock()
    repo.read_by_public_id.return_value = existing
    repo.update_by_id.side_effect = lambda s: s

    generic_payload = SimpleNamespace(
        row_version="rv-new",
        provider="qbo",
        env="prod",
        entity="bill",
        last_sync_datetime="2026-02-01T00:00:00Z",
    )
    result = SyncService(repo=repo).update_by_public_id(
        public_id="pub-hold",
        sync=generic_payload,
    )
    assert result.hold_started_datetime == "2026-01-01T00:00:00"
    repo.update_by_id.assert_called_once()
    passed = repo.update_by_id.call_args[0][0]
    assert passed.hold_started_datetime == "2026-01-01T00:00:00"

    # WatermarkRun._persist_watermark always threads hold_started_datetime explicitly.
    watermark_payload = SimpleNamespace(
        row_version="rv-new",
        provider="qbo",
        env="prod",
        entity="bill",
        last_sync_datetime="2026-02-01T00:00:00Z",
        hold_started_datetime=None,
    )
    repo.reset_mock()
    repo.read_by_public_id.return_value = existing
    result = SyncService(repo=repo).update_by_public_id(
        public_id="pub-hold",
        sync=watermark_payload,
    )
    assert result.hold_started_datetime is None
    passed = repo.update_by_id.call_args[0][0]
    assert passed.hold_started_datetime is None


def test_stamp_hold_start_adopts_canonical_row_on_write_conflict():
    """_stamp_hold_start must adopt the canonical row when the stamp write loses a ROWVERSION race."""
    original = _make_sync(hold_started_datetime=None, last_sync_datetime="2026-01-01T00:00:00Z")
    fake = FakeSyncService([original])
    fake.configure_update_failures(
        1,
        advance_last_sync_to="2026-06-01T00:00:00Z",
    )
    run = _opened_run(fake)
    outcome = SyncOutcome.for_service_pull()
    outcome.record_projection_failure("42")
    run.commit(outcome)
    assert run.sync_record.last_sync_datetime == "2026-06-01T00:00:00Z"
    assert len(fake.updates) == 1
    assert fake.updates[0][1].hold_started_datetime == "2026-03-10T14:30:00Z"


def test_clear_hold_marker_adopts_canonical_row_on_write_conflict():
    """_clear_hold_marker must adopt the canonical row when the clear write loses a ROWVERSION race."""
    stored = "2026-03-01T00:00:00Z"
    hold_start = "2026-02-15T12:00:00Z"
    original = _make_sync(
        last_sync_datetime=stored,
        hold_started_datetime=hold_start,
    )
    fake = FakeSyncService([original])
    fake.configure_update_failures(1, advance_last_sync_to=stored)
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert persisted.last_sync_datetime == stored
    assert run.sync_record.last_sync_datetime == stored
    assert run.sync_record.hold_started_datetime == hold_start
    assert len(fake.updates) == 1
    assert fake.updates[0][1].hold_started_datetime is None


def test_write_allows_forward_or_equal_writes_on_primary_path():
    """Monotonicity guard must not block legitimate forward writes; equal value is a no-op success."""
    original = _make_sync(last_sync_datetime="2026-03-01T00:00:00Z")
    fake = FakeSyncService([original])
    run = _opened_run(fake)
    persisted = run._write("2026-03-01T00:00:00Z")
    assert fake.updates == []
    assert persisted.last_sync_datetime == "2026-03-01T00:00:00Z"
    persisted = run._write("2026-04-01T00:00:00Z")
    assert len(fake.updates) == 1
    assert persisted.last_sync_datetime == "2026-04-01T00:00:00Z"


def test_write_raises_when_update_returns_none_and_leaves_sync_record_unchanged():
    """ROWVERSION races / missing rows must not report an advanced watermark that never persisted."""
    original = _make_sync(last_sync_datetime="2026-01-01T00:00:00Z")
    fake = FakeSyncService([original])
    fake.configure_update_returns_none(True)
    run = _opened_run(fake)
    with pytest.raises(RuntimeError, match="Failed to persist sync watermark"):
        run._write("2026-02-01T00:00:00Z")
    assert run.sync_record.last_sync_datetime == "2026-01-01T00:00:00Z"


def test_write_adopts_concurrent_ahead_watermark_without_raising():
    """When a concurrent run already advanced LastSyncDatetime, adopt it instead of failing the pull."""
    original = _make_sync(last_sync_datetime="2026-01-01T00:00:00Z")
    fake = FakeSyncService([original])
    fake.configure_update_failures(
        1,
        advance_last_sync_to="2026-03-01T00:00:00Z",
    )
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert persisted.last_sync_datetime == "2026-03-01T00:00:00Z"
    assert run.sync_record.last_sync_datetime == "2026-03-01T00:00:00Z"
    assert len(fake.updates) == 1


def test_write_adopt_branch_clears_stale_hold_marker_on_adopted_row():
    """Adopting a concurrently-advanced watermark must clear a stale hold marker on that row."""
    original = _make_sync(
        last_sync_datetime="2026-01-01T00:00:00Z",
        hold_started_datetime="2026-02-15T12:00:00Z",
    )
    fake = FakeSyncService([original])
    fake.configure_update_failures(
        1,
        advance_last_sync_to="2026-03-01T00:00:00Z",
    )
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert persisted.last_sync_datetime == "2026-03-01T00:00:00Z"
    assert run.sync_record.last_sync_datetime == "2026-03-01T00:00:00Z"
    assert persisted.hold_started_datetime is None
    assert run.sync_record.hold_started_datetime is None
    assert len(fake.updates) == 2
    assert fake.updates[0][1].hold_started_datetime is None
    assert fake.updates[-1][1].hold_started_datetime is None


def test_write_retries_once_after_rowversion_race_then_succeeds():
    original = _make_sync(last_sync_datetime="2026-01-01T00:00:00Z")
    fake = FakeSyncService([original])
    fake.configure_update_failures(1)
    run = _opened_run(fake)
    persisted = run._write("2026-02-01T00:00:00Z")
    assert persisted.last_sync_datetime == "2026-02-01T00:00:00Z"
    assert len(fake.updates) == 2


def test_write_raises_when_initial_and_retry_update_both_return_none():
    original = _make_sync(last_sync_datetime="2026-01-01T00:00:00Z")
    fake = FakeSyncService([original])
    fake.configure_update_failures(2)
    run = _opened_run(fake)
    with pytest.raises(RuntimeError, match="Failed to persist sync watermark"):
        run._write("2026-02-01T00:00:00Z")
    assert run.sync_record.last_sync_datetime == "2026-01-01T00:00:00Z"


def test_push_last_sync_cutoff_accepts_string_and_datetime_equivalently():
    """Regression: --push compares bill.modified_datetime to a datetime, not a raw watermark string."""
    from sync_qbo_bill import _parse_datetime

    mod_dt = datetime(2026, 6, 15, 12, 30, 0)
    watermark_string = "2026-06-15T12:00:00Z"
    watermark_datetime = datetime(2026, 6, 15, 12, 0, 0)
    cutoff_from_string = _parse_datetime(watermark_string)
    cutoff_from_datetime = _parse_datetime(watermark_datetime)
    assert cutoff_from_string == cutoff_from_datetime
    assert mod_dt > cutoff_from_string
    assert mod_dt > cutoff_from_datetime
    assert not (mod_dt > _parse_datetime("2026-06-15T13:00:00Z"))


# --------------------------------------------------------------------------- #
# F. Fleet drift guards (AST — do not import sync scripts)
# --------------------------------------------------------------------------- #

_SYNC_SCRIPT_GLOB = "sync_qbo_*.py"
_FORBIDDEN_WATERMARK_HELPERS = frozenset({"_get_or_create_sync_record", "_update_sync_record"})

_QBO_SERVICE_MODULE_BY_ENTITY = {
    "bill": "integrations.intuit.qbo.bill.business.service.QboBillService",
    "purchase": "integrations.intuit.qbo.purchase.business.service.QboPurchaseService",
    "vendorcredit": "integrations.intuit.qbo.vendorcredit.business.service.QboVendorCreditService",
    "invoice": "integrations.intuit.qbo.invoice.business.service.QboInvoiceService",
    "vendor": "integrations.intuit.qbo.vendor.business.service.QboVendorService",
    "account": "integrations.intuit.qbo.account.business.service.QboAccountService",
    "term": "integrations.intuit.qbo.term.business.service.QboTermService",
    "customer": "integrations.intuit.qbo.customer.business.service.QboCustomerService",
    "item": "integrations.intuit.qbo.item.business.service.QboItemService",
    "company_info": "integrations.intuit.qbo.company_info.business.service.QboCompanyInfoService",
    "reimburse_charge": "integrations.intuit.qbo.reimburse_charge.business.service.QboReimburseChargeService",
}

_PULL_ENTITY_NAMES = frozenset(_QBO_SERVICE_MODULE_BY_ENTITY.keys())
_EXEMPT_NON_PULL_SYNC_FROM_QBO_ENTITIES = frozenset({"attachable", "physical_address"})


def _iter_sync_script_paths() -> list[Path]:
    return sorted(SCRIPTS_DIR.glob(_SYNC_SCRIPT_GLOB))


@functools.lru_cache(maxsize=None)
def _parsed_script(path_str: str) -> ast.AST:
    return ast.parse(Path(path_str).read_text(encoding="utf-8"), filename=path_str)


def _offending_attr_call_sites(
    attr_names: frozenset[str],
    *,
    receiver: Optional[str] = "outcome",
) -> list[str]:
    offenders: list[str] = []
    for path in _iter_sync_script_paths():
        tree = _parsed_script(str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in attr_names:
                continue
            if receiver is not None:
                if not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == receiver
                ):
                    continue
            offenders.append(f"{path.name}:{node.lineno}")
    return offenders


def _resolve_class(module_path: str):
    module_name, _, class_name = module_path.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _qbo_entity_dirs_with_sync_from_qbo() -> set[str]:
    """Entity folder names under integrations/intuit/qbo that define sync_from_qbo on their service."""
    qbo_root = REPO_ROOT / "integrations" / "intuit" / "qbo"
    entities: set[str] = set()
    for service_path in qbo_root.glob("*/business/service.py"):
        tree = _parsed_script(str(service_path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "sync_from_qbo":
                entities.add(service_path.parent.parent.name)
                break
    return entities


def test_every_pull_service_returns_sync_outcome():
    """Watermarked pull services must return SyncOutcome — not List/Optional/dict accumulators."""
    for entity, module_path in _QBO_SERVICE_MODULE_BY_ENTITY.items():
        service_cls = _resolve_class(module_path)
        hints = typing.get_type_hints(service_cls.sync_from_qbo, globalns={"SyncOutcome": SyncOutcome})
        return_hint = hints.get("return", inspect.signature(service_cls.sync_from_qbo).return_annotation)
        origin = typing.get_origin(return_hint)
        assert origin is SyncOutcome, (
            f"{entity}: sync_from_qbo return type must be SyncOutcome[T] (U-220 contract); "
            f"got {return_hint!r}. Returning a list/dict or taking an outcome= accumulator "
            f"is the silent-omission regression this unit removed."
        )


def test_no_pull_service_accepts_an_outcome_parameter():
    """Return value cannot be forgotten; an injected outcome= accumulator can be."""
    for entity, module_path in _QBO_SERVICE_MODULE_BY_ENTITY.items():
        service_cls = _resolve_class(module_path)
        sig = inspect.signature(service_cls.sync_from_qbo)
        assert "outcome" not in sig.parameters, (
            f"{entity}: sync_from_qbo must not accept outcome=; callers get SyncOutcome from the "
            f"return value so staging failures cannot be dropped by a forgotten keyword."
        )


def test_sync_scripts_never_construct_a_sync_outcome():
    """Scripts must not mint SyncOutcome — the service return is the only legitimate envelope."""
    offenders: list[str] = []
    for path in _iter_sync_script_paths():
        tree = _parsed_script(str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "SyncOutcome":
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "a script that constructs its own SyncOutcome is laundering failures — the outcome must "
        "come from the service's sync_from_qbo return, and WatermarkRun.commit refuses an "
        "unstamped one at runtime. Offenders: " + ", ".join(offenders)
    )


def test_every_pull_script_commits_a_watermark():
    for path in _iter_sync_script_paths():
        text = path.read_text(encoding="utf-8")
        assert "run.commit(" in text or ".commit(" in text, path.name


def test_sync_scripts_never_call_record_synced():
    offenders = _offending_attr_call_sites(frozenset({"record_synced"}), receiver=None)
    assert not offenders, (
        "staging successes are service-owned; scripts record PROJECTION successes via "
        "record_projected(). Calling record_synced in a script conflated what summary()['synced'] "
        "meant per entity. Offenders: " + ", ".join(offenders)
    )


def test_non_watermarked_qbo_services_are_explicitly_classified():
    defining_entities = _qbo_entity_dirs_with_sync_from_qbo()
    expected = _PULL_ENTITY_NAMES | _EXEMPT_NON_PULL_SYNC_FROM_QBO_ENTITIES
    assert defining_entities == expected, (
        "Every sync_from_qbo on a QBO business service must be either one of the eleven "
        "watermarked pull entities (return SyncOutcome) or an explicit exempt (attachable, "
        "physical_address). A new watermarked pull must join the eleven — do not let it "
        "quietly land only in the exempt set. "
        f"defining={sorted(defining_entities)!r} expected={sorted(expected)!r}"
    )


def test_sync_scripts_do_not_define_per_script_watermark_helpers():
    """Per-script watermark copies are the drift vector U-217 removed; extend WatermarkRun instead."""
    offenders: list[str] = []
    for path in _iter_sync_script_paths():
        tree = _parsed_script(str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _FORBIDDEN_WATERMARK_HELPERS:
                offenders.append(f"{path.name}:{node.name}")
    assert not offenders, (
        "the per-script watermark copies are the drift vector U-217 removed; "
        "add your entity to WatermarkRun instead of re-copying. Offenders: "
        + ", ".join(offenders)
    )


def test_every_sync_qbo_script_references_watermark_run():
    """Every QBO pull script must use WatermarkRun so watermark semantics stay unified."""
    missing: list[str] = []
    for path in _iter_sync_script_paths():
        text = path.read_text(encoding="utf-8")
        if "WatermarkRun" not in text:
            missing.append(path.name)
    assert not missing, (
        "the per-script watermark copies are the drift vector U-217 removed; "
        "add your entity to WatermarkRun instead of re-copying. Missing WatermarkRun: "
        + ", ".join(missing)
    )


def test_every_sync_qbo_script_calls_exit_nonzero_on_sync_failure():
    """A script that prints a failure result and falls off the end exits 0 — Finding 5 (U-240)."""
    missing: list[str] = []
    for path in _iter_sync_script_paths():
        text = path.read_text(encoding="utf-8")
        if "exit_nonzero_on_sync_failure(" not in text:
            missing.append(path.name)
    assert not missing, (
        "every sync_qbo_*.py must route its __main__ result through "
        "exit_nonzero_on_sync_failure so a failed run cannot exit 0. Missing: "
        + ", ".join(missing)
    )


def test_sync_scripts_never_call_outcome_record_skip_directly():
    """Skip vs hold must go through record_projection_error, not service-only skip verbs."""
    offenders = _offending_attr_call_sites(
        frozenset({"record_skip", "_record_skip", "record_staging_skip"}),
        receiver="outcome",
    )
    assert not offenders, (
        "classifying by exception type is what let a transient DB error become a permanent skip; "
        "route projection skips through record_projection_error and keep staging skips in the "
        "service. Offenders: " + ", ".join(offenders)
    )


def _except_handlers_containing_record_projection_error(tree: ast.AST) -> list[ast.ExceptHandler]:
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                if child.func.attr == "record_projection_error":
                    handlers.append(node)
                    break
    return handlers


def _sync_from_qbo_outcome_stamps_from_service_pull(service_path: Path) -> bool:
    tree = _parsed_script(str(service_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "sync_from_qbo":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "SyncOutcome"
                and func.attr == "for_service_pull"
            ):
                return True
    return False


def test_sync_scripts_projection_except_blocks_do_not_append_parallel_failure_lists():
    """Failure bookkeeping belongs in SyncOutcome; parallel local lists are U-217 drift."""
    offenders: list[str] = []
    for path in _iter_sync_script_paths():
        tree = _parsed_script(str(path))
        for handler in _except_handlers_containing_record_projection_error(tree):
            for child in ast.walk(handler):
                if not isinstance(child, ast.Call):
                    continue
                if not isinstance(child.func, ast.Attribute):
                    continue
                if child.func.attr != "append":
                    continue
                if isinstance(child.func.value, ast.Name) and (
                    child.func.value.id.startswith("failed_")
                    or child.func.value.id.startswith("skipped_")
                ):
                    offenders.append(f"{path.name}:{child.lineno}")
    assert not offenders, (
        "failure bookkeeping belongs in SyncOutcome; a parallel local list is the drift U-217 removed. "
        "Offenders: " + ", ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# G. ReimburseCharge TxnDate filter rejection (Finding 4)
# --------------------------------------------------------------------------- #


def test_query_reimburse_charges_page_raises_on_start_date():
    from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient

    mock_http = Mock()
    client = QboInvoiceClient(realm_id="r1", http_client=mock_http)
    with pytest.raises(QboValidationError):
        client.query_reimburse_charges_page(start_date="2026-01-01")
    mock_http.get.assert_not_called()
    mock_http.post.assert_not_called()


def test_query_reimburse_charges_page_raises_on_end_date():
    from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient

    mock_http = Mock()
    client = QboInvoiceClient(realm_id="r1", http_client=mock_http)
    with pytest.raises(QboValidationError):
        client.query_reimburse_charges_page(end_date="2026-06-30")
    mock_http.get.assert_not_called()
    mock_http.post.assert_not_called()


def test_sync_from_qbo_reimburse_charge_raises_on_start_date_before_any_io():
    from integrations.intuit.qbo.reimburse_charge.business.service import QboReimburseChargeService

    with patch("integrations.intuit.qbo.reimburse_charge.business.service.QboInvoiceClient") as mock_client_cls:
        with pytest.raises(QboValidationError):
            QboReimburseChargeService().sync_from_qbo(realm_id="r1", start_date="2026-01-01")
        mock_client_cls.assert_not_called()
