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
lines.

test_recognition_missing_reproduces_the_phantom_duplicate_RED_BASELINE below
is a red/black-box baseline: it drives the ACTUAL primitive
(run_line_identity_fastpath_dbo_only) with a readopt_candidate that has NO
provenance-recognition step (the exact U-362 shape) to prove the bug is real
and lives in the wiring this file fixes, independent of any refactor risk in
the fix itself.

U-362c (this file's second half, below the "collision" marker) fixes a
RESIDUAL bug in the fix above: LinkedTxnId is the source TRANSACTION id, not
a per-line id, so every sibling invoice line drawn from ONE multi-line
Bill/Expense shares the SAME (InvoiceId, LinkedTxnType, LinkedTxnId) — the
COMMON case (1,354 prod groups / 28,979 lines), not an edge case.
U-362b's `read_by_linked_txn` treated a collision as "not found" (refused,
returned None), which fell through to the same phantom-Manual-duplicate bug
class it exists to close, just via a different door. U-362c makes
`read_by_linked_txn` return the FULL sibling set and tie-breaks to the ONE
sibling matching the incoming QBO line's content + position (see
`_recognize_source_linked_line`'s docstring and `find_stale_identity_orphan`).
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from entities.invoice_line_item.business.model import LinkedTxnSibling
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


def _sibling(line_item, *, line_num=1, qbo_amount=None, qbo_description=None, service_date="2026-07-15"):
    """Wraps an InvoiceLineItem-shaped SimpleNamespace as the LinkedTxnSibling
    `read_by_linked_txn` now returns (U-362c) — content fingerprint fields
    default to the line's OWN amount/description for convenience, but a test
    exercising a content mismatch overrides them explicitly."""
    return LinkedTxnSibling(
        line_item=line_item,
        line_num=line_num,
        qbo_amount=qbo_amount if qbo_amount is not None else line_item.amount,
        qbo_description=qbo_description if qbo_description is not None else line_item.description,
        service_date=service_date,
    )


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
    ili_svc.read_by_linked_txn.return_value = [_sibling(existing)]
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
    ili_svc.read_by_linked_txn.return_value = []
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
    ili_svc.read_by_linked_txn.return_value = [_sibling(existing)]
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
    ili_svc.read_by_linked_txn.return_value = [_sibling(still_live_elsewhere)]
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
    ili_svc.read_by_linked_txn.return_value = [_sibling(still_live_elsewhere)]
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
    ili_svc.read_by_linked_txn.return_value = [_sibling(existing)]
    ili_svc.create.return_value = SimpleNamespace(id=200, public_id="pub-200")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=200, public_id="pub-200", qbo_id="7")

    with patch.object(
        InvoiceLineItemConnector, "_recognize_source_linked_line", return_value=None,
    ):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.create.assert_called_once()  # neutered -> the duplicate is back


# ---------------------------------------------------------------------------
# U-362c — the COLLISION regression: N siblings share one LinkedTxn
# ---------------------------------------------------------------------------


def test_collision_old_refusal_shape_reproduces_the_duplicate_RED_BASELINE():
    """RED baseline, mirroring the module-level RED baseline above: pins the
    RESIDUAL U-362c bug mechanically, independent of the fix below. Drives the
    ACTUAL primitive with a readopt_candidate shaped exactly like PRE-FIX
    U-362b — read_by_linked_txn refuses (returns None) because >1 sibling
    shares the LinkedTxn, so recognition finds nothing, the (empty) Manual
    pool finds nothing, and a fresh Manual duplicate gets minted for the
    SECOND sibling. Not modified by the fix (it pins the bug's mechanics
    independent of the fix)."""
    pre_fix_readopt_candidate = Mock(return_value=None)  # the old fetchone()-shaped refusal
    resolve_candidate = Mock(return_value=SimpleNamespace(id=300, public_id="pub-300"))
    stamp_identity = Mock(return_value=SimpleNamespace(id=300, public_id="pub-300", qbo_id="11"))

    outcome = run_line_identity_fastpath_dbo_only(
        parent_local_id=19146,
        qbo_line_id="11",
        entity_label="InvoiceLineItem",
        external_label="QboInvoiceLine",
        lock_resource_label="InvoiceLineItem",
        read_direct_by_parent_and_qbo_line_id=Mock(return_value=None),
        readopt_candidate=pre_fix_readopt_candidate,
        resolve_candidate=resolve_candidate,
        stamp_identity=stamp_identity,
    )

    pre_fix_readopt_candidate.assert_called_once()
    resolve_candidate.assert_called_once()  # ...found nothing, so a fresh line was minted
    assert outcome.entity.id == 300  # the phantom duplicate for the SECOND sibling


