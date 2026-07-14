"""Pure-logic tests for expense coding queue instrumentation (U-005 Phase A).

DB-integration gaps (claim/seed/metrics sprocs) are covered in a future
integration test once migrations are applied.
"""

import base64
from decimal import Decimal

from entities.expense_coding_item.business.model import ExpenseCodingItem
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository
from shared.api.responses import item_response, list_response


def test_expense_coding_item_row_version_roundtrip():
    original_bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    encoded = base64.b64encode(original_bytes).decode("ascii")
    item = ExpenseCodingItem(
        id=1,
        public_id="00000000-0000-0000-0000-000000000001",
        row_version=encoded,
        qbo_purchase_id=10,
        qbo_purchase_line_id=20,
        qbo_line_id="1",
        qbo_purchase_qbo_id="123",
        realm_id="realm",
        vendor_id=None,
        sync_token_at_suggest=None,
        status="pending",
        suggested_project_id=None,
        suggested_sub_cost_code_id=None,
        suggested_description=None,
        suggestion_source=None,
        suggestion_reason=None,
        suggestion_confidence=None,
        suggested_at=None,
        confirmed_project_id=None,
        confirmed_sub_cost_code_id=None,
        confirmed_description=None,
        was_overridden=None,
        confirmed_by_user_id=None,
        confirmed_at=None,
        flag_reason=None,
        flagged_at=None,
        written_at=None,
        write_error=None,
        claimed_by_user_id=None,
        claimed_at=None,
        company_id=1,
        created_by_user_id=17,
        created_datetime="2026-07-14 12:00:00",
        modified_datetime="2026-07-14 12:00:00",
    )

    assert item.row_version_bytes == original_bytes
    assert base64.b64encode(item.row_version_bytes).decode("ascii") == encoded


def test_expense_coding_queue_envelope_shape():
    sample_row = {
        "qbo_purchase_id": 100,
        "qbo_purchase_public_id": "11111111-1111-1111-1111-111111111111",
        "qbo_purchase_qbo_id": "456",
        "sync_token": "0",
        "realm_id": "1234567890",
        "vendor_qbo_id": "99",
        "vendor_name": "Acme Supply",
        "credit": False,
        "total_amt": Decimal("125.50"),
        "txn_date": "2026-07-01",
        "doc_number": "EXP-100",
        "private_note": None,
        "qbo_purchase_line_id": 200,
        "qbo_line_id": "1",
        "line_num": 1,
        "line_amount": Decimal("125.50"),
        "line_description": "Materials",
        "coding_item_public_id": "22222222-2222-2222-2222-222222222222",
        "coding_status": "pending",
        "suggested_project_id": None,
        "suggested_sub_cost_code_id": None,
        "suggested_description": None,
        "suggestion_source": None,
        "suggestion_reason": None,
        "suggestion_confidence": None,
        "confirmed_project_id": None,
        "confirmed_sub_cost_code_id": None,
        "confirmed_description": None,
        "flag_reason": None,
        "claimed_by_user_id": None,
        "claimed_at": None,
    }

    envelope = list_response([sample_row])

    assert envelope == {"data": [sample_row], "count": 1}
    assert set(envelope["data"][0].keys()) == set(sample_row.keys())
    assert isinstance(envelope["data"][0]["line_amount"], Decimal)
    assert isinstance(envelope["data"][0]["total_amt"], Decimal)


def test_expense_coding_metrics_envelope_shape():
    metrics = {
        "total_target_lines": 42,
        "pending_count": 40,
        "suggested_count": 0,
        "flagged_count": 0,
        "confirmed_count": 0,
        "enqueued_count": 0,
        "written_count": 2,
        "changed_in_qbo_count": 0,
        "error_count": 0,
        "accepted_count": 2,
        "overridden_count": 0,
    }

    envelope = item_response(metrics)

    assert envelope == {"data": metrics}
    assert envelope["data"]["total_target_lines"] == 42


def test_expense_coding_queue_row_mapper_keys():
    """Verify the repo row mapper exposes the API contract keys (no DB)."""

    class _Row:
        QboPurchaseId = 1
        QboPurchasePublicId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        QboPurchaseQboId = "789"
        SyncToken = "1"
        RealmId = "realm"
        VendorQboId = "55"
        VendorName = "Vendor"
        Credit = False
        TotalAmt = Decimal("10.00")
        TxnDate = "2026-07-01"
        DocNumber = "DOC"
        PrivateNote = None
        QboPurchaseLineId = 2
        QboLineId = "1"
        LineNum = 1
        LineAmount = Decimal("10.00")
        LineDescription = "Desc"
        CodingItemPublicId = None
        CodingStatus = None
        SuggestedProjectId = None
        SuggestedSubCostCodeId = None
        SuggestedDescription = None
        SuggestionSource = None
        SuggestionReason = None
        SuggestionConfidence = None
        ConfirmedProjectId = None
        ConfirmedSubCostCodeId = None
        ConfirmedDescription = None
        FlagReason = None
        ClaimedByUserId = None
        ClaimedAt = None

    mapped = QboPurchaseLineRepository()._expense_coding_queue_row_to_dict(_Row())

    expected_keys = {
        "qbo_purchase_id",
        "qbo_purchase_public_id",
        "qbo_purchase_qbo_id",
        "sync_token",
        "realm_id",
        "vendor_qbo_id",
        "vendor_name",
        "credit",
        "total_amt",
        "txn_date",
        "doc_number",
        "private_note",
        "qbo_purchase_line_id",
        "qbo_line_id",
        "line_num",
        "line_amount",
        "line_description",
        "coding_item_public_id",
        "coding_status",
        "suggested_project_id",
        "suggested_sub_cost_code_id",
        "suggested_description",
        "suggestion_source",
        "suggestion_reason",
        "suggestion_confidence",
        "confirmed_project_id",
        "confirmed_sub_cost_code_id",
        "confirmed_description",
        "flag_reason",
        "claimed_by_user_id",
        "claimed_at",
    }
    assert set(mapped.keys()) == expected_keys
