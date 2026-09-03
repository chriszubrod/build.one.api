"""Pure-logic tests for U-307c's repoint of reconcile_project.py's SubCostCode
back-fill hop (`repair_qbo_line_item_mappings`, lines ~440-450) off the
hand-rolled `qbo_item_repo.read_by_qbo_id` -> `item_scc_repo.read_by_qbo_item_id`
2-hop onto the shared `cost_code_resolver.resolve_dbo_sub_cost_code` (dbo-native
first, legacy-hop fallback on a miss, with a realm check this hand-rolled
version never had at all).

U-363: `repair_qbo_line_item_mappings` itself was repointed off the retired
qbo.BillLineItemBillLine mapping table onto a direct dbo-native identity stamp
(BillLineItemRepository.set_qbo_identity) — these fixtures/assertions were
updated to match, but the SCC back-fill hop under test here is unchanged.
`bill_line_item_repo` is now a required parameter (DI, matching every sibling
repo already threaded through this function) rather than self-constructed —
tests pass a mock directly instead of patching the class. The stamp is now
verified with a re-read (`read_by_id`) before being counted as a repair — every
success-path test stubs that re-read to reflect the landed identity.

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


def _make_line_item(*, li_id=10, description="Lumber", amount="100.00", sub_cost_code_id=None, qbo_id=None):
    return SimpleNamespace(
        id=li_id, description=description, amount=Decimal(amount), sub_cost_code_id=sub_cost_code_id,
        qbo_id=qbo_id,
    )


def _make_qbo_bill_line(*, qbl_id=20, qbo_line_id="LINE-20", description="Lumber", amount="100.00", line_num=1, item_ref_value=None):
    return SimpleNamespace(
        id=qbl_id, qbo_line_id=qbo_line_id, description=description, amount=Decimal(amount), line_num=line_num,
        item_ref_value=item_ref_value,
    )


def _repos(*, local_qbo_bill, qbo_lines):
    # U-355: qbo.BillBill is retired -- the "already in QBO" check now resolves
    # the local qbo.Bill staging row directly via (bill.qbo_id, realm_id), not a
    # BillBill mapping row's own qbo_bill_id.
    qbo_bill_repo = MagicMock()
    qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = local_qbo_bill
    qbo_bill_line_repo = MagicMock()
    qbo_bill_line_repo.read_by_qbo_bill_id.return_value = qbo_lines
    return qbo_bill_repo, qbo_bill_line_repo


def _bli_repo_stamping_successfully(*, li_id=10, qbo_line_id="LINE-20", realm_id="realm-7"):
    """A bill_line_item_repo mock whose read_by_id re-read (the U-363
    stamp-verification step) reflects a landed stamp — the success-path
    default for these tests."""
    repo = MagicMock()
    repo.read_by_qbo_identity.return_value = None  # no theft-guard collision
    repo.read_by_id.return_value = SimpleNamespace(id=li_id, qbo_id=qbo_line_id, realm_id=realm_id)
    return repo


def test_backfill_resolves_sub_cost_code_dbo_natively_and_threads_realm_id():
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    resolved = SimpleNamespace(id=555)
    bli_repo_instance = _bli_repo_stamping_successfully()

    with patch(f"{MODULE}.resolve_dbo_sub_cost_code", return_value=resolved) as mock_resolve:
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
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
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = _bli_repo_stamping_successfully()
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve:
        repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
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
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = _bli_repo_stamping_successfully()
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve:
        repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
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
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = _bli_repo_stamping_successfully()
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code", return_value=None):
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
            dry_run=False,
            realm_id="realm-7",
        )

    assert repairs_count == 1  # the stamp itself still succeeded
    assert li.sub_cost_code_id is None
    bli_repo_instance.update_by_id.assert_not_called()
    assert not any("back-fill failed" in i for i in issues)


def test_backfill_dry_run_never_resolves_or_stamps_identity():
    bill = _make_bill()
    li = _make_line_item(sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = _bli_repo_stamping_successfully()
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve:
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
            dry_run=True,
            realm_id="realm-7",
        )

    assert repairs_count == 1
    mock_resolve.assert_not_called()
    bli_repo_instance.set_qbo_identity.assert_not_called()
    bli_repo_instance.read_by_id.assert_not_called()  # dry-run never even attempts the verify re-read


def test_backfill_theft_guard_skips_when_qbo_line_already_held_by_another_line():
    """U-363: a matched QboBillLine whose QBO Line.Id is already stamped onto
    a DIFFERENT BillLineItem under this same Bill is a possible sync_from_qbo
    duplicate — never steal it."""
    bill = _make_bill()
    li = _make_line_item(li_id=10, sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = MagicMock()
    bli_repo_instance.read_by_qbo_identity.return_value = SimpleNamespace(id=999)  # a DIFFERENT line
    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve:
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
            dry_run=False,
            realm_id="realm-7",
        )

    assert repairs_count == 0
    bli_repo_instance.set_qbo_identity.assert_not_called()
    mock_resolve.assert_not_called()
    assert any("already held by BillLineItem id=999" in i for i in issues)


def test_backfill_stamp_that_did_not_land_is_flagged_not_silently_counted():
    """U-363 correctness finding: SetBillLineItemQboIdentity's atomic-pair
    guard can silently no-op the QboId write when realm_id is incomplete at
    call time (e.g. a disconnected QBO auth) — the re-read must catch this
    rather than reporting a false success."""
    bill = _make_bill()
    li = _make_line_item(li_id=10, sub_cost_code_id=None)
    qbo_line = _make_qbo_bill_line(item_ref_value="ITEM-Q1", qbo_line_id="LINE-20")
    local_qbo_bill = SimpleNamespace(id=99)
    qbo_bill_repo, qbo_bill_line_repo = _repos(local_qbo_bill=local_qbo_bill, qbo_lines=[qbo_line])

    bli_repo_instance = MagicMock()
    bli_repo_instance.read_by_qbo_identity.return_value = None
    # The stamp call itself succeeds (no exception) but the row it echoes back
    # still shows QboId IS NULL — the atomic-pair guard silently declined.
    bli_repo_instance.read_by_id.return_value = SimpleNamespace(id=10, qbo_id=None, realm_id=None)

    with patch(f"{MODULE}.resolve_dbo_sub_cost_code") as mock_resolve:
        issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id={1: bill},
            line_items_by_bill_id={1: [li]},
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            bill_line_item_repo=bli_repo_instance,
            dry_run=False,
            realm_id=None,
        )

    assert repairs_count == 0
    assert li.qbo_id is None  # never optimistically mutated on a failed stamp
    mock_resolve.assert_not_called()  # SCC back-fill never runs on a failed stamp
    assert any("stamp did not land" in i for i in issues)
