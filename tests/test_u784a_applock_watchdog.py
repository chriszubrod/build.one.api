"""U-784a: report-mode orphan-applock watchdog. Pure tests - fakes only, no DB."""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from integrations.intuit.qbo.base import drift_types
from shared import applock_watchdog as wd
from shared.api import admin as admin_mod

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "shared" / "sql" / "dbo.applock_watchdog.sql"
PY_FILE = ROOT / "shared" / "applock_watchdog.py"

DEAD = "dbdbd6e79e39"
LIVE = "d8b89a407d08"
SILENCE_S = 600
HOLD_S = 900  # deliberately != SILENCE_S so swapped thresholds go red
LOGIN = datetime(2026, 11, 9, 10, 0, 0)
KEY_QBO_SYNC_91 = "91:20261109T100000:771ed52b508e"
KEY_BOX_DRAIN_70 = "70:20261109T100000:f70275f6feb4"


def holder(**kw) -> wd.LockHolder:
    base = dict(
        resource_description="0:[qbo_sync:purchase]:(3e44ecb1)",
        request_mode="X",
        session_id=91,
        host_name=DEAD,
        program_name="python",
        login_time=LOGIN,
        session_status="sleeping",
        holder_idle_seconds=1300,
        transaction_age_seconds=1300,
        other_lock_count=0,
        host_last_request_seconds_ago=1150,
    )
    base.update(kw)
    return wd.LockHolder(**base)


def ev(h, caller=LIVE):
    out = wd.evaluate([h], caller_host=caller, silence_s=SILENCE_S, min_hold_s=HOLD_S)
    assert len(out) == 1
    return out[0]


# ---------------------------------------------------------------- incident replay
def test_incident_replay_three_locks_are_candidates():
    hs = [
        holder(),
        holder(resource_description="0:[box_outbox_drain]:(32264b87)", session_id=70,
               transaction_age_seconds=1310),
        holder(resource_description="0:[box_file_write:2272205120755]:(0a62936d)", session_id=66,
               transaction_age_seconds=1320),
    ]
    out = wd.evaluate(hs, caller_host=LIVE, silence_s=SILENCE_S, min_hold_s=HOLD_S)
    assert [e.candidate for e in out] == [True, True, True]
    assert [e.session_id for e in out] == [91, 70, 66]
    assert [e.resource for e in out] == ["qbo_sync:purchase", "box_outbox_drain", "box_file_write:2272205120755"]
    assert all(all(e.rules.values()) for e in out)
    assert tuple(out[0].rules) == wd.RULE_NAMES
    assert len(wd.RULE_NAMES) == 7


# ---------------------------------------------------------------- one rule fails
@pytest.mark.parametrize("rule,h,caller", [
    ("known_resource", holder(resource_description="0:[qbo_auth_refresh:123]:(aa)"), LIVE),
    ("holder_is_container", holder(host_name="Christophers-MacBook-Pro.local"), LIVE),
    ("host_silent", holder(host_last_request_seconds_ago=30), LIVE),
    ("holder_sleeping", holder(session_status="running"), LIVE),
    ("holder_sleeping", holder(holder_idle_seconds=30), LIVE),
    ("held_long_enough", holder(transaction_age_seconds=120), LIVE),
    ("held_long_enough", holder(transaction_age_seconds=HOLD_S - 1), LIVE),
    ("host_silent", holder(host_last_request_seconds_ago=SILENCE_S - 1), LIVE),
    ("holder_sleeping", holder(holder_idle_seconds=SILENCE_S - 1), LIVE),
    ("only_applock", holder(other_lock_count=3), LIVE),
    ("only_applock", holder(other_lock_count=1), LIVE),
    ("not_self", holder(), DEAD),
])
def test_single_rule_failure_is_not_candidate(rule, h, caller):
    e = ev(h, caller)
    assert e.candidate is False
    assert e.rules[rule] is False
    others = {k: v for k, v in e.rules.items() if k != rule}
    assert all(others.values()), others


@pytest.mark.parametrize("field,rule", [
    ("host_last_request_seconds_ago", "host_silent"),
    ("session_status", "holder_sleeping"),
    ("holder_idle_seconds", "holder_sleeping"),
    ("transaction_age_seconds", "held_long_enough"),
    ("other_lock_count", "only_applock"),
])
def test_null_inputs_never_pass(field, rule):
    e = ev(holder(**{field: None}))
    assert e.rules[rule] is False and e.candidate is False


