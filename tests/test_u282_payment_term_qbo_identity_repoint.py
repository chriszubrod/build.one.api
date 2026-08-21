"""Pure-logic tests for U-282 (Phase-4, term repoint): repoint the `term` connector
family's identity resolution off qbo.Term / qbo.TermPaymentTerm onto dbo.PaymentTerm's
native QboId/RealmId (Phase 2, U-238c) + QboActive mirror (U-275).

Mirrors test_u278_vendorcredit_qbo_identity_repoint.py's Section 1/2 shape exactly —
PaymentTerm carries no row-level RBAC (like Customer, unlike BillCredit/Project), so
PaymentTermService.read_by_qbo_identity is a bare passthrough, no access assertion.
The connector's hard-stop-on-conflict path is built in from day one here (no U-276-style
hotfix needed) — the conflict tests below assert the raise directly, not a since-fixed
fall-through.

Covers:
  1. PaymentTermRepository.read_by_qbo_identity (sproc call shape) + PaymentTermService
     .read_by_qbo_identity (bare passthrough, no RBAC).
  2. TermPaymentTermConnector's direct-identity fast path: hit updates without the
     mapping-table hop + self-heals a missing mapping row; conflict (qbo-side,
     local-side, two-row-crossed) hard-stops via raise + recorded reconciliation issue;
     miss falls through to the pre-existing mapping-table path unchanged; legacy path
     still (re-)stamps identity for rows that predate identity stamping.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.term.connector.payment_term.business.service import (
    TermPaymentTermConnector,
)

TERM_SERVICE = "integrations.intuit.qbo.term.connector.payment_term.business.service"


def _make_qbo_term(**overrides):
    defaults = dict(
        id=30,
        qbo_id="T-99",
        realm_id="realm-1",
        name="Net 30",
        type="STANDARD",
        due_days=30,
        day_of_month_due=None,
        discount_percent=None,
        discount_days=None,
        active=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo-level sproc call shape ---


def test_payment_term_repo_read_by_qbo_identity_calls_sproc():
    from entities.payment_term.persistence.repo import PaymentTermRepository

    repo = PaymentTermRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.payment_term.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.payment_term.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("T-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadPaymentTermByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "T-99", "RealmId": "realm-1"}


def test_payment_term_service_read_by_qbo_identity_is_bare_passthrough():
    """PaymentTerm carries no row-level RBAC (unlike BillCredit/Project) — the new
    method must be a bare passthrough, matching Customer's template, not BillCredit's
    assert_can_access_bill_credit-gated variant."""
    from entities.payment_term.business.service import PaymentTermService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = SimpleNamespace(id=55)
    service = PaymentTermService(repo=repo)

    result = service.read_by_qbo_identity("T-99", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("T-99", "realm-1")
    assert result.id == 55


# --- Section 2: TermPaymentTermConnector fast path ---


def _build_term_connector():
    mapping_repo = Mock()
    payment_term_service = Mock()
    payment_term_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = TermPaymentTermConnector(
        mapping_repo=mapping_repo,
        payment_term_service=payment_term_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, payment_term_service, reconciliation_repo


def test_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30)
    mapping_repo.read_by_payment_term_id.return_value = SimpleNamespace(id=1, qbo_term_id=30)
    mapping_repo.read_by_qbo_term_id.return_value = SimpleNamespace(id=1, payment_term_id=55)

    state, _, _ = connector._resolve_mapping_state(payment_term_id=55, qbo_term=qbo_term)

    assert state == "consistent"


def test_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30)
    mapping_repo.read_by_payment_term_id.return_value = None
    mapping_repo.read_by_qbo_term_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(payment_term_id=55, qbo_term=qbo_term)

    assert state == "missing"


def test_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30)
    mapping_repo.read_by_payment_term_id.return_value = None
    mapping_repo.read_by_qbo_term_id.return_value = SimpleNamespace(id=2, payment_term_id=9)

    state, by_payment_term, by_qbo_term = connector._resolve_mapping_state(
        payment_term_id=55, qbo_term=qbo_term
    )

    assert state == "conflict"
    assert by_payment_term is None
    assert by_qbo_term.payment_term_id == 9


def test_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30)
    mapping_repo.read_by_payment_term_id.return_value = SimpleNamespace(id=3, qbo_term_id=5)
    mapping_repo.read_by_qbo_term_id.return_value = None

    state, by_payment_term, by_qbo_term = connector._resolve_mapping_state(
        payment_term_id=55, qbo_term=qbo_term
    )

    assert state == "conflict"
    assert by_payment_term.qbo_term_id == 5
    assert by_qbo_term is None


def test_resolve_mapping_state_two_row_crossed_conflict():
    connector, mapping_repo, _, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30)
    mapping_repo.read_by_payment_term_id.return_value = SimpleNamespace(id=3, qbo_term_id=5)
    mapping_repo.read_by_qbo_term_id.return_value = SimpleNamespace(id=2, payment_term_id=9)

    state, by_payment_term, by_qbo_term = connector._resolve_mapping_state(
        payment_term_id=55, qbo_term=qbo_term
    )

    assert state == "conflict"
    assert by_payment_term.qbo_term_id == 5
    assert by_qbo_term.payment_term_id == 9


def test_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, payment_term_id=9, qbo_term_id=30)
    local_side = SimpleNamespace(id=3, payment_term_id=55, qbo_term_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_term=qbo_term, dbo_payment_term_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "payment_term_identity_conflict"
    assert "55" in kwargs["details"]  # the dbo-identity-matched PaymentTerm
    assert "9" in kwargs["details"]   # the qbo-side conflicting PaymentTerm
    assert "5" in kwargs["details"]   # the local-side conflicting QboTerm


def test_fast_path_conflict_qbo_side_raises_and_writes_nothing():
    """On a detected qbo-side conflict, sync_from_qbo_term must record the issue and
    RAISE — never fall through to the legacy mapping-table path (the exact bug class
    U-276's pilot shipped and had to hotfix live; this unit builds the hard stop in
    from day one, so there's no fall-through to lock in the ABSENCE of)."""
    connector, mapping_repo, payment_term_service, reconciliation_repo = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30 (old)")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.return_value = None
    conflicting = SimpleNamespace(id=2, payment_term_id=9, qbo_term_id=qbo_term.id)
    mapping_repo.read_by_qbo_term_id.return_value = conflicting

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_term(qbo_term)

    reconciliation_repo.create.assert_called_once()
    payment_term_service.repo.update_by_id.assert_not_called()
    payment_term_service.create.assert_not_called()
    payment_term_service.repo.set_qbo_identity.assert_not_called()
    # _resolve_mapping_state's conflict branch is the ONLY caller of this — the raise
    # means the legacy path is structurally unreachable from a conflict.
    mapping_repo.read_by_qbo_term_id.assert_called_once_with(30)


def test_fast_path_conflict_local_side_only_raises_no_duplicate_create():
    """A 'local-side-only' conflict (direct match exists, but no mapping row binds
    this qbo_term.id to anything — by_qbo_term is None) must ALSO raise, not fall
    through to the create path. Falling through would mint a duplicate PaymentTerm for
    a term `direct` already represents, then steal `direct`'s identity via the
    duplicate's own set_qbo_identity call."""
    connector, mapping_repo, payment_term_service, reconciliation_repo = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30 (old)")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.return_value = SimpleNamespace(id=3, qbo_term_id=5)
    mapping_repo.read_by_qbo_term_id.return_value = None

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_term(qbo_term)

    reconciliation_repo.create.assert_called_once()
    payment_term_service.create.assert_not_called()
    payment_term_service.repo.update_by_id.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_fast_path_missing_write_race_raises_runtime_error():
    """If `direct` is deleted between read_by_qbo_identity and the write
    (repo.update_by_id returns None), the 'missing'-mapping branch must not crash
    trying coerce_id(None.id) — nor crash again inside its own except-handler's log
    statement, which referenced the same None.

    U-291: this connector is now migrated onto the shared run_identity_fastpath()
    helper (U-287) — the 7th/last hand-rolled copy. Before the migration this
    branch only logged a warning and let the None flow through as a silent
    success; the migrated connector's on_apply_returned_none callback must raise
    RuntimeError instead. Renamed from
    test_fast_path_missing_write_race_returns_none_without_crashing, which
    pinned the pre-migration silent behavior by name."""
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = None  # race: row gone on write
    mapping_repo.read_by_payment_term_id.return_value = None
    mapping_repo.read_by_qbo_term_id.return_value = None

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_term(qbo_term)

    mapping_repo.create.assert_not_called()


def test_legacy_path_update_returns_none_raises_runtime_error():
    """The legacy "mapping found" branch calls the SAME shared
    `_apply_payment_term_fields` helper the fast path uses. Before U-291 it only
    conditionally guarded the set_qbo_identity stamp and unconditionally
    `return updated` (None) as success either way."""
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    payment_term_service.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=1, payment_term_id=55, qbo_term_id=qbo_term.id)
    mapping_repo.read_by_qbo_term_id.return_value = existing_mapping
    existing_payment_term = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_id.return_value = existing_payment_term
    payment_term_service.repo.update_by_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_term(qbo_term)


