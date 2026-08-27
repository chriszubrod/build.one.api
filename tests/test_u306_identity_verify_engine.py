"""U-306: close `_verify_dbo_qbo_identity`'s H1 residual (booked by U-297) and
collapse its 2-read verify path into one JOIN'd sproc per family.

Background: `base/identity_consistency.py::_verify_dbo_qbo_identity` is the
shared engine behind `verify_bill_qbo_identity` (originally also
`verify_project_qbo_identity` / `verify_vendor_qbo_identity` /
`verify_customer_qbo_identity` -- U-314 deleted those three once their
families retired their `qbo.*` mapping tables entirely; only Bill's remains).
Before this unit it ran 2 reads (mapping-by-local-id, then a second round
trip for the mapped external row) and, when a family had NO mapping row of
its own, TRUSTED the dbo-stamped QboId unconditionally — LOCAL-SIDE ONLY,
blind to the mapping table already binding that same external id to a
DIFFERENT local row (H1). It now calls one `read_identity_check(local_id,
qbo_id)` per family, backed by `base/sql/identity_consistency_reads.sql`'s
single JOIN'd sproc, which returns both the forward comparison AND the
reverse-direction lookup in one round trip — closing H1 at zero extra query
cost.

Covers:
  1. The engine's decision table via `verify_bill_qbo_identity` (the only
     surviving binding as of U-314) — all 4 forward/reverse combinations,
     including the reverse-conflict-refuses case (H1) and its "same row"
     non-conflict edge case.
  2. `BillBillRepository`'s `read_identity_check` method: correct sproc name
     + params shape, and correct column->field mapping from a REAL row (a
     wrong column/attribute name in `getattr(..., default=None)` fails
     silently, not with an exception — must be exercised with a populated
     row, not just `fetchone() -> None`).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from integrations.intuit.qbo.base.identity_consistency import (
    IdentityCheckResult,
    verify_bill_qbo_identity,
)


def _repo(result: IdentityCheckResult):
    repo = Mock()
    repo.read_identity_check.return_value = result
    return repo


# --- Section 1: engine decision table (via verify_bill_qbo_identity) ---


def test_verify_none_when_no_entity_or_no_qbo_id():
    repo = _repo(IdentityCheckResult(None, None, None))
    for entity in (None, SimpleNamespace(id=1, qbo_id=None), SimpleNamespace(id=1, qbo_id="")):
        assert verify_bill_qbo_identity(entity, bill_bill_repo=repo, qbo_bill_repo=Mock()) is None
    repo.read_identity_check.assert_not_called()


def test_verify_trusts_when_no_mapping_and_no_reverse_conflict():
    """Today's common (0-population) case: nothing to disagree with either way."""
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=None))

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

    assert result == "QBILL-42"
    repo.read_identity_check.assert_called_once_with(local_id=42, qbo_id="QBILL-42")


def test_verify_trusts_when_forward_mapping_agrees():
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBILL-42", reverse_mapped_local_id=42))

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

    assert result == "QBILL-42"


def test_verify_refuses_when_forward_mapping_disagrees():
    """Pre-existing behavior (U-276 round-4): a stale/'stolen' dbo QboId with
    its OWN mapping row pointing elsewhere must never be trusted."""
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBILL-OTHER", reverse_mapped_local_id=42))

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

    assert result is None


def test_verify_refuses_when_unmapped_but_reverse_bound_to_a_different_row():
    """THE H1 FIX: no mapping row of its own, but the mapping table already
    binds this exact QboId to a DIFFERENT Bill. Pre-U-306 this returned
    the dbo value unconditionally (LOCAL-SIDE ONLY, booked as U-297's H1) —
    the live-prod P0 shape this closes: a re-parent/misroute nothing would
    have caught."""
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=99))

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

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
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(
        IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBILL-42", reverse_mapped_local_id=99)
    )

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

    assert result is None


def test_verify_trusts_when_unmapped_and_reverse_points_at_the_same_row():
    """Defensive edge case: reverse-mapped-to-self is not a conflict (can't
    actually occur given the mapping tables' own 1:1 UNIQUE constraints — if
    the reverse side found this row, the forward side would have too — but
    the engine must not misfire if it ever does)."""
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(IdentityCheckResult(mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=42))

    result = verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=Mock())

    assert result == "QBILL-42"


def test_verify_never_touches_the_now_unused_qbo_repo():
    """qbo_bill_repo is accepted-but-unused (U-306 folded its job into
    the JOIN'd read) — kept only so existing callers' kwargs still work.
    Confirms it's genuinely dead, not silently still load-bearing."""
    bill = SimpleNamespace(id=42, qbo_id="QBILL-42")
    repo = _repo(IdentityCheckResult(mapping_id=1, forward_external_qbo_id="QBILL-42", reverse_mapped_local_id=42))
    qbo_bill_repo = Mock()

    verify_bill_qbo_identity(bill, bill_bill_repo=repo, qbo_bill_repo=qbo_bill_repo)

    assert qbo_bill_repo.method_calls == []


# --- Section 2: repo-level read_identity_check (sproc shape + row mapping) ---


def _mock_db(module_path, row):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    ctx_patch = patch(f"{module_path}.get_connection")
    call_patch = patch(f"{module_path}.call_procedure")
    return cursor, ctx_patch, call_patch


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
