"""U-778d1 b2 -- read-only repository over the invoice-push fact sprocs (dbo.invoice_push.sql). No writes."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Iterable, Optional

from integrations.intuit.qbo.invoice_push.business.model import SourceLineFacts, StagedInvoiceLink, dec
from integrations.intuit.qbo.invoice_push.business.ports import ProjectFacts
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _id_tuple(csv: Optional[str]) -> tuple:
    return tuple(int(p) for p in csv.split(",") if p.strip()) if csv else ()


def _str_tuple(csv: Optional[str], dash_is_none: bool = False) -> tuple:
    if not csv:
        return ()
    parts = [p.strip() for p in csv.split(",")]
    return tuple((None if (dash_is_none and p == "-") else p) for p in parts if p)


def _facts(source_type: str, row: Any) -> SourceLineFacts:
    g = lambda name: getattr(row, name, None)  # noqa: E731 -- the credit sproc has no Amount/Markup/Price
    return SourceLineFacts(
        source_type=source_type,
        source_line_id=int(g("LineId")),
        project_id=g("ProjectId"),
        parent_is_draft=bool(g("ParentIsDraft")),
        parent_deleted=False,  # a hard-deleted parent takes its lines with it: the line then has no row at all
        is_billable=bool(g("IsBillable")),
        is_billed=bool(g("IsBilled")),
        referenced_by_ili_ids=_id_tuple(g("InvoiceLineItemIds")),
        sub_cost_code_is_holding=bool(g("IsHoldingCode")),
        amount=dec(g("Amount")),
        markup=dec(g("Markup")),
        price=dec(g("Price")),
        billable_amount=dec(g("BillableAmount")),
        parent_qbo_id=g("ParentQboId"),
        line_qbo_id=g("LineQboId"),
        staged_charge_ids=_str_tuple(g("StagedChargeIds")),
        ili_invoice_qbo_ids=_str_tuple(g("InvoiceLineItemInvoiceQboIds"), dash_is_none=True),
    )


def _fetch(invoke: Callable[[Any], Any], *, one: bool, label: str, passthrough: tuple = ()):
    """Run one sproc on a fresh cursor and return `fetchone()` / `fetchall()`. `invoke(cursor)` holds the literal
    `call_procedure(cursor=..., name="<Sproc>", params=...)` so tests/test_repo_sproc_param_contract.py still sees
    every call site. The cursor is closed explicitly (a pyodbc cursor as a context manager would commit)."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                invoke(cursor)
                return cursor.fetchone() if one else cursor.fetchall()
            finally:
                cursor.close()
    except passthrough:
        raise  # read_source_facts has always passed ValueError through unmapped
    except Exception as error:
        logger.error("read invoice-push %s failed: %s", label, error)
        raise map_database_error(error)


class InvoicePushRepository:
    def read_project_facts(self, project_id: int, doc_number: str) -> Optional[ProjectFacts]:
        row = _fetch(lambda cursor: call_procedure(
            cursor=cursor, name="ReadInvoicePushProjectFacts",
            params={"ProjectId": int(project_id), "DocNumber": doc_number}), one=True, label="project facts")
        if row is None:
            return None
        return ProjectFacts(project_id=row.ProjectId, qbo_id=row.QboId, realm_id=row.RealmId,
                            doc_number_used_locally=bool(row.DocNumberUsedLocally),
                            doc_number_local_invoice_qbo_ids=_str_tuple(
                                getattr(row, "DocNumberLocalInvoiceQboIds", None), dash_is_none=True))

    def read_staged_invoice_links(self, charge_ids: Iterable[str], realm_id: str) -> Dict[str, tuple]:
        """Staged invoice lines (realm-scoped) linking each LIVE charge id -> tuple of StagedInvoiceLink."""
        id_list = sorted({str(i) for i in charge_ids})
        if not id_list:
            return {}
        rows = _fetch(lambda cursor: call_procedure(
            cursor=cursor, name="ReadInvoicePushStagedInvoiceLinksByChargeIds",
            params={"ChargeIdsJson": json.dumps(id_list), "RealmId": realm_id}), one=False,
            label="staged invoice links")
        out: Dict[str, list] = {}
        for row in rows:
            out.setdefault(str(row.ChargeId), []).append(
                StagedInvoiceLink(str(row.ChargeId), str(row.QboInvoiceQboId),
                                  None if row.QboInvoiceLineId is None else str(row.QboInvoiceLineId)))
        return {k: tuple(v) for k, v in out.items()}

    def read_source_facts(self, source_type: str, ids: Iterable[int], realm_id: str) -> Dict[tuple, SourceLineFacts]:
        id_list = sorted({int(i) for i in ids})
        if not id_list:
            return {}
        params = {"IdsJson": json.dumps(id_list), "RealmId": realm_id}
        # one literal call_procedure per sproc (the param-contract guard reads the literal `name=`)
        invokers = {
            "Bill": lambda cursor: call_procedure(cursor=cursor, name="ReadInvoicePushBillLineFacts", params=params),
            "Expense": lambda cursor: call_procedure(cursor=cursor, name="ReadInvoicePushExpenseLineFacts",
                                                     params=params),
            "BillCredit": lambda cursor: call_procedure(cursor=cursor, name="ReadInvoicePushBillCreditLineFacts",
                                                        params=params),
        }
        invoke = invokers.get(source_type)
        if invoke is None:
            raise ValueError(f"unsupported source type {source_type!r}")
        rows = _fetch(invoke, one=False, label=f"{source_type} facts", passthrough=(ValueError,))
        out = {}
        for row in rows:
            f = _facts(source_type, row)
            out[(source_type, f.source_line_id)] = f
        return out
