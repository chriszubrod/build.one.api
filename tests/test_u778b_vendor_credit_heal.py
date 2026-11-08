"""U-778b: forward dbo heal (IsBilled 0->1) now also runs for VendorCredit -> BillCreditLineItem."""
from decimal import Decimal
from types import SimpleNamespace

from integrations.intuit.qbo.billable_refresh.business.service import QboBillableStatusRefreshService
from integrations.intuit.qbo.billable_refresh.business.staged import StagedBillableQboLine


class _Repo:
    def __init__(self, staged=()):
        self.staged = list(staged)
        self.cas_calls = []

    def read_by_billable_status_and_realm_id(self, realm_id, billable_status="Billable"):
        return self.staged

    def read_with_uninvoiced_charge_by_realm_id(self, realm_id):
        return []

    def update_billable_status_by_id(self, *, id, expected_billable_status, billable_status):
        self.cas_calls.append((id, expected_billable_status, billable_status))
        return True


class _VCClient:
    def query_vendor_credits_by_ids(self, ids):
        line = SimpleNamespace(
            id="90", detail_type="AccountBasedExpenseLineDetail",
            account_based_expense_line_detail=SimpleNamespace(billable_status="HasBeenBilled"),
            linked_txn=[],
        )
        return [SimpleNamespace(id="900", line=[line])]


class _Empty:
    def query_bills_by_ids(self, ids):
        return []

    def query_purchases_by_ids(self, ids):
        return []


class _LineSvc:
    def __init__(self, is_billed=False):
        self.is_billed = is_billed
        self.update_calls = []

    def read_by_qbo_identity(self, parent_id, qbo_id):
        return SimpleNamespace(id=1, public_id="cli-pub", row_version="rv", is_billed=self.is_billed)

    def update_by_public_id(self, **kw):
        self.update_calls.append(kw)


def _svc(line_svc):
    staged = [StagedBillableQboLine(id=6, qbo_line_id="90", amount=Decimal("5.00"),
                                    billable_status="Billable", header_qbo_id="900", realm_id="r")]
    vc_repo = _Repo(staged)
    svc = QboBillableStatusRefreshService(
        bill_line_repo=_Repo(), purchase_line_repo=_Repo(), vendor_credit_line_repo=vc_repo,
        bill_client=_Empty(), purchase_client=_Empty(), vendor_credit_client=_VCClient(),
        bill_credit_service_factory=lambda: SimpleNamespace(
            read_by_qbo_identity=lambda qbo_id, realm_id=None: SimpleNamespace(id=3, public_id="credit-pub")
        ),
        bill_credit_line_item_service_factory=lambda: line_svc,
    )
    return svc, vc_repo


def test_vendor_credit_forward_heal_sets_is_billed_without_internal_pipeline():
    line_svc = _LineSvc()
    svc, vc_repo = _svc(line_svc)
    result = svc.run("r")
    assert line_svc.update_calls == [
        {"public_id": "cli-pub", "row_version": "rv", "bill_credit_public_id": "credit-pub", "is_billed": True}
    ]
    assert vc_repo.cas_calls == [(6, "Billable", "HasBeenBilled")]
    assert result["vendor_credit"]["dbo_healed"] == 1


def test_vendor_credit_dry_run_would_heal_no_write():
    line_svc = _LineSvc()
    svc, vc_repo = _svc(line_svc)
    result = svc.run("r", dry_run=True)
    assert line_svc.update_calls == [] and vc_repo.cas_calls == []
    assert result["vendor_credit"]["dbo_would_heal"] == 1


def test_vendor_credit_already_billed_no_write_but_cas():
    line_svc = _LineSvc(is_billed=True)
    svc, vc_repo = _svc(line_svc)
    svc.run("r")
    assert line_svc.update_calls == []
    assert vc_repo.cas_calls == [(6, "Billable", "HasBeenBilled")]
