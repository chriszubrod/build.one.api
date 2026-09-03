"""
U-362b — fix a P0 money double-count in U-362 (410c8172, not deployed, halted
at Gate-2). A Gate-2 adversarial workflow found: retiring
qbo.InvoiceLineItemInvoiceLine removed the ONLY mechanism that let a re-pulled
QBO invoice line find its way back to an existing SOURCE-LINKED local
InvoiceLineItem (SourceType=BillLineItem/ExpenseLineItem/BillCreditLineItem —
created by the billing/complete flow, not the pull path, then linked via
LinkInvoiceLineItemSource). 70 live prod rows are mapped-but-UNSTAMPED
(dbo.QboId NULL, all source-linked, 0 Manual): on deploy, their next routine
re-pull direct-HITs nothing (no QboId), the Manual-only re-adopt correctly
excludes them (they're not Manual), and `_create_line` mints a phantom Manual
duplicate with the same amount under the same invoice — an invoice draw
double-count.

Fix: InvoiceLineItemSourceProvenance (U-272, write-only until this unit) gains
a READ path — (InvoiceId, LinkedTxnType, LinkedTxnId) recognizes an existing
source-linked local line stable across a QBO Line.Id regeneration. Composed
into the SAME `readopt_candidate` primitive slot as the existing Manual-only
fingerprint matcher, tried FIRST (broader scope, any source_type) — the
Manual-only matcher stays exactly as-is as the fallback for genuinely Manual
lines. Also restores the invoice_line_item entry backfill_qbo_identity_lines.py
needs to heal the 70 existing rows before deploy (a separate, orthogonal fix —
this file's tests only cover the code-fix's correctness).

test_recognition_missing_reproduces_the_phantom_duplicate_RED_BASELINE below
is a red/black-box baseline: it drives the ACTUAL primitive
(run_line_identity_fastpath_dbo_only) with a readopt_candidate that has NO
provenance-recognition step (the exact U-362 shape) to prove the bug is real
and lives in the wiring this file fixes, independent of any refactor risk in
the fix itself.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.base.identity_fastpath import run_line_identity_fastpath_dbo_only
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
    InvoiceLineItemConnector,
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_line(**overrides):
    defaults = dict(
        id=42,
        qbo_invoice_id=4,
        qbo_line_id="7",
        description="Materials",
        amount=Decimal("500"),
        unit_price=None,
        qty=None,
        line_num=1,
        service_date="2026-07-15",
        linked_txn_type="ReimburseCharge",
        linked_txn_id="RC-500",
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _unstamped_source_linked_line(**overrides):
    """A live prod shape: created by the billing flow (SourceType=BillLineItem),
    linked via LinkInvoiceLineItemSource, source-provenance stamped on its last
    successful pull under the old mapping-based code — but dbo.QboId was never
    backfilled. Exactly the 70-row gap."""
    defaults = dict(
        id=99, public_id="pub-99", row_version="rv-99", invoice_id=19146,
        source_type="BillLineItem", bill_line_item_id=42, amount=Decimal("500"),
        description="Materials", qbo_id=None, realm_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = InvoiceLineItemConnector()
    ili_svc = Mock()
    ili_svc.repo = Mock()
    ili_svc.read_by_invoice_id.return_value = []
    invoice_service = Mock()
    reconciliation_repo = Mock()
    connector.invoice_line_item_service = ili_svc
    connector.invoice_service = invoice_service
    connector.reconciliation_repo = reconciliation_repo
    return connector, ili_svc, invoice_service, reconciliation_repo


# --- RED baseline: prove the bug lives in the primitive wiring, not a mock artifact ---


def test_recognition_missing_reproduces_the_phantom_duplicate_RED_BASELINE():
    """Black-box repro against the real run_line_identity_fastpath_dbo_only,
    with a readopt_candidate shaped exactly like pre-fix U-362 (Manual-fingerprint
    only, no provenance step). Confirms: a source-linked, unstamped local line
    is NOT found, and resolve_candidate (a fresh Manual create) is the only
    path reached — the phantom duplicate. This is the failure this unit fixes;
    it must go RED against the un-fixed shape and is not itself modified by
    the fix below (it pins the bug's mechanics independent of the fix)."""
    read_direct = Mock(return_value=None)  # MISS: the 70 rows carry QboId=NULL
    manual_only_readopt = Mock(return_value=None)  # exactly U-362's Manual-only pool: no BillLineItem candidate
    resolve_candidate = Mock(return_value=SimpleNamespace(id=200, public_id="pub-200"))
    stamp_identity = Mock(return_value=SimpleNamespace(id=200, public_id="pub-200", qbo_id="7"))

    outcome = run_line_identity_fastpath_dbo_only(
        parent_local_id=19146,
        qbo_line_id="7",
        entity_label="InvoiceLineItem",
        external_label="QboInvoiceLine",
        lock_resource_label="InvoiceLineItem",
        read_direct_by_parent_and_qbo_line_id=read_direct,
        readopt_candidate=manual_only_readopt,
        resolve_candidate=resolve_candidate,
        stamp_identity=stamp_identity,
    )

    manual_only_readopt.assert_called_once()  # the pool WAS consulted...
    resolve_candidate.assert_called_once()  # ...found nothing, so a fresh line was minted
    assert outcome.entity.id == 200  # the phantom duplicate, not the existing source-linked row


# --- GREEN: the fix recognizes the source-linked line instead ---


def test_miss_recognizes_unstamped_source_linked_line_before_creating():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line()
    ili_svc.read_by_qbo_identity.return_value = None
    existing = _unstamped_source_linked_line()
    ili_svc.read_by_linked_txn.return_value = existing
    updated = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc.update_by_public_id.return_value = updated
    reread = SimpleNamespace(id=99, public_id="pub-99", qbo_id="7", realm_id="realm-1")
    ili_svc.read_by_id.return_value = reread

    result = connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    assert result is reread
    ili_svc.read_by_linked_txn.assert_called_once_with(19146, "ReimburseCharge", "RC-500")
    ili_svc.create.assert_not_called()  # NO phantom Manual line
    ili_svc.update_by_public_id.assert_called_once()
    assert ili_svc.update_by_public_id.call_args.args == ("pub-99",)
    # source_type is PRESERVED (BillLineItem), not reset to Manual — amount
    # matches (500 == 500), so _apply_line_fields' existing preserve-on-match
    # logic keeps the source link intact.
    assert ili_svc.update_by_public_id.call_args.kwargs["source_type"] == "BillLineItem"
    ili_svc.repo.set_qbo_identity.assert_called_once_with(id=99, qbo_id="7", realm_id="realm-1")


def test_miss_recognition_scoped_to_invoice_id_via_the_sproc_not_python():
    """The recognition read is invoice-scoped at the SQL layer (WHERE ili.
    InvoiceId = @InvoiceId) — the connector must pass the CURRENT invoice_id,
    never search cross-invoice."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line()
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.read_by_linked_txn.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=200, public_id="pub-200")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=200, public_id="pub-200", qbo_id="7")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.read_by_linked_txn.assert_called_once_with(19146, "ReimburseCharge", "RC-500")


def test_miss_falls_through_to_manual_fingerprint_when_no_linked_txn():
    """A QBO invoice line with no LinkedTxn (a genuinely Manual-originated QBO
    line) skips provenance recognition entirely and reaches the EXISTING
    Manual-fingerprint readopt unchanged — this fix is additive, not a
    replacement of the Manual-only matcher."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(linked_txn_type=None, linked_txn_id=None)
    ili_svc.read_by_qbo_identity.return_value = None
    stale_manual = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", invoice_id=19146,
        source_type="Manual", amount=Decimal("500"), description="Materials", qbo_id="1",
    )
    ili_svc.read_by_invoice_id.return_value = [stale_manual]
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated
    ili_svc.read_by_id.return_value = SimpleNamespace(id=55, public_id="pub-55", qbo_id="7")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.read_by_linked_txn.assert_not_called()
    ili_svc.create.assert_not_called()  # Manual-fingerprint readopt still catches it
    ili_svc.update_by_public_id.assert_called_once()
    assert ili_svc.update_by_public_id.call_args.args == ("pub-55",)


