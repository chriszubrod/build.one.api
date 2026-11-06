"""U-551b-f1: recode_purchase_lines may repoint a line the drain itself wrote."""
import copy
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense.business.errors import PurchaseChangedInQboError
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

NEW_ITEM = QboReferenceType(value="item-42", name="Framing Labor")
CUST = QboReferenceType(value="cust-99", name="TB3")
REALM = "realm-test"
PID = "purchase-123"
TOKEN = "5"


def _item_line(line_id="1", item="item-77", amount="10.00"):
    return {
        "Id": line_id, "LineNum": int(line_id), "Description": "d", "Amount": amount,
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": item, "name": "Old"},
            "CustomerRef": {"value": "cust-55", "name": "Old Cust"},
            "ClassRef": {"value": "cls-1", "name": "C"},
            "BillableStatus": "Billable",
        },
    }


def _placeholder(line_id="1"):
    return {
        "Id": line_id, "LineNum": int(line_id), "Description": "d", "Amount": "10.00",
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "58999", "name": "Cost of construction : NEED TO CATEGORIZE"}},
    }


def _raw(lines, token=TOKEN):
    return {"Id": PID, "SyncToken": token, "PaymentType": "CreditCard", "TxnDate": "2026-07-01", "Line": lines}


def _connector():
    c = PurchaseExpenseConnector(expense_service=MagicMock(), vendor_service=MagicMock(),
                                 vendor_vendor_repo=MagicMock(), qbo_vendor_repo=MagicMock())
    c._get_qbo_item_ref = MagicMock(return_value=NEW_ITEM)
    c._get_qbo_customer_ref = MagicMock(return_value=CUST)
    return c


def _patch(fresh):
    client = MagicMock()
    client.get_purchase_raw.return_value = copy.deepcopy(fresh)
    bumped = copy.deepcopy(fresh)
    bumped["SyncToken"] = str(int(fresh["SyncToken"]) + 1)
    client.update_purchase_raw.return_value = bumped
    cls = MagicMock()
    cls.return_value.__enter__.return_value = client
    cls.return_value.__exit__.return_value = None
    return patch("integrations.intuit.qbo.purchase.external.client.QboPurchaseClient", cls), client


def _spec(line_id="1", repoint_from=None):
    s = {"target_qbo_line_id": line_id, "sub_cost_code_id": 101, "project_id": 202, "description": "new d",
         "quantity": None, "rate": None, "amount": None, "is_billable": None}
    if repoint_from is not None:
        s["repoint_from_item_ref_value"] = repoint_from
    return s


def _run(fresh, specs, token=TOKEN):
    p, client = _patch(fresh)
    with p:
        try:
            res = _connector().recode_purchase_lines(realm_id=REALM, qbo_purchase_qbo_id=PID,
                                                     lines=specs, expected_sync_token=token)
        except PurchaseChangedInQboError as exc:
            return exc, client
    return res, client


def test_repoint_item_based_line_written():
    sibling = _item_line("2", item="item-99")
    fresh = _raw([_item_line("1"), sibling])
    res, client = _run(fresh, [_spec(repoint_from="item-77")])
    assert res["status"] == "written"
    assert res["written_lines"] == [{"qbo_line_id": "1", "item_ref_value": "item-42", "sub_cost_code_id": 101}]
    posted = client.update_purchase_raw.call_args[0][0]
    line = posted["Line"][0]
    assert line["DetailType"] == "ItemBasedExpenseLineDetail"
    assert "AccountBasedExpenseLineDetail" not in line
    d = line["ItemBasedExpenseLineDetail"]
    assert d["ItemRef"] == {"value": "item-42", "name": "Framing Labor"}
    assert d["ClassRef"] == {"value": "cls-1", "name": "C"}
    assert d["BillableStatus"] == "Billable"
    assert d["CustomerRef"] == {"value": "cust-99", "name": "TB3"}
    assert posted["Line"][1] == sibling
    for k in ("Id", "PaymentType", "TxnDate", "SyncToken"):
        assert posted[k] == fresh[k]


def test_repoint_carries_old_customer_when_no_project():
    c = _connector()
    c._get_qbo_customer_ref = MagicMock(return_value=None)
    p, client = _patch(_raw([_item_line("1")]))
    spec = _spec(repoint_from="item-77")
    spec["project_id"] = None
    with p:
        c.recode_purchase_lines(realm_id=REALM, qbo_purchase_qbo_id=PID, lines=[spec], expected_sync_token=TOKEN)
    d = client.update_purchase_raw.call_args[0][0]["Line"][0]["ItemBasedExpenseLineDetail"]
    assert d["CustomerRef"] == {"value": "cust-55", "name": "Old Cust"}


def test_repoint_from_a_different_item_is_changed_in_qbo():
    res, client = _run(_raw([_item_line("1", item="item-88")]), [_spec(repoint_from="item-77")])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


def test_repoint_with_moved_token_is_changed_in_qbo():
    res, client = _run(_raw([_item_line("1")], token="9"), [_spec(repoint_from="item-77")])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


def test_non_placeholder_without_repoint_marker_fails_closed():
    res, client = _run(_raw([_item_line("1")]), [_spec()])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


def test_line_already_carrying_target_item_skipped():
    res, client = _run(_raw([_item_line("1", item="item-42")]), [_spec(repoint_from="item-77")])
    assert res["status"] == "already_recoded"
    assert "written_lines" not in res
    client.update_purchase_raw.assert_not_called()


def test_placeholder_path_reports_written_lines():
    res, client = _run(_raw([_placeholder("1")]), [_spec()])
    assert res["status"] == "written"
    assert res["written_lines"] == [{"qbo_line_id": "1", "item_ref_value": "item-42", "sub_cost_code_id": 101}]


