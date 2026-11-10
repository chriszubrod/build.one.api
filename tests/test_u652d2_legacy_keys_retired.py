"""U-652d2 — legacy line keys retired.

Pins ABSENCE of the legacy response/payload aliases ``qbo_purchase_line_id`` and
``qbo_line_id`` on the producers that exposed them only for old clients, and of the
``QboLineId`` column in ``ReadExternallyResolvedCodingSuggestionCandidates``.

Internal uses of ``qbo_line_id`` (written_lines entries, the expense candidate
``qbo_line_id`` read by ``_uncoded_backfill_status_source_ref``) are live and are
asserted PRESENT here as a guard against over-removal.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
from entities.expense.persistence.repo import ExpenseRepository
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
CODING_SQL = REPO_ROOT / "entities" / "coding_suggestion" / "sql" / "dbo.coding_suggestion.sql"


def _connection_mock(rows: list) -> MagicMock:
    """A patched ``get_connection()`` whose cursor returns ``rows`` from fetchall()."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    gc = MagicMock()
    gc.return_value.__enter__.return_value = conn
    gc.return_value.__exit__.return_value = False
    return gc


def _sproc_body(sql_text: str, name: str) -> str:
    start = sql_text.index(f"CREATE OR ALTER PROCEDURE {name}")
    end = sql_text.index("\nGO", start)
    return sql_text[start:end]


# --- coding suggestion response payload (flag / confirm endpoints) ---


def test_to_api_dict_emits_no_legacy_line_keys_and_no_db_hit():
    item = MagicMock()
    item.to_dict.return_value = {"public_id": "pub-1", "status": "pending"}
    item.realm_id = "realm-1"
    item.qbo_purchase_qbo_id = "PUR-1"
    # The legacy keys were derived from a live staging query. Once retired the
    # payload is the plain to_dict() and must not touch the database at all.
    with patch("shared.database.get_connection", side_effect=AssertionError("no DB")):
        payload = CodingSuggestionService().to_api_dict(item)
    assert payload == {"public_id": "pub-1", "status": "pending"}
    assert "qbo_purchase_line_id" not in payload
    assert "qbo_line_id" not in payload


def test_legacy_single_line_ids_helper_removed():
    assert not hasattr(CodingSuggestionService, "legacy_single_line_ids")


# --- coding suggestion external-candidates reader ---


def test_read_externally_resolved_candidates_drops_qbo_line_id():
    rows = [
        SimpleNamespace(
            PublicId="11111111-1111-1111-1111-111111111111",
            Id=7,
            Status="pending",
            QboPurchaseQboId="PUR-9",
            QboLineId="3",
        )
    ]
    repo = CodingSuggestionRepository.__new__(CodingSuggestionRepository)
    with patch(
        "entities.coding_suggestion.persistence.repo.get_connection",
        _connection_mock(rows),
    ), patch("entities.coding_suggestion.persistence.repo.call_procedure"):
        result = repo.read_externally_resolved_candidates()
    assert len(result) == 1
    assert result[0]["qbo_purchase_qbo_id"] == "PUR-9"
    assert "qbo_line_id" not in result[0]


def test_externally_resolved_sproc_has_no_qbo_line_id_column():
    body = _sproc_body(CODING_SQL.read_text(), "ReadExternallyResolvedCodingSuggestionCandidates")
    assert "QboLineId" not in body
    # The reader's contract (PublicId-keyed apply path) is unchanged.
    assert re.search(r"\[PublicId\]", body)


def test_upsert_from_queue_repo_has_no_dead_legacy_kwargs():
    params = inspect.signature(CodingSuggestionRepository.upsert_from_queue).parameters
    assert "qbo_purchase_line_id" not in params
    assert "qbo_line_id" not in params


# --- expense uncoded candidates (dry-run admin response) ---


def test_expense_uncoded_candidates_drop_qbo_purchase_line_id_keep_live_qbo_line_id():
    rows = [
        SimpleNamespace(
            Id=1,
            PublicId="22222222-2222-2222-2222-222222222222",
            Status="completed",
            ExpenseDate="2026-01-01",
            TotalAmount=10.0,
            ReferenceNumber="REF-1",
            RealmId="realm-1",
            PurchaseQboId="PUR-1",
            QboLineId="5",
            QboPurchaseLineId=301,
            CodingItemPublicId=None,
        )
    ]
    repo = ExpenseRepository.__new__(ExpenseRepository)
    with patch(
        "entities.expense.persistence.repo.get_connection",
        _connection_mock(rows),
    ), patch("entities.expense.persistence.repo.call_procedure"):
        result = repo.read_uncoded_completed_candidates()
    assert "qbo_purchase_line_id" not in result[0]
    # qbo_line_id is LIVE: _uncoded_backfill_status_source_ref reads it on the apply path.
    assert result[0]["qbo_line_id"] == "5"


# --- expense coding queue rows (purchase repo) ---


def test_expense_coding_queue_row_drops_legacy_line_keys():
    row = SimpleNamespace(
        CodingItemPublicId=None,
        SuggestionConfidence=None,
        LineAmount=None,
        QboPurchaseLineId=10,
        QboLineId="4",
    )
    repo = QboPurchaseLineRepository.__new__(QboPurchaseLineRepository)
    result = repo._expense_coding_queue_row_to_dict(row)
    assert "qbo_purchase_line_id" not in result
    assert "qbo_line_id" not in result
