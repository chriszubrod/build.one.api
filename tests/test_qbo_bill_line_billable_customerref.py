from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.external.schemas import QboReferenceType


def _build_connector():
    connector = BillBillConnector(
        mapping_repo=Mock(),
        bill_service=Mock(),
        vendor_service=Mock(),
        vendor_vendor_repo=Mock(),
        qbo_vendor_repo=Mock(),
        qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(),
        bill_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(),
        qbo_item_repo=Mock(),
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
        qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(),
        qbo_term_repo=Mock(),
    )
    # Item mapping always resolves so the SubCostCode->Item gate is passed and we
    # isolate the billable/CustomerRef branch under test.
    connector._get_qbo_item_ref = Mock(return_value=QboReferenceType(value="item-1", name="Item 1"))
    return connector


def _line(**overrides):
    defaults = {
        "id": 500,
        "sub_cost_code_id": 7,
        "project_id": 42,
        "is_billable": True,
        "is_billed": False,
        "markup": None,
        "amount": Decimal("100.00"),
        "quantity": 1,
        "rate": Decimal("100.00"),
        "description": "labor",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_billable_with_project_but_no_customerref_raises():
    connector = _build_connector()
    connector._get_qbo_customer_ref = Mock(return_value=None)
    with pytest.raises(ValueError, match="no QBO CustomerRef mapping"):
        connector._build_qbo_line(_line(project_id=42, is_billable=True), line_num=1)


def test_billable_without_project_is_notbillable_no_raise():
    connector = _build_connector()
    connector._get_qbo_customer_ref = Mock(return_value=None)
    line = connector._build_qbo_line(_line(project_id=None, is_billable=True), line_num=1)
    assert line.item_based_expense_line_detail.billable_status == "NotBillable"


def test_non_billable_is_notbillable():
    connector = _build_connector()
    connector._get_qbo_customer_ref = Mock(return_value=None)
    line = connector._build_qbo_line(_line(project_id=42, is_billable=False), line_num=1)
    assert line.item_based_expense_line_detail.billable_status == "NotBillable"


def test_billable_with_customerref_is_billable():
    connector = _build_connector()
    connector._get_qbo_customer_ref = Mock(return_value=QboReferenceType(value="cust-1", name="Cust 1"))
    line = connector._build_qbo_line(_line(project_id=42, is_billable=True, is_billed=False), line_num=1)
    assert line.item_based_expense_line_detail.billable_status == "Billable"


def test_billable_with_customerref_and_is_billed_is_hasbeenbilled():
    connector = _build_connector()
    connector._get_qbo_customer_ref = Mock(return_value=QboReferenceType(value="cust-1", name="Cust 1"))
    line = connector._build_qbo_line(_line(project_id=42, is_billable=True, is_billed=True), line_num=1)
    assert line.item_based_expense_line_detail.billable_status == "HasBeenBilled"