def test_null_caller_host_never_passes_not_self_and_blocks_everything():
    e = ev(holder(), caller=None)
    assert e.rules["not_self"] is False and e.candidate is False
    assert wd.watchdog_on_container(None) is False


def test_watchdog_not_on_a_container_means_no_candidates():
    e = ev(holder(), caller="Christophers-MacBook-Pro.local")
    assert all(e.rules.values())          # per-lock rules all pass ...
    assert e.candidate is False           # ... but the run-level precondition fails
    assert e.as_dict()["candidate"] is False


def test_null_holder_host_name_not_candidate():
    e = ev(holder(host_name=None))
    assert e.candidate is False
    assert e.rules["holder_is_container"] is False and e.rules["not_self"] is False


# ---------------------------------------------------------------- boundaries
@pytest.mark.parametrize("field,rule,limit", [
    ("host_last_request_seconds_ago", "host_silent", SILENCE_S),
    ("holder_idle_seconds", "holder_sleeping", SILENCE_S),
    ("transaction_age_seconds", "held_long_enough", HOLD_S),
])
def test_threshold_boundaries(field, rule, limit):
    ok = ev(holder(**{field: limit}))
    bad = ev(holder(**{field: limit - 1}))
    assert ok.candidate is True
    assert bad.rules[rule] is False and bad.candidate is False


# ---------------------------------------------------------------- container host shape
@pytest.mark.parametrize("name,expected", [
    ("dbdbd6e79e39", True), ("000000000000", True),
    ("dbdbd6e79e3", False), ("dbdbd6e79e39a", False), ("dbdbd6e79e39.local", False),
    ("DBDBD6E79E39", False), ("ghijklmnopqr", False), ("", False),
])
def test_container_host_re_boundaries(name, expected):
    assert bool(wd.CONTAINER_HOST_RE.match(name)) is expected
    assert ev(holder(host_name=name)).rules["holder_is_container"] is expected
    assert wd.watchdog_on_container(name) is expected
    assert ev(holder(host_name="aaaaaaaaaaaa"), caller=name).candidate is expected


# ---------------------------------------------------------------- allowlist
@pytest.mark.parametrize("name", [
    "qbo_sync:purchase", "qbo_outbox_drain", "ms_outbox_drain", "box_outbox_drain",
    "box_file_write:2272205120755", "ms_excel_write:abc",
])
def test_every_allowlist_prefix_matches(name):
    assert ev(holder(resource_description=f"0:[{name}]:(1)")).rules["known_resource"] is True


@pytest.mark.parametrize("name", [
    "qbo_sync", "xqbo_sync:a", "qbo_outbox", "ms_outbox", "box_outbox", "box_file_write",
    "ms_excel_write", " qbo_sync:a", "QBO_SYNC:a", "qbo_auth_refresh:1", "",
])
def test_near_miss_names_do_not_match(name):
    assert ev(holder(resource_description=f"0:[{name}]:(1)")).rules["known_resource"] is False


def test_allowlist_tracks_the_real_lock_names():
    from integrations.box.outbox.business.worker import DRAIN_LOCK_NAME as box_drain
    from integrations.intuit.qbo.base.locking import qbo_entity_sync_lock_resource
    from integrations.intuit.qbo.outbox.business.worker import DRAIN_LOCK_NAME as qbo_drain
    from integrations.ms.outbox.business.worker import DRAIN_LOCK_NAME as ms_drain

    for real in (qbo_drain, ms_drain, box_drain, qbo_entity_sync_lock_resource("bill")):
        assert real.startswith(wd.ALLOWLIST_PREFIXES), real


@pytest.mark.parametrize("rel,needle", [
    ("integrations/box/excel/business/service.py", 'f"box_file_write:{'),
    ("integrations/ms/sharepoint/external/client.py", 'f"ms_excel_write:{'),
])
def test_inline_lock_name_sources_still_exist(rel, needle):
    assert needle in (ROOT / rel).read_text()


# ---------------------------------------------------------------- parse_resource
def test_parse_resource():
    assert wd.parse_resource("0:[qbo_sync:purchase]:(3e44ecb1)") == "qbo_sync:purchase"
    assert wd.parse_resource("0:[box_file_write:2272205120755]:(0a62936d)") == "box_file_write:2272205120755"
    assert wd.parse_resource("0:[a[b]c]:(1)") == "a[b]c"
    for raw in ("garbage", "", "0:[unterminated", "]x["):
        assert wd.parse_resource(raw) == raw


