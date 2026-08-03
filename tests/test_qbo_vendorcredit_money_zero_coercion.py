"""QBO VendorCredit money coercion guards (U-201).

``Decimal(0)`` is falsy in Python. Staging read/write and BillCredit projection paths
that used ``Decimal(str(x)) if x else None`` dropped a genuine $0.00 to ``None``.

Pure-logic tests pin each of the 11 replaced sites independently via
``shared.api.money.to_decimal_or_none``.
"""

import base64
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_credit.business.model import BillCredit
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import (
    VendorCreditBillCreditMapping,
)
from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository

_REPO = "integrations.intuit.qbo.vendorcredit.persistence.repo"

_LINE_ROW = dict(
    Id=1,
    PublicId="00000000-0000-0000-0000-000000000002",
    RowVersion=b"\x00\x00\x00\x00\x00\x00\x00\x01",
    CreatedDatetime=None,
    ModifiedDatetime=None,
    QboVendorCreditId=10,
    QboLineId="L1",
    LineNum=1,
    Description="Line",
    Amount=None,
    DetailType="AccountBasedExpenseLineDetail",
    ItemRefValue=None,
    ItemRefName=None,
    ClassRefValue=None,
    ClassRefName=None,
    UnitPrice=None,
    Qty=None,
    BillableStatus=None,
    CustomerRefValue=None,
    CustomerRefName=None,
    AccountRefValue=None,
    AccountRefName=None,
)


def _assert_money(value, expected: str):
    """Exact Decimal handoff: never dropped to None, never coerced to float.

    The ``isinstance(value, float)`` check is kept for symmetry with sibling
    money-coercion suites, not because this unit removed float() calls.
    """
    assert value is not None
    assert isinstance(value, Decimal)
    assert not isinstance(value, float)
    assert value == Decimal(expected)


@contextmanager
def _captured_proc_params(row_from_params=None):
    """Patch the repo's DB seam; yield the params dict handed to call_procedure.

    `row_from_params` optionally builds fetchone()'s row FROM the captured params.
    """
    params: dict = {}
    cursor = MagicMock()
    cursor.fetchone.side_effect = lambda: row_from_params(params) if row_from_params else None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    with patch(f"{_REPO}.get_connection") as get_conn, patch(
        f"{_REPO}.call_procedure", side_effect=lambda _c, _n, p: params.update(p)
    ):
        get_conn.return_value.__enter__.return_value = conn
        yield params


def _capture_kwargs(mock_method, returns) -> dict:
    captured: dict = {}

    def side_effect(**kwargs):
        captured.update(kwargs)
        return returns

    mock_method.side_effect = side_effect
    return captured


