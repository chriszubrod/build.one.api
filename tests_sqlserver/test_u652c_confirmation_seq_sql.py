"""U-652c: ConfirmationSeq guards on CodingSuggestion terminal marks (real SQL Server)."""
from __future__ import annotations

import os
import re
import uuid

import pytest

from tests_sqlserver import test_u652_coding_suggestion_sql as u652_sql

pytestmark = u652_sql.pytestmark

_LOCAL_SERVERS = u652_sql._LOCAL_SERVERS
CODING_SUGGESTION_SQL = u652_sql.CODING_SUGGESTION_SQL


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


@pytest.fixture(scope="module")
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = u652_sql._sqlserver_connect(conn_str, autocommit=False)
    name = c.cursor().execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    rcsi = c.cursor().execute(
        "SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = DB_NAME()"
    ).fetchval()
    assert rcsi == 1, "buildone_test must run READ_COMMITTED_SNAPSHOT ON"
    cur = c.cursor()
    u652_sql._drop_coding_artifacts(cur)
    u652_sql._install_supporting_schema(cur)
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset(conn):
    conn.rollback()
    cur = conn.cursor()
    cur.execute("DELETE FROM dbo.ExpenseLineItem")
    cur.execute("DELETE FROM dbo.CodingSuggestion")
    cur.execute(
        "IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL DROP TABLE dbo.ExpenseCodingItem"
    )
    cur.execute(
        "IF OBJECT_ID('dbo.ExpenseCodingItem_u652_backup', 'U') IS NOT NULL "
        "DROP TABLE dbo.ExpenseCodingItem_u652_backup"
    )
    cur.execute("DELETE FROM qbo.PurchaseLine")
    cur.execute("DELETE FROM dbo.Expense")
    cur.execute("DELETE FROM qbo.Purchase")
    conn.commit()
    yield
    conn.rollback()


def _confirm(cur, pub, source="review_submit"):
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionConfirmation
            @PublicId=?,
            @ConfirmedProjectId=1,
            @ConfirmedSubCostCodeId=2,
            @Status=N'enqueued',
            @ConfirmationSource=?;
        """,
        (pub, source),
    )
    row = cur.fetchone()
    return row.ConfirmationSeq if row else None


def _read_snapshot(cur, pub):
    return cur.execute(
        """
        SELECT Status, ConfirmationSource, ConfirmedAt, ConfirmationSeq, WriteError
        FROM dbo.CodingSuggestion WHERE PublicId=?;
        """,
        (pub,),
    ).fetchone()


def _confirm_then_enqueue_then_flag(cur, pub):
    """Simulates worker holding seq N on an enqueued row, then a human flags."""
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionConfirmation
            @PublicId=?,
            @ConfirmedProjectId=1,
            @ConfirmedSubCostCodeId=2,
            @Status=N'confirmed',
            @ConfirmationSource=N'review_submit';
        """,
        (pub,),
    )
    row = cur.fetchone()
    assert row is not None
    seq = row.ConfirmationSeq
    cur.execute("EXEC dbo.MarkCodingSuggestionEnqueued @PublicId=?;", (pub,))
    assert cur.fetchone() is not None
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionFlag
            @PublicId=?,
            @FlagReason=?;
        """,
        (pub, "flagged while worker in flight"),
    )
    flagged = cur.fetchone()
    assert flagged is not None
    assert flagged.Status == "flagged"
    assert flagged.ConfirmationSeq == seq
    return seq


@pytest.fixture
def seeded(conn):
    cur = conn.cursor()
    pub = uuid.uuid4()
    purchase_id, _, expense_id = u652_sql._insert_expense_purchase(cur)
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, QboPurchaseId, QboPurchaseQboId, RealmId, ExpenseId,
            Status, ConfirmationSource, ConfirmationSeq
        ) VALUES (?, N'expense', ?, N'P-100', N'realm-1', ?, N'enqueued', N'review_submit', 0);
        """,
        (pub, purchase_id, expense_id),
    )
    conn.commit()
    return pub


