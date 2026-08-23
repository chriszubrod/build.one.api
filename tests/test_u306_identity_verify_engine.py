"""U-306: close `_verify_dbo_qbo_identity`'s H1 residual (booked by U-297) and
collapse its 2-read verify path into one JOIN'd sproc per family.

Background: `base/identity_consistency.py::_verify_dbo_qbo_identity` is the
shared engine behind `verify_project_qbo_identity` / `verify_vendor_qbo_identity`
/ `verify_bill_qbo_identity` / `verify_customer_qbo_identity`. Before this unit
it ran 2 reads (mapping-by-local-id, then a second round trip for the mapped
external row) and, when a family had NO mapping row of its own, TRUSTED the
dbo-stamped QboId unconditionally — LOCAL-SIDE ONLY, blind to the mapping
table already binding that same external id to a DIFFERENT local row (H1).
It now calls one `read_identity_check(local_id, qbo_id)` per family, backed
by `base/sql/identity_consistency_reads.sql`'s single JOIN'd sproc, which
returns both the forward comparison AND the reverse-direction lookup in one
round trip — closing H1 at zero extra query cost.

Covers:
  1. The engine's decision table via `verify_project_qbo_identity` (the
     shared logic is identical across all 4 bindings; Project is the
     representative case) — all 4 forward/reverse combinations, including
     the NEW reverse-conflict-refuses case (H1) and its "same row" non-conflict
     edge case.
  2. Each of the other 3 wrappers (`verify_vendor_qbo_identity`,
     `verify_bill_qbo_identity`, `verify_customer_qbo_identity`) wires its
     OWN family's repo/kwargs correctly and reproduces the H1 fix — proves
     the fix isn't Project-only, since each is a separate function real
     callers invoke directly.
  3. Each of the 4 mapping repos' new `read_identity_check` method: correct
     sproc name + params shape, and correct column->field mapping from a
     REAL row (a wrong column/attribute name in `getattr(..., default=None)`
     fails silently, not with an exception — must be exercised with a
     populated row, not just `fetchone() -> None`).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from integrations.intuit.qbo.base.identity_consistency import (
    IdentityCheckResult,
    verify_bill_qbo_identity,
    verify_customer_qbo_identity,
    verify_project_qbo_identity,
    verify_vendor_qbo_identity,
)


def _repo(result: IdentityCheckResult):
    repo = Mock()
    repo.read_identity_check.return_value = result
    return repo


# --- Section 1: engine decision table (via verify_project_qbo_identity) ---


def test_verify_none_when_no_entity_or_no_qbo_id():
    repo = _repo(IdentityCheckResult(None, None, None))
    for entity in (None, SimpleNamespace(id=1, qbo_id=None), SimpleNamespace(id=1, qbo_id="")):
        assert verify_project_qbo_identity(entity, customer_project_repo=repo, qbo_customer_repo=Mock()) is None
    repo.read_identity_check.assert_not_called()


def test_verify_trusts_when_no_mapping_and_no_reverse_conflict():
    """Today's common (0-population) case: nothing to disagree with either way."""
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=None))

    result = verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=Mock())

    assert result == "QBO-P-42"
    repo.read_identity_check.assert_called_once_with(local_id=42, qbo_id="QBO-P-42")


def test_verify_trusts_when_forward_mapping_agrees():
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBO-P-42", reverse_mapped_local_id=42))

    result = verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=Mock())

    assert result == "QBO-P-42"


def test_verify_refuses_when_forward_mapping_disagrees():
    """Pre-existing behavior (U-276 round-4): a stale/'stolen' dbo QboId with
    its OWN mapping row pointing elsewhere must never be trusted."""
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBO-P-OTHER", reverse_mapped_local_id=42))

    result = verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=Mock())

    assert result is None


def test_verify_refuses_when_unmapped_but_reverse_bound_to_a_different_row():
    """THE H1 FIX: no mapping row of its own, but the mapping table already
    binds this exact QboId to a DIFFERENT Project. Pre-U-306 this returned
    the dbo value unconditionally (LOCAL-SIDE ONLY, booked as U-297's H1) —
    the live-prod P0 shape this closes: a re-parent/misroute nothing would
    have caught."""
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=99))

    result = verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=Mock())

    assert result is None


