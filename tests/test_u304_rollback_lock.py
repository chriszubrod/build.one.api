"""Pure-logic tests for U-304 — the shared create/rollback race lock.

U-298 and U-302 each hand-copied a recheck-before-rollback: on a create_mapping()
failure, re-run resolve_mapping_state before deciding whether to delete the
just-created header. That recheck closed the COMMON race (a racer's self-heal wins
the mapping insert before our own attempt) but left a narrower one open: the
recheck is a POINT-IN-TIME snapshot with no re-verification immediately before the
actual DELETE call, so a THIRD racer landing in the window between the recheck and
the delete could bind to the header via run_identity_fastpath's own self-heal
insert — and then have it destroyed by the still-in-flight delete, silently losing
a legitimately-completed, possibly line-synced financial record.

`create_race_lock` + `guard_create_mapping_rollback` (base/identity_fastpath.py)
close that window: BOTH the self-heal insert (run_identity_fastpath's own
MISSING-branch handling, opted into per-family via the new `race_lock_mapping_label`
param — Purchase/Invoice only) and the rollback-guard's recheck+delete acquire the
SAME sp_getapplock resource for a given (mapping_label, external_id), so
sp_getapplock — not application logic — makes the two mutually exclusive.

These tests cover the lock's own mechanics in isolation (mirrors
test_u243_mapping_cleanup_lock.py's structure for mapping_cleanup.py's sibling
lock): resource-key shape, fail-closed on a lock-acquire timeout, that the lock is
held across the WHOLE critical section (not just the read), and that the two real
call sites (self-heal + rollback-guard) compute the identical resource key for the
same external id — the actual claim that makes them mutually exclusive in
production. No DB/QBO I/O; `qbo_app_lock` is always patched.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.base.identity_fastpath import (
    CONFLICT,
    CONSISTENT,
    MISSING,
    create_race_lock,
    guard_create_mapping_rollback,
    run_identity_fastpath,
)

# Matches the sibling QBO connector test files' own convention (e.g.
# test_u302_invoice_rollback_race.py) — makes the bare `from conftest import ...`
# below resolve when this file is collected standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted as _granted_lock


@contextmanager
def _denied_lock(*_args, **_kwargs):
    yield False


def _recording_lock_factory():
    """A `qbo_app_lock` stand-in that records every resource_name it's called
    with, granting the lock each time. Shared by every "do two call sites use
    the same resource key" test below instead of each hand-rolling its own copy."""
    recorded = []

    @contextmanager
    def _lock(resource_name, timeout_ms=15000):
        recorded.append(resource_name)
        yield True

    return recorded, _lock


LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"


def _guard_kwargs(**overrides):
    defaults = dict(
        mapping_label="PurchaseExpense",
        external_id=901,
        local_id=77,
        read_by_local_id=Mock(return_value=None),
        read_by_external_id=Mock(return_value=None),
        external_id_attr="qbo_purchase_id",
        record_conflict_issue=Mock(),
        delete_header=Mock(),
        entity_label="Expense",
    )
    defaults.update(overrides)
    return defaults


# --- create_race_lock: resource key shape -----------------------------------


@patch(LOCK_PATCH_TARGET)
def test_lock_resource_key_includes_mapping_label_and_external_id(mock_lock):
    mock_lock.side_effect = [_granted_lock(), _granted_lock()]

    with create_race_lock("PurchaseExpense", 901):
        pass
    with create_race_lock("InvoiceInvoice", 901):
        pass

    assert mock_lock.call_args_list[0].args == ("qbo_mapping_create:PurchaseExpense:901",)
    assert mock_lock.call_args_list[1].args == ("qbo_mapping_create:InvoiceInvoice:901",)


# --- guard_create_mapping_rollback: fail-closed on lock timeout -------------


@patch(LOCK_PATCH_TARGET, _denied_lock)
def test_lock_acquisition_failure_raises_without_recheck_or_delete():
    """FAIL CLOSED: a lock-acquire timeout must never fall through to reading
    state or deleting the header under uncertainty."""
    read_by_local_id = Mock()
    read_by_external_id = Mock()
    delete_header = Mock()
    record_conflict_issue = Mock()

    with pytest.raises(RuntimeError, match="Could not acquire create/rollback lock"):
        guard_create_mapping_rollback(
            **_guard_kwargs(
                read_by_local_id=read_by_local_id,
                read_by_external_id=read_by_external_id,
                delete_header=delete_header,
                record_conflict_issue=record_conflict_issue,
            )
        )

    read_by_local_id.assert_not_called()
    read_by_external_id.assert_not_called()
    delete_header.assert_not_called()
    record_conflict_issue.assert_not_called()