def test_record_confirmation_increments_confirmation_seq(conn, seeded):
    cur = conn.cursor()
    seq1 = _confirm(cur, seeded)
    conn.commit()
    seq2 = _confirm(cur, seeded)
    conn.commit()
    assert seq1 == 1
    assert seq2 == 2
    snap = _read_snapshot(cur, seeded)
    assert snap.ConfirmationSeq == 2


@pytest.mark.parametrize(
    "proc,extra_params",
    [
        ("MarkCodingSuggestionWritten", {"SyncToken": "9"}),
        ("MarkCodingSuggestionError", {"WriteError": "test_err"}),
        ("MarkCodingSuggestionChangedInQbo", {}),
    ],
)
def test_stale_confirmation_seq_returns_no_row_and_leaves_row_untouched(
    conn, seeded, proc, extra_params
):
    cur = conn.cursor()
    stale_seq = _confirm(cur, seeded)
    conn.commit()
    _confirm(cur, seeded)
    conn.commit()
    before = _read_snapshot(cur, seeded)
    assert before.ConfirmationSeq == stale_seq + 1

    params = {"PublicId": seeded, "ExpectedConfirmationSeq": stale_seq, **extra_params}
    placeholders = ", ".join(f"@{k}=?" for k in params)
    cur.execute(f"EXEC dbo.{proc} {placeholders};", tuple(params.values()))
    assert cur.fetchone() is None

    after = _read_snapshot(cur, seeded)
    assert after.Status == before.Status
    assert after.ConfirmationSource == before.ConfirmationSource
    assert after.ConfirmedAt == before.ConfirmedAt
    assert after.ConfirmationSeq == before.ConfirmationSeq
    conn.commit()


@pytest.mark.parametrize(
    "proc,extra_params,expected_status",
    [
        ("MarkCodingSuggestionWritten", {"SyncToken": "9"}, "written"),
        ("MarkCodingSuggestionError", {"WriteError": "test_err"}, "error"),
        ("MarkCodingSuggestionChangedInQbo", {}, "changed_in_qbo"),
    ],
)
def test_current_confirmation_seq_applies_mark(conn, seeded, proc, extra_params, expected_status):
    cur = conn.cursor()
    seq = _confirm(cur, seeded)
    conn.commit()
    params = {"PublicId": seeded, "ExpectedConfirmationSeq": seq, **extra_params}
    placeholders = ", ".join(f"@{k}=?" for k in params)
    cur.execute(f"EXEC dbo.{proc} {placeholders};", tuple(params.values()))
    row = cur.fetchone()
    assert row is not None
    assert row.Status == expected_status
    conn.commit()


@pytest.mark.parametrize("proc", ["MarkCodingSuggestionWritten", "MarkCodingSuggestionError", "MarkCodingSuggestionChangedInQbo"])
def test_null_expected_confirmation_seq_applies_old_behaviour(conn, seeded, proc):
    cur = conn.cursor()
    _confirm(cur, seeded)
    conn.commit()
    cur.execute(f"EXEC dbo.{proc} @PublicId=?;", (seeded,))
    assert cur.fetchone() is not None
    conn.commit()


@pytest.mark.parametrize(
    "proc,extra_params",
    [
        ("MarkCodingSuggestionWritten", {"SyncToken": "9"}),
        ("MarkCodingSuggestionError", {"WriteError": "test_err"}),
        ("MarkCodingSuggestionChangedInQbo", {}),
    ],
)
def test_flagged_after_enqueue_blocks_seq_guarded_mark(
    conn, seeded, proc, extra_params
):
    cur = conn.cursor()
    seq = _confirm_then_enqueue_then_flag(cur, seeded)
    conn.commit()
    params = {"PublicId": seeded, "ExpectedConfirmationSeq": seq, **extra_params}
    placeholders = ", ".join(f"@{k}=?" for k in params)
    cur.execute(f"EXEC dbo.{proc} {placeholders};", tuple(params.values()))
    assert cur.fetchone() is None
    assert _read_snapshot(cur, seeded).Status == "flagged"
    conn.commit()


