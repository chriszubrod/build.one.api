"""U-736: RevokeAllAuthRefreshTokensByAuthId strips rotation grace on recent revokes."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pyodbc
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH_SQL = REPO_ROOT / "entities/auth/sql/dbo.auth.sql"


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


def _drop_auth_schema(cur) -> None:
    cur.execute(
        "IF OBJECT_ID('dbo.FK_AuthRefreshToken_Auth', 'F') IS NOT NULL "
        "ALTER TABLE dbo.AuthRefreshToken DROP CONSTRAINT FK_AuthRefreshToken_Auth"
    )
    cur.execute("IF OBJECT_ID('dbo.AuthRefreshToken', 'U') IS NOT NULL DROP TABLE dbo.AuthRefreshToken")
    cur.execute("IF OBJECT_ID('dbo.Auth', 'U') IS NOT NULL DROP TABLE dbo.Auth")
    for proc in (
        "RevokeAllAuthRefreshTokensByAuthId",
        "RevokeAuthRefreshTokenByHash",
        "CreateAuthRefreshToken",
        "ReadAuthRefreshTokenByHash",
        "CreateAuth",
        "ReadAuths",
        "ReadAuthByPublicId",
    ):
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    _drain(cur)


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    db_name = cur.execute("SELECT DB_NAME()").fetchval()
    assert db_name == "buildone_test", f"refusing to run against database {db_name!r}"
    _drop_auth_schema(cur)
    _exec_file(cur, AUTH_SQL)
    yield c
    _drop_auth_schema(cur)
    c.close()


def _insert_auth(cur, *, auth_id: int, username: str) -> None:
    now = datetime.utcnow()
    cur.execute("SET IDENTITY_INSERT dbo.Auth ON")
    cur.execute(
        """
        INSERT INTO dbo.Auth (Id, CreatedDatetime, Username, PasswordHash)
        VALUES (?, ?, ?, ?)
        """,
        auth_id,
        now,
        username,
        "hash",
    )
    cur.execute("SET IDENTITY_INSERT dbo.Auth OFF")


def _insert_token(
    cur,
    *,
    auth_id: int,
    token_hash: str,
    token_jti: uuid.UUID,
    revoked: datetime | None,
    replaced_by: uuid.UUID | None,
) -> int:
    now = datetime.utcnow()
    expires = now + timedelta(days=30)
    cur.execute(
        """
        INSERT INTO dbo.AuthRefreshToken (
            AuthId, TokenHash, TokenJti, IssuedDatetime, ExpiresDatetime,
            RevokedDatetime, ReplacedByTokenJti
        )
        OUTPUT INSERTED.Id
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        auth_id,
        token_hash,
        str(token_jti),
        now - timedelta(days=1),
        expires,
        revoked,
        str(replaced_by) if replaced_by else None,
    )
    return int(cur.fetchone()[0])


def test_revoke_all_strips_recent_rotation_grace_and_outputs_newly_revoked_only(conn):
    cur = conn.cursor()
    _insert_auth(cur, auth_id=1, username="user-one")
    _insert_auth(cur, auth_id=2, username="user-two")

    now = datetime.utcnow()
    b_jti = uuid.uuid4()
    c_jti = uuid.uuid4()
    other_jti = uuid.uuid4()

    row_a_id = _insert_token(
        cur,
        auth_id=1,
        token_hash="a" * 64,
        token_jti=uuid.uuid4(),
        revoked=now - timedelta(seconds=5),
        replaced_by=b_jti,
    )
    row_b_id = _insert_token(
        cur,
        auth_id=1,
        token_hash="b" * 64,
        token_jti=b_jti,
        revoked=None,
        replaced_by=None,
    )
    row_c_id = _insert_token(
        cur,
        auth_id=1,
        token_hash="c" * 64,
        token_jti=uuid.uuid4(),
        revoked=now - timedelta(minutes=10),
        replaced_by=c_jti,
    )
    other_row_id = _insert_token(
        cur,
        auth_id=2,
        token_hash="d" * 64,
        token_jti=uuid.uuid4(),
        revoked=now - timedelta(seconds=5),
        replaced_by=other_jti,
    )

    cur.execute(
        "EXEC dbo.RevokeAllAuthRefreshTokensByAuthId @AuthId=?, @RevokedDatetime=?",
        1,
        now,
    )
    output_ids = [int(r[0]) for r in cur.fetchall()]
    _drain(cur)

    assert output_ids == [row_b_id]

    cur.execute(
        "SELECT RevokedDatetime, ReplacedByTokenJti FROM dbo.AuthRefreshToken WHERE Id=?",
        row_a_id,
    )
    a_revoked, a_replaced = cur.fetchone()
    assert a_revoked is not None
    assert a_replaced is None

    cur.execute(
        "SELECT RevokedDatetime FROM dbo.AuthRefreshToken WHERE Id=?",
        row_b_id,
    )
    b_revoked = cur.fetchone()[0]
    assert b_revoked is not None

    cur.execute(
        "SELECT ReplacedByTokenJti FROM dbo.AuthRefreshToken WHERE Id=?",
        row_c_id,
    )
    assert cur.fetchone()[0] is not None

    cur.execute(
        "SELECT ReplacedByTokenJti FROM dbo.AuthRefreshToken WHERE Id=?",
        other_row_id,
    )
    assert cur.fetchone()[0] is not None