def test_collision_two_unstamped_siblings_different_amounts_each_incoming_line_recognizes_own_sibling():
    """The money-primary collision regression (U-362c): two source-linked
    UNSTAMPED siblings share one LinkedTxn (a real multi-line Bill/Expense)
    with DIFFERENT amounts. Each incoming QBO line must recognize its OWN
    sibling by content — never both racing for the same one, never a phantom
    Manual, never a wrong rebind."""
    sib_a = _unstamped_source_linked_line(id=99, public_id="pub-99", amount=Decimal("500"), description="Materials")
    sib_b = _unstamped_source_linked_line(id=100, public_id="pub-100", amount=Decimal("300"), description="Labor")
    siblings = [
        _sibling(sib_a, line_num=1, qbo_amount=Decimal("500"), qbo_description="Materials"),
        _sibling(sib_b, line_num=2, qbo_amount=Decimal("300"), qbo_description="Labor"),
    ]

    connector1, ili_svc1, _, _ = _build_connector()
    ili_svc1.read_by_linked_txn.return_value = siblings
    ili_svc1.read_by_qbo_identity.return_value = None
    ili_svc1.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc1.read_by_id.return_value = SimpleNamespace(id=99, public_id="pub-99", qbo_id="10", realm_id="realm-1")
    qbo_line_1 = _make_qbo_line(
        qbo_line_id="10", description="Materials", amount=Decimal("500"), line_num=1, linked_txn_id="RC-500",
    )

    connector1.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line_1, frozenset({"10", "11"}), realm_id="realm-1")

    ili_svc1.create.assert_not_called()  # NO phantom Manual
    ili_svc1.update_by_public_id.assert_called_once()
    assert ili_svc1.update_by_public_id.call_args.args == ("pub-99",)  # recognized sib_a, not sib_b

    connector2, ili_svc2, _, _ = _build_connector()
    ili_svc2.read_by_linked_txn.return_value = siblings
    ili_svc2.read_by_qbo_identity.return_value = None
    ili_svc2.update_by_public_id.return_value = SimpleNamespace(id=100, public_id="pub-100")
    ili_svc2.read_by_id.return_value = SimpleNamespace(id=100, public_id="pub-100", qbo_id="11", realm_id="realm-1")
    qbo_line_2 = _make_qbo_line(
        qbo_line_id="11", description="Labor", amount=Decimal("300"), line_num=2, linked_txn_id="RC-500",
    )

    connector2.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line_2, frozenset({"10", "11"}), realm_id="realm-1")

    ili_svc2.create.assert_not_called()  # NO phantom Manual for the second line either
    ili_svc2.update_by_public_id.assert_called_once()
    assert ili_svc2.update_by_public_id.call_args.args == ("pub-100",)  # recognized sib_b, not sib_a again


