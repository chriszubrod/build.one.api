"""U-797c5a P0-2: VendorCredit client writes must send JSON-safe bodies (Decimal -> string), as the Bill client does."""

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx

import integrations.intuit.qbo.base.client as base_client_mod
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.vendorcredit.external.schemas import (
    QboItemBasedExpenseLineDetail,
    QboReferenceType,
    QboVendorCreditCreate,
    QboVendorCreditLine,
    QboVendorCreditUpdate,
)


class _Auth:
    def ensure_valid_token_classified(self, **kwargs):
        from integrations.intuit.qbo.base.client import AuthFailureKind

        return SimpleNamespace(access_token="tok", realm_id="realm-1"), AuthFailureKind.NONE


class _Budget:
    def record_call_or_raise(self, *a, **kw):
        return None


def _client(monkeypatch, sent, response_body):
    monkeypatch.setattr(base_client_mod, "writes_allowed", lambda: True)

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=response_body)

    http = QboHttpClient(
        realm_id="realm-1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        auth_service=_Auth(),
        api_budget=_Budget(),
    )
    return QboVendorCreditClient(realm_id="realm-1", http_client=http)


def _create_payload():
    detail = QboItemBasedExpenseLineDetail(
        item_ref=QboReferenceType(value="ITEM-5", name="SCC 5"),
        billable_status="NotBillable",
        qty=Decimal("2"),
        unit_price=Decimal("10.50"),
    )
    line = QboVendorCreditLine(
        line_num=1, description="Lumber", amount=Decimal("21.00"),
        detail_type="ItemBasedExpenseLineDetail", item_based_expense_line_detail=detail,
    )
    return QboVendorCreditCreate(
        vendor_ref=QboReferenceType(value="V-55", name="Acme"),
        txn_date="2026-11-01", doc_number="VC-100", line=[line],
    )


CREATED = {"VendorCredit": {"Id": "880", "SyncToken": "0", "Line": []}}


def test_create_sends_valid_json_with_decimal_amounts_as_strings(monkeypatch):
    sent = []
    client = _client(monkeypatch, sent, CREATED)
    client.create_vendor_credit(_create_payload())
    body = json.loads(sent[0].content)
    line = body["Line"][0]
    assert line["Amount"] == "21.00"
    assert line["ItemBasedExpenseLineDetail"]["UnitPrice"] == "10.50"
    assert line["ItemBasedExpenseLineDetail"]["Qty"] == "2"


def test_create_body_matches_the_bill_client_serialisation(monkeypatch):
    """Same mode="json" dump as QboBillClient.create_bill: the amounts are the same strings."""
    sent = []
    client = _client(monkeypatch, sent, CREATED)
    payload = _create_payload()
    client.create_vendor_credit(payload)
    vc_body = json.loads(sent[0].content)
    expected = json.loads(payload.model_dump_json(by_alias=True, exclude_none=True))
    assert vc_body == expected


def test_update_sends_valid_json_with_decimal_amounts_as_strings(monkeypatch):
    sent = []
    client = _client(monkeypatch, sent, CREATED)
    update = QboVendorCreditUpdate(
        id="880", sync_token="0", vendor_ref=QboReferenceType(value="V-55", name="Acme"),
        line=_create_payload().line,
    )
    client.update_vendor_credit(update)
    body = json.loads(sent[0].content)
    assert body["Line"][0]["Amount"] == "21.00"
    assert body["Id"] == "880"
