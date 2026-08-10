"""Pure-logic tests for SyncOutcome + WatermarkRun (U-217 step 3).

Guards watermark hold/advance semantics, query_start-minus-overlap arithmetic,
commit precedence, row resolution, failed-write honesty, and fleet drift in
scripts/sync_qbo_*.py — no live DB or network.
"""
from __future__ import annotations

import ast
import functools
import importlib
import inspect
import sys
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pytest

from integrations.intuit.qbo.base.errors import (
    QboRateLimitError,
    QboServerError,
    QboTimeoutError,
    QboTransportError,
    is_retryable_error,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.sync.business.model import Sync
from integrations.sync.persistence.repo import SyncRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from sync_helper import (  # noqa: E402
    WatermarkRun,
    _normalize_watermark_value,
    _watermark_overlap_seconds,
)

PROVIDER = "qbo"
ENV = "prod"
ENTITY = "bill"

FIXED_QUERY_START = datetime(2026, 3, 10, 14, 30, 0, tzinfo=timezone.utc)


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
    assert "staging failed: a" in reason
    assert "projection failed: b" in reason


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
    DB failures as ValueError(...) from e. HYT00 query timeouts are not in
    shared.database.is_transient_error — classifying them as skip advances the watermark and
    the QBO Purchase is not re-pulled until a human edits it in QBO again.
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
    outcome = SyncOutcome(from_service_pull=True)
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


# --------------------------------------------------------------------------- #
# C. WatermarkRun.commit precedence
# --------------------------------------------------------------------------- #


def test_commit_skip_true_writes_nothing_on_clean_outcome():
    """--skip-sync-update must not mutate dbo.Sync even when the pull succeeded."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome(from_service_pull=True), skip=True)
    assert fake.updates == []


def test_commit_skip_true_writes_nothing_even_with_end_date():
    """Skip must outrank historical end_date imports that would otherwise stamp the watermark."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome(from_service_pull=True), end_date="2020-01-01", skip=True)
    assert fake.updates == []


def test_commit_holding_outcome_writes_nothing_even_when_end_date_supplied():
    """Bill/vendorcredit bug: end_date must not advance the watermark past a failed batch import."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    outcome = SyncOutcome(from_service_pull=True)
    outcome.record_projection_failure("vc-1")
    before = run.sync_record.last_sync_datetime
    run.commit(outcome, end_date="2019-12-31")
    assert fake.updates == []
    assert run.sync_record.last_sync_datetime == before


def test_commit_staging_only_failure_holds_with_no_write():
    """Audit S-01: staging failures invisible to scripts used to advance past missing qbo.* rows."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    outcome = SyncOutcome(from_service_pull=True)
    outcome.record_staging_failure("staging-only")
    run.commit(outcome)
    assert fake.updates == []


def test_commit_skips_only_outcome_advances_watermark_normally():
    """Benign permanent skips must not block incremental sync from moving forward."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    outcome = SyncOutcome(from_service_pull=True)
    outcome.record_staging_skip("perm")
    run.commit(outcome)
    assert len(fake.updates) == 1
    assert fake.updates[0][1].last_sync_datetime == run.watermark_value


def test_commit_clean_outcome_with_end_date_writes_end_of_day_stamp():
    """Historical TxnDate window imports must stamp the watermark to the batch end date."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome(from_service_pull=True), end_date="2024-07-04")
    assert fake.updates[0][1].last_sync_datetime == "2024-07-04T23:59:59"


def test_commit_clean_outcome_without_end_date_writes_watermark_value():
    """Incremental pulls must persist query_start-minus-overlap on success."""
    fake = FakeSyncService([_make_sync()])
    run = _opened_run(fake)
    run.commit(SyncOutcome(from_service_pull=True))
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
    run.commit(SyncOutcome(from_service_pull=True))
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
