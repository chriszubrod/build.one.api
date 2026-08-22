"""Pure-logic tests for U-293 (Phase-4, lines): repoint the `bill_line_item`
connector's LINE identity resolution off qbo.BillLine / qbo.BillLineItemBillLine
onto dbo.BillLineItem's native QboId (U-238b), scoped to its parent Bill, via
the shared base/identity_fastpath.py::run_line_identity_fastpath helper.

Unlike run_identity_fastpath's 8 existing header/reference callers, a line's
QBO identity is unique only WITHIN its own parent transaction (confirmed
against live prod at Gate-1 — real duplicate QboId values are reused across
different Bills), so the direct-read key is (bill_id, qbo_line_id), never a
bare global QboId. The shared helper's state machine is already exhaustively
tested in tests/test_u293_line_identity_fastpath_helper.py; these tests prove
THIS connector's wiring: the callbacks it hands the helper, that a conflict
never writes to the dbo-identity-matched row, and that the legacy 2-hop +
Shape-B fingerprint fallback keeps working unchanged on a fast-path miss.

Mirrors tests/test_u283_bill_qbo_identity_repoint.py's Section-2 shape.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import (
    BillLineItemConnector,
)


def _make_qbo_bill_line(**overrides):
    defaults = dict(
        id=42,
        qbo_bill_id=4,
        qbo_line_id="1",
        line_num=1,
        description="Landscaping",
        amount=1623.75,
        detail_type="AccountBasedExpenseLineDetail",
        item_ref_value=None,
        item_ref_name=None,
        account_ref_value=None,
        account_ref_name=None,
        customer_ref_value=None,
        customer_ref_name=None,
        class_ref_value=None,
        class_ref_name=None,
        billable_status=None,
        qty=None,
        unit_price=None,
        markup_percent=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    mapping_repo = Mock()
    bill_line_item_service = Mock()
    bill_line_item_service.repo = Mock()
    bill_service = Mock()
    reconciliation_repo = Mock()
    connector = BillLineItemConnector(
        mapping_repo=mapping_repo,
        bill_line_item_service=bill_line_item_service,
        bill_service=bill_service,
        reconciliation_repo=reconciliation_repo,
    )
    bill_service.read_by_id.return_value = SimpleNamespace(id=19146, public_id="bill-pub-1")
    return connector, mapping_repo, bill_line_item_service, reconciliation_repo


def test_raise_line_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(id=42, qbo_line_id="1")
    qbo_side = SimpleNamespace(id=2, bill_line_item_id=9, qbo_bill_line_id=42)
    local_side = SimpleNamespace(id=3, bill_line_item_id=55, qbo_bill_line_id=5)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_bill_line=qbo_bill_line, dbo_line_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
        realm_id="realm-1",
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bill_line_item_identity_conflict"
    assert kwargs["realm_id"] == "realm-1"
    assert "BillLineItem 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboBillLine 5" in kwargs["details"]


def test_raise_line_identity_mapping_conflict_issue_qbo_side_only():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(id=42, qbo_line_id="1")
    qbo_side = SimpleNamespace(id=2, bill_line_item_id=9, qbo_bill_line_id=42)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_bill_line=qbo_bill_line, dbo_line_id=55,
        local_side_mapping=None, qbo_side_mapping=qbo_side,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "BillLineItem 9 (mapping 2)" in kwargs["details"]
    assert "local-side" not in kwargs["details"]
    assert kwargs["realm_id"] == ""  # no realm_id passed -> falls back to ""


def test_raise_line_identity_mapping_conflict_issue_local_side_only():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(id=42, qbo_line_id="1")
    local_side = SimpleNamespace(id=3, bill_line_item_id=55, qbo_bill_line_id=5)

    connector._raise_line_identity_mapping_conflict_issue(
        qbo_bill_line=qbo_bill_line, dbo_line_id=55,
        local_side_mapping=local_side, qbo_side_mapping=None,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboBillLine 5" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_fast_path_hit_conflict_raises_and_never_writes():
    connector, mapping_repo, bill_line_item_service, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    bill_line_item_service.repo.read_by_bill_line_item_id = Mock()
    mapping_repo.read_by_bill_line_item_id.return_value = None
    conflicting = SimpleNamespace(id=2, bill_line_item_id=9, qbo_bill_line_id=qbo_bill_line.id)
    mapping_repo.read_by_qbo_bill_line_id.return_value = conflicting
    # If the fast path fell through (it must not), these would let the legacy
    # branch reach and write/adopt a different local row.
    bill_line_item_service.read_by_id.return_value = SimpleNamespace(
        id=9, public_id="pub-9", row_version="rv-9"
    )
    bill_line_item_service.read_by_bill_id.return_value = []
    bill_line_item_service.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any BillLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bill_line_item_identity_conflict"
    mapping_repo.create.assert_not_called()
    bill_line_item_service.repo.set_qbo_identity.assert_not_called()  # NO identity theft


def test_fast_path_miss_does_not_requery_the_mapping_it_already_ruled_out():
    """Efficiency fix (U-293 Gate-2 /simplify pass, confirmed independently by
    two review lenses): on a MISSING classification, resolve_mapping_state
    (inside run_line_identity_fastpath) already calls
    mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line.id) and gets None back
    — the legacy path used to blindly re-issue that exact same query. Memoized
    now; this is the guaranteed-duplicate-round-trip case (a stale orphan
    whose recycled QboId gives a direct hit but no mapping either side), which
    the MISSING-never-self-heals fix makes the ordinary path, not a rare one.

    Scoped to a scenario where Shape-B successfully adopts (content matches),
    which binds the mapping via mapping_repo.create() directly — deliberately
    NOT the same case as the CREATE-branch tests above, whose own
    self.create_mapping() call does its OWN separate, correctness-motivated
    pre-insert re-check of read_by_qbo_bill_line_id (a race guard, not the
    redundant lookup this fix targets) and would make a naive total-call-count
    assertion here wrong."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1", description="Landscaping", amount=1623.75)
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_line_item_id.return_value = None
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    orphan = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55",
        description="Landscaping", amount=1623.75, quantity=None, rate=None,
    )
    bill_line_item_service.read_by_bill_id.return_value = [orphan]  # Shape-B candidate, content matches
    mapping_repo.create.return_value = SimpleNamespace(id=1, bill_line_item_id=55, qbo_bill_line_id=42)
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_line_item_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    assert result is updated
    mapping_repo.create.assert_called_once_with(bill_line_item_id=55, qbo_bill_line_id=42)
    # Exactly one DB call for this id, not two — the legacy path's own lookup
    # must reuse the fast path's already-known answer.
    mapping_repo.read_by_qbo_bill_line_id.assert_called_once_with(42)