# --- guard_create_mapping_rollback: CONSISTENT / CONFLICT / MISSING ---------


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_consistent_state_never_deletes():
    racer_mapping = SimpleNamespace(id=5, qbo_purchase_id=901)
    delete_header = Mock()
    outcome = guard_create_mapping_rollback(
        **_guard_kwargs(
            read_by_local_id=Mock(return_value=racer_mapping),
            read_by_external_id=Mock(return_value=racer_mapping),
            delete_header=delete_header,
        )
    )
    assert outcome.state == CONSISTENT
    assert outcome.delete_succeeded is None
    delete_header.assert_not_called()


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_conflict_state_records_issue_then_deletes():
    by_local = None
    by_external = SimpleNamespace(id=9, qbo_purchase_id=555)
    delete_header = Mock()
    record_conflict_issue = Mock()

    outcome = guard_create_mapping_rollback(
        **_guard_kwargs(
            read_by_local_id=Mock(return_value=by_local),
            read_by_external_id=Mock(return_value=by_external),
            delete_header=delete_header,
            record_conflict_issue=record_conflict_issue,
        )
    )

    assert outcome.state == CONFLICT
    record_conflict_issue.assert_called_once_with(by_local, by_external)
    delete_header.assert_called_once()
    assert outcome.delete_succeeded is True
    assert outcome.delete_exc is None


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_missing_state_deletes_without_recording_conflict():
    delete_header = Mock()
    record_conflict_issue = Mock()

    outcome = guard_create_mapping_rollback(
        **_guard_kwargs(
            read_by_local_id=Mock(return_value=None),
            read_by_external_id=Mock(return_value=None),
            delete_header=delete_header,
            record_conflict_issue=record_conflict_issue,
        )
    )

    assert outcome.state == MISSING
    record_conflict_issue.assert_not_called()
    delete_header.assert_called_once()
    assert outcome.delete_succeeded is True


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_delete_failure_is_captured_not_raised():
    """The original create_mapping exception must survive to the caller (it owns
    the final re-raise) — a compensating-delete failure is reported via the
    outcome, never masked, never itself propagated from this helper."""
    delete_exc = RuntimeError("FK 547")
    outcome = guard_create_mapping_rollback(
        **_guard_kwargs(
            read_by_local_id=Mock(return_value=None),
            read_by_external_id=Mock(return_value=None),
            delete_header=Mock(side_effect=delete_exc),
        )
    )
    assert outcome.state == MISSING
    assert outcome.delete_succeeded is False
    assert outcome.delete_exc is delete_exc


# --- the lock is held across the WHOLE critical section ---------------------
#
# This is the mutation target: if the recheck (or the delete) were ever moved
# outside the `with create_race_lock(...)` block, this test's asserted order
# would break, because entering/exiting the lock would no longer bracket those
# calls. Confirmed red against the pre-U-304 shape (recheck done via a bare
# resolve_mapping_state call with no lock at all) during development — see the
# unit's Gate-2 packet.


@patch(LOCK_PATCH_TARGET)
def test_lock_is_held_across_the_full_conflict_recheck_and_delete(mock_lock):
    call_order = []

    @contextmanager
    def _tracking_lock(*_args, **_kwargs):
        call_order.append("lock_acquired")
        try:
            yield True
        finally:
            call_order.append("lock_released")

    mock_lock.side_effect = _tracking_lock

    by_external = SimpleNamespace(id=9, qbo_purchase_id=555)

    def _read_by_local_id(_local_id):
        call_order.append("read_by_local_id")
        return None

    def _read_by_external_id(_external_id):
        call_order.append("read_by_external_id")
        return by_external

    def _record_conflict_issue(_by_local, _by_external):
        call_order.append("record_conflict_issue")

    def _delete_header():
        call_order.append("delete_header")

    guard_create_mapping_rollback(
        **_guard_kwargs(
            read_by_local_id=_read_by_local_id,
            read_by_external_id=_read_by_external_id,
            record_conflict_issue=_record_conflict_issue,
            delete_header=_delete_header,
        )
    )

    assert call_order == [
        "lock_acquired",
        "read_by_local_id",
        "read_by_external_id",
        "record_conflict_issue",
        "delete_header",
        "lock_released",
    ]


