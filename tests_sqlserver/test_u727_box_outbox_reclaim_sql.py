"""U-727: ReadStaleInProgressBoxOutbox + FailBoxOutbox transition on local SQL Server."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pyodbc
import pytest

from integrations.box.outbox.persistence.repo import BoxOutboxRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTBOX_SQL = REPO_ROOT / "integrations/box/outbox/sql/box.outbox.sql"


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
    raise RuntimeError("tests_sqlserver refuses non-local SERVER")


def _sqlserver_connect(conn_str: str, *, autocommit: bool = False):
    return pyodbc.connect(conn_str, autocommit=autocommit)


def _batches(sql: str) -> list[str]:
    out, cur = [], []
    for line in sql.splitlines():
        if line.strip() == "GO":
            out.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    out.append("\n".join(cur))
    return [b for b in out if any(l.strip() and not l.strip().startswith("--") for l in b.splitlines())]


def _exec_file(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drain(cur) -> None:
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


def _drop_all(cur) -> None:
    for proc in (
        "ReadStaleInProgressBoxOutbox",
        "ClaimNextPendingBoxOutbox",
        "FailBoxOutbox",
        "DeadLetterBoxOutbox",
        "CompleteBoxOutbox",
        "CreateBoxOutbox",
        "ReadBoxOutboxById",
        "ReadBoxOutboxByPublicId",
        "ReadPendingBoxOutboxByEntity",
        "UpdateBoxOutboxPayload",
        "UpdateBoxOutboxReadyAfter",
    ):
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
        cur.execute(f"IF OBJECT_ID('{proc}', 'P') IS NOT NULL DROP PROCEDURE {proc}")
    cur.execute("IF OBJECT_ID('box.Outbox', 'U') IS NOT NULL DROP TABLE box.Outbox")
    cur.execute("IF SCHEMA_ID('box') IS NOT NULL DROP SCHEMA box")
    _drain(cur)


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    db_name = cur.execute("SELECT DB_NAME()").fetchval()
    assert db_name == "buildone_test", f"refusing to run against database {db_name!r}"
    _drop_all(cur)
    _exec_file(cur, OUTBOX_SQL)
    cur.execute("SELECT 1 FROM dbo.[User] WHERE Id = 17")
    assert cur.fetchone(), "buildone_test must have dbo.User Id=17 for BoxOutbox FK"
    yield c
    _drop_all(cur)
    c.close()


def _insert_outbox(
    cur,
    *,
    status: str,
    started_at: datetime | None,
    modified_at: datetime | None = None,
    attempts: int = 1,
    kind: str = "update_box_excel",
) -> int:
    now = datetime.utcnow()
    mod = modified_at if modified_at is not None else now
    cur.execute(
        """
        INSERT INTO box.Outbox (
            CreatedDatetime, ModifiedDatetime,
            Kind, EntityType, EntityPublicId, RequestId,
            Status, Attempts, ReadyAfter, StartedAt, CreatedByUserId
        )
        OUTPUT INSERTED.Id
        VALUES (
            ?, ?,
            ?, 'bill', ?, ?,
            ?, ?, ?, ?, 17
        )
        """,
        now,
        mod,
        kind,
        uuid.uuid4(),
        uuid.uuid4(),
        status,
        attempts,
        now,
        started_at,
    )
    return int(cur.fetchone()[0])


def _read_stale(cur, stale_before: datetime):
    cur.execute("SET NOCOUNT ON;")
    _drain(cur)
    cur.execute(
        "EXEC ReadStaleInProgressBoxOutbox @StaleBefore = ?",
        stale_before,
    )
    rows = cur.fetchall() if cur.description else []
    _drain(cur)
    return rows


def test_read_stale_in_progress_filters_and_orders(conn):
    cur = conn.cursor()
    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=30)
    old = now - timedelta(hours=2)
    recent = now - timedelta(minutes=5)

    id_old = _insert_outbox(cur, status="in_progress", started_at=old)
    _insert_outbox(cur, status="in_progress", started_at=recent)
    _insert_outbox(cur, status="pending", started_at=old)
    _insert_outbox(cur, status="failed", started_at=old)
    _insert_outbox(cur, status="done", started_at=old)
    id_null_started = _insert_outbox(
        cur,
        status="in_progress",
        started_at=None,
        modified_at=old,
    )

    rows = _read_stale(cur, stale_before)
    ids = [int(r.Id) for r in rows]
    assert ids == sorted(ids)
    assert set(ids) == {id_old, id_null_started}
    assert len(rows) == 2

    repo = BoxOutboxRepository()
    mapped = [repo._from_db(r) for r in rows]
    for m in mapped:
        assert m.id is not None
        assert m.row_version is not None
        assert m.kind is not None
        assert m.attempts is not None
        assert m.started_at is not None or m.modified_datetime is not None


def test_fail_box_outbox_moves_stale_in_progress_to_failed_without_incrementing_attempts(
    conn,
):
    cur = conn.cursor()
    stale_started = datetime.utcnow() - timedelta(hours=3)
    row_id = _insert_outbox(
        cur,
        status="in_progress",
        started_at=stale_started,
        attempts=2,
    )
    cur.execute(
        "SELECT RowVersion, Attempts FROM box.Outbox WHERE Id = ?",
        row_id,
    )
    rv, attempts_before = cur.fetchone()

    cur.execute("SET NOCOUNT ON;")
    _drain(cur)
    cur.execute(
        """
        EXEC FailBoxOutbox
            @Id = ?,
            @RowVersion = ?,
            @NextRetryAt = ?,
            @LastError = ?
        """,
        row_id,
        rv,
        datetime.utcnow(),
        "U-727 reclaim: abandoned in_progress claim; retrying",
    )
    _drain(cur)

    cur.execute(
        "SELECT Status, Attempts, LastError FROM box.Outbox WHERE Id = ?",
        row_id,
    )
    status, attempts_after, last_error = cur.fetchone()
    assert status == "failed"
    assert attempts_after == attempts_before
    assert "U-727 reclaim" in last_error
