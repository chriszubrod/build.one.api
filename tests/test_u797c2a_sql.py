"""U-797c2a: static checks on the stamp-by-id sproc, the trimmed result sets, and the
repository call for StampReviewNotificationById."""
from __future__ import annotations

import re
from contextlib import contextmanager

from entities.review.persistence import review_notification_repo as repo_mod
from shared.authz import provenance
from tests.sproc_text import REPO_ROOT

SQL_PATH = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review_notification.sql"


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _batches(text: str) -> list[str]:
    parts = re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    return [p for p in parts if p.strip()]


def _sproc_batch(name: str) -> str:
    matches = [b for b in _batches(_sql()) if re.search(rf"CREATE OR ALTER PROCEDURE\s+dbo\.{name}\b", b)]
    assert len(matches) == 1, name
    return matches[0]


def test_stamp_by_id_is_nocount_no_rollback_and_bin_and_fills_nulls_only():
    batch = _sproc_batch("StampReviewNotificationById")
    assert "SET NOCOUNT ON" in batch
    assert "ROLLBACK" not in batch.upper()
    for column in ("ConversationId", "InternetMessageId", "DraftGraphMessageId"):
        assert f"WHEN [{column}] IS NULL THEN" in batch, column
    assert batch.count("COLLATE Latin1_General_BIN") == 3


def test_stamp_by_id_addresses_the_row_by_id_and_takes_null_defaults():
    batch = _sproc_batch("StampReviewNotificationById")
    assert re.search(r"WHERE\s+\[Id\]\s*=\s*@Id\s+AND", batch)
    assert re.search(r"@Id\s+BIGINT,", batch)
    for param in ("@ConversationId      NVARCHAR(512) = NULL", "@InternetMessageId   NVARCHAR(512) = NULL",
                  "@DraftGraphMessageId NVARCHAR(512) = NULL"):
        assert param in batch, param


def test_stamp_by_id_and_the_outbox_stamp_have_no_trailing_select():
    for name in ("StampReviewNotificationById", "StampReviewNotificationFromOutbox", "SetReviewNotificationOutbox"):
        assert "SELECT" not in _sproc_batch(name).split("AS", 1)[1], name


def test_create_returns_only_the_id_the_callers_read():
    batch = _sproc_batch("CreateReviewNotification")
    assert re.search(r"SELECT \[Id\]\s+FROM dbo\.\[ReviewNotification\]\s+WHERE \[Id\] = @NewId", batch)
    assert "[PublicId]" not in batch.split("SCOPE_IDENTITY()", 1)[1]


def test_find_result_set_one_is_trimmed_to_what_the_binder_and_the_service_read():
    batch = _sproc_batch("FindReviewNotificationForReply")
    first_select = batch[batch.index("SELECT"):batch.index("FROM dbo.[ReviewNotification]")]
    columns = {c.strip() for c in first_select.replace("SELECT", "").split(",")}
    # U-797c2b: Subject added for the retitled-draft guard (the only addition).
    assert columns == {"[Id]", "[Kind]", "[ProjectId]", "[WorkDate]", "[Subject]"}


def test_the_binder_reads_only_kind_workdate_and_projectid_from_the_row():
    src = open(provenance.__file__, encoding="utf-8").read()
    reads = set(re.findall(r'getattr\(notification, "(\w+)"', src))
    # U-797c2b: Subject is read for the retitled-draft guard.
    assert reads == {"Kind", "WorkDate", "ProjectId", "Subject"}


class _Conn:
    def __init__(self):
        self.committed = False

    def cursor(self):
        return self

    def commit(self):
        self.committed = True


def test_repo_stamp_by_id_calls_the_sproc_with_named_params(monkeypatch):
    conn = _Conn()
    calls = []

    @contextmanager
    def fake_connection():
        yield conn

    monkeypatch.setattr(repo_mod, "get_connection", fake_connection)
    monkeypatch.setattr(repo_mod, "call_procedure",
                        lambda cursor, name, params: calls.append((name, params)))
    repo_mod.ReviewNotificationRepository().stamp_by_id(
        id=77, outbox_id=8, conversation_id="C", internet_message_id="<I>", draft_graph_message_id="G")
    assert calls == [("StampReviewNotificationById", {
        "Id": 77, "OutboxId": 8, "ConversationId": "C", "InternetMessageId": "<I>",
        "DraftGraphMessageId": "G"})]
    assert conn.committed
