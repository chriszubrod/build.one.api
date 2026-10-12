"""U-652 — CodingSuggestion SQL layer (static / no live DB pins)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

CODING_SUGGESTION_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
MIGRATION_SQL = REPO_ROOT / "scripts/migrations/u652_coding_suggestion.sql"
QUEUE_SQL = REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"

RENAMED_PROCEDURES = (
    "UpsertCodingSuggestionForPurchase",
    "ReadCodingSuggestionByPublicId",
    "RecordCodingSuggestion",
    "RecordCodingSuggestionFlag",
    "ClaimCodingSuggestion",
    "ReleaseCodingSuggestion",
    "RecordCodingSuggestionConfirmation",
    "MarkCodingSuggestionEnqueued",
    "MarkCodingSuggestionWritten",
    "MarkCodingSuggestionChangedInQbo",
    "MarkCodingSuggestionError",
    "ReadCodingSuggestionStateByExpenseIds",
    "ReadExternallyResolvedCodingSuggestionCandidates",
    "MarkCodingSuggestionResolvedExternally",
)

FILES_MUST_NOT_REFERENCE_EXPENSE_CODING_ITEM = (
    CODING_SUGGESTION_SQL,
    QUEUE_SQL,
)

FILTERED_UNIQUE_INDEX_MARKERS = (
    "BillId",
    "BillCreditId",
    "ExpenseId",
    "QboPurchaseQboId",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing SQL file: {path}"
    return path.read_text(encoding="utf-8")


def _procedure_names_in_file(path: Path) -> set[str]:
    text = _read(path)
    return set(
        re.findall(
            r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?(\w+)",
            text,
            re.IGNORECASE,
        )
    )


@pytest.mark.parametrize("name", RENAMED_PROCEDURES)
def test_coding_suggestion_sql_defines_every_renamed_procedure(name: str):
    assert name in _procedure_names_in_file(CODING_SUGGESTION_SQL)


def test_coding_suggestion_sql_has_target_check_constraint():
    text = _read(CODING_SUGGESTION_SQL)
    assert "CK_CodingSuggestion_Target" in text
    assert "TargetKind" in text


def test_coding_suggestion_sql_has_four_filtered_unique_indexes():
    text = _read(CODING_SUGGESTION_SQL)
    filtered = re.findall(
        r"CREATE\s+UNIQUE\s+INDEX\s+\w+.*?WHERE\s+[^;]+",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    assert len(filtered) >= 4, "expected four filtered unique indexes"
    joined = " ".join(filtered)
    for marker in FILTERED_UNIQUE_INDEX_MARKERS:
        assert marker in joined, f"filtered unique index must reference {marker}"


@pytest.mark.parametrize("path", FILES_MUST_NOT_REFERENCE_EXPENSE_CODING_ITEM)
def test_scoped_sql_files_do_not_reference_expense_coding_item(path: Path):
    text = strip_sql_comments(_read(path))
    assert "ExpenseCodingItem" not in text


def test_read_uncoded_candidates_does_not_reference_expense_coding_item():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, "ReadUncodedCompletedExpenseCandidates"))
    assert "ExpenseCodingItem" not in body
    assert "CodingSuggestion" in body


def test_migration_documents_required_apply_order():
    text = _read(MIGRATION_SQL)
    assert "dbo.coding_suggestion.sql" in text
    assert "qbo.expense_coding_queue.sql" in text
    assert "dbo.expense.sql" in text
    assert "API stopped" in text or "API" in text


def test_migration_has_xact_abort_and_safety_guards():
    text = _read(MIGRATION_SQL)
    assert "SET XACT_ABORT ON" in text
    assert re.search(r"THROW.*duplicate|duplicate.*THROW", text, re.IGNORECASE | re.DOTALL) or (
        "THROW" in text and ("RealmId" in text and "QboPurchaseQboId" in text)
    )
    assert "sp_rename" in text.lower()
    assert "ExpenseCodingItem_u652_backup" in text
    # count verification
    assert re.search(r"COUNT\s*\(", text, re.IGNORECASE)
    assert "PublicId" in text


def test_record_confirmation_declares_confirmed_mixed_codes_and_prefill():
    params = strip_sql_comments(
        sproc_body(CODING_SUGGESTION_SQL, "RecordCodingSuggestionConfirmation")
    )
    assert "@ConfirmedMixedCodes" in params
    assert "@PrefillState" in params


def test_upsert_merges_on_realm_and_purchase_qbo_id():
    body = strip_sql_comments(
        sproc_body(CODING_SUGGESTION_SQL, "UpsertCodingSuggestionForPurchase")
    )
    assert "MERGE" in body.upper()
    assert "RealmId" in body and "QboPurchaseQboId" in body
    assert "TargetKind" in body or "N'expense'" in body


def test_queue_joins_coding_suggestion_by_purchase():
    body = strip_sql_comments(sproc_body(QUEUE_SQL, "ReadExpenseCodingQueue"))
    assert "CodingSuggestion" in body
    assert "UncategorizedLineCount" in body
    assert "ExpenseCodingItem" not in body
