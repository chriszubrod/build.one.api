"""U-797c5a: BillCredit create push (VendorCredit), round 1.

Real dataclasses (BillCredit, BillCreditLineItem) and a REAL QboVendorCreditClient over an
httpx.MockTransport, so the request body that QBO would receive is what gets asserted.
Only the auth seam, the API budget, and the vendor/project/SCC lookups are stand-ins.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

import integrations.intuit.qbo.base.client as base_client_mod
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.cost_code_resolver import HoldingCodeRefused
from integrations.intuit.qbo.base.correlation import idempotency_key_context
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business import service as conn_mod
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient


class FakeAuth:
    def ensure_valid_token_classified(self, **kwargs):
        from integrations.intuit.qbo.base.client import AuthFailureKind

        return SimpleNamespace(access_token="tok", realm_id="realm-1"), AuthFailureKind.NONE


class FakeBudget:
    def record_call_or_raise(self, *a, **kw):
        return None


class FakeRepo:
    """BillCredit / BillCreditLineItem repo: set_qbo_identity + read_by_id over real dataclass rows."""

    def __init__(self, rows, drop_stamp_for=(), raise_on=()):
        self.rows = {r.id: r for r in rows}
        self.drop_stamp_for = set(drop_stamp_for)
        self.raise_on = set(raise_on)
        self.stamps = []

    def set_qbo_identity(self, *, id, qbo_id, realm_id):
        self.stamps.append((id, qbo_id, realm_id))
        if id in self.raise_on:
            raise RuntimeError("stamp write exploded")
        if id in self.drop_stamp_for:
            return
        row = self.rows[id]
        row.qbo_id = qbo_id
        row.realm_id = realm_id

    def read_by_id(self, id):
        return self.rows.get(id)


class FakeService:
    def __init__(self, repo, by_id=None, identity_lookup=None):
        self.repo = repo
        self._by_id = by_id or {}
        self._identity = identity_lookup or {}

    def read_by_id(self, id):
        return self._by_id.get(id)

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        return self._identity.get(qbo_id)


class FakeLineService(FakeService):
    def __init__(self, repo, lines):
        super().__init__(repo, by_id={line.id: line for line in lines})
        self._lines = lines

    def read_by_bill_credit_id(self, bill_credit_id):
        return list(self._lines)


class FakeSccService:
    def __init__(self, sccs):
        self._sccs = sccs

    def read_by_id(self, id):
        return self._sccs.get(id)


class FakeCompany:
    def __init__(self, ap_qbo_id="AP-1", ap_name="Accounts Payable"):
        self.ap_qbo_id = ap_qbo_id
        self.ap_name = ap_name

    def read_by_realm_id(self, realm_id):
        if self.ap_qbo_id is None:
            return None
        return SimpleNamespace(ap_account_qbo_id=self.ap_qbo_id, ap_account_name=self.ap_name)


class FakeAcctRepo:
    def read_by_realm_id_and_account_type(self, realm_id, account_type):
        return []


class FakeRecon:
    pass


def _credit(qbo_id=None, **overrides):
    values = dict(
        id=7, public_id="bc-7", row_version=None, created_datetime=None, modified_datetime=None,
        vendor_id=3, credit_date="2026-11-01T00:00:00", credit_number="VC-100",
        total_amount=Decimal("30"), memo="Note", is_draft=False,
        qbo_id=qbo_id, realm_id="realm-1" if qbo_id else None,
    )
    values.update(overrides)
    return BillCredit(**values)


def _line(id, scc, project_id, description, qty, unit_price, amount, billable, **overrides):
    values = dict(
        id=id, public_id=f"bcli-{id}", row_version=None, created_datetime=None, modified_datetime=None,
        bill_credit_id=7, sub_cost_code_id=scc, project_id=project_id, description=description,
        quantity=qty, unit_price=unit_price, amount=amount, is_billable=billable, is_billed=None,
        billable_amount=None, is_draft=False, qbo_id=None, realm_id=None,
    )
    values.update(overrides)
    return BillCreditLineItem(**values)


def _default_lines():
    return [
        _line(71, 5, 9, "Lumber", Decimal("2"), Decimal("10.50"), Decimal("21.00"), True),
        _line(72, 6, None, "Fee", None, None, Decimal("9.00"), False),
    ]


def _scc(id, qbo_id, holding=False):
    return SimpleNamespace(id=id, qbo_id=qbo_id, name=f"SCC {id}", realm_id="realm-1", is_holding_code=holding)


def _vendor():
    return SimpleNamespace(id=3, qbo_id="V-55", name="Acme", realm_id="realm-1", public_id="v-3")


def _project():
    return SimpleNamespace(id=9, qbo_id="CUST-9", name="Proj", realm_id="realm-1", public_id="p-9")


CREATED_BODY = {
    "VendorCredit": {
        "Id": "880",
        "SyncToken": "0",
        "VendorRef": {"value": "V-55", "name": "Acme"},
        "TxnDate": "2026-11-01",
        "DocNumber": "VC-100",
        "Line": [
            {"Id": "1001", "LineNum": 1, "Amount": "21.00", "Description": "Lumber",
             "DetailType": "ItemBasedExpenseLineDetail",
             "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "ITEM-5", "name": "SCC 5"}}},
            {"Id": "1002", "LineNum": 2, "Amount": "9.00", "Description": "Fee",
             "DetailType": "ItemBasedExpenseLineDetail",
             "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "ITEM-6", "name": "SCC 6"}}},
        ],
    }
}


class Harness:
    """Wires the connector to a REAL QboVendorCreditClient whose HTTP goes to a MockTransport."""

    def __init__(self, monkeypatch, *, credit=None, lines=None, sccs=None, company=None,
                 drop_line_stamp=(), raise_line_stamp=(), drop_header_stamp=False,
                 raise_header_stamp=False, identity_ok=True, response=None, status=200,
                 vendor=None, project=None, project_verifies=True, with_attachments=None):
        self.monkeypatch = monkeypatch
        self.requests = []
        self.recorded = []
        self.upserted = []
        self.response = response if response is not None else (status, CREATED_BODY)
        credit = credit or _credit()
        lines = lines if lines is not None else _default_lines()

        self.credit_repo = FakeRepo(
            [credit], drop_stamp_for=({7} if drop_header_stamp else ()),
            raise_on=({7} if raise_header_stamp else ()),
        )
        self.line_repo = FakeRepo(lines, drop_stamp_for=drop_line_stamp, raise_on=raise_line_stamp)
        identity = {credit.qbo_id: credit} if (credit.qbo_id and identity_ok) else {}
        self.bill_credit_service = FakeService(self.credit_repo, by_id={7: credit}, identity_lookup=identity)
        self.line_service = FakeLineService(self.line_repo, lines)
        vendor = vendor if vendor is not None else _vendor()
        self.vendor_service = FakeService(None, by_id={3: vendor} if vendor else {}, identity_lookup={"V-55": vendor})
        project = project if project is not None else _project()
        self.project_service = FakeService(None, by_id={9: project}, identity_lookup={"CUST-9": project} if project_verifies else {})
        sccs = sccs if sccs is not None else {5: _scc(5, "ITEM-5"), 6: _scc(6, "ITEM-6")}

        harness = self

        def handler(request):
            harness.requests.append(request)
            status_code, body = harness.response
            return httpx.Response(status_code, json=body)

        def factory(realm_id):
            return QboVendorCreditClient(
                realm_id=realm_id,
                http_client=QboHttpClient(
                    realm_id=realm_id,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                    auth_service=FakeAuth(),
                    api_budget=FakeBudget(),
                ),
            )

        def _record(repo, **kwargs):
            harness.recorded.append(kwargs)

        def _upsert(self_svc, qbo_vc, realm_id):
            harness.upserted.append((qbo_vc.id, realm_id))
            return SimpleNamespace(id=500, qbo_id=qbo_vc.id), []

        monkeypatch.setattr(base_client_mod, "writes_allowed", lambda: True)
        monkeypatch.setattr(conn_mod, "record_mapping_issue", _record)
        monkeypatch.setattr(QboVendorCreditService, "upsert_from_external", _upsert)

        self.attachment_calls = []
        attach_kwargs = {}
        if with_attachments is not None:
            attach_kwargs = with_attachments

        self.connector = VendorCreditBillCreditConnector(
            bill_credit_service=self.bill_credit_service,
            bill_credit_line_item_service=self.line_service,
            vendor_service=self.vendor_service,
            reconciliation_repo=FakeRecon(),
            line_item_connector=object(),
            sub_cost_code_service=FakeSccService(sccs),
            project_service=self.project_service,
            company_service=company if company is not None else FakeCompany(),
            qbo_account_repo=FakeAcctRepo(),
            vendor_credit_client_factory=factory,
            **attach_kwargs,
        )

    def push(self):
        return self.connector.sync_to_qbo_vendor_credit(bill_credit_id=7, realm_id="realm-1")

    def body(self):
        assert len(self.requests) == 1, f"expected one QBO write, saw {len(self.requests)}"
        return json.loads(self.requests[0].content)

    def drift_types(self):
        return [r.get("drift_type") for r in self.recorded]


# ------------------------------------------------------------------ payload (P0-1, P0-2)

def test_request_body_is_valid_json_with_string_amounts_and_ordered_lines(monkeypatch):
    h = Harness(monkeypatch)
    h.push()
    body = h.body()
    assert body["VendorRef"] == {"value": "V-55", "name": "Acme"}
    assert body["TxnDate"] == "2026-11-01"
    assert body["DocNumber"] == "VC-100"
    assert body["PrivateNote"] == "Note"
    lines = body["Line"]
    assert [l["LineNum"] for l in lines] == [1, 2]
    assert [l["Description"] for l in lines] == ["Lumber", "Fee"]
    assert lines[0]["Amount"] == "21.00"
    assert isinstance(lines[0]["Amount"], str)
    detail = lines[0]["ItemBasedExpenseLineDetail"]
    assert detail["ItemRef"] == {"value": "ITEM-5", "name": "SCC 5"}
    assert detail["CustomerRef"] == {"value": "CUST-9", "name": "Proj"}
    assert detail["BillableStatus"] == "Billable"
    assert detail["Qty"] == "2"
    assert detail["UnitPrice"] == "10.50"
    assert lines[1]["ItemBasedExpenseLineDetail"]["BillableStatus"] == "NotBillable"
    assert "CustomerRef" not in lines[1]["ItemBasedExpenseLineDetail"]


def test_ap_account_ref_is_sent_from_the_company_cache(monkeypatch):
    h = Harness(monkeypatch)
    h.push()
    assert h.body()["APAccountRef"] == {"value": "AP-1", "name": "Accounts Payable"}


def test_missing_ap_account_is_not_fatal_and_is_omitted(monkeypatch):
    h = Harness(monkeypatch, company=FakeCompany(ap_qbo_id=None))
    h.push()
    assert "APAccountRef" not in h.body()


# ------------------------------------------------------------------ refusals before any write

def test_holding_code_refused_with_no_request(monkeypatch):
    h = Harness(monkeypatch, sccs={5: _scc(5, "ITEM-5", holding=True), 6: _scc(6, "ITEM-6")})
    with pytest.raises(HoldingCodeRefused):
        h.push()
    assert h.requests == []
    assert h.upserted == []


def test_unmapped_line_fails_whole_push_with_no_request(monkeypatch):
    h = Harness(monkeypatch, sccs={5: _scc(5, "ITEM-5"), 6: _scc(6, None)})
    with pytest.raises(ValueError, match="no QBO Item mapping"):
        h.push()
    assert h.requests == []


def test_vendor_without_verified_identity_fails_before_any_write(monkeypatch):
    h = Harness(monkeypatch)
    h.vendor_service._by_id = {}  # the credit's vendor is not found at all
    with pytest.raises(ValueError, match="No QBO vendor mapping"):
        h.push()
    assert h.requests == []


def test_vendor_whose_identity_does_not_verify_fails_before_any_write(monkeypatch):
    vendor = _vendor()
    h = Harness(monkeypatch, vendor=vendor)
    h.vendor_service._identity = {}  # fresh dbo-only read no longer resolves back to this vendor
    with pytest.raises(ValueError, match="No QBO vendor mapping"):
        h.push()
    assert h.requests == []


def test_billable_line_whose_project_identity_fails_to_verify_is_refused(monkeypatch):
    h = Harness(monkeypatch, project_verifies=False)
    with pytest.raises(ValueError, match="no QBO CustomerRef mapping"):
        h.push()
    assert h.requests == []


def test_missing_credit_number_refused_before_any_write(monkeypatch):
    h = Harness(monkeypatch, credit=_credit(credit_number=None))
    with pytest.raises(ValueError, match="credit_number"):
        h.push()
    assert h.requests == []


def test_missing_credit_date_refused_before_any_write(monkeypatch):
    h = Harness(monkeypatch, credit=_credit(credit_date=None))
    with pytest.raises(ValueError, match="credit_date"):
        h.push()
    assert h.requests == []


def test_negative_line_amount_refused_before_any_write(monkeypatch):
    lines = [
        _line(71, 5, 9, "Lumber", Decimal("2"), Decimal("10.50"), Decimal("-21.00"), True),
        _line(72, 6, None, "Fee", None, None, Decimal("9.00"), False),
    ]
    h = Harness(monkeypatch, lines=lines)
    with pytest.raises(ValueError, match="negative"):
        h.push()
    assert h.requests == []


# ------------------------------------------------------------------ duplicate

def test_qbo_duplicate_docnumber_records_issue_and_reraises(monkeypatch):
    fault = {"Fault": {"Error": [{"code": "6140", "Message": "Duplicate Document Number", "Detail": "dup"}],
                       "type": "ValidationFault"}}
    h = Harness(monkeypatch, response=(400, fault))
    from integrations.intuit.qbo.base.errors import QboDuplicateError

    with pytest.raises(QboDuplicateError):
        h.push()
    assert "duplicate_qbo_vc_docnumber" in h.drift_types()
    assert h.credit_repo.stamps == []
    assert h.upserted == []


# ------------------------------------------------------------------ stamping and staging

def test_success_stages_then_stamps_header_and_each_line_by_line_num(monkeypatch):
    h = Harness(monkeypatch)
    result = h.push()
    assert result is not None
    assert h.upserted == [("880", "realm-1")]
    assert (7, "880", "realm-1") in h.credit_repo.stamps
    assert (71, "1001", "realm-1") in h.line_repo.stamps
    assert (72, "1002", "realm-1") in h.line_repo.stamps
    assert h.recorded == []


def test_line_stamp_that_does_not_land_is_recorded_not_raised(monkeypatch):
    h = Harness(monkeypatch, drop_line_stamp=(72,))
    h.push()
    assert "bcli_line_push_stamp_failed" in h.drift_types()


def test_line_stamp_whose_write_raises_is_recorded_not_swallowed(monkeypatch):
    h = Harness(monkeypatch, raise_line_stamp=(72,))
    h.push()
    assert "bcli_line_push_stamp_failed" in h.drift_types()


def test_header_stamp_that_does_not_land_is_recorded_and_not_vouched(monkeypatch):
    h = Harness(monkeypatch, drop_header_stamp=True)
    pushed = h.push()
    assert "bill_credit_push_stamp_failed" in h.drift_types()
    assert pushed.qbo_id is None

def test_header_stamp_whose_write_raises_is_re_raised_for_the_retry(monkeypatch):
    # Re-raise, as Bill does: the retry re-POSTs with the same RequestId, QBO dedups, the stamp is retried.
    h = Harness(monkeypatch, raise_header_stamp=True)
    with pytest.raises(RuntimeError, match="stamp write exploded"):
        h.push()


def test_header_stamp_retry_reuses_the_request_id_so_qbo_does_not_create_twice(monkeypatch):
    h = Harness(monkeypatch, raise_header_stamp=True)
    with idempotency_key_context("req-1"):
        with pytest.raises(RuntimeError):
            h.push()
    h.credit_repo.raise_on = set()
    with idempotency_key_context("req-1"):
        pushed = h.push()
    assert pushed.qbo_id == "880"
    assert len(h.requests) == 2
    assert {r.url.params.get("requestid") for r in h.requests} == {"req-1"}
    assert (7, "880", "realm-1") in h.credit_repo.stamps

# ------------------------------------------------------------------ already in QBO

def test_existing_qbo_id_short_circuits_with_no_write(monkeypatch):
    h = Harness(monkeypatch, credit=_credit(qbo_id="880"))
    pushed = h.push()
    assert pushed.qbo_id == "880"
    assert h.requests == []
    assert h.credit_repo.stamps == []
    assert h.upserted == []

def test_existing_qbo_id_that_no_longer_resolves_is_refused(monkeypatch):
    h = Harness(monkeypatch, credit=_credit(qbo_id="880"), identity_ok=False)
    with pytest.raises(ValueError, match="no longer resolves"):
        h.push()
    assert "bill_credit_identity_conflict" in h.drift_types()
    assert h.requests == []


# ------------------------------------------------------------------ write gate (real layer)

def test_write_gate_off_refuses_create_before_any_network(monkeypatch):
    monkeypatch.delenv("ALLOW_QBO_WRITES", raising=False)
    h = Harness(monkeypatch)
    monkeypatch.setattr(base_client_mod, "writes_allowed", lambda: False)
    with pytest.raises(QboWriteRefusedError):
        h.push()
    assert h.requests == []


# ------------------------------------------------------------------ attachments (P2-5a)

class _Attachment(SimpleNamespace):
    pass


class FakeLinkService:
    def __init__(self, links):
        self._links = links

    def read_by_bill_credit_line_item_id(self, bill_credit_line_item_public_id):
        return self._links.get(bill_credit_line_item_public_id)


class FakeAttachmentService:
    def __init__(self, by_id):
        self._by_id = by_id

    def read_by_id(self, id):
        return self._by_id.get(id)


class FakeAttachableConnector:
    def __init__(self, fail_for=(), budget_for=()):
        self.calls = []
        self.fail_for = set(fail_for)
        self.budget_for = set(budget_for)

    def sync_attachment_to_qbo(self, attachment, realm_id, entity_type, entity_id):
        self.calls.append((attachment.id, realm_id, entity_type, entity_id))
        if attachment.id in self.budget_for:
            raise QboBudgetExceededError("budget")
        if attachment.id in self.fail_for:
            raise RuntimeError("upload broke")


def _attachment_setup(**kwargs):
    attachable = kwargs.pop("attachable", FakeAttachableConnector())
    links = {"bcli-71": SimpleNamespace(attachment_id=501), "bcli-72": SimpleNamespace(attachment_id=501)}
    attachments = {501: _Attachment(id=501, blob_url="https://blob/x", public_id="a-501")}
    return dict(
        bill_credit_line_item_attachment_service=FakeLinkService(links),
        attachment_service=FakeAttachmentService(attachments),
        attachable_attachment_connector=attachable,
    ), attachable


def test_attachments_are_uploaded_once_per_attachment_against_the_vendor_credit(monkeypatch):
    kwargs, attachable = _attachment_setup()
    h = Harness(monkeypatch, with_attachments=kwargs)
    synced, errors = h.connector.sync_attachments_to_qbo_vendor_credit(
        bill_credit_id=7, qbo_vendor_credit_id="880", realm_id="realm-1",
    )
    assert synced == 1 and errors == []
    assert attachable.calls == [(501, "realm-1", "VendorCredit", "880")]


def test_attachment_budget_exhaustion_propagates_for_the_worker_to_park(monkeypatch):
    attachable = FakeAttachableConnector(budget_for=(501,))
    kwargs, _ = _attachment_setup(attachable=attachable)
    h = Harness(monkeypatch, with_attachments=kwargs)
    with pytest.raises(QboBudgetExceededError):
        h.connector.sync_attachments_to_qbo_vendor_credit(
            bill_credit_id=7, qbo_vendor_credit_id="880", realm_id="realm-1",
        )


def test_other_attachment_failures_are_collected_not_raised(monkeypatch):
    attachable = FakeAttachableConnector(fail_for=(501,))
    kwargs, _ = _attachment_setup(attachable=attachable)
    h = Harness(monkeypatch, with_attachments=kwargs)
    synced, errors = h.connector.sync_attachments_to_qbo_vendor_credit(
        bill_credit_id=7, qbo_vendor_credit_id="880", realm_id="realm-1",
    )
    # Bill's dedupe: an attachment is added to the synced set only on success, so a shared
    # attachment that fails on line 1 is attempted (and reported) again on line 2.
    assert synced == 0
    assert len(errors) == 2 and all("501" in e for e in errors)


def test_negative_computed_amount_refused_before_any_write(monkeypatch):
    """F3: the guard reads the COMPUTED amount (qty x unit price), not only line_item.amount."""
    lines = [
        _line(71, 5, 9, "Lumber", Decimal("-2"), Decimal("10.00"), None, True),
        _line(72, 6, None, "Fee", None, None, Decimal("9.00"), False),
    ]
    h = Harness(monkeypatch, lines=lines)
    with pytest.raises(ValueError, match="negative"):
        h.push()
    assert h.requests == []