def test_verify_refuses_when_forward_agrees_but_a_duplicate_qbo_row_conflicts_elsewhere():
    """Codex round-2 review: a forward mapping that agrees is NOT enough on
    its own when the qbo.* staging table holds more than one row for the same
    QboId (possible when RealmId differs/is NULL — the filtered
    UNIQUE(QboId, RealmId) index doesn't prevent it). This entity's OWN
    mapping agrees, but the reverse arm still finds a DIFFERENT local row
    mapped to one of the duplicate staging rows — must refuse, not trust just
    because the forward check alone passed. The original nested-branch
    version only ran the reverse check when `mapping_id` was absent, missing
    this case entirely."""
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(
        IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBO-P-42", reverse_mapped_local_id=99)
    )

    result = verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=Mock())

    assert result is None


def test_verify_trusts_when_unmapped_and_reverse_points_at_the_same_row():
    """Defensive edge case: reverse-mapped-to-self is not a conflict (can't
    actually occur given the mapping tables' own 1:1 UNIQUE constraints — if
    the reverse side found this row, the forward side would have too — but
    the engine must not misfire if it ever does)."""
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=42))

    result = verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=Mock())

    assert result == "QBO-P-42"


def test_verify_never_touches_the_now_unused_qbo_repo():
    """qbo_customer_repo is accepted-but-unused (U-306 folded its job into
    the JOIN'd read) — kept only so the 6 existing callers' kwargs still
    work. Confirms it's genuinely dead, not silently still load-bearing."""
    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    repo = _repo(IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBO-P-42", reverse_mapped_local_id=42))
    qbo_customer_repo = Mock()

    verify_project_qbo_identity(project, customer_project_repo=repo, qbo_customer_repo=qbo_customer_repo)

    assert qbo_customer_repo.method_calls == []


# --- Section 2: the other 3 wrappers wire their own family + reproduce H1 ---


def test_verify_vendor_qbo_identity_binds_vendor_vendor_repo_and_closes_h1():
    vendor = SimpleNamespace(id=7, qbo_id="QV-7")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=999))

    result = verify_vendor_qbo_identity(vendor, vendor_vendor_repo=repo, qbo_vendor_repo=Mock())

    assert result is None
    repo.read_identity_check.assert_called_once_with(local_id=7, qbo_id="QV-7")


def test_verify_bill_qbo_identity_binds_bill_bill_repo_and_closes_h1():
    bill = SimpleNamespace(id=8, qbo_id="QBILL-8")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=999))

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

    assert result is None
    repo.read_identity_check.assert_called_once_with(local_id=8, qbo_id="QBILL-8")


def test_verify_customer_qbo_identity_binds_customer_customer_repo_and_closes_h1():
    customer = SimpleNamespace(id=9, qbo_id="QC-9")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=999))

    result = verify_customer_qbo_identity(customer, customer_customer_repo=repo, qbo_customer_repo=Mock())

    assert result is None
    repo.read_identity_check.assert_called_once_with(local_id=9, qbo_id="QC-9")


# --- Section 3: repo-level read_identity_check (sproc shape + row mapping) ---


def _mock_db(module_path, row):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    ctx_patch = patch(f"{module_path}.get_connection")
    call_patch = patch(f"{module_path}.call_procedure")
    return cursor, ctx_patch, call_patch


def test_customer_project_repo_read_identity_check_calls_sproc_with_local_id_and_qbo_id():
    from integrations.intuit.qbo.customer.connector.project.persistence.repo import (
        CustomerProjectRepository,
    )

    module_path = "integrations.intuit.qbo.customer.connector.project.persistence.repo"
    row = SimpleNamespace(MappingId=None, MappingExternalId=None, ForwardExternalQboId=None, ReverseMappedLocalId=None)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        CustomerProjectRepository().read_identity_check(local_id=42, qbo_id="QBO-P-42")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadCustomerProjectIdentityCheckByProjectId"
    assert mock_call.call_args.kwargs["params"] == {"ProjectId": 42, "QboId": "QBO-P-42"}


def test_customer_project_repo_read_identity_check_maps_a_real_row():
    from integrations.intuit.qbo.customer.connector.project.persistence.repo import (
        CustomerProjectRepository,
    )

    module_path = "integrations.intuit.qbo.customer.connector.project.persistence.repo"
    row = SimpleNamespace(MappingId=1, MappingExternalId=10, ForwardExternalQboId="QBO-P-42", ReverseMappedLocalId=42)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = CustomerProjectRepository().read_identity_check(local_id=42, qbo_id="QBO-P-42")

    assert result == IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBO-P-42", reverse_mapped_local_id=42)


