"""Pure-logic tests for U-718 QBO billable-status refresh sweep (staging CAS + dbo heal)."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboUnexpectedError
from integrations.intuit.qbo.bill.external.schemas import (
    QboAccountBasedExpenseLineDetail,
    QboBill,
    QboBillLine,
)
from integrations.intuit.qbo.billable_refresh.business.line_utils import extract_billable_status
from integrations.intuit.qbo.billable_refresh.business.service import QboBillableStatusRefreshService


def _bill_line(qbo_line_id: str, billable_status: str) -> QboBillLine:
    detail = QboAccountBasedExpenseLineDetail(billable_status=billable_status)
    return QboBillLine(
        id=qbo_line_id,
        detail_type="AccountBasedExpenseLineDetail",
        account_based_expense_line_detail=detail,
    )


def _live_bill(qbo_id: str, lines: list[QboBillLine]):
    return SimpleNamespace(id=qbo_id, line=lines)


class _StagedLine:
    def __init__(
        self,
        *,
        id: int,
        qbo_line_id: str,
        header_qbo_id: str,
        amount="10.00",
    ):
        self.id = id
        self.qbo_line_id = qbo_line_id
        self.amount = Decimal(amount)
        self.billable_status = "Billable"
        self.header_qbo_id = header_qbo_id
        self.realm_id = "realm-1"


class _FakeBillLineRepo:
    def __init__(self, staged_lines=None):
        self.staged_lines = list(staged_lines or [])
        self.cas_calls = []

    def read_by_billable_status_and_realm_id(self, realm_id, billable_status="Billable"):
        assert realm_id == "realm-1"
        assert billable_status == "Billable"
        return self.staged_lines

    def update_billable_status_by_id(self, *, id, expected_billable_status, billable_status):
        self.cas_calls.append(
            {
                "id": id,
                "expected": expected_billable_status,
                "new": billable_status,
            }
        )
        return True


class _FakePurchaseLineRepo:
    def __init__(self, staged_lines=None):
        self.staged_lines = list(staged_lines or [])
        self.cas_calls = []

    def read_by_billable_status_and_realm_id(self, realm_id, billable_status="Billable"):
        return self.staged_lines

    def update_billable_status_by_id(self, *, id, expected_billable_status, billable_status):
        self.cas_calls.append(
            {"id": id, "expected": expected_billable_status, "new": billable_status}
        )
        return True


class _FakeVendorCreditLineRepo:
    def __init__(self, staged_lines=None):
        self.staged_lines = list(staged_lines or [])
        self.cas_calls = []

    def read_by_billable_status_and_realm_id(self, realm_id, billable_status="Billable"):
        return self.staged_lines

    def update_billable_status_by_id(self, *, id, expected_billable_status, billable_status):
        self.cas_calls.append(
            {"id": id, "expected": expected_billable_status, "new": billable_status}
        )
        return True


class _FakeBillClient:
    def __init__(self, bills_by_id=None, batch_side_effect=None):
        self._bills = bills_by_id or {}
        self.batch_side_effect = batch_side_effect
        self.query_calls = []

    def query_bills_by_ids(self, ids):
        self.query_calls.append(list(ids))
        if self.batch_side_effect:
            fn = self.batch_side_effect[len(self.query_calls) - 1]
            if isinstance(fn, Exception):
                raise fn
        return [_FakeBillClient._as_qbo(b) for b in ids if b in self._bills for _ in [0]]

    @staticmethod
    def _as_qbo(header_qbo_id):
        raise NotImplementedError


class _RecordingBillClient:
    """Returns configured live bills keyed by header Qbo id."""

    def __init__(self, live_by_header_id, *, batch_side_effect=None):
        self.live_by_header_id = live_by_header_id
        self.query_calls = []
        self.batch_side_effect = batch_side_effect

    def query_bills_by_ids(self, ids):
        self.query_calls.append(list(ids))
        if self.batch_side_effect is not None:
            idx = len(self.query_calls) - 1
            if idx < len(self.batch_side_effect):
                effect = self.batch_side_effect[idx]
                if isinstance(effect, Exception):
                    raise effect
        return [self.live_by_header_id[i] for i in ids if i in self.live_by_header_id]


class _EmptyEntityClient:
    def query_purchases_by_ids(self, ids):
        return []

    def query_vendor_credits_by_ids(self, ids):
        return []


class _FakeBillService:
    def __init__(self, bill):
        self._bill = bill

    def read_by_qbo_identity(self, qbo_id, realm_id):
        if self._bill and self._bill.qbo_id == qbo_id:
            return self._bill
        return None


class _FakeBillLineItemService:
    def __init__(self, line_item):
        self._line = line_item
        self.update_calls = []

    def read_by_qbo_identity(self, bill_id, qbo_id):
        if self._line and self._line.qbo_line_id == qbo_id:
            return self._line
        return None

    def update_by_public_id(self, **kwargs):
        self.update_calls.append(kwargs)


def _service_for_bill_only(
    *,
    staged,
    live_by_header,
    bill=None,
    bill_line_item=None,
    cas_returns=True,
    dry_run=False,
    batch_side_effect=None,
    purchase_staged=None,
    vendor_staged=None,
):
    bill_repo = _FakeBillLineRepo(staged)
    if not cas_returns:
        bill_repo.update_billable_status_by_id = lambda **kw: False

    purchase_repo = _FakePurchaseLineRepo(purchase_staged or [])
    vc_repo = _FakeVendorCreditLineRepo(vendor_staged or [])

    bill_client = _RecordingBillClient(live_by_header, batch_side_effect=batch_side_effect)
    purchase_client = _EmptyEntityClient()
    vc_client = _EmptyEntityClient()

    bli_svc = _FakeBillLineItemService(bill_line_item)

    svc = QboBillableStatusRefreshService(
        bill_line_repo=bill_repo,
        purchase_line_repo=purchase_repo,
        vendor_credit_line_repo=vc_repo,
        bill_client=bill_client,
        purchase_client=purchase_client,
        vendor_credit_client=vc_client,
        bill_service_factory=lambda: _FakeBillService(bill),
        bill_line_item_service_factory=lambda: bli_svc,
        expense_service_factory=lambda: None,
        expense_line_item_service_factory=lambda: None,
    )
    return svc, bill_repo, bill_client, bli_svc


def test_has_been_billed_live_updates_cas_sibling_unchanged():
    staged = [
        _StagedLine(id=1, qbo_line_id="10", header_qbo_id="100"),
        _StagedLine(id=2, qbo_line_id="11", header_qbo_id="100"),
    ]
    live = _live_bill(
        "100",
        [
            _bill_line("10", "HasBeenBilled"),
            _bill_line("11", "Billable"),
        ],
    )
    bill = SimpleNamespace(id=5, public_id="bill-pub", qbo_id="100")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=5,
        qbo_line_id="10",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"100": live},
        bill=bill,
        bill_line_item=bli,
    )

    result = svc.run("realm-1", dry_run=False)

    assert len(repo.cas_calls) == 1
    assert repo.cas_calls[0] == {
        "id": 1,
        "expected": "Billable",
        "new": "HasBeenBilled",
    }
    assert bli_svc.update_calls == [
        {
            "public_id": "bli-pub",
            "row_version": "rv1",
            "bill_public_id": "bill-pub",
            "is_billed": True,
            "_via_internal_pipeline": True,
        }
    ]
    assert result["bill"]["flipped_to_has_been_billed"] == 1
    assert result["bill"]["dbo_healed"] == 1


def test_not_billable_live_updates_staging_no_dbo_heal():
    staged = [_StagedLine(id=3, qbo_line_id="20", header_qbo_id="200")]
    live = _live_bill("200", [_bill_line("20", "NotBillable")])
    bill = SimpleNamespace(id=6, public_id="bill-pub", qbo_id="200")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=6,
        qbo_line_id="20",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"200": live},
        bill=bill,
        bill_line_item=bli,
    )
    result = svc.run("realm-1", dry_run=False)

    assert repo.cas_calls == [{"id": 3, "expected": "Billable", "new": "NotBillable"}]
    assert bli_svc.update_calls == []
    assert result["bill"]["flipped_to_not_billable"] == 1
    assert result["bill"].get("dbo_healed", 0) == 0


@pytest.mark.parametrize("is_billed", [False, None])
def test_dbo_heal_sets_is_billed_when_false_or_none(is_billed):
    staged = [_StagedLine(id=4, qbo_line_id="30", header_qbo_id="300")]
    live = _live_bill("300", [_bill_line("30", "HasBeenBilled")])
    bill = SimpleNamespace(id=7, public_id="bill-pub", qbo_id="300")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=7,
        qbo_line_id="30",
        is_billed=is_billed,
    )
    svc, _, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"300": live},
        bill=bill,
        bill_line_item=bli,
    )
    svc.run("realm-1", dry_run=False)
    assert len(bli_svc.update_calls) == 1
    assert bli_svc.update_calls[0]["is_billed"] is True


def test_dbo_heal_skips_when_already_billed():
    staged = [_StagedLine(id=5, qbo_line_id="31", header_qbo_id="301")]
    live = _live_bill("301", [_bill_line("31", "HasBeenBilled")])
    bill = SimpleNamespace(id=8, public_id="bill-pub", qbo_id="301")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=8,
        qbo_line_id="31",
        is_billed=True,
    )
    svc, _, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"301": live},
        bill=bill,
        bill_line_item=bli,
    )
    result = svc.run("realm-1", dry_run=False)
    assert bli_svc.update_calls == []
    assert result["bill"]["dbo_healed"] == 0


def test_vendor_credit_flip_no_dbo_heal():
    staged = [_StagedLine(id=9, qbo_line_id="90", header_qbo_id="900")]
    bill_repo = _FakeBillLineRepo([])
    purchase_repo = _FakePurchaseLineRepo([])
    vc_repo = _FakeVendorCreditLineRepo(staged)

    class _VCClient:
        query_calls = []

        def query_vendor_credits_by_ids(self, ids):
            _VCClient.query_calls.append(list(ids))
            from integrations.intuit.qbo.vendorcredit.external.schemas import (
                QboAccountBasedExpenseLineDetail,
                QboVendorCreditLine,
            )

            detail = QboAccountBasedExpenseLineDetail(billable_status="HasBeenBilled")
            line = QboVendorCreditLine(
                id="90",
                detail_type="AccountBasedExpenseLineDetail",
                account_based_expense_line_detail=detail,
            )
            return [SimpleNamespace(id="900", line=[line])]

    svc = QboBillableStatusRefreshService(
        bill_line_repo=bill_repo,
        purchase_line_repo=purchase_repo,
        vendor_credit_line_repo=vc_repo,
        bill_client=_EmptyEntityClient(),
        purchase_client=_EmptyEntityClient(),
        vendor_credit_client=_VCClient(),
    )
    dbo_heal_called = {"n": 0}
    svc._try_dbo_heal_bill = lambda **kw: dbo_heal_called.__setitem__("n", dbo_heal_called["n"] + 1)
    svc._try_dbo_heal_purchase = lambda **kw: dbo_heal_called.__setitem__("n", dbo_heal_called["n"] + 1)

    result = svc.run("realm-1", dry_run=False)
    assert vc_repo.cas_calls
    assert dbo_heal_called["n"] == 0
    assert result["vendor_credit"]["flipped_to_has_been_billed"] == 1


def test_dry_run_dbo_would_heal_only_when_dbo_line_unbilled():
    staged = [_StagedLine(id=6, qbo_line_id="40", header_qbo_id="400")]
    live = _live_bill("400", [_bill_line("40", "HasBeenBilled")])
    bill = SimpleNamespace(id=9, public_id="bill-pub", qbo_id="400")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=9,
        qbo_line_id="40",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"400": live},
        bill=bill,
        bill_line_item=bli,
    )
    result = svc.run("realm-1", dry_run=True)
    assert repo.cas_calls == []
    assert bli_svc.update_calls == []
    assert result["bill"]["dbo_would_heal"] == 1


def test_dry_run_dbo_would_heal_skips_already_billed():
    staged = [_StagedLine(id=61, qbo_line_id="41", header_qbo_id="401")]
    live = _live_bill("401", [_bill_line("41", "HasBeenBilled")])
    bill = SimpleNamespace(id=91, public_id="bill-pub", qbo_id="401")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=91,
        qbo_line_id="41",
        is_billed=True,
    )
    svc, _, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"401": live},
        bill=bill,
        bill_line_item=bli,
    )
    result = svc.run("realm-1", dry_run=True)
    assert result["bill"]["dbo_would_heal"] == 0
    assert bli_svc.update_calls == []


def test_dry_run_dbo_would_heal_skips_missing_dbo_line():
    staged = [_StagedLine(id=62, qbo_line_id="42", header_qbo_id="402")]
    live = _live_bill("402", [_bill_line("42", "HasBeenBilled")])
    bill = SimpleNamespace(id=92, public_id="bill-pub", qbo_id="402")
    svc, _, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"402": live},
        bill=bill,
        bill_line_item=None,
    )
    result = svc.run("realm-1", dry_run=True)
    assert result["bill"]["dbo_would_heal"] == 0
    assert bli_svc.update_calls == []


def test_dry_run_dbo_read_failure_counted_and_next_line_still_evaluated():
    staged = [
        _StagedLine(id=63, qbo_line_id="43", header_qbo_id="500"),
        _StagedLine(id=64, qbo_line_id="44", header_qbo_id="501"),
    ]
    live = {
        "500": _live_bill("500", [_bill_line("43", "HasBeenBilled")]),
        "501": _live_bill("501", [_bill_line("44", "HasBeenBilled")]),
    }
    bill = SimpleNamespace(id=93, public_id="bill-pub", qbo_id="501")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=93,
        qbo_line_id="44",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header=live,
        bill=bill,
        bill_line_item=bli,
    )

    class _RaisingForHeader500(_FakeBillService):
        def read_by_qbo_identity(self, qbo_id, realm_id):
            if qbo_id == "500":
                raise RuntimeError("simulated dbo read failure")
            return super().read_by_qbo_identity(qbo_id, realm_id)

    svc._bill_service_factory = lambda: _RaisingForHeader500(bill)
    result = svc.run("realm-1", dry_run=True)
    assert result["bill"]["dbo_heal_errors"] == 1
    assert result["bill"]["dbo_would_heal"] == 1
    assert result["bill"]["flipped_to_has_been_billed"] == 2
    assert repo.cas_calls == []
    assert bli_svc.update_calls == []
    assert "purchase" in result and "vendor_credit" in result


def test_dry_run_no_writes_counts_dbo_would_heal():
    staged = [_StagedLine(id=6, qbo_line_id="40", header_qbo_id="400")]
    live = _live_bill("400", [_bill_line("40", "HasBeenBilled")])
    bill = SimpleNamespace(id=9, public_id="bill-pub", qbo_id="400")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=9,
        qbo_line_id="40",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"400": live},
        bill=bill,
        bill_line_item=bli,
    )
    result = svc.run("realm-1", dry_run=True)
    assert repo.cas_calls == []
    assert bli_svc.update_calls == []
    assert result["dry_run"] is True
    assert result["bill"]["dbo_would_heal"] == 1
    assert "dbo_healed" not in result["bill"] or result["bill"].get("dbo_healed", 0) == 0


def test_header_missing_live_counted_no_write():
    staged = [_StagedLine(id=7, qbo_line_id="50", header_qbo_id="500")]
    svc, repo, _, _ = _service_for_bill_only(staged=staged, live_by_header={})
    result = svc.run("realm-1", dry_run=False)
    assert repo.cas_calls == []
    assert result["bill"]["headers_missing_live"] == 1


def test_line_missing_from_live_header_counted_no_write():
    staged = [_StagedLine(id=8, qbo_line_id="60", header_qbo_id="600")]
    live = _live_bill("600", [_bill_line("99", "HasBeenBilled")])
    svc, repo, _, _ = _service_for_bill_only(
        staged=staged, live_by_header={"600": live}
    )
    result = svc.run("realm-1", dry_run=False)
    assert repo.cas_calls == []
    assert result["bill"]["lines_missing_live"] == 1


def test_cas_skipped_no_dbo_heal():
    staged = [_StagedLine(id=10, qbo_line_id="70", header_qbo_id="700")]
    live = _live_bill("700", [_bill_line("70", "HasBeenBilled")])
    bill = SimpleNamespace(id=10, public_id="bill-pub", qbo_id="700")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=10,
        qbo_line_id="70",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"700": live},
        bill=bill,
        bill_line_item=bli,
        cas_returns=False,
    )
    result = svc.run("realm-1", dry_run=False)
    assert result["bill"]["cas_skipped"] == 1
    assert len(bli_svc.update_calls) == 1
    assert bli_svc.update_calls[0]["is_billed"] is True


def test_systemic_error_on_second_batch_aborts_run():
    staged = [
        _StagedLine(id=i, qbo_line_id=str(i), header_qbo_id=str(1000 + i))
        for i in range(1, 151)
    ]
    live_map = {
        str(1000 + i): _live_bill(str(1000 + i), [_bill_line(str(i), "Billable")])
        for i in range(1, 151)
    }
    svc, _, client, _ = _service_for_bill_only(
        staged=staged,
        live_by_header=live_map,
        batch_side_effect=[None, QboBudgetExceededError("budget")],
    )
    result = svc.run("realm-1", dry_run=False)
    assert len(client.query_calls) == 2
    assert result["aborted"] is not None
    assert "budget" in result["aborted"].lower() or "Budget" in result["aborted"]
    assert result["bill"]["batch_errors"] >= 0


def test_extract_billable_status_from_detail_types():
    item_detail = SimpleNamespace(billable_status="HasBeenBilled")
    line_item = SimpleNamespace(
        detail_type="ItemBasedExpenseLineDetail",
        item_based_expense_line_detail=item_detail,
        account_based_expense_line_detail=None,
    )
    assert extract_billable_status(line_item) == "HasBeenBilled"

    acct_detail = SimpleNamespace(billable_status="NotBillable")
    line_acct = SimpleNamespace(
        detail_type="AccountBasedExpenseLineDetail",
        item_based_expense_line_detail=None,
        account_based_expense_line_detail=acct_detail,
    )
    assert extract_billable_status(line_acct) == "NotBillable"


def test_query_bills_by_ids_batches_and_strict(monkeypatch):
    from integrations.intuit.qbo.bill.external.client import QboBillClient

    calls = []

    class _Http:
        def get(self, path, *, params=None, operation_name=None):
            calls.append(params["query"])
            return {
                "QueryResponse": {
                    "Bill": [{"Id": "1", "SyncToken": "0", "Line": []}],
                }
            }

        def close(self):
            pass

    client = QboBillClient(realm_id="r1", http_client=_Http())
    ids = [str(i) for i in range(1, 251)]
    client.query_bills_by_ids(ids)
    assert len(calls) == 3

    class _BadHttp:
        def get(self, path, *, params=None, operation_name=None):
            return {}

        def close(self):
            pass

    bad = QboBillClient(realm_id="r1", http_client=_BadHttp())
    with pytest.raises(QboUnexpectedError):
        bad.query_bills_by_ids(["1"])

    class _HttpSkip:
        def __init__(self):
            self.queries = []

        def get(self, path, *, params=None, operation_name=None):
            self.queries.append(params["query"])
            return {"QueryResponse": {"Bill": []}}

        def close(self):
            pass

    http_skip = _HttpSkip()
    skip_client = QboBillClient(realm_id="r1", http_client=http_skip)
    skip_client.query_bills_by_ids(["123", "abc", "456"])
    assert len(http_skip.queries) == 1
    assert "'123'" in http_skip.queries[0]
    assert "'456'" in http_skip.queries[0]
    assert "abc" not in http_skip.queries[0]


def test_heal_raises_run1_no_cas_run2_retries_heal_and_cas():
    staged = [_StagedLine(id=11, qbo_line_id="80", header_qbo_id="800")]
    live = _live_bill("800", [_bill_line("80", "HasBeenBilled")])
    bill = SimpleNamespace(id=11, public_id="bill-pub", qbo_id="800")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=11,
        qbo_line_id="80",
        is_billed=False,
    )
    svc, repo, _, bli_svc = _service_for_bill_only(
        staged=staged,
        live_by_header={"800": live},
        bill=bill,
        bill_line_item=bli,
    )
    heal_attempts = {"n": 0}
    real_heal = svc._try_dbo_heal_bill

    def heal_raises_then_ok(**kw):
        heal_attempts["n"] += 1
        if heal_attempts["n"] == 1:
            raise RuntimeError("heal failed")
        return real_heal(**kw)

    svc._try_dbo_heal_bill = heal_raises_then_ok

    result1 = svc.run("realm-1", dry_run=False)
    assert result1["bill"]["dbo_heal_errors"] == 1
    assert repo.cas_calls == []
    assert bli_svc.update_calls == []

    result2 = svc.run("realm-1", dry_run=False)
    assert result2["bill"]["dbo_heal_errors"] == 0
    assert result2["bill"]["dbo_healed"] == 1
    assert len(repo.cas_calls) == 1
    assert repo.cas_calls[0]["new"] == "HasBeenBilled"
    assert len(bli_svc.update_calls) == 1


def test_heal_precedes_cas_call_order():
    staged = [_StagedLine(id=12, qbo_line_id="81", header_qbo_id="801")]
    live = _live_bill("801", [_bill_line("81", "HasBeenBilled")])
    bill = SimpleNamespace(id=12, public_id="bill-pub", qbo_id="801")
    bli = SimpleNamespace(
        public_id="bli-pub",
        row_version="rv1",
        bill_id=12,
        qbo_line_id="81",
        is_billed=False,
    )
    svc, repo, _, _ = _service_for_bill_only(
        staged=staged,
        live_by_header={"801": live},
        bill=bill,
        bill_line_item=bli,
    )
    order: list[str] = []
    real_heal = svc._try_dbo_heal_bill

    def recording_heal(**kw):
        order.append("heal")
        return real_heal(**kw)

    original_cas = repo.update_billable_status_by_id

    def recording_cas(**kw):
        order.append("cas")
        return original_cas(**kw)

    repo.update_billable_status_by_id = recording_cas
    svc._try_dbo_heal_bill = recording_heal

    svc.run("realm-1", dry_run=False)
    assert order == ["heal", "cas"]


class _FakeExpenseService:
    def __init__(self, expense):
        self._expense = expense

    def read_by_qbo_identity(self, qbo_id, realm_id):
        if self._expense and self._expense.qbo_id == qbo_id:
            return self._expense
        return None


class _FakeExpenseLineItemService:
    def __init__(self, line_item):
        self._line = line_item
        self.update_calls = []

    def read_by_qbo_identity(self, expense_id, qbo_id):
        if self._line and self._line.qbo_line_id == qbo_id:
            return self._line
        return None

    def update_by_public_id(self, **kwargs):
        self.update_calls.append(kwargs)


def test_purchase_heal_updates_expense_line_item_is_billed():
    from integrations.intuit.qbo.purchase.external.schemas import (
        QboAccountBasedExpenseLineDetail,
        QboPurchaseLine,
    )

    staged = [_StagedLine(id=20, qbo_line_id="p1", header_qbo_id="P100")]
    detail = QboAccountBasedExpenseLineDetail(billable_status="HasBeenBilled")
    pline = QboPurchaseLine(
        id="p1",
        detail_type="AccountBasedExpenseLineDetail",
        account_based_expense_line_detail=detail,
    )
    live_purchase = SimpleNamespace(id="P100", line=[pline])

    purchase_repo = _FakePurchaseLineRepo(staged)
    bill_repo = _FakeBillLineRepo([])
    vc_repo = _FakeVendorCreditLineRepo([])

    class _PurchaseClient:
        def query_purchases_by_ids(self, ids):
            return [live_purchase] if "P100" in ids else []

    expense = SimpleNamespace(id=50, public_id="exp-pub", qbo_id="P100")
    eli = SimpleNamespace(
        public_id="eli-pub",
        row_version="rv2",
        expense_id=50,
        qbo_line_id="p1",
        is_billed=False,
    )
    eli_svc = _FakeExpenseLineItemService(eli)

    svc = QboBillableStatusRefreshService(
        bill_line_repo=bill_repo,
        purchase_line_repo=purchase_repo,
        vendor_credit_line_repo=vc_repo,
        bill_client=_EmptyEntityClient(),
        purchase_client=_PurchaseClient(),
        vendor_credit_client=_EmptyEntityClient(),
        expense_service_factory=lambda: _FakeExpenseService(expense),
        expense_line_item_service_factory=lambda: eli_svc,
    )
    result = svc.run("realm-1", dry_run=False)

    assert eli_svc.update_calls == [
        {
            "public_id": "eli-pub",
            "row_version": "rv2",
            "expense_public_id": "exp-pub",
            "is_billed": True,
            "_via_internal_pipeline": True,
        }
    ]
    assert result["purchase"]["dbo_healed"] == 1


def test_systemic_abort_on_first_bill_batch_skips_later_entities():
    staged_bills = [
        _StagedLine(id=i, qbo_line_id=str(i), header_qbo_id=str(2000 + i))
        for i in range(1, 102)
    ]  # 101 headers → two bill batches; systemic on the first
    purchase_staged = [_StagedLine(id=999, qbo_line_id="px", header_qbo_id="P999")]
    live_map = {
        str(2000 + i): _live_bill(str(2000 + i), [_bill_line(str(i), "Billable")])
        for i in range(1, 102)
    }  # matches 101 staged headers
    bill_repo = _FakeBillLineRepo(staged_bills)
    purchase_repo = _FakePurchaseLineRepo(purchase_staged)
    vc_repo = _FakeVendorCreditLineRepo(
        [_StagedLine(id=888, qbo_line_id="vx", header_qbo_id="V888")]
    )
    purchase_reads: list[str] = []

    class _TrackingPurchaseRepo(_FakePurchaseLineRepo):
        def read_by_billable_status_and_realm_id(self, realm_id, billable_status="Billable"):
            purchase_reads.append("read")
            return super().read_by_billable_status_and_realm_id(realm_id, billable_status)

    purchase_repo = _TrackingPurchaseRepo(purchase_staged)

    bill_client = _RecordingBillClient(
        live_map,
        batch_side_effect=[QboBudgetExceededError("budget")],
    )

    class _PurchaseClient:
        query_calls = []

        def query_purchases_by_ids(self, ids):
            _PurchaseClient.query_calls.append(list(ids))
            return []

    class _VCClient:
        query_calls = []

        def query_vendor_credits_by_ids(self, ids):
            _VCClient.query_calls.append(list(ids))
            return []

    svc = QboBillableStatusRefreshService(
        bill_line_repo=bill_repo,
        purchase_line_repo=purchase_repo,
        vendor_credit_line_repo=vc_repo,
        bill_client=bill_client,
        purchase_client=_PurchaseClient(),
        vendor_credit_client=_VCClient(),
    )
    result = svc.run("realm-1", dry_run=False)

    assert len(bill_client.query_calls) == 1
    assert result["aborted"] is not None
    assert purchase_reads == []
    assert _PurchaseClient.query_calls == []
    assert _VCClient.query_calls == []
    assert "purchase" not in result


def test_batch_fetch_drives_cas_for_has_been_billed_line_ids():
    staged = [
        _StagedLine(id=41, qbo_line_id="L41", header_qbo_id="H41"),
        _StagedLine(id=42, qbo_line_id="L42", header_qbo_id="H41"),
        _StagedLine(id=43, qbo_line_id="L43", header_qbo_id="H42"),
    ]
    live_h41 = _live_bill(
        "H41",
        [
            _bill_line("L41", "HasBeenBilled"),
            _bill_line("L42", "Billable"),
        ],
    )
    live_h42 = _live_bill("H42", [_bill_line("L43", "NotBillable")])
    svc, repo, _, _ = _service_for_bill_only(
        staged=staged,
        live_by_header={"H41": live_h41, "H42": live_h42},
        bill=SimpleNamespace(id=1, public_id="b", qbo_id="H41"),
        bill_line_item=SimpleNamespace(
            public_id="x",
            row_version="rv",
            bill_id=1,
            qbo_line_id="L41",
            is_billed=True,
        ),
    )
    svc.run("realm-1", dry_run=False)
    cas_ids = sorted(c["id"] for c in repo.cas_calls)
    assert cas_ids == [41, 43]


@pytest.mark.parametrize(
    "live_status",
    ["", "garbage", None],
)
def test_unknown_live_status_no_write_counted(live_status):
    staged = [_StagedLine(id=51, qbo_line_id="U1", header_qbo_id="HU1")]
    if live_status is None:
        acct_detail = None
    else:
        acct_detail = SimpleNamespace(billable_status=live_status)
    live_line = SimpleNamespace(
        id="U1",
        detail_type="AccountBasedExpenseLineDetail",
        item_based_expense_line_detail=None,
        account_based_expense_line_detail=acct_detail,
    )
    live = SimpleNamespace(id="HU1", line=[live_line])

    svc, repo, _, _ = _service_for_bill_only(
        staged=staged,
        live_by_header={"HU1": live},
    )
    result = svc.run("realm-1", dry_run=False)
    assert repo.cas_calls == []
    assert result["bill"]["unexpected_live_status"] == 1


def test_admin_billable_refresh_closes_clients_on_success(monkeypatch):
    from shared.api import admin as admin_module

    exits: list[str] = []

    class _FakeClient:
        def __init__(self, name, **kw):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            exits.append(self.name)

    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda **kw: _FakeClient("bill", **kw),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient",
        lambda **kw: _FakeClient("purchase", **kw),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.external.client.QboVendorCreditClient",
        lambda **kw: _FakeClient("vc", **kw),
    )

    class _Auth:
        realm_id = "realm-x"

    monkeypatch.setattr(
        "integrations.intuit.qbo.auth.business.service.QboAuthService",
        lambda: SimpleNamespace(ensure_valid_token=lambda: _Auth()),
    )
    monkeypatch.setattr(admin_module, "qbo_sync_lock", lambda *a, **k: _NullLock())

    run_calls = {"n": 0}

    class _Svc:
        def __init__(self, **kw):
            pass

        def run(self, realm_id, *, dry_run=False):
            run_calls["n"] += 1
            return {"ok": True}

    monkeypatch.setattr(
        "integrations.intuit.qbo.billable_refresh.business.service.QboBillableStatusRefreshService",
        _Svc,
    )

    from shared.api.admin import reconcile_qbo_billable_status_router

    asyncio.run(reconcile_qbo_billable_status_router(dry_run=False))
    assert sorted(exits) == ["bill", "purchase", "vc"]
    assert run_calls["n"] == 1


def test_admin_billable_refresh_closes_clients_when_run_raises(monkeypatch):
    from shared.api import admin as admin_module

    exits: list[str] = []

    class _FakeClient:
        def __init__(self, name, **kw):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            exits.append(self.name)

    monkeypatch.setattr(
        "integrations.intuit.qbo.bill.external.client.QboBillClient",
        lambda **kw: _FakeClient("bill", **kw),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient",
        lambda **kw: _FakeClient("purchase", **kw),
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.vendorcredit.external.client.QboVendorCreditClient",
        lambda **kw: _FakeClient("vc", **kw),
    )

    class _Auth:
        realm_id = "realm-x"

    monkeypatch.setattr(
        "integrations.intuit.qbo.auth.business.service.QboAuthService",
        lambda: SimpleNamespace(ensure_valid_token=lambda: _Auth()),
    )
    monkeypatch.setattr(admin_module, "qbo_sync_lock", lambda *a, **k: _NullLock())

    class _Svc:
        def __init__(self, **kw):
            pass

        def run(self, realm_id, *, dry_run=False):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "integrations.intuit.qbo.billable_refresh.business.service.QboBillableStatusRefreshService",
        _Svc,
    )

    from fastapi import HTTPException

    from shared.api.admin import reconcile_qbo_billable_status_router

    with pytest.raises(HTTPException):
        asyncio.run(reconcile_qbo_billable_status_router(dry_run=False))
    assert sorted(exits) == ["bill", "purchase", "vc"]


class _NullLock:
    def __enter__(self):
        return True

    def __exit__(self, *args):
        pass