def test_fast_path_hit_conflict_local_side_only_raises_and_attributes_correctly():
    """Connector-level coverage for the local-side-only conflict shape through
    the REAL entry point and the connector's actual read_by_local_id/
    read_by_external_id callback bindings — not just the pure helper (which
    uses generic mocks) or the private formatter called directly. Proves the
    connector wires local_side_mapping/qbo_side_mapping to the RIGHT mapping
    repo methods, not swapped — the details text distinguishes them, so a
    swapped-argument bug would be caught here even though it wouldn't be by
    the qbo-side-only test alone."""
    connector, mapping_repo, bill_line_item_service, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    # Local-side-only: BillLineItem 55's OWN mapping row binds it to a
    # DIFFERENT QboBillLine (7), and no mapping row binds THIS QboBillLine at all.
    local_side = SimpleNamespace(id=3, bill_line_item_id=55, qbo_bill_line_id=7)
    mapping_repo.read_by_bill_line_item_id.return_value = local_side
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    bill_line_item_service.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any BillLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "DIFFERENT QboBillLine 7" in kwargs["details"]
    assert "qbo-side" not in kwargs["details"]


def test_fast_path_hit_conflict_both_sides_crossed_raises_and_names_both():
    """Both directions bound to DIFFERENT partners — neither side may be
    dropped from the recorded issue. Distinct from the local-only and
    qbo-only shapes; a fix that only handles one direction would miss this."""
    connector, mapping_repo, bill_line_item_service, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    local_side = SimpleNamespace(id=3, bill_line_item_id=55, qbo_bill_line_id=7)
    qbo_side = SimpleNamespace(id=2, bill_line_item_id=9, qbo_bill_line_id=qbo_bill_line.id)
    mapping_repo.read_by_bill_line_item_id.return_value = local_side
    mapping_repo.read_by_qbo_bill_line_id.return_value = qbo_side
    bill_line_item_service.update_by_public_id.side_effect = lambda *a, **k: pytest.fail(
        "must not write to any BillLineItem on a detected identity conflict"
    )

    with pytest.raises(ValueError):
        connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "BillLineItem 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboBillLine 7" in kwargs["details"]