def test_truncated_name_still_matches_prefix():
    assert ev(holder(resource_description="0:[qbo_sy]:(1)")).rules["known_resource"] is False
    assert ev(holder(resource_description="0:[qbo_sync:purchase_long_trunc]:(1)")).rules["known_resource"] is True


# ---------------------------------------------------------------- settings
ENV = ("APPLOCK_WATCHDOG_MODE", "APPLOCK_HOST_SILENCE_MIN", "APPLOCK_MIN_HOLD_MIN")


@pytest.fixture
def clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_settings_defaults(clean_env):
    assert wd.get_settings() == {"mode": "report", "host_silence_min": 10, "min_hold_min": 10}


@pytest.mark.parametrize("raw", ["off", "OFF", " Off ", "oFf"])
def test_settings_off(clean_env, raw):
    clean_env.setenv("APPLOCK_WATCHDOG_MODE", raw)
    assert wd.get_settings()["mode"] == "off"


def test_settings_kill_degrades_to_report_with_warning(clean_env, caplog):
    clean_env.setenv("APPLOCK_WATCHDOG_MODE", "kill")
    with caplog.at_level(logging.WARNING):
        assert wd.get_settings()["mode"] == "report"
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(msgs) == 1 and "kill" in msgs[0].lower() and "not available" in msgs[0]


def test_settings_floors_and_garbage(clean_env):
    clean_env.setenv("APPLOCK_HOST_SILENCE_MIN", "1")
    clean_env.setenv("APPLOCK_MIN_HOLD_MIN", "0")
    s = wd.get_settings()
    assert s["host_silence_min"] == 5 and s["min_hold_min"] == 5
    clean_env.setenv("APPLOCK_HOST_SILENCE_MIN", "abc")
    clean_env.setenv("APPLOCK_MIN_HOLD_MIN", "")
    clean_env.setenv("APPLOCK_WATCHDOG_MODE", "banana")
    s = wd.get_settings()
    assert s == {"mode": "report", "host_silence_min": 10, "min_hold_min": 10}
    clean_env.setenv("APPLOCK_HOST_SILENCE_MIN", "25")
    assert wd.get_settings()["host_silence_min"] == 25


# ---------------------------------------------------------------- dedup key
def test_key_is_a_hard_coded_literal():
    e = ev(holder())
    assert wd._dedup_key(e) == KEY_QBO_SYNC_91
    assert len(wd._dedup_key(e)) <= 64