# --- run_identity_fastpath's MISSING self-heal: race_lock_mapping_label ----
#
# Codex's Gate-2 P1 (round 1): locking only the self-heal's FINAL mapping
# insert still leaves apply_fields' header UPDATE unlocked — a rollback's
# delete could land between that UPDATE and the insert, either destroying the
# header a racer just legitimately wrote to, or (if the UPDATE runs first and
# succeeds) racing the insert against the delete with no serialization at all.
# Fixed by moving the lock to wrap BOTH apply_fields and create_mapping,
# opt-in via `race_lock_mapping_label` so every family that does NOT pass it
# (every run_identity_fastpath caller besides Purchase/Invoice — Customer,
# Vendor, Bill, VendorCredit, CompanyInfo, PhysicalAddress, Attachable,
# CostCode, SubCostCode, PaymentTerm, and others) sees zero behavior change.


def _fastpath_kwargs(**overrides):
    direct = SimpleNamespace(id=55)
    defaults = dict(
        qbo_id="QBO-1",
        realm_id="realm-1",
        external_id=901,
        entity_label="Expense",
        external_label="QboPurchase",
        mapping_label="PurchaseExpense",
        read_direct_by_qbo_identity=Mock(return_value=direct),
        read_by_local_id=Mock(return_value=None),  # MISSING on both sides by default
        read_by_external_id=Mock(return_value=None),
        external_id_attr="qbo_purchase_id",
        record_conflict_issue=Mock(),
        conflict_message=Mock(return_value="conflict"),
        create_mapping=Mock(return_value=SimpleNamespace(id=1)),
        apply_fields=Mock(return_value=direct),
    )
    defaults.update(overrides)
    return defaults


@patch(LOCK_PATCH_TARGET)
def test_missing_self_heal_with_race_lock_holds_lock_across_apply_fields_and_create_mapping(mock_lock):
    """The mutation target for Codex's P1: if apply_fields (or create_mapping)
    were ever moved outside the `with create_race_lock(...)` block, this
    asserted order would break."""
    call_order = []

    @contextmanager
    def _tracking_lock(*_args, **_kwargs):
        call_order.append("lock_acquired")
        try:
            yield True
        finally:
            call_order.append("lock_released")

    mock_lock.side_effect = _tracking_lock
    direct = SimpleNamespace(id=55)

    def _apply_fields(_direct):
        call_order.append("apply_fields")
        return direct

    def _create_mapping(_local_id):
        call_order.append("create_mapping")
        return SimpleNamespace(id=1)

    outcome = run_identity_fastpath(
        **_fastpath_kwargs(
            read_direct_by_qbo_identity=Mock(return_value=direct),
            apply_fields=_apply_fields,
            create_mapping=_create_mapping,
            race_lock_mapping_label="PurchaseExpense",
        )
    )

    assert outcome.hit is True
    assert call_order == ["lock_acquired", "apply_fields", "create_mapping", "lock_released"]


@patch(LOCK_PATCH_TARGET)
def test_missing_self_heal_without_race_lock_label_never_touches_the_lock(mock_lock):
    """Backward-compat / zero blast radius: every family that does NOT pass
    race_lock_mapping_label (everything except Purchase/Invoice today) must see
    byte-identical behavior to before U-304 — no lock acquisition at all, even
    on a MISSING self-heal."""
    direct = SimpleNamespace(id=55)
    outcome = run_identity_fastpath(
        **_fastpath_kwargs(read_direct_by_qbo_identity=Mock(return_value=direct))
        # race_lock_mapping_label omitted -> defaults to None.
    )
    assert outcome.hit is True
    mock_lock.assert_not_called()


@patch(LOCK_PATCH_TARGET, _denied_lock)
def test_missing_self_heal_fails_closed_on_lock_timeout():
    """FAIL CLOSED: a lock-acquire timeout on the self-heal path must never
    fall through to writing fields or inserting a mapping row."""
    direct = SimpleNamespace(id=55)
    apply_fields = Mock(return_value=direct)
    create_mapping = Mock()

    with pytest.raises(RuntimeError, match="Could not acquire create/rollback lock"):
        run_identity_fastpath(
            **_fastpath_kwargs(
                read_direct_by_qbo_identity=Mock(return_value=direct),
                apply_fields=apply_fields,
                create_mapping=create_mapping,
                race_lock_mapping_label="PurchaseExpense",
            )
        )
    apply_fields.assert_not_called()
    create_mapping.assert_not_called()