def test_fast_path_hit_consistent_updates_and_restamps_identity():
    """A fast-path hit still re-stamps identity unconditionally (U-293 Gate-2
    live-data finding: a row can carry a correct QboId with a stale/NULL
    RealmId — skipping the restamp would freeze that gap forever once the
    fast path starts hitting for it, unlike the legacy path which always
    restamps)."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_line_item_service.update_by_public_id.return_value = updated
    mapping_repo.read_by_bill_line_item_id.return_value = SimpleNamespace(
        id=1, qbo_bill_line_id=qbo_bill_line.id
    )
    mapping_repo.read_by_qbo_bill_line_id.return_value = SimpleNamespace(
        id=1, bill_line_item_id=55
    )

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    assert result is updated
    mapping_repo.create.assert_not_called()
    bill_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="1", realm_id="realm-1"
    )


def test_fast_path_hit_falls_back_to_existing_realm_id_when_call_realm_id_missing():
    """U-293-dw: the atomic-pair guard + existing-realm fallback in
    _apply_line_fields is exercised identically via the fast-path hit branch
    (it's the same shared closure the legacy path uses) — this call passes no
    realm_id, but the direct-hit row already carries one, so the restamp must
    still fire using the row's own realm_id rather than being skipped."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1", realm_id="realm-existing"
    )
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_line_item_service.update_by_public_id.return_value = updated
    mapping_repo.read_by_bill_line_item_id.return_value = SimpleNamespace(
        id=1, qbo_bill_line_id=qbo_bill_line.id
    )
    mapping_repo.read_by_qbo_bill_line_id.return_value = SimpleNamespace(
        id=1, bill_line_item_id=55
    )

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line)

    assert result is updated
    bill_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="1", realm_id="realm-existing"
    )


def test_fast_path_hit_missing_falls_through_without_writing_or_minting():
    """MISSING (direct hit, no mapping on either side) must never self-heal for
    a line — see run_line_identity_fastpath's own contract test suite for why.
    Here: falls through to the legacy 2-hop, which ALSO finds no mapping, so
    it proceeds to Shape-B (no unmapped orphans configured -> no match) and
    ultimately CREATEs a fresh line, never writing to the direct-hit row."""
    connector, mapping_repo, bill_line_item_service, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1", description="Materials", amount=500)
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        description="Unrelated", amount=1,
    )
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_bill_line_item_id.return_value = None
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    bill_line_item_service.read_by_bill_id.return_value = []  # no unmapped orphans to fingerprint-match
    created = SimpleNamespace(id=77, public_id="pub-77")
    bill_line_item_service.create.return_value = created

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    assert result is created
    bill_line_item_service.update_by_public_id.assert_not_called()  # never wrote to the direct-hit row
    # A mapping IS minted, but for the freshly-created line, never for the
    # unrelated direct-hit row (id=55) that MISSING declined to touch.
    mapping_repo.create.assert_called_once_with(bill_line_item_id=77, qbo_bill_line_id=qbo_bill_line.id)
    reconciliation_repo.create.assert_not_called()  # MISSING is not a conflict — nothing to record


def test_fast_path_hit_missing_on_a_stale_orphan_does_not_overwrite_it():
    """The exact U-293 Gate-2 P1 finding, reproduced and proven fixed:
    dbo.BillLineItem #55 is a STALE orphan (QboId='1' stamped long ago, no
    live mapping — stale-line cleanup deleted the mapping+staging row but
    never cleared the local stamp) holding real content ('Landscaping' $100).
    A later pull's QboBillLine reuses that same recycled qbo_line_id='1' for
    a completely UNRELATED new line ('Materials' $500). The fast path's
    direct read finds #55 (same BillId+QboId), but because MISSING never
    self-heals, it falls through; Shape-B's content fingerprint then
    correctly declines to adopt #55 (fingerprints don't match) and creates a
    NEW line instead — #55's real content is never touched."""
    connector, mapping_repo, bill_line_item_service, reconciliation_repo = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1", description="Materials", amount=500)
    stale_orphan = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        description="Landscaping", amount=100, quantity=None, rate=None,
    )
    bill_line_item_service.read_by_qbo_identity.return_value = stale_orphan
    mapping_repo.read_by_bill_line_item_id.return_value = None  # #55 has no mapping (orphaned)
    mapping_repo.read_by_qbo_bill_line_id.return_value = None   # this QBO line is fresh, never mapped
    # Shape B sees #55 as the one unmapped candidate on this bill.
    bill_line_item_service.read_by_bill_id.return_value = [stale_orphan]
    created = SimpleNamespace(id=88, public_id="pub-88")
    bill_line_item_service.create.return_value = created

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    # #55's real content was never touched — the orphan was not overwritten.
    bill_line_item_service.update_by_public_id.assert_not_called()
    # A mapping IS minted for the new line (id=88), never binding the
    # unrelated new content to the stale orphan (id=55).
    mapping_repo.create.assert_called_once_with(bill_line_item_id=88, qbo_bill_line_id=qbo_bill_line.id)
    # A genuinely new line was created for the new, unrelated content instead.
    assert result is created
    bill_line_item_service.create.assert_called_once()
    reconciliation_repo.create.assert_not_called()


