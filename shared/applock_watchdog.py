"""U-784a: report-mode orphaned application-lock watchdog.

Finds `sp_getapplock` locks whose holder session belongs to a replaced, silent
App Service container (the 2026-11-09 incident), logs them and records a
deduplicated `[qbo].[ReconciliationIssue]`. REPORT ONLY - nothing here ends a
session; that is a later unit. Calls sprocs in shared/sql/dbo.applock_watchdog.sql
(permissions needed: see its header).

A lock is a candidate only when the watchdog itself runs on a container host
(`watchdog_on_container`, a run-level precondition) AND all seven per-lock rules
in RULE_NAMES hold (see `evaluate`). That is NOT proof the holder's host is dead:
a per-container heartbeat (positive liveness) is a prerequisite for the future
kill mode (U-784b).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Optional

from integrations.intuit.qbo.base.drift_types import DRIFT_APPLOCK_ORPHAN_SUSPECTED
from shared.database import call_procedure, get_connection

logger = logging.getLogger(__name__)

ALLOWLIST_PREFIXES = (
    "qbo_sync:", "qbo_outbox_drain", "ms_outbox_drain", "box_outbox_drain",
    "box_file_write:", "ms_excel_write:",
)
CONTAINER_HOST_RE = re.compile(r"^[0-9a-f]{12}$")
RULE_NAMES = ("known_resource", "holder_is_container", "host_silent", "holder_sleeping",
              "held_long_enough", "only_applock", "not_self")


@dataclass(frozen=True)
class LockHolder:
    resource_description: str
    request_mode: Optional[str]
    session_id: int
    host_name: Optional[str]
    program_name: Optional[str]
    login_time: Any
    session_status: Optional[str]
    holder_idle_seconds: Optional[int]
    transaction_age_seconds: Optional[int]
    other_lock_count: Optional[int]
    host_last_request_seconds_ago: Optional[int]


@dataclass(frozen=True)
class Evaluation:
    resource: str
    session_id: int
    host_name: Optional[str]
    login_time: Any
    rules: dict
    watchdog_on_container: bool
    holder_idle_seconds: Optional[int]
    transaction_age_seconds: Optional[int]
    host_last_request_seconds_ago: Optional[int]
    other_lock_count: Optional[int]

    @property
    def candidate(self) -> bool:
        return self.watchdog_on_container and all(self.rules.values())

    def as_dict(self) -> dict:
        return {
            "resource": self.resource,
            "session_id": self.session_id,
            "host_name": self.host_name,
            "login_time": _iso(self.login_time),
            "rules": dict(self.rules),
            "candidate": self.candidate,
            "holder_idle_seconds": self.holder_idle_seconds,
            "transaction_age_seconds": self.transaction_age_seconds,
            "host_last_request_seconds_ago": self.host_last_request_seconds_ago,
            "other_lock_count": self.other_lock_count,
        }


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def parse_resource(resource_description: str) -> str:
    """Lock name between the FIRST '[' and the LAST ']'; the raw string if absent."""
    raw = resource_description or ""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return raw
    return raw[start + 1:end]


def _is_container(host: Optional[str]) -> bool:
    return bool(host and CONTAINER_HOST_RE.match(host))


def watchdog_on_container(caller_host: Optional[str]) -> bool:
    """Run-level precondition: the watchdog itself runs on a container host (the live replacement)."""
    return _is_container(caller_host)


def evaluate(holders, *, caller_host: Optional[str], silence_s: int, min_hold_s: int) -> list[Evaluation]:
    """Pure. Apply the seven per-lock rules (RULE_NAMES order) to every holder; NULLs never pass."""
    on_container = watchdog_on_container(caller_host)
    out: list[Evaluation] = []
    for h in holders:
        name = parse_resource(h.resource_description)
        rules = {
            "known_resource": name.startswith(ALLOWLIST_PREFIXES),
            "holder_is_container": _is_container(h.host_name),
            "host_silent": h.host_last_request_seconds_ago is not None
            and h.host_last_request_seconds_ago >= silence_s,
            "holder_sleeping": h.session_status == "sleeping"
            and h.holder_idle_seconds is not None
            and h.holder_idle_seconds >= silence_s,
            "held_long_enough": h.transaction_age_seconds is not None
            and h.transaction_age_seconds >= min_hold_s,
            "only_applock": h.other_lock_count is not None and h.other_lock_count == 0,
            "not_self": h.host_name is not None and caller_host is not None
            and h.host_name != caller_host,
        }
        out.append(Evaluation(
            resource=name, session_id=h.session_id, host_name=h.host_name,
            login_time=h.login_time, rules=rules, watchdog_on_container=on_container,
            holder_idle_seconds=h.holder_idle_seconds,
            transaction_age_seconds=h.transaction_age_seconds,
            host_last_request_seconds_ago=h.host_last_request_seconds_ago,
            other_lock_count=h.other_lock_count,
        ))
    return out


def _int_env(name: str, default: int, floor: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except ValueError:
        return default
    return max(value, floor)


def get_settings() -> dict:
    """Read env at call time. Only `off` disables; any other value is report mode."""
    raw = os.getenv("APPLOCK_WATCHDOG_MODE", "report").strip().lower()
    mode = "off" if raw == "off" else "report"
    if raw not in ("off", "report"):
        logger.warning(
            "applock.watchdog.mode_unsupported value=%r - kill mode is not available "
            "in this build; running in report mode", raw,
        )
    return {
        "mode": mode,
        "host_silence_min": _int_env("APPLOCK_HOST_SILENCE_MIN", 10, 5),
        "min_hold_min": _int_env("APPLOCK_MIN_HOLD_MIN", 10, 5),
    }


class ApplockRepository:
    """Calls sprocs in shared/sql/dbo.applock_watchdog.sql (read-only)."""

    def read_all(self) -> tuple[list[LockHolder], Optional[int], Optional[str]]:
        """Both procs on ONE connection -> (holders, visible_user_sessions, caller_host)."""
        # Errors propagate unchanged: map_database_error's keyword heuristics mislabel a
        # missing-proc error (the name contains "Lock") as a concurrency violation.
        with get_connection() as conn:
            # pyodbc per-statement timeout: a stalled DMV read must not hang a worker thread.
            conn.timeout = 30
            cursor = conn.cursor()
            try:
                call_procedure(cursor, "ReadApplicationLockHolders", {})
                rows = cursor.fetchall()
                call_procedure(cursor, "CountVisibleUserSessions", {})
                counts = cursor.fetchone()
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass
        holders = [
            LockHolder(
                resource_description=r.ResourceDescription, request_mode=r.RequestMode,
                session_id=r.SessionId, host_name=r.HostName, program_name=r.ProgramName,
                login_time=r.LoginTime, session_status=r.SessionStatus,
                holder_idle_seconds=r.HolderIdleSeconds,
                transaction_age_seconds=r.TransactionAgeSeconds,
                other_lock_count=r.OtherLockCount,
                host_last_request_seconds_ago=r.HostLastRequestSecondsAgo,
            )
            for r in rows
        ]
        if counts is None:
            return holders, None, None
        return holders, counts.VisibleUserSessions, counts.CallerHostName


def _read_all(reader, settings: dict) -> tuple[list[Evaluation], str, bool]:
    """One read -> (evaluations, visibility, watchdog_on_container). Never records anything."""
    holders, visible_sessions, caller_host = reader.read_all()
    evaluations = evaluate(
        holders, caller_host=caller_host,
        silence_s=settings["host_silence_min"] * 60,
        min_hold_s=settings["min_hold_min"] * 60,
    )
    # Seeing <= 1 session means the DMVs show only our own (missing permission).
    visibility = "ok" if (visible_sessions or 0) > 1 else "limited"
    return evaluations, visibility, watchdog_on_container(caller_host)


def _dedup_key(e: Evaluation) -> str:
    """session:login(UTC, formatted directly - no process-timezone dependence):sha1(resource)."""
    lt = e.login_time
    if hasattr(lt, "strftime"):
        if getattr(lt, "tzinfo", None) is not None:
            lt = lt.astimezone(timezone.utc).replace(tzinfo=None)
        stamp = lt.strftime("%Y%m%dT%H%M%S")
    else:
        stamp = "unknown"
    return f"{e.session_id}:{stamp}:{hashlib.sha1(e.resource.encode()).hexdigest()[:12]}"


def _details(e: Evaluation) -> str:
    return (
        f"Suspected orphaned applock '{e.resource}' held by session {e.session_id} on "
        f"{e.host_name} (host silent {e.host_last_request_seconds_ago}s, transaction "
        f"open {e.transaction_age_seconds}s, idle {e.holder_idle_seconds}s). Report only."
    )


def run_watchdog(*, reader=None, issue_repo=None) -> dict:
    """Detect + report orphaned applocks. Raises if the DB read fails."""
    settings = get_settings()
    if settings["mode"] == "off":
        return {"mode": "off"}
    evaluations, visibility, on_container = _read_all(reader or ApplockRepository(), settings)
    if visibility == "limited":
        logger.warning("applock.watchdog.visibility_limited - the watchdog sees only its own "
                       "session (permissions: see the SQL header)")
    candidates = [e for e in evaluations if e.candidate]
    summary = {"mode": settings["mode"], "locks_seen": len(evaluations), "candidates": [],
               "recorded": 0, "already_recorded": 0, "record_failed": 0,
               "visibility": visibility, "watchdog_on_container": on_container}
    if not candidates:
        return summary

    reconciliation_repo = issue_repo
    if reconciliation_repo is None:
        from integrations.intuit.qbo.reconciliation.persistence.repo import (
            ReconciliationIssueRepository,
        )
        reconciliation_repo = ReconciliationIssueRepository()
    # Suppression must never be the failure mode: if the dedup read fails, report anyway
    # (a possible duplicate beats a missed orphan).
    existing: set = set()
    try:
        existing = set(reconciliation_repo.read_unresolved_issue_keys_by_drift_type(
            DRIFT_APPLOCK_ORPHAN_SUSPECTED))
    except Exception as error:
        logger.warning("applock.dedup_read_failed exc=%s - recording without dedup",
                       type(error).__name__)
    for e in candidates:
        logger.warning(
            "applock.orphan_detected resource=%s session_id=%s host_name=%s login_time=%s "
            "idle_s=%s txn_age_s=%s host_silent_s=%s other_locks=%s rules=%s",
            e.resource, e.session_id, e.host_name, _iso(e.login_time), e.holder_idle_seconds,
            e.transaction_age_seconds, e.host_last_request_seconds_ago, e.other_lock_count, e.rules,
        )
        key = _dedup_key(e)
        issue_key = ("", "applock", key)
        summary["candidates"].append({
            "resource": e.resource, "session_id": e.session_id, "host_name": e.host_name,
            "transaction_age_seconds": e.transaction_age_seconds, "key": key,
        })
        if issue_key in existing:
            summary["already_recorded"] += 1
            continue
        try:
            reconciliation_repo.create(
                drift_type="applock_orphan_suspected", severity="high", action="flagged",
                entity_type="applock", realm_id="", qbo_id=key, details=_details(e),
            )
            existing.add(issue_key)
            summary["recorded"] += 1
        except Exception as error:
            summary["record_failed"] += 1
            logger.warning("applock.orphan_record_failed session_id=%s exc=%s",
                           e.session_id, type(error).__name__)
    return summary


def snapshot(*, reader=None) -> dict:
    """Every application lock (parsed resource, ages, rule results), visibility, the run-level
    precondition and the settings used. Read-only; records nothing."""
    settings = get_settings()
    evaluations, visibility, on_container = _read_all(reader or ApplockRepository(), settings)
    return {"locks": [e.as_dict() for e in evaluations], "visibility": visibility,
            "watchdog_on_container": on_container, "settings": settings}