def test_collision_same_amount_siblings_paired_by_line_num_position():
    """Money-neutral case: two siblings share content (same amount and
    description) — provenance LineNum (the SOURCE document's own line order,
    unlike dbo.Id which is just local creation order) pairs them 1:1 with the
    two incoming QBO lines instead of both racing for the same one. Each
    connector call does a FRESH DB read (side_effect simulates that, exactly
    like the real repo would after the first line's stamp commits)."""
    sib_a = _unstamped_source_linked_line(id=99, public_id="pub-99", amount=Decimal("500"), description="Materials")
    sib_b = _unstamped_source_linked_line(id=100, public_id="pub-100", amount=Decimal("500"), description="Materials")
    unstamped_siblings = [
        _sibling(sib_a, line_num=1, qbo_amount=Decimal("500"), qbo_description="Materials"),
        _sibling(sib_b, line_num=2, qbo_amount=Decimal("500"), qbo_description="Materials"),
    ]
    # After the first call stamps sib_a with qbo_line_id "10", a fresh DB read
    # shows sib_a's qbo_id now live -- the SECOND call must see this state.
    sib_a_stamped = _unstamped_source_linked_line(
        id=99, public_id="pub-99", amount=Decimal("500"), description="Materials", qbo_id="10",
    )
    post_stamp_siblings = [
        _sibling(sib_a_stamped, line_num=1, qbo_amount=Decimal("500"), qbo_description="Materials"),
        _sibling(sib_b, line_num=2, qbo_amount=Decimal("500"), qbo_description="Materials"),
    ]

    connector1, ili_svc1, _, _ = _build_connector()
    ili_svc1.read_by_linked_txn.return_value = unstamped_siblings
    ili_svc1.read_by_qbo_identity.return_value = None
    ili_svc1.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc1.read_by_id.return_value = SimpleNamespace(id=99, public_id="pub-99", qbo_id="10", realm_id="realm-1")
    qbo_line_1 = _make_qbo_line(
        qbo_line_id="10", description="Materials", amount=Decimal("500"), line_num=1, linked_txn_id="RC-500",
    )

    connector1.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line_1, frozenset({"10", "11"}), realm_id="realm-1")

    assert ili_svc1.update_by_public_id.call_args.args == ("pub-99",)  # picked LineNum=1 first

    connector2, ili_svc2, _, _ = _build_connector()
    ili_svc2.read_by_linked_txn.return_value = post_stamp_siblings
    ili_svc2.read_by_qbo_identity.return_value = None
    ili_svc2.update_by_public_id.return_value = SimpleNamespace(id=100, public_id="pub-100")
    ili_svc2.read_by_id.return_value = SimpleNamespace(id=100, public_id="pub-100", qbo_id="11", realm_id="realm-1")
    qbo_line_2 = _make_qbo_line(
        qbo_line_id="11", description="Materials", amount=Decimal("500"), line_num=2, linked_txn_id="RC-500",
    )

    connector2.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line_2, frozenset({"10", "11"}), realm_id="realm-1")

    ili_svc2.create.assert_not_called()  # NOT a phantom duplicate
    assert ili_svc2.update_by_public_id.call_args.args == ("pub-100",)  # paired with sib_b, not sib_a again


def test_collision_content_mismatch_falls_through_no_wrong_rebind():
    """A sibling exists under the LinkedTxn but its content doesn't match the
    incoming QBO line (amount/description differ) — must NOT bind to it (a
    mismatch would overwrite the wrong line's amount and un-bill its true
    source, worse than a duplicate). Falls through to the Manual pool / a
    fresh create instead."""
    connector, ili_svc, _, _ = _build_connector()
    sib = _unstamped_source_linked_line(id=99, public_id="pub-99", amount=Decimal("500"), description="Materials")
    ili_svc.read_by_linked_txn.return_value = [
        _sibling(sib, line_num=1, qbo_amount=Decimal("500"), qbo_description="Materials"),
    ]
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.read_by_invoice_id.return_value = []  # no Manual fallback candidate either
    ili_svc.create.return_value = SimpleNamespace(id=201, public_id="pub-201")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=201, public_id="pub-201", qbo_id="12")
    qbo_line = _make_qbo_line(
        qbo_line_id="12", description="Different Item", amount=Decimal("999"), linked_txn_id="RC-500",
    )

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"12"}), realm_id="realm-1")

    ili_svc.update_by_public_id.assert_not_called()  # never bound to the mismatched sibling
    ili_svc.create.assert_called_once()


