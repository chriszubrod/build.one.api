"""U-778d1 b2 -- InvoicePushDryRunService + LiveQboReader (fakes only: no DB, no QBO, no HTTP).

The fake QBO reader RAISES on any method that is not a read -- the service must complete a whole
dry run / compare without touching one (this unit writes nothing to QBO or the DB).
"""
import copy
import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.intuit.qbo.invoice_push.business.model import (
    FeeItem, FeeLine, LiveCharge, LiveSourceLine, Proposal, SourceLineFacts,
)
from integrations.intuit.qbo.invoice_push.business.payload import DRY_RUN_NOTE
from integrations.intuit.qbo.invoice_push.business.ports import READ_ONLY_QBO_METHODS, ProjectFacts
from integrations.intuit.qbo.invoice_push.business.service import (
    DOC_NUMBER_RE, InvoicePushDryRunService,
)
from integrations.intuit.qbo.invoice_push.external.reader import LiveQboReader
from shared.lifecycle.completion_gate import HoldingCodeRefused

D = Decimal
FX = Path(__file__).parent / "fixtures" / "u778d1"
ROOT = Path(__file__).resolve().parents[1] / "integrations/intuit/qbo/invoice_push"


def load(name):
    return json.loads((FX / name).read_text())


# ---- fakes ---------------------------------------------------------------------------
class FakeRepo:
    def __init__(self, facts=None, project=None, links=None):
        self.links = links or {}
        self.facts = facts if facts is not None else {("Bill", 1): bill_facts()}
        self.project = project if project is not None else ProjectFacts(
            project_id=7, qbo_id="1383", realm_id="R1", doc_number_used_locally=False)
        self.calls = []

    def read_project_facts(self, project_id, doc_number):
        self.calls.append(("project", project_id, doc_number))
        return self.project

    def read_staged_invoice_links(self, charge_ids, realm_id):
        self.calls.append(("links", tuple(charge_ids), realm_id))
        return {c: self.links[c] for c in charge_ids if c in self.links}

    def read_source_facts(self, source_type, ids, realm_id):
        self.calls.append(("facts", source_type, tuple(ids), realm_id))
        return {k: v for k, v in self.facts.items() if k[0] == source_type and k[1] in ids}


class FakeQbo:
    """Four reads + close. ANYTHING else (create_/update_/post/...) raises."""

    def __init__(self, live=None, charges=None, doc_exists=False, invoice=None, doc_ids=None, items=None,
                 fail_on=None):
        self.doc_ids = doc_ids
        self.items = items or {}
        self.fail_on = fail_on
        self.closed = 0
        self.live = live if live is not None else {("P1", "L1"): LiveSourceLine("Billable", "68056")}
        self.charges = charges if charges is not None else {"68056": LiveCharge.from_qbo(load("preflight_rc_68056.json"))}
        self.doc_exists = doc_exists
        self.invoice = invoice
        self.calls = []

    def read_source_lines(self, source_type, parent_qbo_ids):
        self.calls.append(("read_source_lines", source_type, tuple(parent_qbo_ids)))
        return dict(self.live)

    def read_charges(self, ids):
        self.calls.append(("read_charges", tuple(ids)))
        if self.fail_on == "read_charges":
            raise RuntimeError("boom")
        return {i: self.charges[i] for i in ids if i in self.charges}

    def doc_number_invoice_ids(self, doc_number):
        self.calls.append(("doc_number_invoice_ids", doc_number))
        if self.doc_ids is not None:
            return tuple(self.doc_ids)
        return ("5",) if self.doc_exists else ()

    def get_item(self, item_id):
        self.calls.append(("get_item", item_id))
        return copy.deepcopy(self.items[item_id])

    def get_invoice(self, invoice_id):
        self.calls.append(("get_invoice", invoice_id))
        return copy.deepcopy(self.invoice)

    def close(self):
        self.closed += 1

    def __getattr__(self, name):  # only reached for names NOT defined above
        raise AssertionError(f"write/unknown QBO path touched: {name}")


def bill_facts(**kw):
    base = dict(source_type="Bill", source_line_id=1, project_id=7, parent_is_draft=False, parent_deleted=False,
                is_billable=True, is_billed=False, referenced_by_ili_ids=(), sub_cost_code_is_holding=False,
                amount=D("32.50"), markup=D("0.5"), price=D("48.75"), billable_amount=None,
                parent_qbo_id="P1", line_qbo_id="L1", staged_charge_ids=("68056",), ili_invoice_qbo_ids=())
    base.update(kw)
    return SourceLineFacts(**base)


def proposal(**kw):
    base = dict(project_id=7, doc_number="LINKTEST-1", txn_date="2026-11-06", due_date="2026-11-06", payment_term_id=None,
                source_lines=(("Bill", 1),), fee_lines=())
    base.update(kw)
    return Proposal(**base)


def make(repo=None, qbo=None, fee=None, customer="1383", **kw):
    repo = repo or FakeRepo()
    qbo = qbo or FakeQbo()
    svc = InvoicePushDryRunService(
        repo=repo, qbo_reader_factory=lambda realm: qbo,
        customer_resolver=lambda pid: customer,
        fee_item_resolver=fee or (lambda scc, realm: FeeItem("80", "191")),
        **kw,
    )
    return svc, repo, qbo


