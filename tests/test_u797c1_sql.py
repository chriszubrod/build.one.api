"""U-797c1: static checks on the ReviewNotification base SQL and the backfill migration."""
from __future__ import annotations

import re

import pytest

from tests.sproc_text import REPO_ROOT

SQL_PATH = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review_notification.sql"
BACKFILL_PATH = REPO_ROOT / "scripts" / "migrations" / "2026_11_20_u797c1_backfill_review_notification.sql"

EXPECTED_SPROCS = {
    "CreateReviewNotification",
    "AddReviewNotificationTarget",
    "SetReviewNotificationOutbox",
    "StampReviewNotificationFromOutbox",
    "StampReviewNotificationById",  # U-797c2a
    "DeleteReviewNotification",
    "FindReviewNotificationForReply",
    "ReadReviewNotificationTargetSnapshots",  # U-797b1
    "ReadContractLaborReviewNotificationByProjectAndWorkDate",  # U-747
}


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _batches(text: str) -> list[str]:
    parts = re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    return [p for p in parts if p.strip()]


def _sproc_batch(text: str, name: str) -> str:
    matches = [b for b in _batches(text) if re.search(rf"CREATE OR ALTER PROCEDURE\s+dbo\.{name}\b", b)]
    assert len(matches) == 1, f"{name}: expected exactly one batch, got {len(matches)}"
    return matches[0]


def test_header_is_contract_shaped_and_lists_every_sproc():
    lines = _sql().splitlines()
    assert lines[0].startswith("-- Contract: dbo.ReviewNotification")
    assert lines[1].startswith("-- Owns:")
    assert lines[2].startswith("-- Invariants:")
    sprocs_line = [ln for ln in lines if ln.startswith("-- Sprocs:")]
    assert len(sprocs_line) == 1
    listed = {p.strip() for p in sprocs_line[0].removeprefix("-- Sprocs:").split(",") if p.strip()}
    assert listed == EXPECTED_SPROCS


def test_defines_exactly_the_expected_sprocs():
    defined = set(re.findall(r"CREATE OR ALTER PROCEDURE\s+dbo\.(\w+)", _sql()))
    assert defined == EXPECTED_SPROCS


def test_both_tables_exist_with_idempotent_guards():
    text = _sql()
    assert "IF OBJECT_ID('dbo.ReviewNotification', 'U') IS NULL" in text
    assert "IF OBJECT_ID('dbo.ReviewNotificationTarget', 'U') IS NULL" in text
    assert "CREATE TABLE [dbo].[ReviewNotification]" in text
    assert "CREATE TABLE [dbo].[ReviewNotificationTarget]" in text


def test_three_graph_id_columns_are_binary_collation():
    text = _sql()
    for col in ("ConversationId", "InternetMessageId", "DraftGraphMessageId"):
        assert re.search(rf"\[{col}\]\s+NVARCHAR\(512\)\s+COLLATE Latin1_General_BIN", text), col


def test_unique_filtered_indexes_and_conversation_index():
    text = _sql()
    assert re.search(r"CREATE UNIQUE INDEX UX_ReviewNotification_ReviewId .*WHERE \[ReviewId\] IS NOT NULL", text)
    assert re.search(
        r"CREATE UNIQUE INDEX UX_ReviewNotification_CrewClaim .*\(\[ProjectId\], \[WorkDate\]\) WHERE \[Kind\] = N'contract_labor'",
        text,
    )
    assert re.search(r"CREATE UNIQUE INDEX UX_ReviewNotification_OutboxId .*WHERE \[OutboxId\] IS NOT NULL", text)
    assert re.search(r"CREATE INDEX IX_ReviewNotification_ConversationId .*WHERE \[ConversationId\] IS NOT NULL", text)


def test_target_table_has_exactly_one_document_check_and_unique_tuple():
    text = _sql()
    assert "CHECK (" in text and "= 1)," in text
    for col in ("BillId", "ExpenseId", "BillCreditId", "ContractLaborId"):
        assert f"[{col}]" in text
    assert "UNIQUE ([ReviewNotificationId], [BillId], [ExpenseId], [BillCreditId], [ContractLaborId])" in text