def test_collision_mutation_neutering_content_filter_causes_wrong_rebind():
    """Direct mutation-proof of the tie-break itself: patch
    find_stale_identity_orphan (as imported into the connector module) to
    ignore content/position and always return the FIRST sibling — simulating
    what would happen if the tie-break's filtering were silently disabled.
    Confirms the WRONG sibling gets bound to the incoming QBO line (a
    content-blind rebind — money corruption, worse than a duplicate),
    proving the real content+position filter is load-bearing."""
    sib_a = _unstamped_source_linked_line(id=99, public_id="pub-99", amount=Decimal("500"), description="Materials")
    sib_b = _unstamped_source_linked_line(id=100, public_id="pub-100", amount=Decimal("300"), description="Labor")
    siblings = [
        _sibling(sib_a, line_num=1, qbo_amount=Decimal("500"), qbo_description="Materials"),
        _sibling(sib_b, line_num=2, qbo_amount=Decimal("300"), qbo_description="Labor"),
    ]
    connector, ili_svc, _, _ = _build_connector()
    ili_svc.read_by_linked_txn.return_value = siblings
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="pub-99", qbo_id="11")
    # QBO line's OWN content is "Labor"/300 -- correctly pairs with sib_b,
    # never sib_a. A neutered matcher that ignores content picks sib_a
    # (siblings[0]) regardless.
    qbo_line_2 = _make_qbo_line(
        qbo_line_id="11", description="Labor", amount=Decimal("300"), line_num=2, linked_txn_id="RC-500",
    )

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service."
        "find_stale_identity_orphan",
        return_value=siblings[0],
    ):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line_2, frozenset({"11"}), realm_id="realm-1")

    # The neutered matcher wrongly bound the "Labor"/300 QBO line to sib_a
    # ("Materials"/500) -- exactly the content-blind-rebind class U-362c's
    # real filter prevents.
    assert ili_svc.update_by_public_id.call_args.args == ("pub-99",)


def test_collision_stamp_failure_on_first_sibling_does_not_mispair_the_second_line():
    """Gate-2 adversarial finding (Codex, 2026-09-03): a stamp failure on the
    FIRST sibling of a same-content collision leaves it still eligible (its
    qbo_id stays None) even though ITS OWN incoming QBO line's Line.Id
    ("10") is already live at the header level (live_qbo_line_ids is built
    from what QBO returned for the whole pull, independent of whether the
    local stamp actually landed). A tie-break that picked purely by "first
    eligible in LineNum/.id order" would then wrongly grab the still-
    unstamped LineNum=1 sibling for the SECOND incoming line (LineNum=2)
    too -- an identity swap, not just a duplicate. The exact-LineNum-match
    preference in `_recognize_source_linked_line`'s position_key must win
    over plain position order here."""
    connector, ili_svc, _, _ = _build_connector()
    # sib_a's stamp for its own line_num=1 QBO line ("10") FAILED earlier in
    # this same pull -- still qbo_id=None, still "eligible" by the theft
    # guard (None is never a live id).
    sib_a_still_unstamped = _unstamped_source_linked_line(
        id=99, public_id="pub-99", amount=Decimal("500"), description="Materials",
    )
    sib_b = _unstamped_source_linked_line(id=100, public_id="pub-100", amount=Decimal("500"), description="Materials")
    siblings = [
        _sibling(sib_a_still_unstamped, line_num=1, qbo_amount=Decimal("500"), qbo_description="Materials"),
        _sibling(sib_b, line_num=2, qbo_amount=Decimal("500"), qbo_description="Materials"),
    ]
    ili_svc.read_by_linked_txn.return_value = siblings
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=100, public_id="pub-100")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=100, public_id="pub-100", qbo_id="11", realm_id="realm-1")
    # Processing the SECOND line (line_num=2, QBO id "11"). "10" is already
    # live at the header level, but sib_a's LOCAL stamp never landed.
    qbo_line_2 = _make_qbo_line(
        qbo_line_id="11", description="Materials", amount=Decimal("500"), line_num=2, linked_txn_id="RC-500",
    )

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line_2, frozenset({"10", "11"}), realm_id="realm-1")

    ili_svc.create.assert_not_called()  # NOT a phantom duplicate
    # Correctly paired with sib_b (LineNum=2, an EXACT match on the incoming
    # line's own line_num), never the still-eligible sib_a (LineNum=1).
    assert ili_svc.update_by_public_id.call_args.args == ("pub-100",)