def test_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.return_value = None  # mapping missing on this side...
    mapping_repo.read_by_qbo_term_id.return_value = None  # ...and no conflicting mapping either

    connector.sync_from_qbo_term(qbo_term)

    mapping_repo.create.assert_called_once_with(payment_term_id=55, qbo_term_id=30)


def test_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the pre-check and
    the create() call (no sp_getapplock serializes this — same known gap as U-276/278).
    The create() failure must not just be a bare warning — re-check and record a real
    conflict issue when that's what actually happened."""
    connector, mapping_repo, payment_term_service, reconciliation_repo = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_term_id.side_effect = [
        None, SimpleNamespace(id=9, payment_term_id=3, qbo_term_id=qbo_term.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_term(qbo_term)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "payment_term_identity_conflict"
    # A regression that silently dropped the still-valid updated entity (e.g. returning
    # None instead) on this escalation path would pass a call-count-only assertion.
    assert result is direct_hit


def test_fast_path_self_heal_race_recheck_consistent_stays_silent():
    """Mirror of the escalation test above for the CONSISTENT recheck outcome:
    create()'s failure re-check resolving to 'consistent' means a concurrent
    sync already created the correct mapping — genuinely benign, nothing to
    record or raise. (The OTHER non-conflict outcome, 'missing' on recheck, is
    NOT benign — see test_fast_path_self_heal_race_recheck_still_missing_holds,
    U-291 P2.)"""
    connector, mapping_repo, payment_term_service, reconciliation_repo = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.side_effect = [
        None, SimpleNamespace(id=5, qbo_term_id=qbo_term.id)
    ]
    mapping_repo.read_by_qbo_term_id.side_effect = [None]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_term(qbo_term)

    reconciliation_repo.create.assert_not_called()
    assert result is direct_hit


def test_fast_path_self_heal_race_recheck_still_missing_holds():
    """U-291 P2: create()'s failure re-check STILL showing no mapping on either
    side (not a self-resolved race -> consistent, not an escalated conflict) is
    a genuine unresolved failure — transient DB/network, not a duplicate-key
    race. The field write already landed, but silently treating this as full
    success left zero durable trace and a permanently-unmapped PaymentTerm (this
    record won't be re-pulled again until QBO sees another change to it, so
    "next tick" never comes). Must raise so this holds for retry instead.
    Replaces the 'missing' half of
    test_fast_path_self_heal_race_recheck_not_conflict_stays_silent, which
    pinned the pre-fix (buggy) silent-success behavior for this half."""
    connector, mapping_repo, payment_term_service, reconciliation_repo = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_term_id.side_effect = [None, None]  # still missing on recheck
    mapping_repo.create.side_effect = Exception("transient deadlock")

    with pytest.raises(RuntimeError, match="TermPaymentTerm"):
        connector.sync_from_qbo_term(qbo_term)

    reconciliation_repo.create.assert_not_called()


def test_fast_path_hit_consistent_skips_mapping_write_and_identity_restamp():
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = direct_hit
    mapping_repo.read_by_payment_term_id.return_value = SimpleNamespace(id=1, qbo_term_id=30)
    mapping_repo.read_by_qbo_term_id.return_value = SimpleNamespace(id=1, payment_term_id=55)

    result = connector.sync_from_qbo_term(qbo_term)

    assert result is direct_hit
    mapping_repo.create.assert_not_called()
    payment_term_service.create.assert_not_called()
    # Identity is already correct by construction on the fast path — must not re-stamp
    # (the row was found BY that exact identity; re-stamping is a wasted round trip on
    # the steady-state path this feature exists to keep cheap — mirrors U-276/278).
    payment_term_service.repo.set_qbo_identity.assert_not_called()


def test_fast_path_hit_consistent_update_returns_none_raises_runtime_error():
    """U-291: the far more common steady-state case for an already-mapped
    PaymentTerm (mirrors the equivalent customer/vendor/project regression
    tests) — on_apply_returned_none must fire here too, not just on the rarer
    'missing' self-heal window test_fast_path_missing_write_race_raises_runtime
    _error covers. Before this connector's migration onto the shared helper,
    run_identity_fastpath only invoked the callback when state == MISSING, so a
    race on the 'consistent' path fell through with NO callback and NO
    exception at all."""
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.return_value = None  # race: row gone on write
    mapping_repo.read_by_payment_term_id.return_value = SimpleNamespace(id=1, qbo_term_id=30)
    mapping_repo.read_by_qbo_term_id.return_value = SimpleNamespace(id=1, payment_term_id=55)

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_term(qbo_term)

    mapping_repo.create.assert_not_called()
    payment_term_service.repo.set_qbo_identity.assert_not_called()


def test_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1")
    payment_term_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_term_id.return_value = None
    created = SimpleNamespace(id=77)
    payment_term_service.create.return_value = created

    result = connector.sync_from_qbo_term(qbo_term)

    payment_term_service.read_by_qbo_identity.assert_called_once_with("T-99", "realm-1")
    assert result is created
    payment_term_service.create.assert_called_once()


def test_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native identity
    match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id=None)
    mapping_repo.read_by_qbo_term_id.return_value = None
    payment_term_service.create.return_value = SimpleNamespace(id=1)

    connector.sync_from_qbo_term(qbo_term)

    payment_term_service.read_by_qbo_identity.assert_not_called()


def test_legacy_path_still_stamps_identity_after_apply():
    """Regression coverage for the shared-helper refactor: set_qbo_identity was moved
    OUT of _apply_payment_term_fields (the fast path must not call it — see above), so
    the legacy mapping-table UPDATE path must call it itself afterward, since a
    mapping-table-matched row may predate identity stamping."""
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1", active=True)
    payment_term_service.read_by_qbo_identity.return_value = None  # no dbo identity yet
    mapping_repo.read_by_qbo_term_id.return_value = SimpleNamespace(id=1, payment_term_id=55)
    stored_pt = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_id.return_value = stored_pt
    payment_term_service.repo.update_by_id.return_value = stored_pt

    connector.sync_from_qbo_term(qbo_term)

    payment_term_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="T-99", realm_id="realm-1", active=True
    )