def codes(items):
    return sorted({i.split(":", 1)[0] for i in items})


# ---- happy path ----------------------------------------------------------------------
def test_valid_proposal_yields_the_linktest_payload():
    svc, repo, qbo = make()
    r = svc.dry_run(proposal())
    assert r.ok and r.header_reasons == () and r.lines[0].ok
    expected = load("linktest_invoice_payload.json")
    expected["DueDate"] = "2026-11-06"
    expected["PrivateNote"] = DRY_RUN_NOTE
    assert json.dumps(r.payload, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert r.expected is not None and r.expected.doc_number == "LINKTEST-1"
    assert repo.calls[0] == ("project", 7, "LINKTEST-1")
    assert ("facts", "Bill", (1,), "R1") in repo.calls
    assert [c[0] for c in qbo.calls] == ["read_source_lines", "read_charges", "doc_number_invoice_ids"]
    assert ("read_charges", ("68056",)) in qbo.calls


def test_fee_line_resolved_and_included():
    fee = FeeLine(3, "Builder's fee", D("1"), D("100"))
    seen = []
    svc, _, _ = make(fee=lambda scc, realm: seen.append((scc, realm)) or FeeItem("80", "191"))
    r = svc.dry_run(proposal(fee_lines=(fee,)))
    assert r.ok and seen == [(3, "R1")]
    assert r.payload["Line"][-1]["SalesItemLineDetail"]["ItemRef"] == {"value": "80"}


def test_result_to_dict_is_json_serialisable():
    svc, _, _ = make()
    d = svc.dry_run(proposal()).to_dict()
    json.dumps(d)
    assert d["ok"] is True and d["payload"]["DocNumber"] == "LINKTEST-1" and d["expected"]["doc_number"] == "LINKTEST-1"


# ---- each fact source failing --------------------------------------------------------
def test_missing_source_row():
    svc, _, qbo = make(repo=FakeRepo(facts={}))
    r = svc.dry_run(proposal())
    assert not r.ok and r.payload is None and r.expected is None
    assert codes(r.header_reasons) == ["source_line_unavailable"] and r.lines == ()
    assert qbo.calls == []


def test_live_line_not_billable():
    svc, _, _ = make(qbo=FakeQbo(live={("P1", "L1"): LiveSourceLine("NotBillable", "68056")}))
    r = svc.dry_run(proposal())
    assert not r.ok and r.payload is None
    assert codes(r.lines[0].reasons) == ["not_billable_in_qbo"]


def test_live_line_missing_in_qbo():
    svc, _, _ = make(qbo=FakeQbo(live={}))
    r = svc.dry_run(proposal())
    assert codes(r.lines[0].reasons) == ["source_not_in_qbo"] and r.payload is None


def test_charge_invoiced():
    c = load("preflight_rc_68056.json")
    c["HasBeenInvoiced"] = True
    svc, _, _ = make(qbo=FakeQbo(charges={"68056": LiveCharge.from_qbo(c)}))
    r = svc.dry_run(proposal())
    assert not r.ok and codes(r.lines[0].reasons) == ["charge_invoiced"] and r.payload is None


def test_staged_charge_mismatch_is_a_warning_only():
    svc, _, _ = make(repo=FakeRepo(facts={("Bill", 1): bill_facts(staged_charge_ids=("11111",))}))
    r = svc.dry_run(proposal())
    assert r.ok and r.payload is not None
    assert codes(r.lines[0].warnings) == ["staged_charge_mismatch"]


def test_local_state_reasons_flow_through():
    svc, _, _ = make(repo=FakeRepo(facts={("Bill", 1): bill_facts(referenced_by_ili_ids=(5,), is_billed=True)}))
    r = svc.dry_run(proposal())
    assert codes(r.lines[0].reasons) == ["already_on_invoice", "locally_billed"]


def test_unsupported_source_type_reported():
    svc, repo, _ = make()
    r = svc.dry_run(proposal(source_lines=(("Widget", 1),)))
    assert not r.ok and "unsupported_source_type" in codes(r.header_reasons)
    assert not any(c[0] == "facts" for c in repo.calls)


def test_no_project_and_no_customer():
    svc, _, qbo = make(repo=FakeRepo(project=None))
    repo2 = FakeRepo()
    repo2.project = None
    svc2 = InvoicePushDryRunService(repo=repo2, qbo_reader_factory=lambda r: qbo,
                                    customer_resolver=lambda p: "1", fee_item_resolver=lambda s, r: None)
    # FakeRepo(project=None) falls back to the default project; force a genuinely missing one
    r = svc2.dry_run(proposal())
    assert not r.ok and codes(r.header_reasons) == ["project_not_found"] and r.payload is None
    svc3, _, _ = make(customer=None)
    r3 = svc3.dry_run(proposal())
    assert "no_customer" in codes(r3.header_reasons) and r3.payload is None


def test_project_without_realm_is_refused_before_any_qbo_call():
    repo = FakeRepo(project=ProjectFacts(7, "1383", None, False))
    svc, _, qbo = make(repo=repo)
    r = svc.dry_run(proposal())
    assert codes(r.header_reasons) == ["no_realm"] and qbo.calls == []


# ---- doc number ----------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["a'b", "-x", "x" * 22, "ok\n", "a;b", " lead", "q\"", "tab\tx", "é1"])
def test_bad_doc_number_rejected_before_any_use(bad):
    svc, repo, qbo = make()
    r = svc.dry_run(proposal(doc_number=bad))
    assert "doc_number_invalid" in codes(r.header_reasons) and r.payload is None
    assert not any(c[0] == "doc_number_invoice_ids" for c in qbo.calls)
    assert repo.calls[0] == ("project", 7, "")  # the bad value never reaches the sproc either


@pytest.mark.parametrize("good", ["1", "INV-0042", "A b.c_d/e#f-g", "x" * 21])
def test_doc_number_whitelist_accepts(good):
    assert DOC_NUMBER_RE.fullmatch(good)


def test_doc_number_used_locally():
    repo = FakeRepo(project=ProjectFacts(7, "1383", "R1", True))
    svc, _, _ = make(repo=repo)
    r = svc.dry_run(proposal())
    assert codes(r.header_reasons) == ["doc_number_used_locally"] and not r.ok and r.payload is None


def test_doc_number_used_in_qbo():
    svc, _, _ = make(qbo=FakeQbo(doc_exists=True))
    r = svc.dry_run(proposal())
    assert codes(r.header_reasons) == ["doc_number_used_in_qbo"] and r.payload is None


# ---- fee lines -----------------------------------------------------------------------
FEE = FeeLine(3, "Builder's fee", D("1"), D("100"))


def test_holding_code_fee_line():
    def boom(scc, realm):
        raise HoldingCodeRefused("holding")
    svc, _, _ = make(fee=boom)
    r = svc.dry_run(proposal(fee_lines=(FEE,)))
    assert codes(r.header_reasons) == ["fee_holding_code"] and r.payload is None


def test_unresolvable_fee_item():
    svc, _, _ = make(fee=lambda scc, realm: None)
    r = svc.dry_run(proposal(fee_lines=(FEE,)))
    assert codes(r.header_reasons) == ["fee_item_unresolved"] and r.payload is None


# ---- no writes -----------------------------------------------------------------------
def test_fake_raises_on_a_write_method():
    with pytest.raises(AssertionError):
        FakeQbo().create_invoice({})


def test_service_guard_refuses_a_non_read_method():
    from integrations.intuit.qbo.invoice_push.business.service import _qbo_call
    with pytest.raises(AssertionError):
        _qbo_call(FakeQbo(), "create_invoice", {})
    assert {"read_source_lines", "read_charges", "doc_number_invoice_ids", "get_invoice", "get_item"} <= READ_ONLY_QBO_METHODS
    assert not any(re.match(r"(create|update|delete|send|post|void|put)", m) for m in READ_ONLY_QBO_METHODS)


@pytest.mark.parametrize("rel", ["business/service.py", "external/reader.py", "persistence/repo.py"])
def test_no_write_verbs_in_source(rel):
    src = (ROOT / rel).read_text()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    for token in (".post(", ".post_multipart(", ".put(", ".delete(", "create_invoice", "update_invoice",
                  "send_invoice", "void_invoice", "delete_invoice", ".create(", ".update("):
        assert token not in code, f"{rel} contains {token}"


# ---- compare -------------------------------------------------------------------------
def hand_built(payload, **over):
    inv = copy.deepcopy(payload)
    inv.update({"Id": "9001", "SyncToken": "0", "PrivateNote": "built by hand in QBO", "TotalAmt": 48.75})
    for i, ln in enumerate(inv["Line"], start=1):
        ln["Id"] = str(i)
    inv["Line"].append({"DetailType": "SubTotalLineDetail", "Amount": 48.75, "SubTotalLineDetail": {}})
    inv.update(over)
    return inv


def expected_payload():
    svc, _, _ = make()
    return svc.dry_run(proposal()).payload


def test_compare_identical_ignoring_private_note():
    inv = hand_built(expected_payload())
    svc, _, qbo = make(qbo=FakeQbo(invoice=inv))
    out = svc.compare(proposal(), "9001")
    assert out["diffs"] == []
    assert out["dry_run"]["payload"] is not None
    assert ("get_invoice", "9001") in qbo.calls


def test_compare_reports_a_changed_amount_as_one_diff():
    inv = hand_built(expected_payload())
    inv["Line"][0]["Amount"] = 49.00
    svc, _, _ = make(qbo=FakeQbo(invoice=inv))
    diffs = svc.compare(proposal(), "9001")["diffs"]
    assert len(diffs) == 1 and diffs[0]["path"] == "Line[0].amount"
    assert diffs[0]["expected"] == "32.50" and diffs[0]["actual"] == "49.00"


def test_compare_does_not_tolerate_real_defects():
    qbo = FakeQbo(live={("P1", "L1"): LiveSourceLine("NotBillable", "68056")}, invoice=hand_built(expected_payload()))
    svc, _, _ = make(qbo=qbo)
    out = svc.compare(proposal(), "9001")
    assert out["diffs"] is None and out["dry_run"]["ok"] is False and out["dry_run"]["payload"] is None


def test_compare_when_nothing_to_build_has_no_diffs():
    svc, _, qbo = make(repo=FakeRepo(facts={}), qbo=FakeQbo(invoice=hand_built(expected_payload())))
    out = svc.compare(proposal(), "9001")
    assert out["diffs"] is None and out["dry_run"]["ok"] is False


def test_compare_rejects_a_non_numeric_invoice_id():
    svc, _, qbo = make()
    with pytest.raises(ValueError):
        svc.compare(proposal(), "9001/../x")
    assert qbo.calls == []


# ---- LiveQboReader (real read adapter, fake transport) --------------------------------
class FakeHttp:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.gets = []

    def get(self, path, *, params=None, timeout_tier="A", operation_name=None):
        self.gets.append((path, params, operation_name))
        for key, val in self.responses.items():
            if key in (path if params is None else params.get("query", path)):
                return val
        return {"QueryResponse": {}}

    def post(self, *a, **k):
        raise AssertionError("POST on the dry-run reader")

    post_multipart = post

    def close(self):
        pass


def test_reader_doc_number_query_is_escaped_and_read_only():
    http = FakeHttp({"DocNumber": {"QueryResponse": {"Invoice": [{"Id": "5"}]}}})
    r = LiveQboReader("R1", http_client=http)
    assert r.doc_number_invoice_ids("A'B") == ("5",)
    path, params, _ = http.gets[0]
    assert path == "query" and params["query"] == "SELECT Id FROM Invoice WHERE DocNumber = 'A\\'B'"


def test_reader_doc_number_absent_vs_malformed_response():
    r = LiveQboReader("R1", http_client=FakeHttp({"DocNumber": {"QueryResponse": {}}}))
    assert r.doc_number_invoice_ids("1") == ()
    with pytest.raises(Exception):  # a response with no QueryResponse is NOT proof of absence
        LiveQboReader("R1", http_client=FakeHttp({"DocNumber": {"Fault": {}}})).doc_number_invoice_ids("1")


def test_reader_get_invoice_is_a_plain_get():
    http = FakeHttp({"invoice/9001": {"Invoice": {"Id": "9001", "Line": []}}})
    assert LiveQboReader("R1", http_client=http).get_invoice("9001") == {"Id": "9001", "Line": []}
    assert http.gets[0][0] == "invoice/9001"


def test_reader_charges_batched_by_id_query():
    rc = load("preflight_rc_68056.json")
    http = FakeHttp({"ReimburseCharge": {"QueryResponse": {"ReimburseCharge": [rc]}}})
    out = LiveQboReader("R1", http_client=http).read_charges(["68056"])
    assert list(out) == ["68056"] and out["68056"].has_been_invoiced is False
    assert "FROM ReimburseCharge WHERE Id IN ('68056')" in http.gets[0][1]["query"]


def test_reader_source_lines_parse_status_and_charge_link():
    from integrations.intuit.qbo.bill.external.schemas import QboBill
    bill = QboBill(**{"Id": "P1", "SyncToken": "0", "Line": [
        {"Id": "L1", "Amount": 32.5, "DetailType": "ItemBasedExpenseLineDetail",
         "ItemBasedExpenseLineDetail": {"BillableStatus": "Billable"},
         "LinkedTxn": [{"TxnId": "68056", "TxnType": "ReimburseCharge"}]},
        {"Id": "L2", "Amount": 1, "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"BillableStatus": "NotBillable"}}]})

    class BillClient:
        def query_bills_by_ids(self, ids):
            self.ids = ids
            return [bill]

    bc = BillClient()
    out = LiveQboReader("R1", http_client=FakeHttp(), bill_client=bc).read_source_lines("Bill", ["P1"])
    assert bc.ids == ["P1"]
    assert out[("P1", "L1")] == LiveSourceLine("Billable", "68056")
    assert out[("P1", "L2")] == LiveSourceLine("NotBillable", None)


def test_reader_exposes_only_read_methods():
    public = {n for n in dir(LiveQboReader) if not n.startswith("_")}
    assert public <= READ_ONLY_QBO_METHODS


# ======================= fix round 1 (U-778d1 f1) =======================
def link(charge, inv="555", line="7"):
    from integrations.intuit.qbo.invoice_push.business.model import StagedInvoiceLink
    return StagedInvoiceLink(charge, inv, line)


def test_f1_staged_links_are_read_for_the_resolved_live_charge_ids():
    repo = FakeRepo(facts={("Bill", 1): bill_facts(staged_charge_ids=("OLD",))}, links={"68056": (link("68056"),)})
    svc, _, _ = make(repo=repo)
    r = svc.dry_run(proposal(due_date="2026-11-06"))
    assert ("links", ("68056",), "R1") in repo.calls
    assert not r.ok and "charge_on_staged_invoice" in codes(r.lines[0].reasons) and r.payload is None


def test_f1_links_on_the_stale_staged_charge_do_not_block_the_live_replacement():
    repo = FakeRepo(facts={("Bill", 1): bill_facts(staged_charge_ids=("OLD",))}, links={"OLD": (link("OLD"),)})
    svc, _, _ = make(repo=repo)
    r = svc.dry_run(proposal(due_date="2026-11-06"))
    assert r.ok and codes(r.lines[0].warnings) == ["staged_charge_mismatch"]


class ChargesViaReader(FakeQbo):
    """FakeQbo whose charges come from the REAL LiveQboReader parsing a raw QBO row."""

    def __init__(self, raw_rc, **kw):
        super().__init__(**kw)
        self.rdr = LiveQboReader("R1", http_client=FakeHttp({"ReimburseCharge": {
            "QueryResponse": {"ReimburseCharge": [raw_rc]}}}))

    def read_charges(self, ids):
        return self.rdr.read_charges(ids)


def test_f2_a_charge_without_has_been_invoiced_is_charge_malformed_not_eligible():
    raw = load("preflight_rc_68056.json")
    raw.pop("HasBeenInvoiced")
    svc, _, _ = make(qbo=ChargesViaReader(raw))
    r = svc.dry_run(proposal(due_date="2026-11-06"))
    assert not r.ok and r.payload is None and codes(r.lines[0].reasons) == ["charge_malformed"]


def test_f2_a_charge_without_line_ids_is_charge_malformed():
    raw = load("preflight_rc_68056.json")
    raw["Line"][0].pop("Id")
    svc, _, _ = make(qbo=ChargesViaReader(raw))
    assert codes(svc.dry_run(proposal(due_date="2026-11-06")).lines[0].reasons) == ["charge_malformed"]


def test_f2_reader_returns_a_malformed_marker_per_row_not_an_exception():
    from integrations.intuit.qbo.invoice_push.business.model import MalformedCharge
    raw = load("preflight_rc_68056.json")
    raw.pop("HasBeenInvoiced")
    out = LiveQboReader("R1", http_client=FakeHttp({"ReimburseCharge": {
        "QueryResponse": {"ReimburseCharge": [raw]}}})).read_charges(["68056"])
    assert isinstance(out["68056"], MalformedCharge)


def good_invoice(**over):
    inv = hand_built(expected_payload_due())
    inv["CustomerRef"] = {"value": "1383"}
    inv.update(over)
    return inv


def expected_payload_due():
    svc, _, _ = make()
    return svc.dry_run(proposal(due_date="2026-11-06")).payload


def charge_invoiced_by(inv_id):
    c = load("preflight_rc_68056.json")
    c["HasBeenInvoiced"] = True
    c["LinkedTxn"] = [{"TxnId": inv_id, "TxnType": "Invoice"}]
    return LiveCharge.from_qbo(c)


def shadow_state(inv_id="9001", **kw):
    repo = FakeRepo(facts={("Bill", 1): bill_facts(is_billed=True, referenced_by_ili_ids=(9,),
                                                   ili_invoice_qbo_ids=(inv_id,))},
                    project=ProjectFacts(7, "1383", "R1", True, (inv_id,)), links={"68056": (link("68056", inv_id),)})
    qbo = FakeQbo(live={("P1", "L1"): LiveSourceLine("HasBeenBilled", "68056")},
                  charges={"68056": charge_invoiced_by(inv_id)}, doc_ids=(inv_id,), invoice=good_invoice(), **kw)
    return repo, qbo


def test_f3_compare_tolerates_state_that_points_at_the_compared_invoice():
    repo, qbo = shadow_state("9001")
    svc, _, _ = make(repo=repo, qbo=qbo)
    out = svc.compare(proposal(due_date="2026-11-06"), "9001")
    assert out["dry_run"]["ok"] is True and out["diffs"] == []


@pytest.mark.parametrize("which", ["charge", "staged", "ili", "doc_qbo", "doc_local"])
def test_f3_compare_refuses_state_that_points_at_another_invoice(which):
    repo, qbo = shadow_state("9001")
    if which == "charge":
        qbo.charges = {"68056": charge_invoiced_by("9999")}
    elif which == "staged":
        repo.links = {"68056": (link("68056", "9999"),)}
    elif which == "ili":
        repo.facts = {("Bill", 1): bill_facts(is_billed=True, referenced_by_ili_ids=(9,), ili_invoice_qbo_ids=("9999",))}
    elif which == "doc_qbo":
        qbo.doc_ids = ("9999",)
    else:
        repo.project = ProjectFacts(7, "1383", "R1", True, ("9999",))
    svc, _, _ = make(repo=repo, qbo=qbo)
    out = svc.compare(proposal(due_date="2026-11-06"), "9001")
    assert out["dry_run"]["ok"] is False and out["diffs"] is None


def test_f3_compare_of_an_invoice_for_another_customer_is_not_found_and_returns_nothing():
    from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound
    repo, qbo = shadow_state("9001")
    qbo.invoice = good_invoice(CustomerRef={"value": "4242"})
    svc, _, _ = make(repo=repo, qbo=qbo)
    with pytest.raises(InvoiceNotFound) as e:
        svc.compare(proposal(due_date="2026-11-06"), "9001")
    assert "4242" not in str(e.value) and "LINKTEST" not in str(e.value)
    assert qbo.closed >= 1
    qbo.invoice = good_invoice()
    qbo.invoice.pop("CustomerRef")
    with pytest.raises(InvoiceNotFound):
        svc.compare(proposal(due_date="2026-11-06"), "9001")


def test_f4_a_non_home_currency_actual_invoice_is_a_diff():
    repo, qbo = shadow_state("9001")
    qbo.invoice = good_invoice(CurrencyRef={"value": "CAD"}, ExchangeRate=0.73)
    svc, _, _ = make(repo=repo, qbo=qbo)
    paths = {d["path"] for d in svc.compare(proposal(due_date="2026-11-06"), "9001")["diffs"]}
    assert {"currency_ref", "exchange_rate"} <= paths


def test_f6_an_actual_invoice_without_total_amt_is_a_diff():
    repo, qbo = shadow_state("9001")
    qbo.invoice = good_invoice()
    qbo.invoice.pop("TotalAmt")
    svc, _, _ = make(repo=repo, qbo=qbo)
    assert svc.compare(proposal(due_date="2026-11-06"), "9001")["diffs"]


@pytest.mark.parametrize("body", [
    {"QueryResponse": {"Fault": {"Error": []}}},
    {"QueryResponse": {"Invoice": None}},
    {"QueryResponse": {"Invoice": [{}]}},
    {"QueryResponse": {"Invoice": [{"SyncToken": "0"}]}},
    {"Fault": {"Error": []}},
    {"QueryResponse": None},
    {},
])
def test_f5_doc_number_read_raises_on_any_degraded_response(body):
    from integrations.intuit.qbo.base.errors import QboUnexpectedError
    r = LiveQboReader("R1", http_client=FakeHttp({"DocNumber": body}))
    with pytest.raises(QboUnexpectedError):
        r.doc_number_invoice_ids("1")


def test_f5_doc_number_read_returns_ids_or_empty():
    r = LiveQboReader("R1", http_client=FakeHttp({"DocNumber": {"QueryResponse": {"Invoice": [{"Id": "5"}, {"Id": "6"}]}}}))
    assert r.doc_number_invoice_ids("1") == ("5", "6")
    assert LiveQboReader("R1", http_client=FakeHttp({"DocNumber": {"QueryResponse": {}}})).doc_number_invoice_ids("1") == ()


def test_f5_service_refuses_eligibility_when_the_doc_number_read_raises():
    class Bad(FakeQbo):
        def doc_number_invoice_ids(self, n):
            return LiveQboReader("R1", http_client=FakeHttp({"DocNumber": {"QueryResponse": {"Fault": {}}}})).doc_number_invoice_ids(n)
    from integrations.intuit.qbo.base.errors import QboUnexpectedError
    svc, _, _ = make(qbo=Bad())
    with pytest.raises(QboUnexpectedError):
        svc.dry_run(proposal(due_date="2026-11-06"))


def test_f7_payment_term_resolves_through_the_dbo_mapping_or_is_unmapped():
    seen = []
    svc, _, _ = make(payment_term_resolver=lambda tid, realm: seen.append((tid, realm)) or "77")
    r = svc.dry_run(proposal(due_date="2026-11-06", payment_term_id="2"))
    assert r.ok and r.payload["SalesTermRef"] == {"value": "77"} and seen == [("2", "R1")]
    svc, _, _ = make(payment_term_resolver=lambda tid, realm: None)
    r = svc.dry_run(proposal(due_date="2026-11-06", payment_term_id="2"))
    assert not r.ok and codes(r.header_reasons) == ["payment_term_unmapped"] and r.payload is None
    svc, _, _ = make(payment_term_resolver=lambda tid, realm: pytest.fail("no term asked"))
    assert svc.dry_run(proposal(due_date="2026-11-06")).ok


ITEM = {"Id": "80", "IncomeAccountRef": {"value": "191", "name": "Fee income"}}


def test_f9_fee_item_income_account_comes_from_the_qbo_item():
    qbo = FakeQbo(items={"80": ITEM})
    svc, _, _ = make(qbo=qbo, fee=lambda scc, realm: FeeItem("80"))
    r = svc.dry_run(proposal(due_date="2026-11-06", fee_lines=(FEE,)))
    assert r.ok and ("get_item", "80") in qbo.calls
    assert r.payload["Line"][-1]["SalesItemLineDetail"]["ItemAccountRef"] == {"value": "191"}


@pytest.mark.parametrize("item", [{"Id": "80"}, {"Id": "80", "IncomeAccountRef": {}}, {"Id": "81", "IncomeAccountRef": {"value": "191"}}])
def test_f9_unresolvable_income_account_is_fee_item_unresolved(item):
    svc, _, _ = make(qbo=FakeQbo(items={"80": item}), fee=lambda scc, realm: FeeItem("80"))
    r = svc.dry_run(proposal(due_date="2026-11-06", fee_lines=(FEE,)))
    assert codes(r.header_reasons) == ["fee_item_unresolved"] and r.payload is None


def test_f9_reader_get_item_is_a_plain_get():
    http = FakeHttp({"item/80": {"Item": ITEM}})
    assert LiveQboReader("R1", http_client=http).get_item("80") == ITEM
    assert http.gets[0][0] == "item/80"
    from integrations.intuit.qbo.base.errors import QboUnexpectedError
    with pytest.raises(QboUnexpectedError):
        LiveQboReader("R1", http_client=FakeHttp({"item/80": {"Fault": {}}})).get_item("80")


def test_f10_an_invoice_total_at_or_above_1e11_is_refused_before_building():
    a = FeeLine(3, "A", D("1"), D("99999999999"))
    b = FeeLine(3, "B", D("1"), D("99999999999"))
    svc, _, _ = make()
    r = svc.dry_run(proposal(due_date="2026-11-06", source_lines=(), fee_lines=(a, b)))
    assert not r.ok and "amount_out_of_range" in codes(r.header_reasons) and r.payload is None


def test_f12_compare_closes_the_reader_when_a_preflight_read_raises():
    qbo = FakeQbo(fail_on="read_charges", invoice=good_invoice())
    svc, _, _ = make(qbo=qbo)
    with pytest.raises(RuntimeError):
        svc.compare(proposal(due_date="2026-11-06"), "9001")
    assert qbo.closed == 1


def test_f12_dry_run_and_compare_close_exactly_once_on_the_happy_paths():
    svc, _, qbo = make()
    svc.dry_run(proposal(due_date="2026-11-06"))
    assert qbo.closed == 1
    repo, qbo2 = shadow_state("9001")
    make(repo=repo, qbo=qbo2)[0].compare(proposal(due_date="2026-11-06"), "9001")
    assert qbo2.closed == 1


def test_f15_reader_module_documents_the_read_contract():
    from integrations.intuit.qbo.invoice_push.external import reader
    assert "IncrementQboApiUsage" in reader.__doc__ and "float" in reader.__doc__


# ======================= fix round 2 (EM, after the round-1 re-review) =======================
def test_r2_foreign_project_source_line_short_circuits_with_no_details_and_no_qbo_reads():
    """P1: a source line from ANOTHER project must stop the run before any live read, and no per-line
    diagnostics (which would carry that project's invoice ids, customer, amounts) may be returned."""
    repo = FakeRepo(facts={("Bill", 1): bill_facts(project_id=999, staged_charge_ids=("55555",),
                                                    amount=D("8765.43"), price=D("9876.54"))})
    svc, _, qbo = make(repo=repo)
    res = svc.dry_run(proposal())
    assert not res.ok and res.payload is None
    assert codes(res.header_reasons) == ["source_line_unavailable"]
    assert res.lines == ()
    blob = json.dumps(res.to_dict(), default=str)
    for leaked in ("8765.43", "9876.54", "55555", "999"):
        assert leaked not in blob
    assert qbo.calls == []


def test_r2_foreign_project_source_line_in_compare_also_short_circuits():
    repo = FakeRepo(facts={("Bill", 1): bill_facts(project_id=999)})
    inv = hand_built(expected_payload())
    svc, _, qbo = make(repo=repo, qbo=FakeQbo(invoice=inv))
    out = svc.compare(proposal(), "9001")
    assert out["diffs"] is None
    assert qbo.calls == []  # not even the compared invoice is read
    assert out["dry_run"]["payload"] is None and out["dry_run"]["lines"] in ([], ())


def test_r3_foreign_and_missing_source_lines_are_indistinguishable():
    """A caller authorized for project 7 cannot tell a line of another project from a line that does not exist."""
    foreign, _, _ = make(repo=FakeRepo(facts={("Bill", 1): bill_facts(project_id=999)}))
    missing, _, _ = make(repo=FakeRepo(facts={}))
    a = foreign.dry_run(proposal()).to_dict()
    b = missing.dry_run(proposal()).to_dict()
    assert a == b


class _QboRaising(FakeQbo):
    def __init__(self, raise_on, **kw):
        super().__init__(**kw)
        self.raise_on = raise_on

    def get_invoice(self, invoice_id):
        if self.raise_on == "get_invoice":
            from integrations.intuit.qbo.base.errors import QboNotFoundError
            raise QboNotFoundError("Object Not Found")
        return super().get_invoice(invoice_id)

    def get_item(self, item_id):
        if self.raise_on == "get_item":
            from integrations.intuit.qbo.base.errors import QboNotFoundError
            raise QboNotFoundError("Object Not Found")
        return super().get_item(item_id)


def test_r3_qbo_absence_of_the_compared_invoice_is_invoice_not_found():
    from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound
    svc, _, _ = make(qbo=_QboRaising("get_invoice", invoice=hand_built(expected_payload())))
    with pytest.raises(InvoiceNotFound):
        svc.compare(proposal(), "9001")


def test_r3_qbo_absence_elsewhere_is_not_masked_as_invoice_not_found():
    from integrations.intuit.qbo.base.errors import QboNotFoundError
    from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound
    fees = (FeeLine(sub_cost_code_id=1, description="fee", qty=D("1"), unit_price=D("10")),)
    qbo = _QboRaising("get_item", invoice=hand_built(expected_payload()), items={})
    svc = InvoicePushDryRunService(repo=FakeRepo(), qbo_reader_factory=lambda realm: qbo,
                                   customer_resolver=lambda pid: "1383",
                                   fee_item_resolver=lambda scc, realm: FeeItem("80"))  # no account -> get_item
    with pytest.raises(QboNotFoundError) as e:
        svc.compare(proposal(fee_lines=fees), "9001")
    assert not isinstance(e.value, InvoiceNotFound)


@pytest.mark.parametrize("returned_id", ["9999", None])
def test_r2_compare_requires_the_fetched_invoice_to_be_the_requested_one(returned_id):
    from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound
    inv = hand_built(expected_payload())
    if returned_id is None:
        inv.pop("Id")
    else:
        inv["Id"] = returned_id
    svc, _, _ = make(qbo=FakeQbo(invoice=inv))
    with pytest.raises(InvoiceNotFound):
        svc.compare(proposal(), "9001")


def test_r2_money_bound_applies_to_the_amounts_actually_emitted():
    """P2: two fees 99999999999.99 + 0.005 sum (unrounded) under the bound but round to 1E11."""
    fees = (FeeLine(sub_cost_code_id=1, description="fee a", qty=D("1"), unit_price=D("99999999999.99")),
            FeeLine(sub_cost_code_id=1, description="fee b", qty=D("1"), unit_price=D("0.005")))
    svc, _, _ = make()
    res = svc.dry_run(proposal(source_lines=(), fee_lines=fees))
    assert not res.ok and res.payload is None
    assert "amount_out_of_range" in codes(res.header_reasons)


def _cad_charges():
    rc = load("preflight_rc_68056.json")
    rc["CurrencyRef"] = {"value": "CAD"}
    return {"68056": LiveCharge.from_qbo(rc)}


def test_r2_configured_home_currency_reaches_the_expected_invoice():
    """P2: with home currency CAD (and CAD charges), a matching CAD invoice compares equal; a USD one differs."""
    svc0, _, _ = make(qbo=FakeQbo(charges=_cad_charges()), home_currency="CAD")
    payload = svc0.dry_run(proposal()).payload
    assert payload is not None
    cad = hand_built(payload, CurrencyRef={"value": "CAD"})
    svc, _, _ = make(qbo=FakeQbo(invoice=cad, charges=_cad_charges()), home_currency="CAD")
    assert svc.compare(proposal(), "9001")["diffs"] == []
    usd = hand_built(payload, CurrencyRef={"value": "USD"})
    svc2, _, _ = make(qbo=FakeQbo(invoice=usd, charges=_cad_charges()), home_currency="CAD")
    assert svc2.compare(proposal(), "9001")["diffs"]


# ---- B1: fee-item resolution is memoized per sub cost code within a run -----------------
def test_b1_same_sub_cost_code_resolves_one_item_once():
    qbo = FakeQbo(items={"80": ITEM})
    resolved = []

    def resolver(scc, realm):
        resolved.append(scc)
        return FeeItem("80")  # no account -> get_item

    svc, _, _ = make(qbo=qbo, fee=resolver)
    r = svc.dry_run(proposal(fee_lines=(FEE, FeeLine(3, "Second fee", D("2"), D("50")))))
    assert r.ok
    assert [c for c in qbo.calls if c[0] == "get_item"] == [("get_item", "80")]
    assert resolved == [3]


# ======================= Pass-2 equivalence fixes (EM) =======================
class _FetchRaises:
    """A connection whose cursor raises `exc` from fetchall/fetchone (call_procedure is stubbed out)."""
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        exc = self.exc

        class _C:
            def fetchall(self):
                raise exc

            def fetchone(self):
                raise exc

            def close(self):
                pass
        return _C()


def test_p2eq_source_facts_fetch_value_error_passes_through_unchanged(monkeypatch):
    import integrations.intuit.qbo.invoice_push.persistence.repo as repo_mod
    monkeypatch.setattr(repo_mod, "get_connection", lambda: _FetchRaises(ValueError("bad row")))
    monkeypatch.setattr(repo_mod, "call_procedure", lambda **kw: None)
    with pytest.raises(ValueError) as e:
        repo_mod.InvoicePushRepository().read_source_facts("Bill", [1], "R1")
    assert type(e.value) is ValueError and str(e.value) == "bad row"


def test_p2eq_other_reads_still_map_errors(monkeypatch):
    import integrations.intuit.qbo.invoice_push.persistence.repo as repo_mod
    monkeypatch.setattr(repo_mod, "get_connection", lambda: _FetchRaises(ValueError("bad row")))
    monkeypatch.setattr(repo_mod, "call_procedure", lambda **kw: None)
    with pytest.raises(Exception) as e:
        repo_mod.InvoicePushRepository().read_project_facts(7, "X")
    assert type(e.value) is not ValueError
