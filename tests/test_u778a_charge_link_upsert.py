"""U-778a bite 2: the pull's line upsert persists each line's own ReimburseCharge link."""
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.bill.external.schemas import QboBillLine
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.external.schemas import QboPurchaseLine
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
from integrations.intuit.qbo.vendorcredit.external.schemas import QboVendorCreditLine


def _live(model, lid, charge):
    d = {
        "Id": lid, "LineNum": 1, "Amount": 10, "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "1"}, "BillableStatus": "Billable"},
    }
    if charge is not None:
        d["LinkedTxn"] = [{"TxnId": charge, "TxnType": "ReimburseCharge"}]
    return model(**d)


class FakeLineRepo:
    """Serves bill/purchase style line repos."""
    def __init__(self, existing_ids=()):
        self.existing = {i: SimpleNamespace(id=100 + n, row_version_bytes=b"x", qbo_line_id=i)
                         for n, i in enumerate(existing_ids)}
        self.created, self.updated = [], []

    def read_by_qbo_bill_id_and_qbo_line_id(self, qbo_bill_id, qbo_line_id):
        return self.existing.get(qbo_line_id)

    read_by_qbo_purchase_id_and_qbo_line_id = lambda self, qbo_purchase_id, qbo_line_id: self.existing.get(qbo_line_id)

    def read_by_qbo_bill_id(self, _):
        return list(self.existing.values())

    read_by_qbo_purchase_id = read_by_qbo_bill_id

    def create(self, **kw):
        self.created.append(kw)

    def update_by_id(self, **kw):
        self.updated.append(kw)

    def delete_by_id(self, _):
        pass


class FakeVcRepo:
    def __init__(self, existing_ids=()):
        self.existing = {i: SimpleNamespace(id=100 + n, public_id=None, row_version=None, created_datetime=None,
                                            modified_datetime=None, qbo_line_id=i)
                         for n, i in enumerate(existing_ids)}
        self.created, self.updated = [], []

    def read_line_by_vendor_credit_id_and_qbo_line_id(self, vc_id, qbo_line_id):
        return self.existing.get(qbo_line_id)

    def read_lines_by_vendor_credit_id(self, _):
        return list(self.existing.values())

    def create_line(self, line):
        self.created.append(line)

    def update_line_by_id(self, line, set_reimburse_charge_qbo_id=False):
        self.updated.append((line, set_reimburse_charge_qbo_id))

    def delete_line_by_id(self, _):
        pass


def _bill(repo):
    s = QboBillService(repo=object(), line_repo=repo)
    return lambda lines: s._upsert_bill_lines(1, lines), QboBillLine


def _purchase(repo):
    s = QboPurchaseService(repo=object(), line_repo=repo)
    return lambda lines: s._upsert_purchase_lines(1, lines), QboPurchaseLine


@pytest.mark.parametrize("build", [_bill, _purchase])
class TestBillPurchase:
    def test_create_passes_charge(self, build):
        repo = FakeLineRepo()
        run, model = build(repo)
        run([_live(model, "1", "68056")])
        assert repo.created[0]["reimburse_charge_qbo_id"] == "68056"

    def test_update_passes_charge_and_flag(self, build):
        repo = FakeLineRepo(["1"])
        run, model = build(repo)
        run([_live(model, "1", "68056")])
        assert repo.updated[0]["reimburse_charge_qbo_id"] == "68056"
        assert repo.updated[0]["set_reimburse_charge_qbo_id"] is True

    def test_no_link_clears(self, build):
        repo = FakeLineRepo(["1"])
        run, model = build(repo)
        run([_live(model, "1", None)])
        assert repo.updated[0]["reimburse_charge_qbo_id"] is None
        assert repo.updated[0]["set_reimburse_charge_qbo_id"] is True

    def test_two_lines_each_own_charge(self, build):
        repo = FakeLineRepo(["1"])
        run, model = build(repo)
        run([_live(model, "1", "111"), _live(model, "2", "222")])
        assert {(u["reimburse_charge_qbo_id"]) for u in repo.updated} == {"111"}
        assert repo.created[0]["qbo_line_id"] == "2" and repo.created[0]["reimburse_charge_qbo_id"] == "222"


class TestVendorCredit:
    def _run(self, repo, lines):
        QboVendorCreditService(repo=repo)._upsert_vendor_credit_lines(1, lines)

    def test_create_passes_charge(self):
        repo = FakeVcRepo()
        self._run(repo, [_live(QboVendorCreditLine, "1", "68056")])
        assert repo.created[0].reimburse_charge_qbo_id == "68056"

    def test_update_passes_charge_and_flag(self):
        repo = FakeVcRepo(["1"])
        self._run(repo, [_live(QboVendorCreditLine, "1", "68056")])
        line, flag = repo.updated[0]
        assert line.reimburse_charge_qbo_id == "68056" and flag is True

    def test_no_link_clears(self):
        repo = FakeVcRepo(["1"])
        self._run(repo, [_live(QboVendorCreditLine, "1", None)])
        line, flag = repo.updated[0]
        assert line.reimburse_charge_qbo_id is None and flag is True

    def test_two_lines_each_own_charge(self):
        repo = FakeVcRepo(["1"])
        self._run(repo, [_live(QboVendorCreditLine, "1", "111"), _live(QboVendorCreditLine, "2", "222")])
        assert repo.updated[0][0].reimburse_charge_qbo_id == "111"
        assert repo.created[0].reimburse_charge_qbo_id == "222"
