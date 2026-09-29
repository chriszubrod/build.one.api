"""U-549 Phase C2 Slice A — data layer (RampChaserDigest + follow-up full-row read).

Pure-logic / mocked DB — no live pyodbc.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.ramp_chaser_digest.persistence.repo import RampChaserDigestRepository
from entities.ramp_transaction_follow_up.business.model import RampTransactionFollowUp
from entities.ramp_transaction_follow_up.persistence.repo import RampTransactionFollowUpRepository
from tests.sproc_text import (
    REPO_ROOT,
    sproc_body,
    sproc_params,
    split_top_level,
    strip_sql_comments,
)

DIGEST_SQL = REPO_ROOT / "entities/ramp_chaser_digest/sql/dbo.ramp_chaser_digest.sql"
FOLLOW_UP_SQL = REPO_ROOT / "entities/ramp_transaction_follow_up/sql/dbo.ramp_transaction_follow_up.sql"

_ROW_VERSION = b"\x00\x01\x02\x03\x04\x05\x06\x07"


def _follow_up_row(**overrides) -> SimpleNamespace:
    base = {
        "Id": 1,
        "PublicId": "11111111-1111-1111-1111-111111111111",
        "RowVersion": _ROW_VERSION,
        "RampTransactionId": "txn-abc",
        "CardHolderRampUserId": "user-1",
        "CardHolderName": "Chris",
        "MerchantName": "Lowe's",
        "Amount": Decimal("92.50"),
        "TransactionDate": "2026-09-18",
        "NeedsMemo": True,
        "NeedsReceipt": True,
        "FirstSeenAt": "2026-09-18T12:00:00",
        "LastDraftedAt": None,
        "DraftMessageId": None,
        "LastNotifiedAt": None,
        "NotifyCount": 0,
        "EscalatedAt": None,
        "ResolvedAt": None,
        "CreatedAt": "2026-09-18T12:00:00",
        "UpdatedAt": "2026-09-18T12:00:00",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _digest_row(**overrides) -> SimpleNamespace:
    base = {
        "Id": 10,
        "PublicId": "22222222-2222-2222-2222-222222222222",
        "RowVersion": _ROW_VERSION,
        "CardHolderRampUserId": "user-1",
        "WeekOf": date(2026, 9, 23),
        "DraftMessageId": None,
        "ConversationId": None,
        "InternetMessageId": None,
        "LastDraftedAt": None,
        "LastNotifiedAt": None,
        "NotifyCount": 0,
        "Outcome": None,
        "RecipientHash": None,
        "CreatedAt": "2026-09-23T08:00:00",
        "UpdatedAt": "2026-09-23T08:00:00",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --- SQL pins: RampChaserDigest idempotency anchor --------------------------------


def test_u549_c2_digest_unique_index_on_cardholder_week():
    text = DIGEST_SQL.read_text(encoding="utf-8")
    assert "UQ_RampChaserDigest_CardHolder_WeekOf" in text
    assert (
        "ON dbo.[RampChaserDigest] ([CardHolderRampUserId], [WeekOf])"
        in text
    )


def test_u549_c2_upsert_merge_keys_cardholder_and_week():
    body = strip_sql_comments(sproc_body(DIGEST_SQL, "UpsertRampChaserDigest"))
    assert re.search(
        r"ON\s+target\.\[CardHolderRampUserId\]\s*=\s*source\.CardHolderRampUserId\s+"
        r"AND\s+target\.\[WeekOf\]\s*=\s*source\.WeekOf",
        body,
        re.IGNORECASE | re.DOTALL,
    )


def test_u549_c2_draft_message_id_column_width_512():
    text = DIGEST_SQL.read_text(encoding="utf-8")
    assert "[DraftMessageId] NVARCHAR(512)" in text
    assert "[ConversationId] NVARCHAR(512)" in text


def test_u549_c2_read_outstanding_filters_unsent_drafts():
    body = strip_sql_comments(sproc_body(DIGEST_SQL, "ReadOutstandingRampChaserDigests"))
    assert re.search(r"\[DraftMessageId\]\s+IS\s+NOT\s+NULL", body, re.I)
    assert re.search(r"\[LastNotifiedAt\]\s+IS\s+NULL", body, re.I)


def test_u549_c2_read_uncaptured_filters_missing_draft_id():
    body = strip_sql_comments(sproc_body(DIGEST_SQL, "ReadUncapturedRampChaserDigests"))
    assert re.search(r"\[DraftMessageId\]\s+IS\s+NULL", body, re.I)
    assert re.search(r"\[LastNotifiedAt\]\s+IS\s+NULL", body, re.I)


def test_u549_c2_stamp_notified_idempotent_on_last_notified():
    body = strip_sql_comments(sproc_body(DIGEST_SQL, "StampRampChaserDigestNotified"))
    assert re.search(r"\[LastNotifiedAt\]\s+IS\s+NULL", body, re.I)


def test_u549_c2_sqlite_second_upsert_same_pair_updates_not_inserts():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE RampChaserDigest (
            Id INTEGER PRIMARY KEY,
            CardHolderRampUserId TEXT NOT NULL,
            WeekOf TEXT NOT NULL,
            UpdatedAt TEXT,
            UNIQUE (CardHolderRampUserId, WeekOf)
        )
        """
    )

    def upsert(cardholder: str, week_of: str) -> int:
        row = conn.execute(
            """
            SELECT Id FROM RampChaserDigest
            WHERE CardHolderRampUserId = ? AND WeekOf = ?
            """,
            (cardholder, week_of),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE RampChaserDigest SET UpdatedAt = 't2' WHERE Id = ?",
                (row[0],),
            )
            return row[0]
        conn.execute(
            """
            INSERT INTO RampChaserDigest (CardHolderRampUserId, WeekOf, UpdatedAt)
            VALUES (?, ?, 't1')
            """,
            (cardholder, week_of),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    first_id = upsert("user-1", "2026-09-23")
    second_id = upsert("user-1", "2026-09-23")
    assert first_id == second_id
    assert conn.execute("SELECT COUNT(*) FROM RampChaserDigest").fetchone()[0] == 1


# --- ReadUnresolvedRampTransactionFollowUps column parity ---------------------


def _select_list_from_sproc(sql_path: Path, sproc_name: str) -> str:
    body = strip_sql_comments(sproc_body(sql_path, sproc_name))
    match = re.search(r"\bSELECT\b(.*?)\bFROM\b", body, re.IGNORECASE | re.DOTALL)
    assert match is not None, f"{sproc_name} must have SELECT ... FROM"
    fragment = re.sub(r"\s+", " ", match.group(1).strip())
    fragment = re.sub(r"^TOP\s+\d+\s+", "", fragment, flags=re.IGNORECASE)
    return fragment


def _output_list_from_sproc(sql_path: Path, sproc_name: str) -> str:
    body = strip_sql_comments(sproc_body(sql_path, sproc_name))
    match = re.search(
        r"\bOUTPUT\b(.*?)(?:\bWHERE\b|;)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, f"{sproc_name} must have OUTPUT ... WHERE or OUTPUT ... ;"
    return re.sub(r"\s+", " ", match.group(1).strip())


_READ_UNRESOLVED_EXPECTED_COLUMNS = [
    "Id",
    "PublicId",
    "RowVersion",
    "RampTransactionId",
    "CardHolderRampUserId",
    "CardHolderName",
    "MerchantName",
    "Amount",
    "TransactionDate",
    "NeedsMemo",
    "NeedsReceipt",
    "FirstSeenAt",
    "LastDraftedAt",
    "DraftMessageId",
    "LastNotifiedAt",
    "NotifyCount",
    "EscalatedAt",
    "ResolvedAt",
    "CreatedAt",
    "UpdatedAt",
]


def _projected_column_names(select_fragment: str) -> list[str]:
    """The name each projection EXPOSES to pyodbc — its alias, else the bare column.

    Must read the OUTPUT name, never a source column nested inside an expression:
    `CONVERT(VARCHAR(30), r.[FirstSeenAt], 126) AS [FirstSeenAt]` exposes the
    alias, and a naive scan matches the inner `r.[FirstSeenAt]` instead — which
    passes unchanged when the alias alone is renamed, the exact drift this test
    exists to catch.
    """
    names = []
    for part in split_top_level(select_fragment):
        alias = re.search(r"\bAS\s*\[(\w+)\]\s*$", part, flags=re.IGNORECASE)
        if alias:
            names.append(alias.group(1))
            continue
        bare = re.fullmatch(r"(?:\w+\.)?\[(\w+)\]", part)
        # Load-bearing, not defensive boilerplate: without it an unrecognised
        # projection shape is silently skipped and the column list still lines up.
        assert bare, f"unparseable projection: {part!r}"
        names.append(bare.group(1))
    return names


def test_read_unresolved_projects_explicit_twenty_columns_for_from_db():
    """RampTransactionFollowUpRepository._from_db reads columns by name from pyodbc rows.

    A dropped or renamed column in ReadUnresolvedRampTransactionFollowUps would not
    fail at the SQL layer — getattr would silently yield None for that model field.
    """
    select_list = _select_list_from_sproc(
        FOLLOW_UP_SQL, "ReadUnresolvedRampTransactionFollowUps"
    )
    assert _projected_column_names(select_list) == _READ_UNRESOLVED_EXPECTED_COLUMNS


def test_u549_c2_unresolved_read_datetime_fields_use_convert_126():
    body = strip_sql_comments(
        sproc_body(FOLLOW_UP_SQL, "ReadUnresolvedRampTransactionFollowUps")
    )
    for col in (
        "FirstSeenAt",
        "LastDraftedAt",
        "LastNotifiedAt",
        "EscalatedAt",
        "ResolvedAt",
        "CreatedAt",
        "UpdatedAt",
    ):
        assert f"CONVERT(VARCHAR(30), r.[{col}], 126)" in body


# --- Repo: read_unresolved returns full models --------------------------------


def test_u549_c2_read_unresolved_returns_full_rows_not_id_strings():
    repo = RampTransactionFollowUpRepository()
    row = _follow_up_row()
    cursor = MagicMock()
    cursor.fetchall.return_value = [row]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch(
        "entities.ramp_transaction_follow_up.persistence.repo.conn_ctx"
    ) as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = conn
        results = repo.read_unresolved()

    assert len(results) == 1
    item = results[0]
    assert isinstance(item, RampTransactionFollowUp)
    assert item.ramp_transaction_id == "txn-abc"
    assert item.merchant_name == "Lowe's"
    assert item.amount == Decimal("92.50")
    assert item.first_seen_at == "2026-09-18T12:00:00"
    assert isinstance(item.first_seen_at, str)


def test_u549_c2_read_unresolved_calls_full_row_sproc():
    repo = RampTransactionFollowUpRepository()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch(
        "entities.ramp_transaction_follow_up.persistence.repo.conn_ctx"
    ) as mock_ctx, patch(
        "entities.ramp_transaction_follow_up.persistence.repo.call_procedure"
    ) as call_proc:
        mock_ctx.return_value.__enter__.return_value = conn
        repo.read_unresolved()
        call_proc.assert_called_once()
        assert call_proc.call_args.kwargs["name"] == "ReadUnresolvedRampTransactionFollowUps"
        assert call_proc.call_args.kwargs["params"] == {}


# --- Repo: digest datetime fields as ISO strings --------------------------------


def test_u549_c2_digest_from_db_datetime_fields_are_strings():
    repo = RampChaserDigestRepository()
    row = _digest_row(
        LastDraftedAt="2026-09-23T09:15:00",
        LastNotifiedAt="2026-09-24T10:00:00",
        CreatedAt="2026-09-23T08:00:00",
        UpdatedAt="2026-09-24T10:00:00",
    )
    digest = repo._from_db(row)
    assert digest is not None
    assert digest.last_drafted_at == "2026-09-23T09:15:00"
    assert digest.last_notified_at == "2026-09-24T10:00:00"
    assert digest.created_at == "2026-09-23T08:00:00"
    assert digest.updated_at == "2026-09-24T10:00:00"
    for value in (
        digest.last_drafted_at,
        digest.last_notified_at,
        digest.created_at,
        digest.updated_at,
    ):
        assert isinstance(value, str)


_READ_DIGEST_EXPECTED_COLUMNS = [
    "Id",
    "PublicId",
    "RowVersion",
    "CardHolderRampUserId",
    "WeekOf",
    "DraftMessageId",
    "ConversationId",
    "InternetMessageId",
    "LastDraftedAt",
    "LastNotifiedAt",
    "NotifyCount",
    "Outcome",
    "RecipientHash",
    "CreatedAt",
    "UpdatedAt",
]


@pytest.mark.parametrize(
    "sproc_name",
    [
        "ReadRampChaserDigestByCardHolderAndWeek",
        "ReadUncapturedRampChaserDigests",
        "ReadOutstandingRampChaserDigests",
    ],
)
def test_u570_digest_read_sprocs_project_recipient_hash(sproc_name: str):
    select_list = _select_list_from_sproc(DIGEST_SQL, sproc_name)
    assert _projected_column_names(select_list) == _READ_DIGEST_EXPECTED_COLUMNS


@pytest.mark.parametrize(
    "sproc_name",
    [
        "UpsertRampChaserDigest",
        "StampRampChaserDigestDrafted",
        "StampRampChaserDigestOutcome",
        "StampRampChaserDigestNotified",
    ],
)
def test_u570_digest_output_sprocs_project_recipient_hash(sproc_name: str):
    output_list = _output_list_from_sproc(DIGEST_SQL, sproc_name)
    assert _projected_column_names(output_list) == _READ_DIGEST_EXPECTED_COLUMNS


def test_u549_c2_digest_sql_datetime_outputs_use_convert_126():
    """Every datetime column of every digest sproc must ship as CONVERT(...,126).

    Drop one and _from_db hands the caller a datetime for that field and a str
    for the rest. Asserted per-column with a regex on purpose: a plain
    `"CONVERT(VARCHAR(30)" in body and col in body` passes when ANY column is
    converted and the name merely appears somewhere, which pinned nothing.
    """
    for sproc in (
        "UpsertRampChaserDigest",
        "ReadRampChaserDigestByCardHolderAndWeek",
        "ReadUncapturedRampChaserDigests",
        "ReadOutstandingRampChaserDigests",
        "StampRampChaserDigestDrafted",
        "StampRampChaserDigestOutcome",
        "StampRampChaserDigestNotified",
    ):
        body = strip_sql_comments(sproc_body(DIGEST_SQL, sproc))
        for col in ("LastDraftedAt", "LastNotifiedAt", "CreatedAt", "UpdatedAt"):
            pattern = (
                r"CONVERT\(\s*VARCHAR\(30\)\s*,\s*"
                r"(?:\w+\.)?\[" + col + r"\]\s*,\s*126\s*\)"
            )
            assert re.search(pattern, body), f"{sproc}: {col} not CONVERT(...,126)"


# --- Sproc param contract (repo ↔ SQL) ----------------------------------------


def _sql_declared_params(sql_path: Path, sproc: str) -> set[str]:
    return {m.lower() for m in re.findall(r"@(\w+)", sproc_params(sql_path, sproc))}


@pytest.mark.parametrize(
    "sql_path,sproc,repo_keys",
    [
        (
            DIGEST_SQL,
            "UpsertRampChaserDigest",
            {"CardHolderRampUserId", "WeekOf", "RecipientHash"},
        ),
        (
            DIGEST_SQL,
            "ReadLatestRampChaserDigestRecipientHash",
            {"CardHolderRampUserId", "WeekOf"},
        ),
        (
            DIGEST_SQL,
            "ReadRampChaserDigestByCardHolderAndWeek",
            {"CardHolderRampUserId", "WeekOf"},
        ),
        (DIGEST_SQL, "ReadUncapturedRampChaserDigests", set()),
        (DIGEST_SQL, "ReadOutstandingRampChaserDigests", set()),
        (
            DIGEST_SQL,
            "StampRampChaserDigestDrafted",
            {
                "CardHolderRampUserId",
                "WeekOf",
                "DraftMessageId",
                "ConversationId",
                "InternetMessageId",
            },
        ),
        (
            DIGEST_SQL,
            "StampRampChaserDigestOutcome",
            {"CardHolderRampUserId", "WeekOf", "Outcome"},
        ),
        (
            DIGEST_SQL,
            "StampRampChaserDigestNotified",
            {"CardHolderRampUserId", "WeekOf", "Outcome"},
        ),
        (
            FOLLOW_UP_SQL,
            "ReadUnresolvedRampTransactionFollowUps",
            set(),
        ),
    ],
)
def test_u549_c2_repo_params_declared_in_sql(
    sql_path: Path, sproc: str, repo_keys: set[str]
):
    declared = _sql_declared_params(sql_path, sproc)
    expected = {k.lower() for k in repo_keys}
    # Equality, not subset: a subset check is vacuous for the no-argument sprocs
    # and let a declared-but-unreachable @CardHolderRampUserId ship on
    # ReadUnresolvedRampTransactionFollowUps, whose repo method sends params={}.
    assert expected == declared, (
        f"{sproc}: repo sends {sorted(expected)}, SQL declares {sorted(declared)}"
    )


@pytest.mark.parametrize(
    "method_name,kwargs,expected_sproc,expected_keys",
    [
        (
            "upsert",
            {"card_holder_ramp_user_id": "u1", "week_of": "2026-09-23"},
            "UpsertRampChaserDigest",
            {"CardHolderRampUserId", "WeekOf", "RecipientHash"},
        ),
        (
            "read_latest_recipient_hash",
            {"card_holder_ramp_user_id": "u1", "week_of": "2026-09-23"},
            "ReadLatestRampChaserDigestRecipientHash",
            {"CardHolderRampUserId", "WeekOf"},
        ),
        (
            "stamp_drafted",
            {
                "card_holder_ramp_user_id": "u1",
                "week_of": "2026-09-23",
                "draft_message_id": "draft-id",
                "conversation_id": "conv",
                "internet_message_id": "imid",
            },
            "StampRampChaserDigestDrafted",
            {
                "CardHolderRampUserId",
                "WeekOf",
                "DraftMessageId",
                "ConversationId",
                "InternetMessageId",
            },
        ),
        (
            "stamp_outcome",
            {
                "card_holder_ramp_user_id": "u1",
                "week_of": "2026-09-23",
                "outcome": "unsent_carryover",
            },
            "StampRampChaserDigestOutcome",
            {"CardHolderRampUserId", "WeekOf", "Outcome"},
        ),
        (
            "stamp_notified",
            {
                "card_holder_ramp_user_id": "u1",
                "week_of": "2026-09-23",
                "outcome": "sent",
            },
            "StampRampChaserDigestNotified",
            {"CardHolderRampUserId", "WeekOf", "Outcome"},
        ),
    ],
)
def test_u549_c2_digest_repo_call_procedure_param_keys(
    method_name: str,
    kwargs: dict,
    expected_sproc: str,
    expected_keys: set[str],
):
    repo = RampChaserDigestRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = _digest_row()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    declared = _sql_declared_params(DIGEST_SQL, expected_sproc)

    conn_patch_target = (
        "entities.ramp_chaser_digest.persistence.repo.get_connection"
        if method_name == "read_latest_recipient_hash"
        else "entities.ramp_chaser_digest.persistence.repo.conn_ctx"
    )

    with patch(conn_patch_target) as mock_conn, patch(
        "entities.ramp_chaser_digest.persistence.repo.call_procedure"
    ) as call_proc:
        if method_name == "read_latest_recipient_hash":
            cursor = MagicMock()
            cursor.fetchone.return_value = SimpleNamespace(RecipientHash="abc")
            conn = MagicMock()
            conn.cursor.return_value = cursor
            conn.__enter__ = MagicMock(return_value=conn)
            conn.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value = conn
        else:
            cursor = MagicMock()
            cursor.fetchone.return_value = _digest_row()
            conn = MagicMock()
            conn.cursor.return_value = cursor
            mock_conn.return_value.__enter__.return_value = conn
        getattr(repo, method_name)(**kwargs)
        assert call_proc.call_args.kwargs["name"] == expected_sproc
        sent = call_proc.call_args.kwargs["params"]
        assert set(sent.keys()) == expected_keys
        assert {k.lower() for k in sent.keys()} <= declared
