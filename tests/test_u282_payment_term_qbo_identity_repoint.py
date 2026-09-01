"""Pure-logic tests for U-282 (Phase-4, term repoint) / U-352: repoint the `term`
connector family's identity resolution off qbo.Term / qbo.TermPaymentTerm onto
dbo.PaymentTerm's native QboId/RealmId (Phase 2, U-238c) + QboActive mirror (U-275).

Covers:
  1. PaymentTermRepository.read_by_qbo_identity (sproc call shape) + PaymentTermService
     .read_by_qbo_identity (bare passthrough, no RBAC).
  2. TermPaymentTermConnector's identity resolution — as of U-352 this is the
     DBO-ONLY fast path (`run_identity_fastpath_dbo_only`): no qbo.TermPaymentTerm
     read or write of any kind, mirroring U-350's CompanyInfoCompanyConnector /
     U-310's CustomerCustomerConnector / U-313's VendorVendorConnector one-for-one.
     Two PaymentTerm-specific divergences from those siblings are each pinned by
     their own test below: a HIT preserves a human-edited Name (Company always
     overwrites) and deliberately does NOT refresh the QboActive mirror (a
     pre-existing staleness tradeoff, U-282) — and a genuine MISS has no by-name
     adopt step at all (unlike Company/Address/SubCostCode), so there is no
     duplicate-by-name conflict scenario to test here.
"""
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted
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
#
# U-352 UPDATE: this family is now the DBO-ONLY fast path
# (`run_identity_fastpath_dbo_only`), mirroring U-350's CompanyInfoCompanyConnector /
# U-310's CustomerCustomerConnector / U-313's VendorVendorConnector one-for-one. No
# qbo.TermPaymentTerm read or write of any kind, so there is no mapping-table
# fallback, no self-heal, and no mapping-vs-dbo conflict state left to test.
#
# `_stamp_payment_term_identity` stamps directly (no candidate-scoped lock, unlike
# Company's `_stamp_company_identity`) — PaymentTerm's create path never adopts a
# pre-existing row by name, so there is no side-channel collision for that lock to
# protect (`/simplify` altitude finding, U-352). Only the outer create lock
# (`run_identity_fastpath_dbo_only`'s own, serializing racers for the SAME incoming
# qbo_id) remains.

FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"


def _build_term_connector():
    payment_term_service = Mock()
    payment_term_service.repo = Mock()
    connector = TermPaymentTermConnector(payment_term_service=payment_term_service)
    return connector, payment_term_service


def test_payment_term_direct_hit_updates_fields_no_create_or_stamp():
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", name="Net 30")
    direct_hit = SimpleNamespace(id=55, name="Old Name")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_term(qbo_term)

    assert result is updated
    payment_term_service.repo.update_by_id.assert_called_once()
    payment_term_service.create.assert_not_called()
    payment_term_service.repo.set_qbo_identity.assert_not_called()


def test_payment_term_direct_hit_preserves_human_edited_name():
    """Unlike CompanyInfoCompanyConnector's always-overwrite, PaymentTerm's HIT
    branch preserves a human-edited Name (pre-existing behavior via
    preserve_human_edited_name, unchanged by this migration)."""
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", name="Net 30 (QBO)")
    direct_hit = SimpleNamespace(id=55, name="Net 30 (Curated)")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.side_effect = lambda pt: pt

    with patch(
        f"{TERM_SERVICE}.preserve_human_edited_name", return_value="Net 30 (Curated)"
    ) as mock_preserve:
        connector.sync_from_qbo_term(qbo_term)

    mock_preserve.assert_called_once_with("Net 30 (Curated)", "Net 30 (QBO)")
    assert direct_hit.name == "Net 30 (Curated)"


def test_payment_term_direct_hit_does_not_refresh_qbo_active():
    """U-282's deliberate staleness tradeoff, explicitly preserved by U-352 rather
    than importing SubCostCode's own refresh-every-hit pattern: a HIT never calls
    set_qbo_identity at all, so QboActive is not refreshed even when QBO's Active
    flag has changed since this PaymentTerm was last synced."""
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", active=False)
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.side_effect = lambda pt: pt

    connector.sync_from_qbo_term(qbo_term)

    payment_term_service.repo.set_qbo_identity.assert_not_called()


def test_payment_term_direct_hit_preserves_zero_discount_percent():
    """Codex xhigh P3: a falsy-zero coercion bug (`if x else None` instead of
    `if x is not None else None`) inherited verbatim from the pre-U-352 legacy
    code would drop a genuine QBO Decimal("0") discount to None — and
    UpdatePaymentTermById unconditionally SETs DiscountPercent (no CASE WHEN
    NULL-preserve guard), so this would wipe a real 0% discount to NULL on
    every re-sync. Money guards must be `is not None`, never a bare
    truthiness check (Decimal("0") is falsy)."""
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", discount_percent=Decimal("0"))
    direct_hit = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.read_by_qbo_identity.return_value = direct_hit
    payment_term_service.repo.update_by_id.side_effect = lambda pt: pt

    connector.sync_from_qbo_term(qbo_term)

    assert direct_hit.discount_percent == 0.0
    assert direct_hit.discount_percent is not None


