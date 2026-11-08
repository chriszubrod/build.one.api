"""Pure-logic tests for U-778b: reverse billable flip (QBO un-invoiced charge -> staging Billable, dbo 1->0 guarded)."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.errors import QboBudgetExceededError
from integrations.intuit.qbo.billable_refresh.business.service import QboBillableStatusRefreshService
from integrations.intuit.qbo.billable_refresh.business.staged import StagedBillableQboLine

REALM = "realm-1"
CHARGE = "77"


def _live_line(line_id, status, charge=CHARGE, detail_type="AccountBasedExpenseLineDetail"):
    linked = [{"TxnId": charge, "TxnType": "ReimburseCharge"}] if charge else []
    return SimpleNamespace(
        id=line_id,
        detail_type=detail_type,
        account_based_expense_line_detail=SimpleNamespace(billable_status=status),
        linked_txn=linked,
    )


def _candidate(id=1, line="10", header="100", charge=CHARGE):
    return StagedBillableQboLine(
        id=id, qbo_line_id=line, amount=Decimal("21.27"), billable_status="HasBeenBilled",
        header_qbo_id=header, realm_id=REALM, charge_qbo_id=charge,
    )


class _Repo:
    def __init__(self, candidates=(), *, forward=(), cas_result=True, read_raises=None):
        self.candidates = list(candidates)
        self.forward = list(forward)
        self.cas_result = cas_result
        self.read_raises = read_raises
        self.cas_calls = []

    def read_by_billable_status_and_realm_id(self, realm_id, billable_status="Billable"):
        return self.forward

    def read_with_uninvoiced_charge_by_realm_id(self, realm_id):
        assert realm_id == REALM
        if self.read_raises:
            raise self.read_raises
        return self.candidates

    def update_billable_status_by_id(self, *, id, expected_billable_status, billable_status):
        self.cas_calls.append((id, expected_billable_status, billable_status))
        return self.cas_result


class _Client:
    def __init__(self, live_by_id, effects=None):
        self.live = live_by_id
        self.effects = effects or []
        self.calls = []

    def _q(self, ids):
        self.calls.append(list(ids))
        if self.effects:
            raise self.effects[0]
        return [self.live[i] for i in ids if i in self.live]

    query_bills_by_ids = _q
    query_purchases_by_ids = _q
    query_vendor_credits_by_ids = _q


class _HeaderSvc:
    def __init__(self, header_qbo_id="100", present=True):
        self.header_qbo_id = header_qbo_id
        self.present = present
        self.realms = []

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        self.realms.append(realm_id)
        if self.present and qbo_id == self.header_qbo_id:
            return SimpleNamespace(id=5, public_id="parent-pub")
        return None


class _LineSvc:
    def __init__(self, *, is_billed=True, present=True, raises=None, source_id=900):
        self.source_id = source_id
        self.is_billed = is_billed
        self.present = present
        self.raises = raises
        self.update_calls = []

    def read_by_qbo_identity(self, parent_id, qbo_id):
        if not self.present:
            return None
        return SimpleNamespace(id=self.source_id, public_id="line-pub", row_version="rv-1", is_billed=self.is_billed)

    def update_by_public_id(self, **kwargs):
        if self.raises:
            raise self.raises
        self.update_calls.append(kwargs)


class _IliSvc:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def read_ids_by_source_line_item(self, source_type, source_line_item_id):
        self.calls.append((source_type, source_line_item_id))
        return self.rows


class _ReconRepo:
    def __init__(self, existing=(), create_raises=None):
        self.existing = list(existing)
        self.create_raises = create_raises
        self.created = []

    def read_unresolved_issue_keys_by_drift_type(self, drift_type):
        assert drift_type == "charge_uninvoiced_locally_billed"
        return self.existing

    def create(self, **kwargs):
        if self.create_raises:
            raise self.create_raises
        self.created.append(kwargs)
        self.existing.append((kwargs["realm_id"], kwargs["entity_type"], kwargs["qbo_id"]))


def _build(entity="bill", *, candidates=(), live=None, header=None, line=None, ili=None, recon=None,
           repo=None, client=None, **svc_kwargs):
    repos = {"bill": _Repo(), "purchase": _Repo(), "vendor_credit": _Repo()}
    clients = {"bill": _Client({}), "purchase": _Client({}), "vendor_credit": _Client({})}
    repos[entity] = repo or _Repo(candidates)
    clients[entity] = client or _Client(live or {})
    header = header or _HeaderSvc()
    line = line or _LineSvc()
    ili = ili or _IliSvc()
    recon = recon or _ReconRepo()
    svc = QboBillableStatusRefreshService(
        bill_line_repo=repos["bill"], purchase_line_repo=repos["purchase"],
        vendor_credit_line_repo=repos["vendor_credit"],
        bill_client=clients["bill"], purchase_client=clients["purchase"],
        vendor_credit_client=clients["vendor_credit"],
        bill_service_factory=lambda: header, bill_line_item_service_factory=lambda: line,
        expense_service_factory=lambda: header, expense_line_item_service_factory=lambda: line,
        bill_credit_service_factory=lambda: header, bill_credit_line_item_service_factory=lambda: line,
        invoice_line_item_service_factory=lambda: ili,
        reconciliation_repo_factory=lambda: recon,
        **svc_kwargs,
    )
    return svc, repos[entity], line, ili, recon


def _live_header(status="Billable", charge=CHARGE, line_id="10"):
    return {"100": SimpleNamespace(id="100", line=[_live_line(line_id, status, charge)])}


def test_happy_path_unbills_dbo_then_cas():
    svc, repo, line, _, recon = _build(candidates=[_candidate()], live=_live_header())
    result = svc.run(REALM)
    rev = result["bill"]["reverse"]
    assert line.update_calls == [
        {"public_id": "line-pub", "row_version": "rv-1", "bill_public_id": "parent-pub",
         "is_billed": False, "_via_internal_pipeline": True}
    ]
    assert repo.cas_calls == [(1, "HasBeenBilled", "Billable")]
    assert rev["dbo_unbilled"] == 1 and rev["flipped_to_billable"] == 1
    assert rev["candidate_lines"] == 1 and rev["headers_fetched"] == 1
    assert recon.created == []
    assert result["aborted"] is None


def test_dry_run_writes_nothing_and_reports_would():
    svc, repo, line, _, recon = _build(candidates=[_candidate()], live=_live_header())
    rev = svc.run(REALM, dry_run=True)["bill"]["reverse"]
    assert line.update_calls == [] and repo.cas_calls == [] and recon.created == []
    assert rev["dbo_would_unbill"] == 1
    assert "dbo_unbilled" not in rev and "flipped_to_billable" in rev and rev["flipped_to_billable"] == 0


def test_dry_run_would_flag_writes_no_issue():
    svc, repo, line, _, recon = _build(
        candidates=[_candidate()], live=_live_header(), ili=_IliSvc([(3, 8)])
    )
    rev = svc.run(REALM, dry_run=True)["bill"]["reverse"]
    assert rev["dbo_would_flag"] == 1
    assert recon.created == [] and repo.cas_calls == [] and line.update_calls == []


def test_live_still_has_been_billed_no_write():
    svc, repo, line, _, _ = _build(candidates=[_candidate()], live=_live_header(status="HasBeenBilled"))
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["reverse_still_billed"] == 1
    assert line.update_calls == [] and repo.cas_calls == []


@pytest.mark.parametrize("live_charge", ["78", None])
def test_live_charge_differs_or_missing_no_write(live_charge):
    svc, repo, line, _, _ = _build(candidates=[_candidate()], live=_live_header(charge=live_charge))
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["reverse_charge_changed"] == 1
    assert line.update_calls == [] and repo.cas_calls == []


def test_live_line_or_header_missing_counts_no_write():
    svc, repo, line, _, _ = _build(
        candidates=[_candidate(id=1, line="99"), _candidate(id=2, header="404")], live=_live_header()
    )
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["lines_missing_live"] == 1 and rev["headers_missing_live"] == 1
    assert line.update_calls == [] and repo.cas_calls == []


def test_local_invoice_line_flags_once_and_does_not_cas():
    recon = _ReconRepo()
    ili = _IliSvc([(3, 8), (4, 9)])
    svc, repo, line, _, _ = _build(candidates=[_candidate()], live=_live_header(), ili=ili, recon=recon)
    first = svc.run(REALM)["bill"]["reverse"]
    assert first["dbo_flagged"] == 1 and first["flipped_to_billable"] == 0
    assert repo.cas_calls == []
    assert line.update_calls == []
    assert ili.calls == [("BillLineItem", 900)]
    assert len(recon.created) == 1
    issue = recon.created[0]
    assert issue["drift_type"] == "charge_uninvoiced_locally_billed"
    assert issue["severity"] == "high" and issue["action"] == "flagged"
    assert issue["entity_type"] == "BillLineItem" and issue["realm_id"] == REALM
    assert issue["qbo_id"] == "100:10" and issue["entity_public_id"] == "line-pub"
    assert "77" in issue["details"] and "[3, 4]" in issue["details"]


def test_flagged_line_stays_candidate_dedupes_then_self_heals_when_invoice_line_gone():
    recon = _ReconRepo()
    ili = _IliSvc([(3, 8)])
    svc, repo, line, _, _ = _build(candidates=[_candidate()], live=_live_header(), ili=ili, recon=recon)
    assert svc.run(REALM)["bill"]["reverse"]["dbo_flagged"] == 1
    # run 2: the local invoice line is still there -> deduped, no second issue, no CAS
    second = svc.run(REALM)["bill"]["reverse"]
    assert second["dbo_flag_deduped"] == 1 and second["dbo_flagged"] == 0
    assert len(recon.created) == 1 and repo.cas_calls == [] and line.update_calls == []
    # run 3: the local invoice line is gone -> dbo un-billed and staging flipped
    ili.rows = []
    third = svc.run(REALM)["bill"]["reverse"]
    assert third["dbo_unbilled"] == 1 and third["flipped_to_billable"] == 1
    assert len(line.update_calls) == 1 and repo.cas_calls == [(1, "HasBeenBilled", "Billable")]
    assert len(recon.created) == 1


def test_dbo_step_raises_no_cas():
    svc, repo, _, _, _ = _build(
        candidates=[_candidate()], live=_live_header(), line=_LineSvc(raises=RuntimeError("boom"))
    )
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_unbill_errors"] == 1 and repo.cas_calls == []
    assert rev["flipped_to_billable"] == 0


def test_issue_write_raises_no_cas():
    svc, repo, line, _, _ = _build(
        candidates=[_candidate()], live=_live_header(), ili=_IliSvc([(3, 8)]),
        recon=_ReconRepo(create_raises=RuntimeError("db down")),
    )
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_unbill_errors"] == 1 and repo.cas_calls == [] and line.update_calls == []


def test_ili_read_raises_no_cas_and_no_dbo_write():
    class _Boom:
        def read_ids_by_source_line_item(self, *a):
            raise RuntimeError("x")

    svc, repo, line, _, _ = _build(candidates=[_candidate()], live=_live_header(), ili=_Boom())
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_unbill_errors"] == 1 and repo.cas_calls == [] and line.update_calls == []


def test_dbo_already_unbilled_cas_only():
    svc, repo, line, ili, _ = _build(
        candidates=[_candidate()], live=_live_header(), line=_LineSvc(is_billed=False)
    )
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_already_unbilled"] == 1 and line.update_calls == [] and ili.calls == []
    assert repo.cas_calls == [(1, "HasBeenBilled", "Billable")]


def test_dbo_unmapped_cas_only():
    svc, repo, line, _, _ = _build(candidates=[_candidate()], live=_live_header(), line=_LineSvc(present=False))
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_unmapped"] == 1 and repo.cas_calls == [(1, "HasBeenBilled", "Billable")]


def test_cas_returns_false_counts_skipped():
    svc, repo, _, _, _ = _build(
        candidates=[_candidate()], live=_live_header(), repo=_Repo([_candidate()], cas_result=False)
    )
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["cas_skipped"] == 1 and rev["flipped_to_billable"] == 0


def test_systemic_error_aborts_reverse_pass():
    client = _Client({}, effects=[QboBudgetExceededError("cap")])
    svc, repo, line, _, _ = _build(candidates=[_candidate()], client=client)
    result = svc.run(REALM)
    assert result["aborted"] == "QboBudgetExceededError"
    assert repo.cas_calls == [] and line.update_calls == []


def test_candidate_read_failure_counts_batch_error_and_skips_entity():
    svc, repo, _, _, _ = _build(repo=_Repo(read_raises=RuntimeError("sql")))
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["batch_errors"] == 1 and rev["candidate_lines"] == 0


def test_purchase_unbills_with_internal_pipeline():
    svc, repo, line, ili, _ = _build("purchase", candidates=[_candidate()], live=_live_header())
    rev = svc.run(REALM)["purchase"]["reverse"]
    assert line.update_calls == [
        {"public_id": "line-pub", "row_version": "rv-1", "expense_public_id": "parent-pub",
         "is_billed": False, "_via_internal_pipeline": True}
    ]
    assert ili.calls == [("ExpenseLineItem", 900)]
    assert rev["dbo_unbilled"] == 1 and repo.cas_calls == [(1, "HasBeenBilled", "Billable")]


def test_vendor_credit_unbills_without_internal_pipeline():
    svc, repo, line, ili, _ = _build("vendor_credit", candidates=[_candidate()], live=_live_header())
    svc.run(REALM)
    assert line.update_calls == [
        {"public_id": "line-pub", "row_version": "rv-1", "bill_credit_public_id": "parent-pub",
         "is_billed": False}
    ]
    assert "_via_internal_pipeline" not in line.update_calls[0]
    assert ili.calls == [("BillCreditLineItem", 900)]


def test_forward_pass_dict_unchanged_without_reverse_candidates():
    """Existing forward keys and values are byte-identical; reverse is purely additive."""
    svc, _, _, _, _ = _build()
    result = svc.run(REALM)
    forward = {k: v for k, v in result["bill"].items() if k != "reverse"}
    assert forward == {
        "staged_billable_lines": 0, "headers_fetched": 0, "headers_missing_live": 0,
        "lines_missing_live": 0, "flipped_to_has_been_billed": 0, "flipped_to_not_billable": 0,
        "cas_skipped": 0, "dbo_heal_errors": 0, "batch_errors": 0, "unexpected_live_status": 0,
        "dbo_healed": 0,
    }
    assert list(result.keys()) == ["dry_run", "aborted", "bill", "purchase", "vendor_credit"]
    assert result["bill"]["reverse"]["candidate_lines"] == 0


# ---- R2: fail closed on a missing source id -------------------------------------------------------------

def test_unbill_with_source_id_none_errors_and_writes_nothing():
    from entities.invoice_line_item.business.service import InvoiceLineItemService

    class _Repo2:
        def read_ids_by_source_line_item(self, **kw):
            return []  # an empty result must NOT be read as "no local invoice line" when the id is unusable

    real_ili = InvoiceLineItemService(repo=_Repo2())
    svc, repo, line, _, _ = _build(
        candidates=[_candidate()], live=_live_header(), line=_LineSvc(source_id=None), ili=real_ili
    )
    from shared.authz.context import system_authz

    with system_authz():  # the real service read is system-caller only (H2)
        rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_unbill_errors"] == 1
    assert line.update_calls == [] and repo.cas_calls == []


@pytest.mark.parametrize("bad", [None, 0, -1, True, "5"])
def test_read_ids_by_source_line_item_rejects_unusable_ids(bad):
    from entities.invoice_line_item.business.service import InvoiceLineItemService

    class _Repo2:
        def read_ids_by_source_line_item(self, **kw):
            return []

    from shared.authz.context import system_authz

    with system_authz(), pytest.raises(ValueError):
        InvoiceLineItemService(repo=_Repo2()).read_ids_by_source_line_item("BillLineItem", bad)


# ---- R3: pin the unpinned guards ----------------------------------------------------------------------

@pytest.mark.parametrize("status,detail_type", [
    ("NotBillable", "AccountBasedExpenseLineDetail"),
    (None, None),
])
def test_other_live_status_is_skipped_even_when_charge_matches(status, detail_type):
    live = {"100": SimpleNamespace(id="100", line=[_live_line("10", status, CHARGE, detail_type=detail_type)])}
    svc, repo, line, ili, _ = _build(candidates=[_candidate()], live=live)
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["reverse_other_status"] == 1
    assert line.update_calls == [] and repo.cas_calls == [] and ili.calls == []


def test_dbo_parent_lookup_receives_the_run_realm():
    header = _HeaderSvc()
    svc, _, _, _, _ = _build(candidates=[_candidate()], live=_live_header(), header=header)
    svc.run(REALM)
    assert header.realms == [REALM]


@pytest.mark.parametrize("entity,cls_path", [
    ("bill", ("entities.bill_line_item.business.service", "BillLineItemService")),
    ("purchase", ("entities.expense_line_item.business.service", "ExpenseLineItemService")),
    ("vendor_credit", ("entities.bill_credit_line_item.business.service", "BillCreditLineItemService")),
])
def test_refresh_kwargs_bind_to_the_real_line_service_signature(entity, cls_path):
    import importlib
    import inspect

    cls = getattr(importlib.import_module(cls_path[0]), cls_path[1])
    sig = inspect.signature(cls.update_by_public_id)

    # un-bill
    svc, _, line, _, _ = _build(entity, candidates=[_candidate()], live=_live_header())
    svc.run(REALM)
    assert len(line.update_calls) == 1
    sig.bind(None, **line.update_calls[0])
    # forward heal
    line2 = _LineSvc(is_billed=False)
    svc2, _, _, _, _ = _build(entity, line=line2)
    heal = {"bill": svc2._try_dbo_heal_bill, "purchase": svc2._try_dbo_heal_purchase,
            "vendor_credit": svc2._try_dbo_heal_vendor_credit}[entity]
    assert heal(realm_id=REALM, header_qbo_id="100", qbo_line_id="10") is True
    sig.bind(None, **line2.update_calls[0])
    for call in (line.update_calls[0], line2.update_calls[0]):
        assert ("_via_internal_pipeline" in call) == (entity != "vendor_credit")


# ---- R4: unmapped is a warning ------------------------------------------------------------------------

def test_unmapped_logs_warning_and_still_cas(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    svc, repo, _, _, _ = _build(candidates=[_candidate()], live=_live_header(), line=_LineSvc(present=False))
    svc.run(REALM)
    msgs = [r.getMessage() for r in caplog.records if "reverse_unmapped" in r.getMessage()]
    assert len(msgs) == 1 and "100" in msgs[0] and "10" in msgs[0] and "77" in msgs[0]
    assert repo.cas_calls == [(1, "HasBeenBilled", "Billable")]


def test_unmapped_header_logs_warning(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    svc, _, _, _, _ = _build(candidates=[_candidate()], live=_live_header(), header=_HeaderSvc(present=False))
    svc.run(REALM)
    assert any("reverse_unmapped" in r.getMessage() for r in caplog.records)


# ---- R5: per-run cap on 1->0 writes -------------------------------------------------------------------

def _two_candidates():
    live = {"100": SimpleNamespace(id="100", line=[_live_line("10", "Billable"), _live_line("11", "Billable")])}
    return [_candidate(id=1, line="10"), _candidate(id=2, line="11")], live


def test_cap_defers_extra_unbills_without_cas():
    cands, live = _two_candidates()
    svc, repo, line, _, _ = _build(candidates=cands, live=live, max_unbill_per_run=1)
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_unbilled"] == 1 and rev["deferred_by_cap"] == 1
    assert len(line.update_calls) == 1 and repo.cas_calls == [(1, "HasBeenBilled", "Billable")]


def test_cap_applies_to_dry_run():
    cands, live = _two_candidates()
    svc, repo, line, _, _ = _build(candidates=cands, live=live, max_unbill_per_run=1)
    rev = svc.run(REALM, dry_run=True)["bill"]["reverse"]
    assert rev["dbo_would_unbill"] == 1 and rev["deferred_by_cap"] == 1
    assert line.update_calls == [] and repo.cas_calls == []


def test_cap_does_not_limit_flags_or_cas_only():
    cands, live = _two_candidates()
    svc, repo, _, _, recon = _build(candidates=cands, live=live, ili=_IliSvc([(3, 8)]),
                                    line=_LineSvc(), max_unbill_per_run=0)
    rev = svc.run(REALM)["bill"]["reverse"]
    assert rev["dbo_flagged"] == 2 and rev["deferred_by_cap"] == 0
    svc2, repo2, _, _, _ = _build(candidates=cands, live=live, line=_LineSvc(is_billed=False), max_unbill_per_run=0)
    rev2 = svc2.run(REALM)["bill"]["reverse"]
    assert rev2["dbo_already_unbilled"] == 2 and len(repo2.cas_calls) == 2


def test_default_cap_is_50():
    import inspect
    assert inspect.signature(QboBillableStatusRefreshService.__init__).parameters["max_unbill_per_run"].default == 50


# ---- R6: drift type registered ------------------------------------------------------------------------

def test_drift_type_registered_and_fits_column():
    from integrations.intuit.qbo.base import drift_types

    assert drift_types.DRIFT_CHARGE_UNINVOICED_LOCALLY_BILLED == "charge_uninvoiced_locally_billed"
    assert drift_types.DRIFT_CHARGE_UNINVOICED_LOCALLY_BILLED in drift_types.KNOWN_DRIFT_TYPES
    assert len(drift_types.DRIFT_CHARGE_UNINVOICED_LOCALLY_BILLED) <= 32  # [DriftType] NVARCHAR(32)