def test_every_batch_is_go_terminated_and_no_sproc_batch_holds_two_modules():
    text = _sql()
    # A CREATE PROCEDURE body runs to the end of its batch: each one must end at a GO.
    raw_batches = _batches(text)
    for batch in raw_batches:
        creates = re.findall(r"^\s*CREATE\s+OR\s+ALTER\s+PROCEDURE", batch, re.MULTILINE | re.IGNORECASE)
        assert len(creates) <= 1, "two CREATE PROCEDURE statements in one batch"
    # Every GO separator line exists, so the last batch is the final sproc (file ends with GO).
    assert re.search(r"^\s*GO\s*$", text, re.MULTILINE)
    assert text.rstrip().endswith("GO")


def test_no_top_level_statement_escapes_a_procedure_body():
    # A column-zero statement after CREATE PROCEDURE in the same batch is swallowed into it.
    for batch in _batches(_sql()):
        if re.search(r"CREATE OR ALTER PROCEDURE", batch):
            lines = [ln for ln in batch.splitlines() if ln.strip() and not ln.lstrip().startswith("--")]
            assert re.match(r"^CREATE OR ALTER PROCEDURE", lines[0]), lines[0]
            for line in lines[1:]:
                assert not re.match(r"^(IF|ALTER|UPDATE|INSERT|DELETE|CREATE|EXEC)\b", line), line


def test_every_sproc_sets_nocount_and_never_rolls_back():
    text = _sql()
    for name in EXPECTED_SPROCS:
        batch = _sproc_batch(text, name)
        assert "SET NOCOUNT ON" in batch, name
        assert "ROLLBACK" not in batch.upper(), name


def test_create_claim_uses_updlock_and_returns_from_scope_identity():
    batch = _sproc_batch(_sql(), "CreateReviewNotification")
    assert "WITH (UPDLOCK, HOLDLOCK)" in batch
    assert "N'contract_labor'" in batch
    assert "SCOPE_IDENTITY()" in batch
    # The result set is keyed on the new id: a duplicate claim leaves @NewId NULL -> empty set.
    assert re.search(r"WHERE\s+\[Id\]\s*=\s*@NewId", batch)


def test_create_sproc_params_default_to_null():
    batch = _sproc_batch(_sql(), "CreateReviewNotification")
    for param in ("@ReviewId  BIGINT = NULL", "@Subject   NVARCHAR(1024) = NULL",
                  "@ProjectId BIGINT = NULL", "@WorkDate  DATE = NULL"):
        assert param in batch, param


def test_stamp_only_fills_nulls():
    batch = _sproc_batch(_sql(), "StampReviewNotificationFromOutbox")
    assert "WHEN [ConversationId] IS NULL THEN" in batch
    assert "WHEN [InternetMessageId] IS NULL THEN" in batch
    assert "WHEN [DraftGraphMessageId] IS NULL THEN" in batch


def test_delete_removes_targets_before_the_notification():
    batch = _sproc_batch(_sql(), "DeleteReviewNotification")
    assert batch.index("ReviewNotificationTarget") < batch.index("DELETE FROM dbo.[ReviewNotification]")


def test_find_is_bin_exact_and_returns_two_result_sets():
    batch = _sproc_batch(_sql(), "FindReviewNotificationForReply")
    assert batch.count("COLLATE Latin1_General_BIN") >= 2
    assert batch.count("SELECT") >= 2


def test_backfill_is_a_gated_set_based_migration():
    text = BACKFILL_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*CREATE\s+(OR\s+ALTER\s+)?PROCEDURE", text, re.MULTILINE | re.IGNORECASE)
    assert "N'backfill'" in text
    assert "NOT EXISTS" in text  # idempotent re-apply
    assert "$.review_id" in text
    assert "$.conversation_id" in text
    # The sent-mail fallback uses the legacy resolver's rule (outbound status, subject equality).
    assert re.search(r"dbo\.\[?EmailMessage\]?", text) and "m.[ProcessingStatus] = N'outbound'" in text
    for batch in _batches(text):
        assert not re.search(r"^\s*ROLLBACK", batch, re.MULTILINE | re.IGNORECASE)
    assert text.rstrip().endswith(("GO", ";")) or text.rstrip().endswith("GO")


def test_delete_header_no_longer_says_no_cascade_for_the_target_fk():
    # Fix round 2 item 3: the target FK CASCADEs, so the old "no CASCADE DELETE here" line is wrong.
    text = _sql()
    assert "no CASCADE DELETE here" not in text
    assert "the target FK CASCADEs" in text