def test_payment_term_genuine_miss_create_preserves_zero_discount_percent():
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(
        qbo_id="T-99", realm_id="realm-1", name="Net 30", type=None, due_days=None,
        active=True, discount_percent=Decimal("0"),
    )
    payment_term_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=300, qbo_id=None, realm_id=None)
    payment_term_service.create.return_value = created
    stamped = SimpleNamespace(id=300, qbo_id="T-99", realm_id="realm-1")
    payment_term_service.read_by_id.return_value = stamped

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector.sync_from_qbo_term(qbo_term)

    payment_term_service.create.assert_called_once_with(
        name="Net 30", description=None, discount_percent=0.0, discount_days=None, due_days=None,
    )


def test_payment_term_genuine_miss_creates_new_and_stamps_identity_with_active():
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(
        qbo_id="T-99", realm_id="realm-1", name="Net 30", type=None, due_days=None, active=True,
    )
    payment_term_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=300, qbo_id=None, realm_id=None)
    payment_term_service.create.return_value = created
    stamped = SimpleNamespace(id=300, qbo_id="T-99", realm_id="realm-1")
    payment_term_service.read_by_id.return_value = stamped

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        result = connector.sync_from_qbo_term(qbo_term)

    assert result is stamped
    payment_term_service.create.assert_called_once_with(
        name="Net 30", description=None, discount_percent=None, discount_days=None, due_days=None,
    )
    payment_term_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="T-99", realm_id="realm-1", active=True
    )


def test_payment_term_genuine_miss_deactivation_guard_blocks_create():
    """U-219: no adopt path; the deactivation guard runs directly before create —
    an inactive-and-never-before-synced QboTerm must never mint a new PaymentTerm."""
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", active=False)
    payment_term_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError):
            connector.sync_from_qbo_term(qbo_term)

    payment_term_service.create.assert_not_called()


def test_payment_term_race_discovered_hit_adopts_racer_without_create():
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1")
    racer_row = SimpleNamespace(id=400, qbo_id="T-99", realm_id="realm-1", name="Net 30")
    payment_term_service.read_by_qbo_identity.side_effect = [None, racer_row]
    payment_term_service.repo.update_by_id.side_effect = lambda pt: pt

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        result = connector.sync_from_qbo_term(qbo_term)

    assert result is racer_row
    payment_term_service.create.assert_not_called()
    payment_term_service.repo.set_qbo_identity.assert_not_called()
    assert payment_term_service.read_by_qbo_identity.call_args_list == [
        call("T-99", "realm-1"),
        call("T-99", "realm-1"),
    ]


def test_payment_term_update_returning_none_raises_runtime_error():
    """A ROWVERSION race on the HIT branch (update_by_id affected 0 rows) must
    raise RuntimeError, NOT ValueError (U-291 discipline, carried through the
    repoint) — record_projection_error classifies a plain ValueError as a
    permanent SKIP that advances the watermark past a PaymentTerm whose fields
    were never written. RuntimeError holds it for retry."""
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1")
    payment_term_service.read_by_qbo_identity.return_value = SimpleNamespace(id=55, name="Net 30")
    payment_term_service.repo.update_by_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_term(qbo_term)

    payment_term_service.create.assert_not_called()
    payment_term_service.repo.set_qbo_identity.assert_not_called()


def test_payment_term_no_qbo_id_raises():
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id=None)

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_term(qbo_term)

    payment_term_service.read_by_qbo_identity.assert_not_called()


def test_payment_term_stamp_identity_threads_active():
    """The one param Company's own MISS-branch write never carries, since Company
    has no QboActive column — PaymentTerm's does. No lock to patch here (U-352
    /simplify): `_stamp_payment_term_identity` stamps directly, unlike Company's
    lock-guarded `_stamp_company_identity`."""
    connector, payment_term_service = _build_term_connector()
    candidate = SimpleNamespace(id=150)
    stamped = SimpleNamespace(id=150, qbo_id="T-99", realm_id="realm-1")
    payment_term_service.read_by_id.return_value = stamped
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", active=False)

    result = connector._stamp_payment_term_identity(candidate, qbo_term)

    payment_term_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="T-99", realm_id="realm-1", active=False
    )
    assert result is stamped


def test_payment_term_stamp_identity_returns_none_when_candidate_deleted_before_stamp():
    """U-352 /simplify: with no candidate-scoped lock, a concurrent delete of the
    just-created candidate between `resolve_candidate`'s `.create()` and this call
    is not pre-empted — `set_qbo_identity` affects 0 rows (silent no-op, matching
    the pre-U-352 legacy `create_mapping`'s own unlocked behavior) and the
    post-stamp re-read returns None. `run_identity_fastpath_dbo_only`'s own
    `stamped is None` check turns that into `raise_concurrent_write_race`, so the
    race still surfaces — just via this re-read rather than a pre-emptive lock."""
    connector, payment_term_service = _build_term_connector()
    candidate = SimpleNamespace(id=150)
    payment_term_service.read_by_id.return_value = None
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", active=True)

    result = connector._stamp_payment_term_identity(candidate, qbo_term)

    payment_term_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="T-99", realm_id="realm-1", active=True
    )
    assert result is None


def test_payment_term_genuine_miss_stamp_returning_none_raises_runtime_error():
    """End-to-end: a stamp-time candidate-deleted race surfaces through
    sync_from_qbo_term as RuntimeError (U-291 discipline), not a silent None."""
    connector, payment_term_service = _build_term_connector()
    qbo_term = _make_qbo_term(qbo_id="T-99", realm_id="realm-1", active=True)
    payment_term_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=300, qbo_id=None, realm_id=None)
    payment_term_service.create.return_value = created
    payment_term_service.read_by_id.return_value = None  # candidate gone by stamp time

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector.sync_from_qbo_term(qbo_term)