@pytest.mark.parametrize(
    "proc,extra_params,expected_status",
    [
        ("MarkCodingSuggestionWritten", {"SyncToken": "9"}, "written"),
        ("MarkCodingSuggestionError", {"WriteError": "test_err"}, "error"),
        ("MarkCodingSuggestionChangedInQbo", {}, "changed_in_qbo"),
    ],
)
def test_enqueued_with_matching_seq_still_applies_after_enqueue_mark(
    conn, seeded, proc, extra_params, expected_status
):
    cur = conn.cursor()
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionConfirmation
            @PublicId=?,
            @ConfirmedProjectId=1,
            @ConfirmedSubCostCodeId=2,
            @Status=N'confirmed',
            @ConfirmationSource=N'review_submit';
        """,
        (seeded,),
    )
    seq = cur.fetchone().ConfirmationSeq
    cur.execute("EXEC dbo.MarkCodingSuggestionEnqueued @PublicId=?;", (seeded,))
    assert cur.fetchone().Status == "enqueued"
    conn.commit()
    params = {"PublicId": seeded, "ExpectedConfirmationSeq": seq, **extra_params}
    placeholders = ", ".join(f"@{k}=?" for k in params)
    cur.execute(f"EXEC dbo.{proc} {placeholders};", tuple(params.values()))
    row = cur.fetchone()
    assert row is not None
    assert row.Status == expected_status
    conn.commit()


@pytest.mark.parametrize(
    "proc,extra_params,expected_status",
    [
        ("MarkCodingSuggestionWritten", {"SyncToken": "9"}, "written"),
        ("MarkCodingSuggestionError", {"WriteError": "test_err"}, "error"),
        ("MarkCodingSuggestionChangedInQbo", {}, "changed_in_qbo"),
    ],
)
def test_null_expected_seq_still_applies_to_flagged_row_legacy(
    conn, seeded, proc, extra_params, expected_status
):
    cur = conn.cursor()
    _confirm_then_enqueue_then_flag(cur, seeded)
    conn.commit()
    params = {"PublicId": seeded, **extra_params}
    placeholders = ", ".join(f"@{k}=?" for k in params)
    cur.execute(f"EXEC dbo.{proc} {placeholders};", tuple(params.values()))
    row = cur.fetchone()
    assert row is not None
    assert row.Status == expected_status
    conn.commit()


def test_mark_changed_in_qbo_still_honours_expected_row_version(conn, seeded):
    cur = conn.cursor()
    _confirm(cur, seeded)
    conn.commit()
    rv = cur.execute(
        "SELECT RowVersion FROM dbo.CodingSuggestion WHERE PublicId=?;", (seeded,)
    ).fetchone().RowVersion
    cur.execute(
        "UPDATE dbo.CodingSuggestion SET ModifiedDatetime=SYSUTCDATETIME() WHERE PublicId=?;",
        (seeded,),
    )
    conn.commit()
    cur.execute(
        "EXEC dbo.MarkCodingSuggestionChangedInQbo @PublicId=?, @ExpectedRowVersion=?;",
        (seeded, rv),
    )
    assert cur.fetchone() is None
    assert _read_snapshot(cur, seeded).Status == "enqueued"
    rv2 = cur.execute(
        "SELECT RowVersion FROM dbo.CodingSuggestion WHERE PublicId=?;", (seeded,)
    ).fetchone().RowVersion
    cur.execute(
        "EXEC dbo.MarkCodingSuggestionChangedInQbo @PublicId=?, @ExpectedRowVersion=?;",
        (seeded, rv2),
    )
    row = cur.fetchone()
    assert row is not None
    assert row.Status == "changed_in_qbo"
    conn.commit()


def test_confirmation_seq_alter_is_idempotent(conn):
    cur = conn.cursor()
    u652_sql._exec_batches(cur, CODING_SUGGESTION_SQL)
    u652_sql._exec_batches(cur, CODING_SUGGESTION_SQL)
    col = cur.execute(
        "SELECT COL_LENGTH('dbo.CodingSuggestion', 'ConfirmationSeq')"
    ).fetchval()
    assert col is not None
    conn.commit()