def test_collision_identical_content_and_missing_line_num_falls_back_to_id_order_deterministically():
    """The genuinely-irreducible tie: two DIFFERENT local siblings share
    identical content AND neither has a recorded LineNum (e.g. legacy
    provenance rows backfilled before LineNum tracking, or a QBO response
    that omitted it). No signal exists to prefer one over the other -- the
    tie-break falls back to stable dbo.Id order, deterministically and
    without raising, same documented fallback as any other position_key
    non-match. (Money-neutral either way: same amount, same description.)"""
    connector, ili_svc, _, _ = _build_connector()
    sib_a = _unstamped_source_linked_line(id=99, public_id="pub-99", amount=Decimal("500"), description="Materials")
    sib_b = _unstamped_source_linked_line(id=100, public_id="pub-100", amount=Decimal("500"), description="Materials")
    siblings = [
        _sibling(sib_a, line_num=None, qbo_amount=Decimal("500"), qbo_description="Materials"),
        _sibling(sib_b, line_num=None, qbo_amount=Decimal("500"), qbo_description="Materials"),
    ]
    ili_svc.read_by_linked_txn.return_value = siblings
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="pub-99", qbo_id="11")
    qbo_line = _make_qbo_line(
        qbo_line_id="11", description="Materials", amount=Decimal("500"), line_num=None, linked_txn_id="RC-500",
    )

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"11"}), realm_id="realm-1")

    ili_svc.create.assert_not_called()  # NOT a phantom duplicate
    assert ili_svc.update_by_public_id.call_args.args == ("pub-99",)  # deterministic: lower dbo.Id wins


def test_collision_negative_zero_amount_still_content_matches_positive_zero():
    """Gate-2 adversarial finding (Claude reviewer, 2026-09-03):
    `Decimal("-0.00").normalize()` formats as `"-0"` while
    `Decimal("0.00").normalize()` formats as `"0"` -- two numerically
    IDENTICAL amounts (`Decimal("-0.00") == Decimal("0.00")` is `True`) that
    would fingerprint as different strings and miss a genuine content match
    for a real $0.00 draw line. `_normalize_for_fingerprint` cancels the
    negative-zero sign (`+ 0`) before formatting -- confirm a sibling whose
    provenance carries positive zero still recognizes an incoming QBO line
    reporting negative zero (and vice versa)."""
    connector, ili_svc, _, _ = _build_connector()
    sib = _unstamped_source_linked_line(id=99, public_id="pub-99", amount=Decimal("0.00"), description="Retainage")
    ili_svc.read_by_linked_txn.return_value = [
        _sibling(sib, line_num=1, qbo_amount=Decimal("0.00"), qbo_description="Retainage"),
    ]
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="pub-99", qbo_id="7")
    qbo_line = _make_qbo_line(
        qbo_line_id="7", description="Retainage", amount=Decimal("-0.00"), line_num=1, linked_txn_id="RC-500",
    )

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.create.assert_not_called()  # NOT a phantom duplicate
    assert ili_svc.update_by_public_id.call_args.args == ("pub-99",)


def test_collision_recognition_uses_provenance_content_not_the_users_live_edit():
    """Codex Gate-2 finding (P3, 2026-09-03): the sibling's OWN
    InvoiceLineItem.description/amount are user-editable (a human can edit
    an invoice line on the web UI) and must never feed the recognition
    fingerprint -- only the immutable provenance QboAmount/QboDescription
    snapshot may. Simulates a human having edited the live line's amount
    AFTER it was originally drawn, while the provenance snapshot (frozen at
    draw time) still matches the incoming QBO line exactly -- recognition
    must still succeed via provenance, ignoring the edited live fields."""
    connector, ili_svc, _, _ = _build_connector()
    # Live dbo fields were human-edited to $550 / "Materials (adjusted)" --
    # provenance still carries the ORIGINAL $500 / "Materials" QBO snapshot.
    edited_live_line = _unstamped_source_linked_line(
        id=99, public_id="pub-99", amount=Decimal("550"), description="Materials (adjusted)",
    )
    ili_svc.read_by_linked_txn.return_value = [
        LinkedTxnSibling(
            line_item=edited_live_line, line_num=1,
            qbo_amount=Decimal("500"), qbo_description="Materials", service_date="2026-07-15",
        ),
    ]
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=99, public_id="pub-99")
    ili_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="pub-99", qbo_id="7")
    qbo_line = _make_qbo_line(qbo_line_id="7", description="Materials", amount=Decimal("500"), linked_txn_id="RC-500")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"7"}), realm_id="realm-1")

    ili_svc.create.assert_not_called()  # recognized via provenance, NOT a phantom duplicate
    assert ili_svc.update_by_public_id.call_args.args == ("pub-99",)