@patch(LOCK_PATCH_TARGET)
def test_missing_self_heal_and_rollback_guard_share_one_lock_key(mock_lock):
    """The actual claim that closes the race: run_identity_fastpath's locked
    self-heal and guard_create_mapping_rollback must acquire sp_getapplock
    under the IDENTICAL resource name for the same (mapping_label,
    external_id) — proven at the pure-function level; the connector-level
    tests below prove the real wiring passes matching values for both."""
    recorded, _recording_lock = _recording_lock_factory()
    mock_lock.side_effect = _recording_lock
    direct = SimpleNamespace(id=55)

    run_identity_fastpath(
        **_fastpath_kwargs(
            read_direct_by_qbo_identity=Mock(return_value=direct),
            mapping_label="PurchaseExpense",
            external_id=901,
            race_lock_mapping_label="PurchaseExpense",
        )
    )
    guard_create_mapping_rollback(
        **_guard_kwargs(
            mapping_label="PurchaseExpense",
            external_id=901,
            local_id=77,
            read_by_local_id=Mock(return_value=None),
            read_by_external_id=Mock(return_value=None),
        )
    )

    assert len(recorded) == 2
    assert recorded[0] == recorded[1] == "qbo_mapping_create:PurchaseExpense:901"


# --- connector-level end-to-end: the real wiring, not a synthetic call ------
#
# Drives InvoiceInvoiceConnector's ACTUAL sync_from_qbo_invoice through both
# the self-heal path (a direct fast-path hit with no mapping row yet) and the
# rollback-guard path (create_mapping fails on the plain CREATE branch),
# recording the real sp_getapplock resource name each uses. Both must resolve
# to the SAME string for the same external QBO id — the concrete proof (not
# just an isolated create_race_lock/run_identity_fastpath unit claim) that the
# wiring in InvoiceInvoiceConnector.sync_from_qbo_invoice actually closes the
# race in production. (PurchaseExpenseConnector's own equivalent test was
# retired U-354 along with race_lock_mapping_label/create_race_lock's use in
# that connector — run_identity_fastpath_dbo_only's own create lock replaces
# it entirely; see test_u283b_purchase_qbo_identity_repoint.py.)


@patch(LOCK_PATCH_TARGET)
def test_invoice_connector_self_heal_and_rollback_share_one_lock_key(mock_lock):
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    recorded, _recording_lock = _recording_lock_factory()
    mock_lock.side_effect = _recording_lock

    def _make_connector():
        mapping_repo = Mock()
        invoice_service = Mock()
        invoice_service.repo = Mock()
        project_service = Mock()
        connector = InvoiceInvoiceConnector(
            mapping_repo=mapping_repo, invoice_service=invoice_service, project_service=project_service,
        )
        connector._get_project_public_id = Mock(return_value="project-pub-1")
        connector._sync_line_items = Mock()
        connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=None)
        return connector, mapping_repo, invoice_service, project_service

    qbo_invoice = SimpleNamespace(
        id=901, qbo_id="INV-77", realm_id="realm-1", customer_ref_value="qbo-customer-1",
        doc_number="5001", txn_date="2026-08-01", due_date="2026-08-15", private_note="draw",
        total_amt=100, sync_token="3",
    )
    one_line = [SimpleNamespace(id=1)]

    # Self-heal leg: a direct fast-path hit with no mapping row anywhere (MISSING).
    connector, mapping_repo, invoice_service, _ = _make_connector()
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", invoice_number="5001", row_version="rv-55")
    invoice_service.read_by_qbo_identity.return_value = direct_hit
    invoice_service.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")
    mapping_repo.read_by_invoice_id.return_value = None
    mapping_repo.read_by_qbo_invoice_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=1)
    connector.sync_from_qbo_invoice(qbo_invoice, one_line)

    # Rollback-guard leg: plain CREATE path, create_mapping's own insert fails
    # for a reason no racer resolves (MISSING on both sides of the recheck too).
    connector2, mapping_repo2, invoice_service2, project_service2 = _make_connector()
    invoice_service2.read_by_qbo_identity.return_value = None  # fast path misses
    mapping_repo2.read_by_qbo_invoice_id.return_value = None
    project_service2.read_by_public_id.return_value = SimpleNamespace(id=42)
    invoice_service2.repo.read_by_invoice_number_and_project_id.return_value = None
    invoice_service2.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    mapping_repo2.read_by_invoice_id.return_value = None
    mapping_repo2.create.side_effect = Exception("UNIQUE constraint violation")
    invoice_service2.delete_by_public_id.return_value = None
    with pytest.raises(Exception, match="UNIQUE constraint violation"):
        connector2.sync_from_qbo_invoice(qbo_invoice, one_line)

    assert len(recorded) == 2
    assert recorded[0] == recorded[1] == "qbo_mapping_create:InvoiceInvoice:901"