def test_repoint_spec_on_placeholder_recodes_it():
    res, _ = _run(_raw([_placeholder("1")]), [_spec(repoint_from="item-77")])
    assert res["status"] == "written"


# ---- R2-3: token fence applies to EVERY repoint outcome -------------------------------------

def test_repoint_already_carrying_target_with_moved_token_is_changed_in_qbo():
    res, client = _run(_raw([_item_line("1", item="item-42")], token="9"), [_spec(repoint_from="item-77")])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


def test_repoint_already_carrying_target_reports_verified_lines():
    res, client = _run(_raw([_item_line("1", item="item-42")]), [_spec(repoint_from="item-77")])
    assert res["status"] == "already_recoded"
    assert res["verified_lines"] == [{"qbo_line_id": "1", "item_ref_value": "item-42", "sub_cost_code_id": 101}]
    client.update_purchase_raw.assert_not_called()


def test_first_write_already_recoded_has_no_token_check_and_no_verified_lines():
    res, client = _run(_raw([_item_line("1", item="item-42")], token="9"), [_spec()])
    assert res["status"] == "already_recoded"
    assert "verified_lines" not in res and "written_lines" not in res
    client.update_purchase_raw.assert_not_called()


# ---- R2-8a / R2-8b --------------------------------------------------------------------------

def test_mixed_targets_written_lines_lists_only_the_mutated_line():
    fresh = _raw([_placeholder("1"), _item_line("2", item="item-42")])
    res, client = _run(fresh, [_spec("1"), _spec("2")])
    assert res["status"] == "written"
    assert res["written_lines"] == [{"qbo_line_id": "1", "item_ref_value": "item-42", "sub_cost_code_id": 101}]


def test_non_repoint_spec_on_account_based_non_placeholder_line_is_changed_in_qbo():
    human_gl = {
        "Id": "1", "LineNum": 1, "Description": "d", "Amount": "10.00",
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "77", "name": "Materials"}},
    }
    res, client = _run(_raw([human_gl]), [_spec()])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


def test_non_repoint_spec_on_item_based_line_without_an_item_value_is_changed_in_qbo():
    """Battery M4b (EM pin): an ItemBased line whose ItemRef carries no value, against a spec with NO
    repoint marker, must still fail closed - `None == None` must never read as "carries our marker"."""
    malformed = _item_line("1", item="item-77")
    malformed["ItemBasedExpenseLineDetail"]["ItemRef"] = {"name": "no value"}
    res, client = _run(_raw([malformed]), [_spec()])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


# ---- R3-A(iv): verified_lines rides a repoint `written`, never a first-write `written` -------

def test_repoint_written_carries_verified_lines_for_already_carrying_siblings():
    fresh = _raw([_item_line("1", item="item-77"), _item_line("2", item="item-42")])
    res, client = _run(fresh, [_spec("1", repoint_from="item-77"), _spec("2", repoint_from="item-88")])
    assert res["status"] == "written"
    assert res["written_lines"] == [{"qbo_line_id": "1", "item_ref_value": "item-42", "sub_cost_code_id": 101}]
    assert res["verified_lines"] == [{"qbo_line_id": "2", "item_ref_value": "item-42", "sub_cost_code_id": 101}]


def test_repoint_written_with_nothing_verified_still_has_the_key():
    res, _ = _run(_raw([_item_line("1", item="item-77")]), [_spec("1", repoint_from="item-77")])
    assert res["status"] == "written" and res["verified_lines"] == []


def test_first_write_written_has_no_verified_lines_key():
    fresh = _raw([_placeholder("1"), _item_line("2", item="item-42")])
    res, _ = _run(fresh, [_spec("1"), _spec("2")])
    assert res["status"] == "written" and "verified_lines" not in res


# ---- R3-E(2): mixed specs -> the fence applies if ANY spec is a repoint ----------------------

def test_mixed_first_write_and_repoint_specs_with_moved_token_is_changed_in_qbo():
    fresh = _raw([_item_line("1", item="item-42"), _item_line("2", item="item-42")], token="9")
    res, client = _run(fresh, [_spec("1"), _spec("2", repoint_from="item-77")])
    assert isinstance(res, PurchaseChangedInQboError)
    client.update_purchase_raw.assert_not_called()


# ---- U-551b-f1 round 6 (M4): verify_purchase_untouched is one GET and a string compare ----------

@pytest.mark.parametrize("live,expected,untouched", [
    ("6", "6", True),
    ("7", "6", False),
    (7, "7", True),        # QBO may hand back an int; compared as strings, exactly like the repoint fence
    ("6", 6, True),
])
def test_verify_purchase_untouched_compares_the_live_sync_token_as_strings(live, expected, untouched):
    raw = _raw([_item_line()], token=live)
    patcher, client = _patch(raw)
    client.get_purchase_raw.return_value = raw
    with patcher:
        got = _connector().verify_purchase_untouched(
            realm_id=REALM, qbo_purchase_qbo_id=PID, expected_sync_token=expected)
    assert got is untouched
    client.get_purchase_raw.assert_called_once_with(PID)
    client.update_purchase_raw.assert_not_called()


def test_verify_purchase_untouched_never_writes_and_lets_a_qbo_error_propagate():
    from integrations.intuit.qbo.base.errors import QboError

    patcher, client = _patch(_raw([_item_line()]))
    client.get_purchase_raw.side_effect = QboError("down")
    with patcher, pytest.raises(QboError):
        _connector().verify_purchase_untouched(realm_id=REALM, qbo_purchase_qbo_id=PID, expected_sync_token="5")
    client.update_purchase_raw.assert_not_called()