def test_key_aware_login_normalised_to_utc():
    aware = datetime(2026, 11, 9, 5, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert wd._dedup_key(ev(holder(login_time=aware))) == KEY_QBO_SYNC_91


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs tzset")
def test_key_independent_of_process_timezone(monkeypatch):
    old = os.environ.get("TZ")
    try:
        for tz in ("UTC", "America/Chicago", "Asia/Kolkata"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            assert wd._dedup_key(ev(holder())) == KEY_QBO_SYNC_91
    finally:
        if old is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", old)
        time.tzset()


def test_house_guard_discovers_the_writer():
    from tests.test_qbo_reconciliation_recorder import discover_reconciliation_issue_write_literals

    found = [c for rel, _, c in discover_reconciliation_issue_write_literals()
             if rel == "shared/applock_watchdog.py"]
    assert len(found) == 1
    assert (found[0].drift_type, found[0].entity_type, found[0].severity, found[0].action) == (
        drift_types.DRIFT_APPLOCK_ORPHAN_SUSPECTED, "applock", "high", "flagged")
    assert drift_types.DRIFT_APPLOCK_ORPHAN_SUSPECTED == "applock_orphan_suspected"


# ---------------------------------------------------------------- run_watchdog
class FakeReader:
    def __init__(self, holders=None, visible=7, caller=LIVE, boom=None):
        self.calls = 0
        self._h, self._visible, self._caller, self._boom = holders or [], visible, caller, boom

    def read_all(self):
        self.calls += 1
        if self._boom:
            raise self._boom
        return self._h, self._visible, self._caller


class FakeIssues:
    def __init__(self, existing=None, boom_on=None, read_boom=False):
        self.created, self.queried, self.existing, self.boom_on = [], [], existing or [], boom_on
        self.read_boom = read_boom

    def read_unresolved_issue_keys_by_drift_type(self, drift_type):
        self.queried.append(drift_type)
        if self.read_boom:
            raise RuntimeError("key read down")
        return list(self.existing)

    def create(self, **kw):
        if self.boom_on is not None and len(self.created) + 1 == self.boom_on:
            self.created.append(None)
            raise RuntimeError("db down")
        self.created.append(kw)


def _incident():
    return [holder(), holder(resource_description="0:[box_outbox_drain]:(32264b87)", session_id=70)]


def test_run_off_never_touches_db(clean_env):
    clean_env.setenv("APPLOCK_WATCHDOG_MODE", "off")
    r, i = FakeReader(_incident()), FakeIssues()
    assert wd.run_watchdog(reader=r, issue_repo=i) == {"mode": "off"}
    assert r.calls == 0 and i.queried == [] and i.created == []


def test_run_report_records_one_issue_per_candidate(clean_env):
    hs = _incident() + [holder(resource_description="0:[qbo_auth_refresh:1]:(2)", session_id=5)]
    r, i = FakeReader(hs), FakeIssues()
    out = wd.run_watchdog(reader=r, issue_repo=i)
    assert out["mode"] == "report" and out["locks_seen"] == 3 and out["visibility"] == "ok"
    assert out["recorded"] == 2 and out["already_recorded"] == 0 and out["record_failed"] == 0
    assert len(out["candidates"]) == 2
    assert i.queried == ["applock_orphan_suspected"]
    for kw, key in zip(i.created, (KEY_QBO_SYNC_91, KEY_BOX_DRAIN_70)):
        assert kw["drift_type"] == "applock_orphan_suspected"
        assert kw["severity"] == "high" and kw["action"] == "flagged"
        assert kw["entity_type"] == "applock" and kw["realm_id"] == ""
        assert kw["qbo_id"] == key
        assert isinstance(kw["details"], str) and "\n" not in kw["details"] and kw["details"]


def test_run_logs_orphan_detected(clean_env, caplog):
    with caplog.at_level(logging.WARNING):
        wd.run_watchdog(reader=FakeReader(_incident()), issue_repo=FakeIssues())
    assert sum("applock.orphan_detected" in r.getMessage() for r in caplog.records) == 2


def test_run_skips_existing_key(clean_env):
    i = FakeIssues(existing=[("", "applock", KEY_QBO_SYNC_91)])
    out = wd.run_watchdog(reader=FakeReader(_incident()), issue_repo=i)
    assert out["already_recorded"] == 1 and out["recorded"] == 1
    assert len(i.created) == 1 and i.created[0]["qbo_id"] == KEY_BOX_DRAIN_70


def test_run_identical_candidates_recorded_once(clean_env):
    i = FakeIssues()
    out = wd.run_watchdog(reader=FakeReader([holder(), holder()]), issue_repo=i)
    assert out["recorded"] == 1 and out["already_recorded"] == 1
    assert len(i.created) == 1


def test_run_recording_failure_is_isolated(clean_env):
    i = FakeIssues(boom_on=1)
    out = wd.run_watchdog(reader=FakeReader(_incident()), issue_repo=i)
    assert out["record_failed"] == 1 and out["recorded"] == 1 and len(i.created) == 2


def test_run_reader_failure_propagates(clean_env):
    with pytest.raises(RuntimeError, match="sql down"):
        wd.run_watchdog(reader=FakeReader(boom=RuntimeError("sql down")), issue_repo=FakeIssues())


def test_run_no_candidates_does_not_query_issues(clean_env):
    i = FakeIssues()
    out = wd.run_watchdog(reader=FakeReader([holder(host_last_request_seconds_ago=1)]), issue_repo=i)
    assert out["candidates"] == [] and out["recorded"] == 0 and i.created == []
    assert i.queried == []


def test_visibility_limited_when_only_own_session_seen(clean_env, caplog):
    with caplog.at_level(logging.WARNING):
        out = wd.run_watchdog(reader=FakeReader([], visible=1), issue_repo=FakeIssues())
    assert out["visibility"] == "limited"
    assert sum("applock.watchdog.visibility_limited" in r.getMessage() for r in caplog.records) == 1


def test_visibility_boundary_two_sessions_is_ok(clean_env):
    run = lambda v: wd.run_watchdog(reader=FakeReader([], visible=v), issue_repo=FakeIssues())["visibility"]
    assert (run(2), run(1), run(0), run(None)) == ("ok", "limited", "limited", "limited")


def test_q5_failed_key_read_does_not_suppress_the_report(clean_env, caplog):
    """Suppression must never be the failure mode: a failed dedup read still records."""
    i = FakeIssues(read_boom=True)
    with caplog.at_level(logging.WARNING):
        out = wd.run_watchdog(reader=FakeReader(_incident()), issue_repo=i)
    assert out["recorded"] == 2 and out["record_failed"] == 0 and len(i.created) == 2
    assert sum("applock.dedup_read_failed" in r.getMessage() for r in caplog.records) == 1


def test_key_read_happens_once_not_per_candidate(clean_env):
    i = FakeIssues()
    wd.run_watchdog(reader=FakeReader(_incident()), issue_repo=i)
    assert i.queried == ["applock_orphan_suspected"]


def test_watchdog_off_container_reports_nothing(clean_env):
    i = FakeIssues()
    out = wd.run_watchdog(reader=FakeReader(_incident(), caller="Chris-MBP.local"), issue_repo=i)
    assert out["candidates"] == [] and out["watchdog_on_container"] is False and i.created == []
    assert wd.run_watchdog(reader=FakeReader(_incident()), issue_repo=FakeIssues())["watchdog_on_container"] is True


def test_bad_mode_warns_once_per_call(clean_env, caplog):
    clean_env.setenv("APPLOCK_WATCHDOG_MODE", "kill")
    for call in (lambda: wd.run_watchdog(reader=FakeReader([]), issue_repo=FakeIssues()),
                 lambda: wd.snapshot(reader=FakeReader([]))):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            call()
        assert sum("mode_unsupported" in r.getMessage() for r in caplog.records) == 1


def test_settings_minutes_are_wired_to_the_right_thresholds(clean_env):
    clean_env.setenv("APPLOCK_HOST_SILENCE_MIN", "7")    # 420 s
    clean_env.setenv("APPLOCK_MIN_HOLD_MIN", "20")       # 1200 s

    def cand(**kw):
        return wd.snapshot(reader=FakeReader([holder(**kw)]))["locks"][0]

    ok = cand(host_last_request_seconds_ago=430, holder_idle_seconds=430, transaction_age_seconds=1210)
    assert ok["candidate"] is True
    assert cand(host_last_request_seconds_ago=430, holder_idle_seconds=430,
                transaction_age_seconds=1190)["rules"]["held_long_enough"] is False
    assert cand(host_last_request_seconds_ago=410, holder_idle_seconds=430,
                transaction_age_seconds=1210)["rules"]["host_silent"] is False
    assert cand(host_last_request_seconds_ago=430, holder_idle_seconds=410,
                transaction_age_seconds=1210)["rules"]["holder_sleeping"] is False


def test_snapshot_shape_and_lock_keys(clean_env):
    snap = wd.snapshot(reader=FakeReader(_incident(), visible=1))
    assert set(snap) == {"locks", "visibility", "watchdog_on_container", "settings"}
    assert snap["visibility"] == "limited" and snap["watchdog_on_container"] is True
    assert snap["settings"] == wd.get_settings()
    lock = snap["locks"][0]
    assert list(lock) == ["resource", "session_id", "host_name", "login_time", "rules", "candidate",
                          "holder_idle_seconds", "transaction_age_seconds",
                          "host_last_request_seconds_ago", "other_lock_count"]
    assert lock["resource"] == "qbo_sync:purchase" and lock["candidate"] is True
    assert lock["login_time"] == "2026-11-09T10:00:00" and tuple(lock["rules"]) == wd.RULE_NAMES
    assert lock["session_id"] == 91 and lock["host_name"] == DEAD


# ---------------------------------------------------------------- repository mapping
class _Cur:
    def __init__(self, holder_rows, count_row):
        self.holder_rows, self.count_row, self.sqls, self.closed = holder_rows, count_row, [], False

    def execute(self, sql, params=None):
        self.sqls.append(sql)

    def fetchall(self):
        assert "ReadApplicationLockHolders" in self.sqls[-1]
        return self.holder_rows

    def fetchone(self):
        assert "CountVisibleUserSessions" in self.sqls[-1]
        return self.count_row

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, cur):
        self.cur, self.cursors = cur, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        self.cursors += 1
        return self.cur


def test_repository_maps_every_column_to_the_right_field_on_one_connection(monkeypatch):
    login = datetime(2026, 1, 2, 3, 4, 5)
    holder_row = SimpleNamespace(
        ResourceDescription="res", RequestMode="mode", SessionId=11, HostName="host",
        ProgramName="prog", LoginTime=login, SessionStatus="stat", HolderIdleSeconds=21,
        TransactionAgeSeconds=22, OtherLockCount=23, HostLastRequestSecondsAgo=24)
    cur = _Cur([holder_row], SimpleNamespace(VisibleUserSessions=9, CallerHostName="caller"))
    conn = _Conn(cur)
    opened = []
    monkeypatch.setattr(wd, "get_connection", lambda: opened.append(1) or conn)
    holders, visible, caller = wd.ApplockRepository().read_all()
    assert holders == [wd.LockHolder(
        resource_description="res", request_mode="mode", session_id=11, host_name="host",
        program_name="prog", login_time=login, session_status="stat", holder_idle_seconds=21,
        transaction_age_seconds=22, other_lock_count=23, host_last_request_seconds_ago=24)]
    assert (visible, caller) == (9, "caller")
    assert len(opened) == 1 and cur.closed is True
    assert [q.split("EXEC dbo.")[1].strip() for q in cur.sqls] == [
        "ReadApplicationLockHolders", "CountVisibleUserSessions"]


def _row(**kw):
    base = dict(
        ResourceDescription="0:[qbo_sync:purchase]:(3e44ecb1)", RequestMode="X", SessionId=91,
        HostName=DEAD, ProgramName="python", LoginTime=LOGIN, SessionStatus="sleeping",
        HolderIdleSeconds=1300, TransactionAgeSeconds=1300, OtherLockCount=0,
        HostLastRequestSecondsAgo=1150)
    base.update(kw)
    return SimpleNamespace(**base)


class _ProcNotFound(Exception):
    pass


def test_repository_propagates_the_original_error_unchanged(monkeypatch):
    # map_database_error would relabel this as a concurrency violation (the proc name has "Lock").
    msg = "Could not find stored procedure 'dbo.ReadApplicationLockHolders'."

    class Boom(_Cur):
        def execute(self, sql, params=None):
            raise _ProcNotFound(msg)

    monkeypatch.setattr(wd, "get_connection", lambda: _Conn(Boom([], None)))
    with pytest.raises(_ProcNotFound) as info:
        wd.ApplockRepository().read_all()
    assert type(info.value) is _ProcNotFound and info.value.args == (msg,)


def test_read_all_sets_a_30_second_query_timeout_before_the_procs_run(monkeypatch):
    seen = []

    class Recording(_Cur):
        def execute(self, sql, params=None):
            seen.append(conn.timeout)
            self.sqls.append(sql)

    cur = Recording([_row()], SimpleNamespace(VisibleUserSessions=9, CallerHostName=LIVE))
    conn = _Conn(cur)
    monkeypatch.setattr(wd, "get_connection", lambda: conn)
    wd.ApplockRepository().read_all()
    assert seen == [30, 30]


def test_read_all_without_a_count_row_fails_closed(clean_env, monkeypatch):
    cur = _Cur([_row()], None)
    monkeypatch.setattr(wd, "get_connection", lambda: _Conn(cur))
    holders, visible, caller = wd.ApplockRepository().read_all()
    assert len(holders) == 1 and visible is None and caller is None
    i = FakeIssues()
    out = wd.run_watchdog(reader=wd.ApplockRepository(), issue_repo=i)
    assert out["visibility"] == "limited" and out["watchdog_on_container"] is False
    assert out["locks_seen"] == 1 and out["candidates"] == [] and out["recorded"] == 0
    assert i.created == [] and i.queried == []


def test_rule_names_are_pinned_exactly():
    assert wd.RULE_NAMES == ("known_resource", "holder_is_container", "host_silent",
                             "holder_sleeping", "held_long_enough", "only_applock", "not_self")


def test_allowlist_prefixes_are_pinned_exactly():
    assert wd.ALLOWLIST_PREFIXES == ("qbo_sync:", "qbo_outbox_drain", "ms_outbox_drain",
                                     "box_outbox_drain", "box_file_write:", "ms_excel_write:")


# ---------------------------------------------------------------- routes
def _find(path, method):
    for r in admin_mod.router.routes:
        if r.path.endswith(path) and method in r.methods:
            return r
    raise AssertionError(f"{method} {path} not registered")


def test_post_route_has_drain_secret_dependency():
    r = _find("/locks/watchdog", "POST")
    assert any(d.dependency is admin_mod._require_drain_secret for d in r.dependencies)


def test_get_route_requires_system_admin():
    r = _find("/locks", "GET")
    assert r.path.endswith("/admin/locks") or r.path == "/locks"
    names = [d.call.__qualname__ for d in r.dependant.dependencies]
    assert any("require_system_admin" in n for n in names), names


@pytest.fixture
def client():
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


def test_post_route_returns_timed_envelope(client, monkeypatch):
    summary = {"mode": "report", "locks_seen": 0, "candidates": [], "recorded": 0,
               "already_recorded": 0, "record_failed": 0}
    monkeypatch.setattr(wd, "run_watchdog", lambda: summary)

    async def _ok():
        return None

    app.dependency_overrides[admin_mod._require_drain_secret] = _ok
    resp = client.post("/api/v1/admin/locks/watchdog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["job"] == "applock_watchdog" and body["result"] == summary


def test_post_route_requires_secret(client):
    assert client.post("/api/v1/admin/locks/watchdog").status_code in (401, 503)


def test_get_route_denies_non_admin_and_lists_for_admin(client, monkeypatch):
    snap = {"locks": [{"resource": "qbo_sync:purchase"}], "visibility": "limited",
            "watchdog_on_container": True,
            "settings": {"mode": "report", "host_silence_min": 10, "min_hold_min": 10}}
    monkeypatch.setattr(wd, "snapshot", lambda: snap)

    async def _user(admin):
        return {"sub": "s", "user_id": 1, "is_system_admin": admin, "company_id": 1}

    async def _non():
        return await _user(False)

    async def _adm():
        return await _user(True)

    app.dependency_overrides[get_current_user_api] = _non
    assert client.get("/api/v1/admin/locks").status_code in (401, 403)
    app.dependency_overrides[get_current_user_api] = _adm
    resp = client.get("/api/v1/admin/locks")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["locks"] == [{"resource": "qbo_sync:purchase"}]
    assert set(data["settings"]) == {"mode", "host_silence_min", "min_hold_min"}
    assert data["visibility"] == "limited"
    assert data == snap  # the route returns the snapshot as-is, reading no env itself


# ---------------------------------------------------------------- SQL / source hygiene
def _batches(sql: str) -> list[str]:
    """Same GO rule as scripts/run_sql.py: a line whose strip().upper() is GO."""
    out, cur = [], []
    for line in sql.split("\n"):
        if line.strip().upper() == "GO":
            if cur:
                out.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def test_sql_file_shape():
    sql = SQL_FILE.read_text()
    procs = re.findall(r"CREATE OR ALTER PROCEDURE\s+(dbo\.\w+)", sql, flags=re.I)
    assert sorted(procs) == ["dbo.CountVisibleUserSessions", "dbo.ReadApplicationLockHolders"]
    batches = [b for b in _batches(sql) if "CREATE OR ALTER" in b.upper()]
    assert len(batches) == 2
    for b in batches:
        assert re.search(r"SET NOCOUNT ON", b, flags=re.I)
    assert sql.rstrip().splitlines()[-1].strip().upper() == "GO"
    assert "-- Contract:" in sql
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP)\b", sql.split("CREATE OR ALTER", 1)[1], flags=re.I)


def test_sql_file_has_activity_and_database_filters():
    sql = SQL_FILE.read_text()
    assert "GREATEST" not in sql.upper()
    assert "resource_database_id = DB_ID()" in sql
    assert "sys.dm_exec_requests" in sql and "@@SPID" in sql and "last_request_end_time" in sql
    holders, count = [b[b.upper().index("CREATE OR ALTER"):] for b in _batches(sql) if "CREATE OR ALTER" in b.upper()]
    assert "CallerHostName" not in holders and "CallerHostName" in count


def test_the_word_KILL_appears_nowhere():
    for p in (SQL_FILE, PY_FILE):
        assert "KILL" not in p.read_text(), p
