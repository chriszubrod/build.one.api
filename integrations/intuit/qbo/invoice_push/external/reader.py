"""U-778d1 b2 -- the live QBO READ adapter for the dry run. GET / query only; there is no write method on this class.

Read contract (precise): NO business writes -- nothing is created or changed in QBO or in dbo business tables. The
shared QBO transport still does what it does for ANY read: it meters each call (`IncrementQboApiUsage`, fail-open) and
may refresh the OAuth token (a credential write) -- the normal side effects of a QBO read, not part of the dry run's
business effect. Money: live QBO JSON is decoded by the transport as float; every value is converted with
`Decimal(str(x))`, which is exact for the bounded money range the rules enforce (|x| < 1e11, <= 2 dp) -- anything
outside that bound is refused upstream (`amount_out_of_range`).

Source headers come from the existing by-id clients (line `LinkedTxn` and `BillableStatus` parsed since U-778a),
charges from a batched `SELECT * FROM ReimburseCharge WHERE Id IN (...)`, the DocNumber check is a query, and
`get_invoice` is a plain GET returning the raw JSON (for canonical comparison).
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, Sequence

from integrations.intuit.qbo.base.charge_link import reimburse_charge_id_from_linked_txn
from integrations.intuit.qbo.base.errors import QboUnexpectedError
from integrations.intuit.qbo.base.paging import _strict_query_rows, query_records_by_ids
from integrations.intuit.qbo.invoice_push.business.model import (
    ChargeMalformed, LiveCharge, LiveSourceLine, MalformedCharge,
)

logger = logging.getLogger(__name__)


# source type -> (client module, client class, by-ids query method); imported lazily (the clients pull in the transport)
_SOURCE_CLIENTS = {
    "Bill": ("integrations.intuit.qbo.bill.external.client", "QboBillClient", "query_bills_by_ids"),
    "Expense": ("integrations.intuit.qbo.purchase.external.client", "QboPurchaseClient", "query_purchases_by_ids"),
    "BillCredit": ("integrations.intuit.qbo.vendorcredit.external.client", "QboVendorCreditClient",
                   "query_vendor_credits_by_ids"),
}


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class LiveQboReader:
    def __init__(self, realm_id: str, *, http_client: Any = None, bill_client: Any = None,
                 purchase_client: Any = None, vendorcredit_client: Any = None):
        self.realm_id = realm_id
        self._owns_http = http_client is None
        if http_client is None:
            from integrations.intuit.qbo.base.client import QboHttpClient
            http_client = QboHttpClient(realm_id=realm_id, minor_version=65)
        self._http = http_client
        self._clients = {"Bill": bill_client, "Expense": purchase_client, "BillCredit": vendorcredit_client}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _client(self, source_type: str):
        c = self._clients.get(source_type)
        if c is not None:
            return c
        if source_type not in _SOURCE_CLIENTS:
            raise ValueError(f"unsupported source type {source_type!r}")
        module, cls, _ = _SOURCE_CLIENTS[source_type]
        c = self._clients[source_type] = getattr(importlib.import_module(module), cls)(
            realm_id=self.realm_id, http_client=self._http)
        return c

    def read_source_lines(self, source_type: str, parent_qbo_ids: Sequence[str]) -> Dict[tuple, LiveSourceLine]:
        ids = sorted({str(i) for i in parent_qbo_ids})
        if not ids:
            return {}
        client = self._client(source_type)
        docs = getattr(client, _SOURCE_CLIENTS[source_type][2])(ids)
        out: Dict[tuple, LiveSourceLine] = {}
        for doc in docs:
            for ln in doc.line or []:
                if ln.id is None:
                    continue
                detail = ln.item_based_expense_line_detail or ln.account_based_expense_line_detail
                status = getattr(detail, "billable_status", None) if detail is not None else None
                out[(str(doc.id), str(ln.id))] = LiveSourceLine(
                    billable_status=status, charge_id=reimburse_charge_id_from_linked_txn(ln.linked_txn))
        return out

    def read_charges(self, ids: Sequence[str]) -> Dict[str, Any]:
        """id -> LiveCharge, or MalformedCharge for a row that fails strict validation (never a default)."""
        wanted = sorted({str(i) for i in ids})
        rows = query_records_by_ids(
            self._http, entity="ReimburseCharge", ids=wanted,
            operation_name="qbo.invoice_push.reimburse_charge_by_ids", parse=lambda row: row)
        out: Dict[str, Any] = {}
        for r in rows:
            try:
                c = LiveCharge.from_qbo(r)
                out[c.id] = c
            except ChargeMalformed as e:
                rid = r.get("Id") if isinstance(r, dict) else None
                if rid is not None and str(rid) in wanted:
                    out[str(rid)] = MalformedCharge(str(rid), str(e))
        return out

    def doc_number_invoice_ids(self, doc_number: str) -> tuple:
        """Ids of the QBO invoices using `doc_number`. Strict: a Fault, a non-list/null Invoice or a row without an
        Id raises (never "absent")."""
        query = f"SELECT Id FROM Invoice WHERE DocNumber = '{_escape(doc_number)}'"
        data = self._http.get("query", params={"query": query}, operation_name="qbo.invoice_push.doc_number_check")
        rows = _strict_query_rows(data, entity="Invoice", where=query, label="invoice DocNumber query")
        ids = []
        for row in rows:
            rid = row.get("Id") if isinstance(row, dict) else None
            if rid is None or not str(rid).strip():
                raise QboUnexpectedError("QBO invoice DocNumber query returned a row without an Id", detail=query)
            ids.append(str(rid))
        return tuple(ids)

    def _get_one(self, entity: str, entity_id: str) -> Dict[str, Any]:
        """GET <entity>/<id> -> the unwrapped record dict (strict: anything else raises)."""
        key = entity.lower()
        data = self._http.get(f"{key}/{entity_id}", operation_name=f"qbo.invoice_push.{key}_get")
        rec = data.get(entity) if isinstance(data, dict) else None
        if not isinstance(rec, dict):
            raise QboUnexpectedError(f"{key} GET returned no {entity}", detail=str(entity_id))
        return rec

    def get_item(self, item_id: str) -> Dict[str, Any]:
        return self._get_one("Item", item_id)

    def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        return self._get_one("Invoice", invoice_id)
