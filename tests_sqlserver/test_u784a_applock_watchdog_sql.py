"""U-784a: execute shared/sql/dbo.applock_watchdog.sql against a throwaway local SQL Server.

Real procs, real repository. Fake host names come from the ODBC WSID keyword.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
import warnings
import hashlib
from datetime import datetime
from pathlib import Path

import pyodbc
import pytest

import shared.database as db
from shared import applock_watchdog as wd

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}
# U784_SQL_FILE: test-only hook so a mutated scratch copy of the SQL can be installed (mutation proof).
SQL_FILE = Path(os.environ.get("U784_SQL_FILE")
                or Path(__file__).resolve().parent.parent / "shared" / "sql" / "dbo.applock_watchdog.sql")
if os.environ.get("U784_SQL_FILE"):
    warnings.warn(
        f"U784_SQL_FILE set: installing {SQL_FILE} (sha256 "
        f"{hashlib.sha256(SQL_FILE.read_bytes()).hexdigest()}) instead of shared/sql/dbo.applock_watchdog.sql",
        stacklevel=1)
_RUN = uuid.uuid4().hex[:8]  # per-run names: concurrent runs must not collide
HOLDER_HOST = uuid.uuid4().hex[:12]  # 12-hex container shape, unique per run
RESOURCE = f"qbo_sync:u784test{_RUN}"
SCRATCH = f"dbo.u784_scratch_{_RUN}"
INSTALL_LOCK = "u784_proc_install"


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


_OVERRIDE_KEYS = ("ADDRESS", "ADDR", "DSN")  # can override SERVER= in msodbcsql
_REFUSED_EDITIONS = {5: "Azure SQL Database", 8: "Azure SQL Managed Instance"}


def _conn_str_refusal(conn_str: str):
    """Reason (str) to refuse this connection string, or None."""
    keys = {part.partition("=")[0].strip().upper() for part in conn_str.split(";")}
    bad = [k for k in _OVERRIDE_KEYS if k in keys]
    if bad:
        return ("tests_sqlserver refuses to run: BUILDONE_TEST_SQLSERVER must not contain "
                + "/".join(f"{k.capitalize()}=" for k in bad))
    if _server_host(conn_str) not in _LOCAL_SERVERS:
        return "tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost"
    return None


def _edition_refusal(cur):
    """Reason (str) when connected to Azure SQL Database (5) or Managed Instance (8), else None."""
    edition = int(cur.execute("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT)").fetchone()[0])
    if edition in _REFUSED_EDITIONS:
        return f"tests_sqlserver refuses to run: server is {_REFUSED_EDITIONS[edition]} (EngineEdition {edition})"
    return None


if os.environ.get("BUILDONE_TEST_SQLSERVER"):
    _refusal = _conn_str_refusal(os.environ["BUILDONE_TEST_SQLSERVER"])
    if _refusal:
        raise RuntimeError(_refusal)


def _go_batches(sql: str) -> list[str]:
    """Same rule as scripts/run_sql.py: a batch separator is a line whose strip().upper() is GO."""
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
    return [b for b in (x.strip() for x in out) if b]


def _cs(**extra: str) -> str:
    base = re.sub(r"(?i);?\s*(APP|WSID)=[^;]*", "", os.environ["BUILDONE_TEST_SQLSERVER"]).rstrip(";")
    return base + "".join(f";{k}={v}" for k, v in extra.items())


@pytest.fixture(autouse=True)
def _route_get_connection_to_test_server(monkeypatch):
    monkeypatch.setattr(db, "_connect",
                        lambda: pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=False))


@pytest.fixture(scope="module", autouse=True)
def _install_procs():
    if not os.environ.get("BUILDONE_TEST_SQLSERVER"):
        yield
        return
    batches = _go_batches(SQL_FILE.read_text())
    conn = pyodbc.connect(_cs(APP="u784-setup"), autocommit=True)
    cur = conn.cursor()
    _reason = _edition_refusal(cur)
    if _reason:
        conn.close()
        raise RuntimeError(_reason)
    # Hold the session-owned install applock for the WHOLE module run, so a concurrent run
    # cannot reinstall the procs under us. Wait up to 120 s (> a full run).
    cur.execute("DECLARE @r INT; EXEC @r = sp_getapplock @Resource=?, @LockMode='Exclusive', "
                "@LockOwner='Session', @LockTimeout=120000; SELECT @r", INSTALL_LOCK)
    assert cur.fetchone()[0] >= 0, "could not take the install lock"
    try:
        for b in batches:
            cur.execute(b)
        cur.execute(f"IF OBJECT_ID('{SCRATCH}') IS NOT NULL DROP TABLE {SCRATCH}; CREATE TABLE {SCRATCH} (id INT PRIMARY KEY)")
        yield
    finally:
        try:
            cur.execute(f"IF OBJECT_ID('{SCRATCH}') IS NOT NULL DROP TABLE {SCRATCH}")
        finally:
            # Explicit release BEFORE close: ODBC pooling keeps session applocks alive across reuse.
            cur.execute("EXEC sp_releaseapplock @Resource=?, @LockOwner='Session'", INSTALL_LOCK)
            conn.close()


def _holder_conn(app="u784-holder", host=HOLDER_HOST) -> pyodbc.Connection:
    return pyodbc.connect(_cs(APP=app, WSID=host), autocommit=False)


def _take_lock(conn, resource=RESOURCE) -> int:
    cur = conn.cursor()
    cur.execute("SELECT @@SPID")
    spid = int(cur.fetchone()[0])
    cur.execute(
        "DECLARE @r INT; EXEC @r = sp_getapplock @Resource=?, @LockMode='Exclusive', "
        "@LockOwner='Transaction', @LockTimeout=0; SELECT @r", resource)
    assert cur.fetchone()[0] >= 0
    return spid


@pytest.fixture
def holder_conn():
    conn = _holder_conn()
    try:
        yield conn
    finally:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass


def _mine(rows):
    return [r for r in rows if wd.parse_resource(r.resource_description) == RESOURCE]


def test_read_holders_maps_every_field(holder_conn):
    spid = _take_lock(holder_conn)
    with pyodbc.connect(_cs(APP="u784-observer"), autocommit=True) as obs:
        observer_host = obs.cursor().execute("SELECT HOST_NAME()").fetchone()[0]
    all_holders, visible, caller = wd.ApplockRepository().read_all()
    rows = _mine(all_holders)
    assert len(rows) == 1
    r = rows[0]
    assert wd.parse_resource(r.resource_description) == RESOURCE
    assert r.request_mode == "X"
    assert r.session_id == spid
    assert r.host_name == HOLDER_HOST
    assert r.program_name == "u784-holder"
    assert isinstance(r.login_time, datetime)
    assert r.session_status == "sleeping"
    assert r.other_lock_count == 0
    assert r.transaction_age_seconds is not None and r.transaction_age_seconds >= 0
    assert r.holder_idle_seconds is not None and r.holder_idle_seconds >= 0
    assert r.host_last_request_seconds_ago is not None
    assert caller == observer_host and caller != HOLDER_HOST
    assert visible >= 2  # holder session + the watchdog's own (>= 2 means "ok")


def test_other_locks_are_counted(holder_conn):
    _take_lock(holder_conn)
    holder_conn.cursor().execute(f"INSERT INTO {SCRATCH} (id) VALUES (784)")  # row lock + intent locks, txn open
    (r,) = _mine(wd.ApplockRepository().read_all()[0])
    assert r.other_lock_count >= 1
    holder_conn.rollback()


def test_running_request_counts_as_host_activity(holder_conn):
    """Timeline (t0 = lock taken; the holder session is idle from then on):
    sleeper WAITFOR 6 s from ~t0+1 (ends ~t0+7); observe ~3 s after it ends (~t0+10).
    Correct (latest of start/end, MAX): ~3. Start-only: ~9. MIN: ~10 (the idle holder)."""
    _take_lock(holder_conn)
    time.sleep(1)
    sleeper = _holder_conn(app="u784-sleeper")
    t = threading.Thread(target=lambda: sleeper.cursor().execute("WAITFOR DELAY '00:00:06'"))
    try:
        t.start()
        time.sleep(1.5)
        repo = wd.ApplockRepository()
        (r,) = _mine(repo.read_all()[0])
        assert r.host_last_request_seconds_ago == 0
        t.join()
        time.sleep(3)
        (r,) = _mine(repo.read_all()[0])
        assert 2 <= r.host_last_request_seconds_ago <= 7, r.host_last_request_seconds_ago
    finally:
        t.join()
        sleeper.close()


def test_visible_session_count_matches_dmv(holder_conn):
    _take_lock(holder_conn)
    sleeper = _holder_conn(app="u784-second")
    try:
        _, visible, _ = wd.ApplockRepository().read_all()
        with pyodbc.connect(_cs(APP="u784-observer"), autocommit=True) as obs:
            expected = obs.cursor().execute(
                "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1 AND host_name IS NOT NULL"
            ).fetchone()[0]
        assert visible >= 3 and abs(visible - expected) <= 2  # holder + sleeper + watchdog; pool churn tolerance
    finally:
        sleeper.close()


def test_lock_in_another_database_is_not_returned(holder_conn):
    cur = holder_conn.cursor()
    try:
        cur.execute("USE master")
    except pyodbc.Error:
        pytest.skip("cannot USE master on this test server")
    _take_lock(holder_conn)
    assert _mine(wd.ApplockRepository().read_all()[0]) == []


# ---------------------------------------------------------------- C6: guard helpers (no DB)
class _EditionCur:
    def __init__(self, value):
        self.value = value

    def execute(self, sql, params=None):
        return self

    def fetchone(self):
        return (self.value,)


@pytest.mark.parametrize("edition", [5, 8])
def test_refuses_azure_sql_database_and_managed_instance(edition):
    assert _edition_refusal(_EditionCur(edition)) is not None


@pytest.mark.parametrize("edition", [2, 3, 4])
def test_allows_on_prem_engine_editions(edition):
    assert _edition_refusal(_EditionCur(edition)) is None


@pytest.mark.parametrize("cs", [
    "DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1;Address=10.0.0.5;UID=u",
    "DRIVER=x;SERVER=localhost;ADDR=10.0.0.5",
    "DRIVER=x;SERVER=localhost;addr=10.0.0.5",
    "DRIVER=x;SERVER=127.0.0.1;DSN=prod",
    "DRIVER=x;SERVER=localhost;dsn = prod",
    "DRIVER=x;SERVER=remote.example.com;UID=u",
])
def test_refuses_override_keys_and_remote_servers(cs):
    assert _conn_str_refusal(cs) is not None


def test_accepts_plain_local_connection_string():
    assert _conn_str_refusal("DRIVER=x;SERVER=127.0.0.1,1433;UID=u;PWD=p") is None