def test_miss_creates_fresh_manual_when_neither_recognition_path_matches():
    """The genuinely-new-line case must still work: no direct hit, no
    provenance match, no Manual fingerprint match -> fresh CREATE, unchanged."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(linked_txn_type=None, linked_txn_id=None)
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.read_by_invoice_id.return_value = []
    ili_svc.create.return_value = SimpleNamespace(id=201, public_id="pub-201")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=201, public_id="pub-201", qbo_id="7")

    result = connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.create.assert_called_once()
    assert result.id == 201


def test_recognized_line_never_rolled_back_on_stamp_failure():
    """Same decision-§2 shape as the Manual-fingerprint readopt: a recognized
    source-linked row is REAL, pre-existing data (likely FK'd from completion/
    draw records) — a stamp failure must leave it untouched, never deleted."""
    connector, ili_svc, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line()
    ili_svc.read_by_qbo_identity.return_value = None
    existing = _unstamped_source_linked_line()
    ili_svc.read_by_linked_txn.return_value = existing
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.delete_by_public_id.assert_not_called()  # never rolled back
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "ili_line_readopt_failed"


def test_recognition_excludes_a_candidate_whose_current_identity_is_still_live():
    """Theft guard, mirrored from find_stale_identity_orphan's own rationale:
    if the recognized row's CURRENT qbo_id is in live_qbo_line_ids (bound
    elsewhere in this same pull), it must not be stolen. Falls through to a
    fresh Manual create rather than risk corrupting a different line's
    identity — an extreme edge case (would mean a data-model invariant broke
    elsewhere), but defended anyway."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="7")
    ili_svc.read_by_qbo_identity.return_value = None
    still_live_elsewhere = _unstamped_source_linked_line(qbo_id="3")  # bound to a DIFFERENT live line
    ili_svc.read_by_linked_txn.return_value = still_live_elsewhere
    ili_svc.read_by_invoice_id.return_value = []  # no Manual fallback candidate either
    ili_svc.create.return_value = SimpleNamespace(id=202, public_id="pub-202")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=202, public_id="pub-202", qbo_id="7")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"3", "7"}), realm_id="realm-1")

    ili_svc.update_by_public_id.assert_not_called()
    ili_svc.create.assert_called_once()


