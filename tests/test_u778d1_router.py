"""U-778d1 b2 -- invoice-push dry-run/compare router (dependency overrides; no DB, no QBO)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from integrations.intuit.qbo.invoice_push.api import router as push_router
from integrations.intuit.qbo.invoice_push.api.router import get_push_service
from shared.access import EntityNotAccessibleError
from shared.rbac_constants import Modules

BODY = {
    "project_id": 7, "doc_number": "INV-1", "txn_date": "2026-11-06", "due_date": "2026-11-06",
    "source_lines": [{"source_type": "Bill", "source_line_id": 1}],
    "fee_lines": [{"sub_cost_code_id": 3, "description": "Fee", "qty": "1", "unit_price": "100.00"}],
}
COMPARE_BODY = {"proposal": BODY, "qbo_invoice_id": "9001"}
DRY = "/api/v1/qbo/invoice-push/dry-run"
CMP = "/api/v1/qbo/invoice-push/compare"


class FakeService:
    def __init__(self):
        self.calls = []

    def dry_run(self, proposal):
        self.calls.append(("dry_run", proposal))
        from integrations.intuit.qbo.invoice_push.business.service import DryRunResult
        return DryRunResult(ok=False, header_reasons=("no_lines: x",), lines=(), payload=None, expected=None)

    def compare(self, proposal, qbo_invoice_id):
        self.calls.append(("compare", proposal, qbo_invoice_id))
        return {"dry_run": {"ok": False}, "diffs": None}


@pytest.fixture
def env(monkeypatch):
    svc = FakeService()
    state = {"perms": "admin", "accessible": True, "denied_project": None}

    def fake_perms(user):
        p = state["perms"]
        if p == "admin":
            from shared.rbac import SYSTEM_ADMIN_GRANT
            return SYSTEM_ADMIN_GRANT
        return p

    monkeypatch.setattr("shared.rbac._get_user_permissions", fake_perms)

    def fake_access(project_id):
        state["denied_project"] = project_id
        if not state["accessible"]:
            raise EntityNotAccessibleError("Project", project_id)

    monkeypatch.setattr(push_router, "assert_can_access_project", fake_access)
    app.dependency_overrides[get_current_user_api] = lambda: {"sub": "u1", "user_id": 5}
    app.dependency_overrides[get_push_service] = lambda: svc
    monkeypatch.setenv("QBO_INVOICE_PUSH", "dry_run")
    yield SimpleNamespace(client=TestClient(app), svc=svc, state=state, monkeypatch=monkeypatch)
    app.dependency_overrides.pop(get_current_user_api, None)
    app.dependency_overrides.pop(get_push_service, None)


def perms(**kw):
    return {Modules.INVOICES: SimpleNamespace(can_read=True, can_create=False, can_update=False,
                                              can_delete=False, can_complete=False, **kw)}


@pytest.mark.parametrize("path,body", [(DRY, BODY), (CMP, COMPARE_BODY)])
def test_flag_off_is_404_and_service_untouched(env, path, body):
    env.monkeypatch.setenv("QBO_INVOICE_PUSH", "off")
    assert env.client.post(path, json=body).status_code == 404
    env.monkeypatch.delenv("QBO_INVOICE_PUSH")
    assert env.client.post(path, json=body).status_code == 404  # default is off
    env.monkeypatch.setenv("QBO_INVOICE_PUSH", "bogus")
    assert env.client.post(path, json=body).status_code == 404  # unknown value fails closed
    assert env.svc.calls == []


@pytest.mark.parametrize("mode", ["dry_run", "on"])
def test_dry_run_200_envelope(env, mode):
    env.monkeypatch.setenv("QBO_INVOICE_PUSH", mode)
    r = env.client.post(DRY, json=BODY)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False and data["header_reasons"] == ["no_lines: x"] and data["payload"] is None
    kind, proposal = env.svc.calls[0]
    assert kind == "dry_run" and proposal.project_id == 7
    assert proposal.source_lines == (("Bill", 1),)
    assert str(proposal.fee_lines[0].unit_price) == "100.00"
    assert env.state["denied_project"] == 7


def test_compare_200_envelope(env):
    r = env.client.post(CMP, json=COMPARE_BODY)
    assert r.status_code == 200 and r.json() == {"data": {"dry_run": {"ok": False}, "diffs": None}}
    assert env.svc.calls[0][0] == "compare" and env.svc.calls[0][2] == "9001"


@pytest.mark.parametrize("path,body", [(DRY, BODY), (CMP, COMPARE_BODY)])
def test_caller_without_can_complete_is_refused(env, path, body):
    env.state["perms"] = perms()
    assert env.client.post(path, json=body).status_code == 403
    assert env.svc.calls == []


@pytest.mark.parametrize("path,body", [(DRY, BODY), (CMP, COMPARE_BODY)])
def test_can_complete_user_is_allowed(env, path, body):
    p = perms()
    p[Modules.INVOICES].can_complete = True
    env.state["perms"] = p
    assert env.client.post(path, json=body).status_code == 200


@pytest.mark.parametrize("path,body", [(DRY, BODY), (CMP, COMPARE_BODY)])
def test_project_access_enforced_as_404_before_service(env, path, body):
    env.state["accessible"] = False
    assert env.client.post(path, json=body).status_code == 404
    assert env.svc.calls == []


def test_unauthenticated_is_rejected():
    app.dependency_overrides.pop(get_current_user_api, None)
    r = TestClient(app).post(DRY, json=BODY)
    assert r.status_code in (401, 403)


@pytest.mark.parametrize("mutate", [
    lambda b: b.pop("project_id"),
    lambda b: b.update(source_lines="x"),
    lambda b: b["source_lines"][0].update(source_line_id="abc"),
    lambda b: b["fee_lines"][0].update(qty="nope"),
])
def test_bad_body_is_422(env, mutate):
    import copy
    b = copy.deepcopy(BODY)
    mutate(b)
    assert env.client.post(DRY, json=b).status_code == 422
    assert env.svc.calls == []


def test_routes_are_registered_on_the_app():
    paths = {r.path for r in app.routes}
    assert DRY in paths and CMP in paths


# ======================= fix round 1 (U-778d1 f1) =======================
def test_f3_compare_of_an_invoice_outside_the_project_is_404(env):
    from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound

    def boom(proposal, qid):
        raise InvoiceNotFound(qid)
    env.svc.compare = boom
    r = env.client.post(CMP, json=COMPARE_BODY)
    assert r.status_code == 404 and "9001" not in r.text


@pytest.mark.parametrize("field,val", [("txn_date", "not-a-date"), ("txn_date", "2026-99-99"),
                                       ("due_date", "2026-02-30"), ("due_date", None)])
def test_f13_f8_dates_validated_and_due_date_required(env, field, val):
    body = {**BODY, "due_date": "2026-11-06"}
    body[field] = val
    assert env.client.post(DRY, json=body).status_code == 422
    assert env.svc.calls == []
    body.pop("due_date")
    assert env.client.post(DRY, json=body).status_code == 422


def test_f13_dates_serialised_as_iso(env):
    ok = env.client.post(DRY, json={**BODY, "due_date": "2026-12-01"})
    assert ok.status_code == 200
    p = env.svc.calls[0][1]
    assert (p.txn_date, p.due_date) == ("2026-11-06", "2026-12-01")


def test_f15_read_contract_is_documented():
    from integrations.intuit.qbo.invoice_push.business import service
    for doc in (push_router.__doc__, service.__doc__):
        assert "IncrementQboApiUsage" in doc and "refresh" in doc and "no business writes" in doc.lower()


# ======================= fix round 2 (EM) =======================
def test_r3_router_maps_only_invoice_not_found_to_404(env):
    """The service turns QBO absence OF THE COMPARED INVOICE into InvoiceNotFound; the router maps only that.
    An unrelated missing resource or any other failure must escape, never become the opaque 404."""
    from integrations.intuit.qbo.base.errors import QboNotFoundError
    from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound

    def not_found(proposal, qid):
        raise InvoiceNotFound(qid)
    env.svc.compare = not_found
    r = env.client.post(CMP, json=COMPARE_BODY)
    assert r.status_code == 404 and r.json().get("detail") == "Not found"

    for exc in (QboNotFoundError("Object Not Found (a fee item)"), RuntimeError("unrelated")):
        def boom(proposal, qid, _e=exc):
            raise _e
        env.svc.compare = boom
        with pytest.raises(type(exc)):
            env.client.post(CMP, json=COMPARE_BODY)


@pytest.mark.parametrize("path_in_body", ["project_id", "source_line_id", "sub_cost_code_id", "payment_term_id"])
@pytest.mark.parametrize("bad", [0, 2 ** 63])
def test_b2_ids_outside_bigint_range_are_422(env, path_in_body, bad):
    import copy
    b = copy.deepcopy(BODY)
    if path_in_body == "project_id":
        b["project_id"] = bad
    elif path_in_body == "source_line_id":
        b["source_lines"][0]["source_line_id"] = bad
    elif path_in_body == "sub_cost_code_id":
        b["fee_lines"][0]["sub_cost_code_id"] = bad
    else:
        b["payment_term_id"] = str(bad)
    assert env.client.post(DRY, json=b).status_code == 422
    assert env.svc.calls == []
