"""U-654b: BillTextCodingSuggestionRepository complete/claim edge cases (pure)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillTextCodingSuggestionRepository,
)


def test_complete_stale_extraction_returns_none_not_raise():
    repo = BillTextCodingSuggestionRepository()
    with patch(
        "entities.coding_suggestion.persistence.bill_text_suggestion_repo.get_connection"
    ) as gc:
        conn = MagicMock()
        gc.return_value.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = None
        row = repo.complete(
            bill_id=1,
            attachment_id=2,
            extracted_datetime=datetime(2026, 5, 1, 10, 0, 0, 123000),
            status="done",
            model_version="u654b-text-v1",
        )
    assert row is None


def test_empty_claim_result_set_returns_none():
    repo = BillTextCodingSuggestionRepository()
    with patch(
        "entities.coding_suggestion.persistence.bill_text_suggestion_repo.get_connection"
    ) as gc:
        conn = MagicMock()
        gc.return_value.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = SimpleNamespace(BillId=None)
        assert repo.claim_next() is None