def test_fast_path_update_returns_none_raises_runtime_error():
    """ROWVERSION race — RuntimeError, deliberately NOT ValueError (U-291's
    ruling): a plain ValueError would be classified as a permanent skip,
    advancing the watermark past a merely transient race."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    direct_hit = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55", qbo_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = direct_hit
    bill_line_item_service.update_by_public_id.return_value = None
    mapping_repo.read_by_bill_line_item_id.return_value = SimpleNamespace(
        id=1, qbo_bill_line_id=qbo_bill_line.id
    )
    mapping_repo.read_by_qbo_bill_line_id.return_value = SimpleNamespace(
        id=1, bill_line_item_id=55
    )

    with pytest.raises(RuntimeError, match="Failed to update BillLineItem"):
        connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    bill_line_item_service.repo.set_qbo_identity.assert_not_called()


def test_legacy_path_update_returns_none_also_raises_runtime_error():
    """The legacy 'mapping found' branch now reuses the SAME _apply_line_fields
    closure the fast path uses (U-293 consolidation, mirroring
    BillBillConnector's header-level pattern) — one fix covers both call sites
    by construction. Pin it explicitly on the legacy path too."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=1, bill_line_item_id=55, qbo_bill_line_id=qbo_bill_line.id)
    mapping_repo.read_by_qbo_bill_line_id.return_value = existing_mapping
    existing_line = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55")
    bill_line_item_service.read_by_id.return_value = existing_line
    bill_line_item_service.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="Failed to update BillLineItem"):
        connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")


def test_fast_path_miss_falls_back_to_legacy_mapping_table_path():
    """No dbo row carries this identity yet (e.g. QboId is NULL, matching the
    live 'true regression' row found at Gate-1) — the pre-existing 2-hop
    mapping-table path must still run, and now reuses the SAME
    _apply_line_fields closure (proving no duplicated/diverging update logic
    between the two paths)."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = None
    existing_mapping = SimpleNamespace(id=1, bill_line_item_id=55, qbo_bill_line_id=qbo_bill_line.id)
    mapping_repo.read_by_qbo_bill_line_id.return_value = existing_mapping
    existing_line = SimpleNamespace(id=55, public_id="pub-55", row_version="rv-55")
    bill_line_item_service.read_by_id.return_value = existing_line
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bill_line_item_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    bill_line_item_service.read_by_qbo_identity.assert_called_once_with(19146, "1")
    assert result is updated
    bill_line_item_service.repo.set_qbo_identity.assert_called_once()  # legacy path still stamps


def test_fast_path_skipped_entirely_when_no_qbo_line_id():
    """A QBO line with no external line id can't possibly have a dbo-native
    identity match — the fast-path lookup should not even be attempted, and
    the create path (no mapping at all) proceeds as before with the correct
    field mapping (not just a correct-looking mocked return value)."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(
        qbo_line_id=None, description="Materials", amount=250,
    )
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    bill_line_item_service.read_by_bill_id.return_value = []  # Shape B: no orphans to adopt
    created = SimpleNamespace(id=77, public_id="pub-77")
    bill_line_item_service.create.return_value = created

    result = connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    bill_line_item_service.read_by_qbo_identity.assert_not_called()
    assert result is created
    bill_line_item_service.create.assert_called_once_with(
        bill_public_id="bill-pub-1",
        sub_cost_code_id=None,
        project_public_id=None,
        description="Materials",
        quantity=None,
        rate=None,
        amount=250,
        is_billable=None,
        is_billed=None,
        markup=None,
        price=None,
        is_draft=False,
    )


def test_fast_path_direct_lookup_is_scoped_to_this_bill_not_a_bare_qbo_id():
    """The core design point: the direct read is (bill_id, qbo_line_id), never
    a bare global qbo_line_id — a QBO line id like '1' is reused across every
    Bill's first line (confirmed against live prod)."""
    connector, mapping_repo, bill_line_item_service, _ = _build_connector()
    qbo_bill_line = _make_qbo_bill_line(qbo_line_id="1")
    bill_line_item_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    bill_line_item_service.read_by_bill_id.return_value = []
    bill_line_item_service.create.return_value = SimpleNamespace(id=1, public_id="p")

    connector.sync_from_qbo_bill_line(19146, qbo_bill_line, realm_id="realm-1")

    bill_line_item_service.read_by_qbo_identity.assert_called_once_with(19146, "1")
