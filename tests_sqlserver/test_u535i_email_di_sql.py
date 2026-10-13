"""U-535i: EmailAttachment DiResultJson optimistic UPDATE against real SQL Server.

Uses a minimal dbo.EmailAttachment table (Id + DiResultJson + ModifiedDatetime + RowVersion).
Full email_message schema is not required for this predicate test.
"""
from __future__ import annotations

import contextlib
import os
import re

import pyodbc
import pytest

from entities.email_message.persistence import repo as email_repo_mod
from entities.email_message.persistence.repo import EmailAttachmentRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}


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


def _install_minimal_email_attachment(conn: pyodbc.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        IF OBJECT_ID('dbo.EmailAttachment', 'U') IS NOT NULL
            DROP TABLE dbo.[EmailAttachment]
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.[EmailAttachment] (
            [Id] BIGINT NOT NULL PRIMARY KEY,
            [DiResultJson] NVARCHAR(MAX) NULL,
            [ModifiedDatetime] DATETIME2(7) NULL,
            [RowVersion] ROWVERSION NOT NULL
        )
        """
    )
    conn.commit()


@contextlib.contextmanager
def _as_repo_connection(conn):
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


@pytest.fixture(scope="module")
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = pyodbc.connect(conn_str, autocommit=False)
    name = c.cursor().execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    _install_minimal_email_attachment(c)
    yield c
    c.close()


@pytest.fixture
def repo(conn, monkeypatch):
    monkeypatch.setattr(
        email_repo_mod,
        "get_connection",
        lambda *a, **k: _as_repo_connection(conn),
    )
    return EmailAttachmentRepository()


def _long_payload(prefix: str, total_len: int = 9000) -> str:
    pad = "x" * max(0, total_len - len(prefix))
    return prefix + pad


def test_di_result_json_optimistic_update_applies_with_current_rowversion(conn, repo):
    row_id = 9001
    raw = _long_payload('{"content":"123-45-6789","note":"')
    masked = _long_payload('{"content":"***-**-6789","note":"')
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.[EmailAttachment] (Id, DiResultJson) VALUES (?, ?)",
        row_id,
        raw,
    )
    conn.commit()
    row_version = cur.execute(
        "SELECT RowVersion FROM dbo.[EmailAttachment] WHERE Id = ?", row_id
    ).fetchval()

    assert repo.update_di_result_json(
        id=row_id,
        di_result_json=masked,
        expected_row_version=row_version,
    )
    stored = cur.execute(
        "SELECT DiResultJson FROM dbo.[EmailAttachment] WHERE Id = ?",
        row_id,
    ).fetchval()
    assert stored == masked


def test_di_result_json_optimistic_update_skips_when_stale(conn, repo):
    row_id = 9002
    raw = _long_payload('{"content":"before","v":')
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.[EmailAttachment] (Id, DiResultJson) VALUES (?, ?)",
        row_id,
        raw,
    )
    conn.commit()
    stale_row_version = cur.execute(
        "SELECT RowVersion FROM dbo.[EmailAttachment] WHERE Id = ?", row_id
    ).fetchval()
    # A concurrent edit that differs only by trailing spaces: NVARCHAR equality would
    # call it unchanged, RowVersion does not.
    cur.execute("UPDATE dbo.[EmailAttachment] SET DiResultJson = DiResultJson + N'   ' WHERE Id = ?", row_id)
    conn.commit()
    raw = cur.execute("SELECT DiResultJson FROM dbo.[EmailAttachment] WHERE Id = ?", row_id).fetchval()

    ok = repo.update_di_result_json(
        id=row_id,
        di_result_json=_long_payload('{"content":"***-**-6789","v":'),
        expected_row_version=stale_row_version,
    )
    assert ok is False
    assert cur.execute(
        "SELECT DiResultJson FROM dbo.[EmailAttachment] WHERE Id = ?",
        row_id,
    ).fetchval() == raw