def test_legacy_path_missing_payment_term_heals_by_recreating():
    """Mapping exists but the bound PaymentTerm reads empty — the legacy path deletes
    the stale mapping and falls through to create a fresh PaymentTerm (pre-existing
    behavior, untouched by this unit's fast-path addition)."""
    connector, mapping_repo, payment_term_service, _ = _build_term_connector()
    qbo_term = _make_qbo_term(id=30, qbo_id="T-99", realm_id="realm-1", active=True)
    payment_term_service.read_by_qbo_identity.return_value = None
    stale_mapping = SimpleNamespace(id=1, payment_term_id=55)
    # First call (main flow) finds the stale mapping; the second call (inside
    # create_mapping's own 1:1 guard, post-delete) must see it gone.
    mapping_repo.read_by_qbo_term_id.side_effect = [stale_mapping, None]
    mapping_repo.read_by_payment_term_id.return_value = None
    payment_term_service.read_by_id.return_value = None  # bound row gone
    created = SimpleNamespace(id=88)
    payment_term_service.create.return_value = created

    result = connector.sync_from_qbo_term(qbo_term)

    mapping_repo.delete_by_id.assert_called_once_with(1)
    assert result is created
    mapping_repo.create.assert_called_once_with(payment_term_id=88, qbo_term_id=30)