def test_vendor_vendor_repo_read_identity_check_calls_sproc_with_local_id_and_qbo_id():
    from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import (
        VendorVendorRepository,
    )

    module_path = "integrations.intuit.qbo.vendor.connector.vendor.persistence.repo"
    row = SimpleNamespace(MappingId=None, MappingExternalId=None, ForwardExternalQboId=None, ReverseMappedLocalId=None)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        VendorVendorRepository().read_identity_check(local_id=7, qbo_id="QV-7")

    assert mock_call.call_args.kwargs["name"] == "ReadVendorVendorIdentityCheckByVendorId"
    assert mock_call.call_args.kwargs["params"] == {"VendorId": 7, "QboId": "QV-7"}


def test_vendor_vendor_repo_read_identity_check_maps_a_real_row():
    from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import (
        VendorVendorRepository,
    )

    module_path = "integrations.intuit.qbo.vendor.connector.vendor.persistence.repo"
    row = SimpleNamespace(MappingId=2, MappingExternalId=20, ForwardExternalQboId="QV-OTHER", ReverseMappedLocalId=7)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = VendorVendorRepository().read_identity_check(local_id=7, qbo_id="QV-7")

    assert result == IdentityCheckResult(mapping_id=2, forward_external_qbo_id="QV-OTHER", reverse_mapped_local_id=7)


def test_bill_bill_repo_read_identity_check_calls_sproc_with_local_id_and_qbo_id():
    from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository

    module_path = "integrations.intuit.qbo.bill.connector.bill.persistence.repo"
    row = SimpleNamespace(MappingId=None, MappingExternalId=None, ForwardExternalQboId=None, ReverseMappedLocalId=None)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        BillBillRepository().read_identity_check(local_id=8, qbo_id="QBILL-8")

    assert mock_call.call_args.kwargs["name"] == "ReadBillBillIdentityCheckByBillId"
    assert mock_call.call_args.kwargs["params"] == {"BillId": 8, "QboId": "QBILL-8"}


def test_bill_bill_repo_read_identity_check_maps_a_real_row():
    from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository

    module_path = "integrations.intuit.qbo.bill.connector.bill.persistence.repo"
    row = SimpleNamespace(MappingId=3, MappingExternalId=30, ForwardExternalQboId="QBILL-8", ReverseMappedLocalId=8)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = BillBillRepository().read_identity_check(local_id=8, qbo_id="QBILL-8")

    assert result == IdentityCheckResult(mapping_id=3, forward_external_qbo_id="QBILL-8", reverse_mapped_local_id=8)


def test_customer_customer_repo_read_identity_check_calls_sproc_with_local_id_and_qbo_id():
    from integrations.intuit.qbo.customer.connector.customer.persistence.repo import (
        CustomerCustomerRepository,
    )

    module_path = "integrations.intuit.qbo.customer.connector.customer.persistence.repo"
    row = SimpleNamespace(MappingId=None, MappingExternalId=None, ForwardExternalQboId=None, ReverseMappedLocalId=None)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        CustomerCustomerRepository().read_identity_check(local_id=9, qbo_id="QC-9")

    assert mock_call.call_args.kwargs["name"] == "ReadCustomerCustomerIdentityCheckByCustomerId"
    assert mock_call.call_args.kwargs["params"] == {"CustomerId": 9, "QboId": "QC-9"}


def test_customer_customer_repo_read_identity_check_maps_a_real_row():
    from integrations.intuit.qbo.customer.connector.customer.persistence.repo import (
        CustomerCustomerRepository,
    )

    module_path = "integrations.intuit.qbo.customer.connector.customer.persistence.repo"
    row = SimpleNamespace(MappingId=4, MappingExternalId=40, ForwardExternalQboId="QC-9", ReverseMappedLocalId=9)
    cursor, ctx_patch, call_patch = _mock_db(module_path, row)

    with ctx_patch as mock_conn_ctx, call_patch:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = CustomerCustomerRepository().read_identity_check(local_id=9, qbo_id="QC-9")

    assert result == IdentityCheckResult(mapping_id=4, forward_external_qbo_id="QC-9", reverse_mapped_local_id=9)
