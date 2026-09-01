"""Pure-logic tests for U-307c's repoint of reconcile_project.py's SubCostCode
back-fill hop (`repair_qbo_line_item_mappings`, lines ~440-450) off the
hand-rolled `qbo_item_repo.read_by_qbo_id` -> `item_scc_repo.read_by_qbo_item_id`
2-hop onto the shared `cost_code_resolver.resolve_dbo_sub_cost_code` (dbo-native
first, legacy-hop fallback on a miss, with a realm check this hand-rolled
version never had at all).

No harness existed for `repair_qbo_line_item_mappings` before this unit --
covers only the changed hop, not the whole function's pre-existing match/create/
dry-run machinery.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.reconcile_project import repair_qbo_line_item_mappings

MODULE = "scripts.reconcile_project"


def _make_bill(bill_id=1, bill_number="B-1", qbo_id="BILL-99"):
    return SimpleNamespace(id=bill_id, bill_number=bill_number, qbo_id=qbo_id)


def _make_line_item(*, li_id=10, description="Lumber", amount="100.00", sub_cost_code_id=None):
    return SimpleNamespace(
        id=li_id, description=description, amount=Decimal(amount), sub_cost_code_id=sub_cost_code_id,
    )


def _make_qbo_bill_line(*, qbl_id=20, description="Lumber", amount="100.00", line_num=1, item_ref_value=None):
    return SimpleNamespace(
        id=qbl_id, description=description, amount=Decimal(amount), line_num=line_num,
        item_ref_value=item_ref_value,
    )


def _repos(*, local_qbo_bill, qbo_lines, existing_mapping=None):
    # U-355: qbo.BillBill is retired -- the "already in QBO" check now resolves
    # the local qbo.Bill staging row directly via (bill.qbo_id, realm_id), not a
    # BillBill mapping row's own qbo_bill_id.
    qbo_bill_repo = MagicMock()
    qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = local_qbo_bill
    bill_line_item_bill_line_repo = MagicMock()
    bill_line_item_bill_line_repo.read_by_bill_line_item_id.return_value = None
    bill_line_item_bill_line_repo.read_by_qbo_bill_line_id.return_value = existing_mapping
    qbo_bill_line_repo = MagicMock()
    qbo_bill_line_repo.read_by_qbo_bill_id.return_value = qbo_lines
    return qbo_bill_repo, bill_line_item_bill_line_repo, qbo_bill_line_repo


def test_backfill_resolves_sub_cost_code_dbo_natively_and_threads_realm_id():
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, bl_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    resolved = SimpleNamespace(id=555)
    bli_repo_instance = MagicMock()

    with patch(f"{MODULE}.resolve_dbo_sub_cost_code", return_value=resolved) as mock_resolve, patch(
        f"{MODULE}.BillLineItemRepository", return_value=bli_repo_instance
    ):
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            bill_line_item_bill_line_repo=bl_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            dry_run=False,
            realm_id="realm-7",
        )

    assert repairs_count == 1
    mock_resolve.assert_called_once_with("ITEM-Q1", "realm-7")
    assert li.sub_cost_code_id == 555
    bli_repo_instance.update_by_id.assert_called_once_with(li)
    assert not any("back-fill failed" in i for i in issues)


def test_backfill_skips_when_sub_cost_code_already_set():
    """Targeted: never overwrites an already-coded line."""
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=42)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, bl_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = MagicMock()
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve, patch(
        f"{MODULE}.BillLineItemRepository", return_value=bli_repo_instance
    ):
        repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            bill_line_item_bill_line_repo=bl_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            dry_run=False,
            realm_id="realm-7",
        )

    mock_resolve.assert_not_called()
    bli_repo_instance.update_by_id.assert_not_called()
    assert li.sub_cost_code_id == 42


def test_backfill_no_item_ref_value_skips_resolution():
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value=None)
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, bl_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve, patch(
        f"{MODULE}.BillLineItemRepository"
    ):
        repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            bill_line_item_bill_line_repo=bl_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            dry_run=False,
            realm_id="realm-7",
        )

    mock_resolve.assert_not_called()
    assert li.sub_cost_code_id is None


def test_backfill_resolver_miss_leaves_sub_cost_code_null_no_error():
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, bl_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = MagicMock()
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code", return_value=None), patch(
        f"{MODULE}.BillLineItemRepository", return_value=bli_repo_instance
    ):
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            bill_line_item_bill_line_repo=bl_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            dry_run=False,
            realm_id="realm-7",
        )

    assert repairs_count == 1  # the mapping itself still succeeded
    assert li.sub_cost_code_id is None
    bli_repo_instance.update_by_id.assert_not_called()
    assert not any("back-fill failed" in i for i in issues)


def test_backfill_dry_run_never_resolves_or_creates_mapping():
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, bl_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve:
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            bill_line_item_bill_line_repo=bl_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            dry_run=True,
            realm_id="realm-7",
        )

    assert repairs_count == 1
    mock_resolve.assert_not_called()
    bl_repo.create.assert_not_called()