def test_recognition_theft_guard_normalizes_types_before_comparing():
    """This codebase has a documented str-vs-int QBO id-keyspace history
    (feedback_qbo_dbo_id_keyspaces) — find_stale_identity_orphan normalizes
    both sides through normalize_qbo_id specifically because of it. The
    source-linked theft guard must do the same: an int-typed candidate.qbo_id
    against a str-typed live_qbo_line_ids member (or vice versa) must still
    be recognized as the SAME id, not silently pass a raw `in` check that
    would let a still-live line be stolen."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="7")
    ili_svc.read_by_qbo_identity.return_value = None
    # candidate.qbo_id is INT 3; live_qbo_line_ids carries STR "3" (as the
    # connector itself always builds it, from QboInvoiceLine.qbo_line_id) --
    # a real caller mismatch this codebase's own history warns about.
    still_live_elsewhere = _unstamped_source_linked_line(qbo_id=3)
    ili_svc.read_by_linked_txn.return_value = still_live_elsewhere
    ili_svc.read_by_invoice_id.return_value = []
    ili_svc.create.return_value = SimpleNamespace(id=202, public_id="pub-202")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=202, public_id="pub-202", qbo_id="7")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"3", "7"}), realm_id="realm-1")

    ili_svc.update_by_public_id.assert_not_called()  # still correctly excluded
    ili_svc.create.assert_called_once()


# --- mutation-proof harness: neuter the recognition step, confirm the duplicate returns ---


def test_mutation_neutering_recognition_reintroduces_the_phantom_duplicate():
    """Direct mutation-proof of the fix itself (not just the primitive): patch
    InvoiceLineItemConnector to skip provenance recognition (return None
    unconditionally, simulating the pre-fix connector) and confirm the exact
    phantom-duplicate regression returns — this test must fail (go RED) if the
    recognition step is ever silently disabled."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line()
    ili_svc.read_by_qbo_identity.return_value = None
    existing = _unstamped_source_linked_line()
    ili_svc.read_by_linked_txn.return_value = existing
    ili_svc.create.return_value = SimpleNamespace(id=200, public_id="pub-200")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=200, public_id="pub-200", qbo_id="7")

    with patch.object(
        InvoiceLineItemConnector, "_recognize_source_linked_line", return_value=None,
    ):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.create.assert_called_once()  # neutered -> the duplicate is back