def _header_row(**overrides) -> SimpleNamespace:
    base = dict(
        Id=1,
        PublicId="00000000-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x00\x00\x00\x00\x00\x00\x01",
        CreatedDatetime=None,
        ModifiedDatetime=None,
        RealmId="realm",
        QboId="123",
        SyncToken="0",
        VendorRefValue="1",
        VendorRefName="V",
        TxnDate="2026-08-02",
        DocNumber="VC-0",
        TotalAmt=Decimal("0.00"),
        PrivateNote=None,
        APAccountRefValue=None,
        APAccountRefName=None,
        CurrencyRefValue=None,
        CurrencyRefName=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _qbo_vc(**overrides) -> QboVendorCredit:
    base = dict(
        id=10,
        public_id="qvc-1",
        row_version=base64.b64encode(b"\x00\x01").decode("ascii"),
        created_datetime=None,
        modified_datetime=None,
        realm_id="realm-1",
        qbo_id="999",
        sync_token="1",
        vendor_ref_value="42",
        vendor_ref_name="Vendor",
        txn_date="2026-08-02",
        doc_number="VC-0",
        total_amt=Decimal("0"),
        private_note=None,
        ap_account_ref_value=None,
        ap_account_ref_name=None,
        currency_ref_value=None,
        currency_ref_name=None,
    )
    base.update(overrides)
    return QboVendorCredit(**base)


def _bill_credit(**overrides) -> BillCredit:
    base = dict(
        id=20,
        public_id="bc-1",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=1,
        credit_date="2026-08-02",
        credit_number="VC-0",
        total_amount=Decimal("100.00"),
        memo=None,
        is_draft=False,
    )
    base.update(overrides)
    return BillCredit(**base)


def _qbo_line(**overrides) -> QboVendorCreditLine:
    base = dict(
        id=None,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_vendor_credit_id=10,
        qbo_line_id="line-1",
        line_num=1,
        description="Line",
        amount=Decimal("0"),
        detail_type="AccountBasedExpenseLineDetail",
        item_ref_value=None,
        item_ref_name=None,
        class_ref_value=None,
        class_ref_name=None,
        unit_price=Decimal("0"),
        qty=Decimal("0"),
        billable_status=None,
        customer_ref_value=None,
        customer_ref_name=None,
        account_ref_value=None,
        account_ref_name=None,
    )
    base.update(overrides)
    return QboVendorCreditLine(**base)


def _connector_with_fakes():
    mapping_repo = MagicMock()
    bill_credit_service = MagicMock()
    bill_credit_line_item_service = MagicMock()
    vendor_service = MagicMock()
    reconciliation_repo = MagicMock()
    connector = VendorCreditBillCreditConnector(
        mapping_repo=mapping_repo,
        bill_credit_service=bill_credit_service,
        bill_credit_line_item_service=bill_credit_line_item_service,
        vendor_service=vendor_service,
        reconciliation_repo=reconciliation_repo,
    )
    connector._get_vendor_public_id = MagicMock(return_value="vendor-pid")
    connector._sync_line_items = MagicMock()
    return connector, mapping_repo, bill_credit_service


def test_connector_create_total_amount_preserves_zero():
    """Site: connector CREATE ``total_amount=`` handoff (service.py CREATE path)."""
    connector, mapping_repo, bill_credit_service = _connector_with_fakes()
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None
    captured = _capture_kwargs(bill_credit_service.create, _bill_credit())

    connector.sync_from_qbo_vendor_credit(_qbo_vc(), qbo_lines=[])

    _assert_money(captured["total_amount"], "0")


def test_connector_update_total_amount_preserves_zero():
    """Site: connector UPDATE ``total_amount=`` handoff (service.py UPDATE path)."""
    connector, mapping_repo, bill_credit_service = _connector_with_fakes()
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = VendorCreditBillCreditMapping(
        id=1,
        public_id="map-1",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        qbo_vendor_credit_id=10,
        bill_credit_id=20,
    )
    existing = _bill_credit()
    bill_credit_service.read_by_id.return_value = existing
    captured = _capture_kwargs(bill_credit_service.update_by_public_id, existing)

    connector.sync_from_qbo_vendor_credit(_qbo_vc(), qbo_lines=[])

    _assert_money(captured["total_amount"], "0")


def test_repo_create_staging_total_amt_preserves_zero():
    """Site: ``create`` staging payload ``TotalAmt`` (repo.py)."""
    with _captured_proc_params() as params:
        QboVendorCreditRepository().create(_qbo_vc())

    _assert_money(params["TotalAmt"], "0")


def test_repo_update_staging_total_amt_preserves_zero():
    """Site: ``update_by_qbo_id`` staging payload ``TotalAmt`` (repo.py)."""
    with _captured_proc_params() as params:
        QboVendorCreditRepository().update_by_qbo_id(_qbo_vc())

    _assert_money(params["TotalAmt"], "0")


def test_repo_create_line_staging_money_preserves_zero():
    """Sites: ``create_line`` payload ``Amount`` / ``UnitPrice`` / ``Qty`` (repo.py)."""
    with _captured_proc_params() as params:
        QboVendorCreditRepository().create_line(_qbo_line())

    _assert_money(params["Amount"], "0")
    _assert_money(params["UnitPrice"], "0")
    _assert_money(params["Qty"], "0")


def test_repo_from_db_total_amt_preserves_zero():
    """Site: ``_from_db`` ``total_amt=`` read mapping (repo.py)."""
    mapped = QboVendorCreditRepository()._from_db(_header_row(TotalAmt=Decimal("0.00")))
    _assert_money(mapped.total_amt, "0.00")


@pytest.mark.parametrize(
    "field,attr",
    [
        ("Amount", "amount"),
        ("UnitPrice", "unit_price"),
        ("Qty", "qty"),
    ],
)
def test_repo_line_from_db_money_preserves_zero(field, attr):
    """Sites: ``_line_from_db`` line money read mappings (repo.py, one param each)."""
    row = SimpleNamespace(**{**_LINE_ROW, field: Decimal("0")})
    mapped = QboVendorCreditRepository()._line_from_db(row)
    _assert_money(getattr(mapped, attr), "0")


def test_end_to_end_zero_credit_creates_with_zero_total():
    """Chained pull-chain spec: repo write + repo read + connector CREATE together.

    Scope-6 could not support this composition test; reverting ANY ONE of repo.py write
    ``TotalAmt``, repo.py ``_from_db`` ``total_amt``, or the connector CREATE site to the
    truthy ``if x else None`` form must turn THIS test RED (deliberately non-isolated;
    the other nine stay one-site-each).
    """
    # Fidelity: CreateQboVendorCredit unconditionally INSERT VALUES (…, @TotalAmt, …)
    # and OUTPUTs the inserted row — the DB stores exactly what the caller passes.

    def _row_echoing_insert(p):
        # The composition must read the value the WRITE site produced, never the
        # _header_row default — otherwise a payload that drops TotalAmt would
        # still go green.
        assert "TotalAmt" in p, "create payload must carry TotalAmt"
        return _header_row(Id=10, **p)

    with _captured_proc_params(_row_echoing_insert) as params:
        staged = QboVendorCreditRepository().create(_qbo_vc(total_amt=Decimal("0")))

    connector, mapping_repo, bill_credit_service = _connector_with_fakes()
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None
    captured = _capture_kwargs(bill_credit_service.create, _bill_credit())

    connector.sync_from_qbo_vendor_credit(staged, qbo_lines=[])

    _assert_money(captured["total_amount"], "0")
