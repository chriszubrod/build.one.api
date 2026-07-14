"""Pure-logic tests for expense coding suggestion engine (U-005 Phase B)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from entities.expense_coding_item.business.hint_extractor import extract_hints
from entities.expense_coding_item.business.suggestion_service import ExpenseCodingSuggestionService


def test_extract_hints_address_memo():
    hints = extract_hints(
        "Emison Cordova - Foam board insulation for 1577 Moran Rd project"
    )
    assert hints["address_hint"] == "1577 Moran Rd"
    assert hints["project_hint"] is None
    assert hints["scc_hint"] is None
    assert hints["clean_description"] == "Foam board insulation"


def test_extract_hints_code_memo():
    hints = extract_hints("Chris Zubrod - HE12 - Dumpster - 2.01")
    assert hints["project_hint"] == "HE12"
    assert hints["scc_hint"] == "2.01"
    assert hints["clean_description"] == "Dumpster"
    assert hints["address_hint"] is None


def test_extract_hints_raw_merchant_memo():
    hints = extract_hints("THE HOME DEPOT #0723 - 3892")
    assert hints["project_hint"] is None
    assert hints["scc_hint"] is None
    assert hints["address_hint"] is None
    assert hints["clean_description"] is None


def _make_service(**overrides):
    defaults = {
        "project_service": MagicMock(),
        "sub_cost_code_service": MagicMock(),
        "suggestion_repo": MagicMock(),
        "item_sub_cost_code_repo": MagicMock(),
        "coding_item_service": MagicMock(),
        "qbo_purchase_service": MagicMock(),
    }
    defaults.update(overrides)
    return ExpenseCodingSuggestionService(**defaults)


def test_suggest_for_item_full_project_and_scc():
    project = SimpleNamespace(id=10, name="Test Project")
    project_service = MagicMock()
    project_service.read_by_abbreviation.return_value = project

    sub_cost_code_service = MagicMock()
    sub_cost_code_service.find_for_reply.return_value = [
        {
            "sub_cost_code": {"id": 20, "number": "2.01", "name": "Dumpster"},
            "confidence": 0.92,
        }
    ]

    item_sub_cost_code_repo = MagicMock()
    item_sub_cost_code_repo.read_by_sub_cost_code_id.return_value = SimpleNamespace(id=1)

    service = _make_service(
        project_service=project_service,
        sub_cost_code_service=sub_cost_code_service,
        item_sub_cost_code_repo=item_sub_cost_code_repo,
    )

    result = service.suggest_for_item(
        SimpleNamespace(
            private_note="Chris Zubrod - HE12 - Dumpster - 2.01",
            vendor_id=99,
        )
    )

    assert result["status"] == "suggested"
    assert result["project_id"] == 10
    assert result["sub_cost_code_id"] == 20
    assert result["description"] == "Dumpster"
    assert result["source"] == "code+scc_shorthand"
    assert "matched project code HE12" in result["reason"]
    assert "cost-code shorthand 2.01" in result["reason"]
    assert result["confidence"] == Decimal("0.92")


def test_suggest_for_item_partial_vendor_history_only():
    project_service = MagicMock()
    project_service.read_by_abbreviation.return_value = None
    project_service.find_for_invoice.return_value = []

    suggestion_repo = MagicMock()
    suggestion_repo.read_vendor_dominant_sub_cost_code.return_value = {
        "sub_cost_code_id": 30,
        "number": "4.10",
        "name": "Materials",
        "top_count": 8,
        "total_count": 10,
    }

    item_sub_cost_code_repo = MagicMock()
    item_sub_cost_code_repo.read_by_sub_cost_code_id.return_value = SimpleNamespace(id=1)

    service = _make_service(
        project_service=project_service,
        suggestion_repo=suggestion_repo,
        item_sub_cost_code_repo=item_sub_cost_code_repo,
    )

    result = service.suggest_for_item(
        SimpleNamespace(private_note="THE HOME DEPOT #0723 - 3892", vendor_id=5)
    )

    assert result["status"] == "suggested"
    assert result["project_id"] is None
    assert result["sub_cost_code_id"] == 30
    assert result["source"] == "vendor_history"
    assert "project unresolved — needs cardholder" in result["reason"]
    assert result["confidence"] == Decimal("0.8")


def test_suggest_for_item_neither_resolves_flags():
    project_service = MagicMock()
    project_service.read_by_abbreviation.return_value = None
    project_service.find_for_invoice.return_value = []

    suggestion_repo = MagicMock()
    suggestion_repo.read_vendor_dominant_sub_cost_code.return_value = None

    service = _make_service(
        project_service=project_service,
        suggestion_repo=suggestion_repo,
    )

    result = service.suggest_for_item(
        SimpleNamespace(private_note="THE HOME DEPOT #0723 - 3892", vendor_id=5)
    )

    assert result["status"] == "flagged"
    assert result["project_id"] is None
    assert result["sub_cost_code_id"] is None
    assert "needs cardholder follow-up" in result["reason"]


def test_suggest_for_item_drops_unmapped_sub_cost_code():
    project_service = MagicMock()
    project_service.read_by_abbreviation.return_value = SimpleNamespace(id=10, name="HE12 Project")

    sub_cost_code_service = MagicMock()
    sub_cost_code_service.find_for_reply.return_value = [
        {
            "sub_cost_code": {"id": 20, "number": "2.01", "name": "Dumpster"},
            "confidence": 0.95,
        }
    ]

    item_sub_cost_code_repo = MagicMock()
    item_sub_cost_code_repo.read_by_sub_cost_code_id.return_value = None

    service = _make_service(
        project_service=project_service,
        sub_cost_code_service=sub_cost_code_service,
        item_sub_cost_code_repo=item_sub_cost_code_repo,
    )

    result = service.suggest_for_item(
        SimpleNamespace(private_note="Chris Zubrod - HE12 - Dumpster - 2.01", vendor_id=1)
    )

    assert result["status"] == "suggested"
    assert result["project_id"] == 10
    assert result["sub_cost_code_id"] is None
    assert "sub-cost-code unresolved — needs cardholder" in result["reason"]


def test_vendor_history_confidence_ratio():
    suggestion_repo = MagicMock()
    suggestion_repo.read_vendor_dominant_sub_cost_code.return_value = {
        "sub_cost_code_id": 30,
        "number": "4.10",
        "name": "Materials",
        "top_count": 6,
        "total_count": 9,
    }

    item_sub_cost_code_repo = MagicMock()
    item_sub_cost_code_repo.read_by_sub_cost_code_id.return_value = SimpleNamespace(id=1)

    service = _make_service(
        suggestion_repo=suggestion_repo,
        item_sub_cost_code_repo=item_sub_cost_code_repo,
    )

    result = service.suggest_for_item(
        SimpleNamespace(private_note="Cardholder - misc", vendor_id=5)
    )

    assert result["status"] == "suggested"
    assert result["confidence"] == Decimal("6") / Decimal("9")
