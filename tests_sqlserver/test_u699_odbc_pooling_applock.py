"""U-699: Transaction sp_getapplock safety with pyodbc ODBC connection pooling.

unixODBC pooling reuses the same SQL Server session (same @@SPID / connect_time).
Session-owned applocks and SET state survive close→connect reuse; lock helpers must
use LockOwner='Transaction' and end the transaction on every exit path.
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pyodbc
import pytest

import shared.database as db
from integrations.box.base.locking import box_app_lock
from integrations.intuit.qbo.base.locking import qbo_app_lock
from integrations.ms.base.locking import ms_app_lock
from shared.db_lock import app_lock

import shared.database  # noqa: F401 — ensure pyodbc.pooling = True

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}
_OBSERVER_APP = "u699-observer"

LOCK_HELPERS: list[tuple[str, Callable[..., contextmanager]]] = [
    ("app_lock", app_lock),
    ("ms_app_lock", ms_app_lock),
    ("box_app_lock", box_app_lock),
    ("qbo_app_lock", qbo_app_lock),
]


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(
    os.environ["BUILDONE_TEST_SQLSERVER"]
) not in _LOCAL_SERVERS:
    raise RuntimeError(
        "tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost"
    )


@pytest.fixture(autouse=True)
def _route_get_connection_to_test_server(monkeypatch):
    def _connect():
        return pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=False)

    monkeypatch.setattr(db, "_connect", _connect)


def _with_app_suffix(conn_str: str, app_name: str) -> str:
    upper = conn_str.upper()
    if "APP=" in upper or "APPLICATION NAME=" in upper:
        stripped = re.sub(r"(?i);?\s*APP=[^;]*", "", conn_str)
        stripped = re.sub(r"(?i);?\s*Application Name=[^;]*", "", stripped)
        base = stripped.rstrip(";")
    else:
        base = conn_str.rstrip(";")
    return f"{base};APP={app_name}"


def _pooled_connect() -> pyodbc.Connection:
    assert pyodbc.pooling is True
    return pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=False)


@contextmanager
def _observer_connect() -> Iterator[pyodbc.Connection]:
    """Distinct ODBC pool key (APP=) → different SQL session from helper checkouts."""
    conn_str = _with_app_suffix(os.environ["BUILDONE_TEST_SQLSERVER"], _OBSERVER_APP)
    conn = pyodbc.connect(conn_str, autocommit=True)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _spid(cur: pyodbc.Cursor) -> int:
    cur.execute("SELECT @@SPID")
    return int(cur.fetchone()[0])


def _session_fingerprint(cur: pyodbc.Cursor) -> tuple[int, Any]:
    spid = _spid(cur)
    cur.execute(
        """
        SELECT c.connect_time
        FROM sys.dm_exec_connections AS c
        WHERE c.session_id = @@SPID
        """
    )
    row = cur.fetchone()
    assert row is not None
    return spid, row[0]


def _trancount(cur: pyodbc.Cursor) -> int:
    cur.execute("SELECT @@TRANCOUNT AS trancount")
    return int(cur.fetchone()[0])


def _applock_test_session(cur: pyodbc.Cursor, resource: str) -> int:
    """Try to acquire from another session (Session owner, no wait). >=0 iff lock is free."""
    cur.execute(
        """
        DECLARE @result INT;
        EXEC @result = sp_getapplock
            @Resource = ?,
            @LockMode = 'Exclusive',
            @LockOwner = 'Session',
            @LockTimeout = 0;
        SELECT @result AS Result;
        """,
        resource,
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else -999


def _release_applock_session(cur: pyodbc.Cursor, resource: str) -> None:
    cur.execute(
        "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session';",
        resource,
    )


def _assert_observer_session_distinct_from(holder_spid: int) -> None:
    with _observer_connect() as observer:
        cur = observer.cursor()
        assert _spid(cur) != holder_spid, "observer must be a different SQL session than the lock holder"
        cur.close()


def _assert_lock_free_from_observer(observer: pyodbc.Connection, resource: str) -> None:
    cur = observer.cursor()
    code = _applock_test_session(cur, resource)
    assert code >= 0, f"lock must be free for observer session, sp_getapplock returned {code}"
    _release_applock_session(cur, resource)
    cur.close()


class _FailReleaseCursor:
    """Cursor proxy that raises on sp_releaseapplock (pyodbc cursors are not monkeypatchable)."""

    def __init__(self, inner: pyodbc.Cursor) -> None:
        self._inner = inner

    def execute(self, sql, *params, **kw):
        if "sp_releaseapplock" in str(sql):
            raise pyodbc.Error("HY000", "simulated release failure")
        return self._inner.execute(sql, *params, **kw)

    def fetchone(self):
        return self._inner.fetchone()

    def close(self) -> None:
        self._inner.close()


class _ReleaseFailConnection:
    """Delegate to a real pyodbc connection but fail sp_releaseapplock on cursors."""

    def __init__(self, conn: pyodbc.Connection) -> None:
        self._conn = conn

    def cursor(self) -> _FailReleaseCursor:
        return _FailReleaseCursor(self._conn.cursor())

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _patch_get_connection_fail_release(monkeypatch: pytest.MonkeyPatch, patch_target: str) -> None:
    real = db.get_connection

    @contextmanager
    def _wrapped(*args, **kwargs):
        with real(*args, **kwargs) as conn:
            yield _ReleaseFailConnection(conn)

    monkeypatch.setattr(patch_target, _wrapped)


def test_pooled_checkout_reuses_same_physical_session():
    conn_a = _pooled_connect()
    try:
        cur_a = conn_a.cursor()
        spid_a, ct_a = _session_fingerprint(cur_a)
        cur_a.close()
    finally:
        conn_a.close()

    conn_b = _pooled_connect()
    try:
        cur_b = conn_b.cursor()
        spid_b, ct_b = _session_fingerprint(cur_b)
        cur_b.close()
    finally:
        conn_b.close()

    assert spid_a == spid_b
    assert ct_a == ct_b


def test_get_connection_leaves_pooled_session_trancount_at_manual_commit_baseline():
    with db.get_connection() as conn:
        conn.execute("SELECT 1").fetchone()
    conn = _pooled_connect()
    try:
        cur = conn.cursor()
        assert _trancount(cur) <= 1, "manual-commit baseline is @@TRANCOUNT=1 on a fresh checkout"
        cur.close()
    finally:
        conn.close()


def test_pooled_checkout_retains_set_transaction_isolation_level():
    """Documents that session-scoped SET state survives pooled reuse (guard for database.py)."""
    conn_a = _pooled_connect()
    try:
        cur = conn_a.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cur.close()
    finally:
        conn_a.close()

    conn_b = _pooled_connect()
    try:
        cur = conn_b.cursor()
        cur.execute(
            """
            SELECT CASE transaction_isolation_level
                WHEN 1 THEN 'READ UNCOMMITTED'
                WHEN 2 THEN 'READ COMMITTED'
                WHEN 3 THEN 'REPEATABLE READ'
                WHEN 4 THEN 'SERIALIZABLE'
                WHEN 5 THEN 'SNAPSHOT'
                ELSE 'OTHER'
            END
            FROM sys.dm_exec_sessions
            WHERE session_id = @@SPID
            """
        )
        level = cur.fetchone()[0]
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cur.close()
        assert level == "READ UNCOMMITTED"
    finally:
        conn_b.close()


@pytest.mark.parametrize("name,helper", LOCK_HELPERS)
def test_lock_free_after_failed_release(name, helper, monkeypatch):
    resource = f"u699_{name}_rel_{uuid.uuid4()}"
    patch_module = {
        "app_lock": "shared.db_lock.get_connection",
        "ms_app_lock": "integrations.ms.base.locking.get_connection",
        "box_app_lock": "integrations.box.base.locking.get_connection",
        "qbo_app_lock": "integrations.intuit.qbo.base.locking.get_connection",
    }[name]
    _patch_get_connection_fail_release(monkeypatch, patch_module)

    holder_spid: dict[str, int] = {}
    with helper(resource, timeout_ms=5000) as acquired:
        assert acquired is True
        with db.get_connection() as conn:
            cur = conn.cursor()
            holder_spid["v"] = _spid(cur)
            cur.close()
        _assert_observer_session_distinct_from(holder_spid["v"])

    with _observer_connect() as observer:
        _assert_lock_free_from_observer(observer, resource)

    conn = _pooled_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT APPLOCK_MODE('public', ?, 'Session')",
            resource,
        )
        assert cur.fetchone()[0] in ("NoLock", "UnLock")
        cur.execute(
            "SELECT APPLOCK_MODE('public', ?, 'Transaction')",
            resource,
        )
        assert cur.fetchone()[0] in ("NoLock", "UnLock")
        assert _trancount(cur) <= 1
        cur.close()
    finally:
        conn.close()


@pytest.mark.parametrize("name,helper", LOCK_HELPERS)
def test_lock_free_after_exception_in_caller_block(name, helper):
    resource = f"u699_{name}_exc_{uuid.uuid4()}"
    holder_spid: dict[str, int] = {}

    with pytest.raises(RuntimeError):
        with helper(resource, timeout_ms=5000) as acquired:
            assert acquired is True
            with db.get_connection() as conn:
                cur = conn.cursor()
                holder_spid["v"] = _spid(cur)
                cur.close()
            _assert_observer_session_distinct_from(holder_spid["v"])
            raise RuntimeError("caller bailed")

    with _observer_connect() as observer:
        _assert_lock_free_from_observer(observer, resource)


@pytest.mark.parametrize("name,helper", LOCK_HELPERS)
def test_second_session_times_out_while_lock_held(name, helper):
    resource = f"u699_{name}_cont_{uuid.uuid4()}"
    entered = {"ok": False}
    holder_spid: dict[str, int] = {}

    with helper(resource, timeout_ms=30_000) as acquired:
        assert acquired is True
        entered["ok"] = True
        with db.get_connection() as conn:
            cur = conn.cursor()
            holder_spid["v"] = _spid(cur)
            cur.close()
        with _observer_connect() as observer:
            cur = observer.cursor()
            assert _spid(cur) != holder_spid["v"]
            code = _applock_test_session(cur, resource)
            cur.close()
            assert code == -1, f"expected timeout (-1), got {code}"

    assert entered["ok"]
