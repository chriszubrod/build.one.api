"""Pure-logic tests for U-289 (Phase-4 repoint) + U-307c (dbo-only pull
repoint): the item connector family's identity resolution off qbo.Item /
qbo.Item{CostCode,SubCostCode} onto dbo.{CostCode,SubCostCode}'s native
QboId/RealmId, then (U-307c) retire the qbo.Item/qbo.ItemCostCode/
qbo.ItemSubCostCode staging WRITES on the pull path entirely in favor of
`run_identity_fastpath_dbo_only`.

Cross-family item-ref resolvers (bill_line_item/expense_line_item/
bill_credit_line_item's inbound ItemRef->SubCostCode lookups; Bill's live
push item-ref resolution) are explicitly OUT of scope -- they were already
repointed onto `base/cost_code_resolver.py` by U-307a/b and are unaffected by
this connector-side repoint.

Covers:
  1. CostCodeRepository/SubCostCodeRepository.read_by_qbo_identity (sproc call
     shape) + the corresponding Service passthroughs (bare, no row-level RBAC).
  2. ItemCostCodeConnector's dbo-only fast path (U-307c): a direct or
     race-discovered hit updates fields and writes nothing else; a genuine
     miss (re-confirmed under the create lock) adopts an existing unmapped
     CostCode by number (RAW name overwrite, U-219) or creates fresh, then
     stamps identity -- never writes qbo.ItemCostCode. There is no more
     "conflict" state or legacy mapping-table fallback (Wave-5 "trust dbo
     alone" -- no second store left to drift from); a number-matched row
     already carrying a DIFFERENT QboId raises instead of being silently
     re-pointed (Decision 2's duplicate-QboId guard -- the one genuinely new,
     correctness-critical piece of this repoint).
  3. ItemSubCostCodeConnector: the same dbo-only shape, PLUS (a) the parent
     CostCode resolution is now dbo-native (`CostCodeService.read_by_qbo_
     identity`, closing the standing TODO.md item) and required before the
     fast path even runs, and (b) the QboActive dbo-native mirror (U-275) is
     refreshed after every successful resolve via a QboId/RealmId-omitted
     set_qbo_identity call -- the one documented deviation from CostCode,
     since SubCostCode is the only family in this batch carrying an Active
     mirror that must stay current even when identity itself hasn't moved.
  4. `for_entity` field-ownership registry sanity for CostCode/SubCostCode
     (salvaged from the now-deleted test_qbo_item_mapping_heal.py -- generic
     registry coverage, unrelated to the connector's own mapping-table logic
     that file otherwise tested exclusively).
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from integrations.intuit.qbo.base.field_ownership import BOTH_EDITABLE, for_entity
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.cost_code.business.service import (
    ItemCostCodeConnector,
)
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import (
    ItemSubCostCodeConnector,
)

CC_SERVICE = "integrations.intuit.qbo.item.connector.cost_code.business.service"
SCC_SERVICE = "integrations.intuit.qbo.item.connector.sub_cost_code.business.service"

FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
# _stamp_{cost,sub_cost}_code_identity's own lock now lives in the shared
# stamp_dbo_identity_with_lock (U-328/U-331) inside identity_fastpath.py --
# same target as the create lock above, not a separate connector-module import.
CC_STAMP_LOCK_TARGET = FASTPATH_LOCK_TARGET
SCC_STAMP_LOCK_TARGET = FASTPATH_LOCK_TARGET


def _make_qbo_item(**overrides):
    defaults = dict(
        id=None,  # U-307c: always transient -- never a real staging PK.
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="ITEM-99",
        sync_token="0",
        realm_id="realm-1",
        name="13.01 Rough Carpentry",
        description=None,
        active=True,
        type="Service",
        parent_ref_value=None,
        parent_ref_name=None,
        level=0,
        fully_qualified_name=None,
        sku=None,
        unit_price=None,
        purchase_cost=None,
        taxable=None,
        income_account_ref_value=None,
        income_account_ref_name=None,
        expense_account_ref_value=None,
        expense_account_ref_name=None,
    )
    defaults.update(overrides)
    return QboItem(**defaults)


def _make_cost_code(**overrides):
    defaults = dict(
        id=100, public_id="cc-pub-100", qbo_id=None, realm_id=None,
        number="13", name="Rough Carpentry", description=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_sub_cost_code(**overrides):
    defaults = dict(
        id=200, public_id="scc-pub-200", qbo_id=None, realm_id=None,
        number="13.01", name="Rough Carpentry", description=None, cost_code_id=100,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _granted_lock(*_args, **_kwargs):
    @contextmanager
    def _cm(*_a, **_k):
        yield True

    return _cm()


def _denied_lock(*_args, **_kwargs):
    @contextmanager
    def _cm(*_a, **_k):
        yield False

    return _cm()


def _recording_lock_factory(recorded):
    def _recording_lock(resource_name, timeout_ms=15000):
        recorded.append(resource_name)

        @contextmanager
        def _cm():
            yield True

        return _cm()

    return _recording_lock


# --- Section 1: repo-level sproc call shape ---


def test_cost_code_repo_read_by_qbo_identity_calls_sproc():
    from entities.cost_code.persistence.repo import CostCodeRepository

    repo = CostCodeRepository()
    cursor = Mock()
    cursor.fetchone.return_value = None

    with patch("entities.cost_code.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.cost_code.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("ITEM-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadCostCodeByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "ITEM-99", "RealmId": "realm-1"}


def test_sub_cost_code_repo_read_by_qbo_identity_calls_sproc():
    from entities.sub_cost_code.persistence.repo import SubCostCodeRepository

    repo = SubCostCodeRepository()
    cursor = Mock()
    cursor.fetchone.return_value = None

    with patch("entities.sub_cost_code.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.sub_cost_code.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("ITEM-10", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadSubCostCodeByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "ITEM-10", "RealmId": "realm-1"}


def test_cost_code_service_read_by_qbo_identity_is_bare_passthrough():
    """CostCode has no row-level RBAC (unlike BillCredit/Project) — the new method
    must be a bare passthrough, matching Customer's template."""
    from entities.cost_code.business.service import CostCodeService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = "sentinel"
    service = CostCodeService(repo=repo)

    result = service.read_by_qbo_identity("ITEM-99", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("ITEM-99", "realm-1")
    assert result == "sentinel"


def test_sub_cost_code_service_read_by_qbo_identity_is_bare_passthrough():
    from entities.sub_cost_code.business.service import SubCostCodeService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = "sentinel"
    service = SubCostCodeService(repo=repo)

    result = service.read_by_qbo_identity("ITEM-10", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("ITEM-10", "realm-1")
    assert result == "sentinel"


def test_field_ownership_registry_cost_code_and_sub_cost_code_name_both_editable():
    """CostCode/SubCostCode registry keys resolve and name is both_editable."""
    assert for_entity("CostCode").ownership_of("name") == BOTH_EDITABLE
    assert for_entity("SubCostCode").ownership_of("name") == BOTH_EDITABLE


# --- Section 2: ItemCostCodeConnector dbo-only fast path ---


def _build_cost_code_connector():
    cost_code_service = Mock()
    cost_code_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = ItemCostCodeConnector(
        cost_code_service=cost_code_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, cost_code_service, reconciliation_repo


def test_cost_code_direct_hit_updates_fields_no_create_or_stamp():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1", name="13 Rough Carpentry")
    direct_hit = _make_cost_code(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    updated = _make_cost_code(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    cost_code_service.repo.update_by_id.assert_called_once()
    cost_code_service.create.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_not_called()
    cost_code_service.read_by_number.assert_not_called()


def test_cost_code_direct_hit_preserves_non_blank_local_name():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(name="13 Rough Carpentry (deleted)")
    direct_hit = _make_cost_code(name="Curated Local Name")
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    cost_code_service.repo.update_by_id.side_effect = lambda c: c

    result = connector.sync_from_qbo_item(qbo_item)

    assert result.name == "Curated Local Name"


def test_cost_code_genuine_miss_creates_new_and_stamps_identity():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1", name="13 Rough Carpentry")
    cost_code_service.read_by_qbo_identity.return_value = None
    cost_code_service.read_by_number.return_value = None
    created = _make_cost_code(id=300, qbo_id=None, realm_id=None)
    cost_code_service.create.return_value = created
    stamped = _make_cost_code(id=300, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_id.side_effect = [created, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        CC_STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is stamped
    cost_code_service.create.assert_called_once_with(
        number="13", name="Rough Carpentry", description=None
    )
    cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="ITEM-99", realm_id="realm-1"
    )


def test_cost_code_genuine_miss_adopts_existing_unmapped_by_number_raw_name():
    """U-219: adopt-by-number is a RAW name overwrite, bypassing preserve_human_edited_name."""
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", name="13 Rough Carpentry")
    cost_code_service.read_by_qbo_identity.return_value = None
    existing = _make_cost_code(id=150, qbo_id=None, name="Old Curated Name")
    cost_code_service.read_by_number.return_value = existing
    cost_code_service.repo.update_by_id.side_effect = lambda c: c
    stamped = _make_cost_code(id=150, qbo_id="ITEM-99", realm_id="realm-1", name="Rough Carpentry")
    cost_code_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        CC_STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is stamped
    assert existing.name == "Rough Carpentry"  # raw overwrite, not preserved
    cost_code_service.create.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="ITEM-99", realm_id="realm-1"
    )


def test_cost_code_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """Codex round-2 P1: resolve_candidate must be PURE for the adopt-by-number
    case -- no field write, no update_by_id call. The field write happens only
    in _stamp_cost_code_identity, atomically with the identity stamp under the
    candidate's own lock, or two concurrent QboItems number-matching the same
    row could each mutate it before either acquires that lock. Direct unit
    test on resolve_candidate itself (not the full sync_from_qbo_item
    integration) so a regression that moves the write back here is caught
    even if the integration-level assertions happen to still read correctly."""
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1", name="13 Rough Carpentry")
    existing = _make_cost_code(id=150, qbo_id=None, realm_id=None, name="Untouched Name")
    cost_code_service.read_by_number.return_value = existing

    candidate = connector._resolve_cost_code_candidate(
        qbo_item, number="13", name="Rough Carpentry", description="new desc"
    )

    assert candidate is existing
    assert existing.name == "Untouched Name"
    assert existing.description is None
    cost_code_service.repo.update_by_id.assert_not_called()


def test_cost_code_duplicate_qbo_id_guard_raises_and_records_issue():
    """Decision 2 (the one genuinely new, correctness-critical piece of this
    repoint): a number-matched CostCode already carrying a DIFFERENT QboId
    must NOT be returned as the candidate -- stamp_identity's theft-clear
    would silently re-point it. Must raise + record a duplicate_qbo_item
    issue instead, mirroring today's mapping-table-era contract exactly.
    Mutation target: deleting this guard makes the row get silently re-bound."""
    connector, cost_code_service, reconciliation_repo = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", name="13 Rough Carpentry")
    cost_code_service.read_by_qbo_identity.return_value = None
    existing = _make_cost_code(id=150, qbo_id="ITEM-OTHER", realm_id="realm-1")
    cost_code_service.read_by_number.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_item(qbo_item)

    cost_code_service.repo.update_by_id.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_item"


def test_cost_code_duplicate_guard_catches_same_qbo_id_different_realm():
    """Codex round-1 P1: QBO ids are only unique WITHIN a realm, so a QboId-only
    check would let a same-QboId-different-realm row through and overwrite its
    name/description before _stamp_cost_code_identity's own (qbo_id AND
    realm_id) check ever runs. Must raise from resolve_candidate BEFORE any
    field mutation, matching _stamp_cost_code_identity's exact comparison."""
    connector, cost_code_service, reconciliation_repo = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1", name="13 Rough Carpentry")
    cost_code_service.read_by_qbo_identity.return_value = None
    existing = _make_cost_code(id=150, qbo_id="ITEM-99", realm_id="realm-OTHER", name="Untouched")
    cost_code_service.read_by_number.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_item(qbo_item)

    assert existing.name == "Untouched"  # never mutated before the raise
    cost_code_service.repo.update_by_id.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()


def test_cost_code_resolve_candidate_allows_reresolve_to_same_qbo_id():
    """A benign re-resolve (existing.qbo_id already equals the incoming qbo_id)
    must proceed normally -- the guard only blocks a DIFFERENT identity."""
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1", name="13 Rough Carpentry")
    cost_code_service.read_by_qbo_identity.return_value = None
    existing = _make_cost_code(id=150, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_number.return_value = existing
    cost_code_service.repo.update_by_id.side_effect = lambda c: c
    stamped = _make_cost_code(id=150, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        CC_STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is stamped


def test_cost_code_inactive_unmapped_raises_without_creating():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(active=False, qbo_id="ITEM-99")
    cost_code_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="inactive in QBO and has no local"):
            connector.sync_from_qbo_item(qbo_item)

    cost_code_service.read_by_number.assert_not_called()
    cost_code_service.create.assert_not_called()


def test_cost_code_race_discovered_hit_adopts_racer_without_create():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")
    racer_row = _make_cost_code(id=400, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_qbo_identity.side_effect = [None, racer_row]
    cost_code_service.repo.update_by_id.side_effect = lambda c: c

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is racer_row
    cost_code_service.create.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_not_called()
    assert cost_code_service.read_by_qbo_identity.call_args_list == [
        call("ITEM-99", "realm-1"),
        call("ITEM-99", "realm-1"),
    ]


def test_cost_code_no_qbo_id_raises():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id=None)

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_item(qbo_item)

    cost_code_service.read_by_qbo_identity.assert_not_called()


def test_cost_code_lock_resource_key_matches_dbo_only_namespace():
    connector, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_qbo_identity.return_value = None
    cost_code_service.read_by_number.return_value = None
    cost_code_service.create.return_value = _make_cost_code(id=300)
    cost_code_service.read_by_id.return_value = _make_cost_code(
        id=300, qbo_id="ITEM-99", realm_id="realm-1"
    )
    recorded = []

    # FASTPATH_LOCK_TARGET and CC_STAMP_LOCK_TARGET are now the SAME name
    # (both locks live in identity_fastpath.py, U-328/U-331) -- one recording
    # patch captures both acquisitions, in order.
    with patch(FASTPATH_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector.sync_from_qbo_item(qbo_item)

    assert recorded == [
        "qbo_dbo_identity_create:CostCode:ITEM-99:realm-1",
        "qbo_dbo_identity_stamp:CostCode:300",
    ]


def test_cost_code_stamp_identity_refuses_to_overwrite_different_existing_identity():
    """Also proves the new Decision-2 on_conflict wiring (U-328/U-331) reuses
    `_raise_duplicate_qbo_item_issue` (the SAME recorder/DriftType
    resolve_candidate's own number-match guard already uses) on this
    stamp-time race — Codex xhigh P3 finding on the stamp-lock-helper diff."""
    connector, cost_code_service, reconciliation_repo = _build_cost_code_connector()
    candidate = _make_cost_code(id=150)
    cost_code_service.read_by_id.return_value = _make_cost_code(
        id=150, qbo_id="ITEM-OTHER", realm_id="realm-1"
    )
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(CC_STAMP_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries QBO identity ITEM-OTHER"):
            connector._stamp_cost_code_identity(candidate, qbo_item, name="X", description=None)

    cost_code_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_item"
    cost_code_service.repo.update_by_id.assert_not_called()  # never mutated before the raise


def test_cost_code_stamp_identity_applies_field_write_atomically_with_stamp():
    """Codex round-2 P1 fix: the field write happens INSIDE this method, under
    the candidate lock, not in resolve_candidate -- confirms it's actually applied."""
    connector, cost_code_service, _ = _build_cost_code_connector()
    candidate = _make_cost_code(id=150)
    unmapped = _make_cost_code(id=150, qbo_id=None, realm_id=None, name="Old Name")
    cost_code_service.read_by_id.return_value = unmapped
    cost_code_service.repo.update_by_id.side_effect = lambda c: c
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(CC_STAMP_LOCK_TARGET, side_effect=_granted_lock):
        connector._stamp_cost_code_identity(candidate, qbo_item, name="New Name", description="new desc")

    assert unmapped.name == "New Name"
    assert unmapped.description == "new desc"
    cost_code_service.repo.update_by_id.assert_called_once_with(unmapped)
    cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="ITEM-99", realm_id="realm-1"
    )


def test_cost_code_stamp_identity_concurrent_update_race_raises_and_holds():
    """D1 (docs/design/stamp-lock-helper.md): before U-328/U-331, this
    connector's own `_stamp_cost_code_identity` called `update_by_id` and
    discarded its return value entirely -- a concurrent ROWVERSION race on
    this exact write silently succeeded at `set_qbo_identity` anyway
    (TODO.md:44-51, U-316 follow-up). Migrating onto the shared
    `stamp_dbo_identity_with_lock` closes this structurally: `update_by_id`
    returning None (the race) must now raise and hold for retry, and
    set_qbo_identity must never fire on that path. Mutation target: reverting
    the shared helper's apply_fields None-guard reproduces the pre-fix
    silent-success bug and this test goes red."""
    connector, cost_code_service, _ = _build_cost_code_connector()
    candidate = _make_cost_code(id=150)
    unmapped = _make_cost_code(id=150, qbo_id=None, realm_id=None, name="Old Name")
    cost_code_service.read_by_id.return_value = unmapped
    cost_code_service.repo.update_by_id.return_value = None  # race: row gone on write
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(CC_STAMP_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector._stamp_cost_code_identity(candidate, qbo_item, name="New Name", description="new desc")

    cost_code_service.repo.set_qbo_identity.assert_not_called()


def test_two_racers_number_matching_the_same_cost_code_serialize_and_the_loser_never_mutates_fields():
    """The actual race Codex round-2 found, reproduced with REAL threads (not
    sequential calls dressed up as a race): two genuinely concurrent QboItems
    with DIFFERENT qbo_ids (so no contention on run_identity_fastpath_dbo_
    only's own qbo_id-keyed create lock) that both number-match the SAME
    unmapped local CostCode. A real threading.Lock stands in for
    sp_getapplock's cross-connection mutual exclusion.

    Proves two things directly, not just the final outcome: (1) mutual
    exclusion actually held during the read-guard-write-stamp sequence (an
    occupancy probe, not an inference from who "won"), and (2) the LOSER's
    incoming field values never landed on the row -- only the winner's did,
    matching whichever qbo_id the row ended up stamped with. Mutation target:
    this is exactly what breaks if the field write is moved back into
    resolve_candidate (outside this lock) or the lock is removed/keyed wrong."""
    import threading
    import time
    from contextlib import contextmanager

    connector, cost_code_service, _ = _build_cost_code_connector()

    state_lock = threading.Lock()
    state = {"qbo_id": None, "realm_id": None, "name": None}

    occupancy = {"current": 0, "max": 0}

    def _enter_critical_section():
        occupancy["current"] += 1
        occupancy["max"] = max(occupancy["max"], occupancy["current"])

    def _exit_critical_section():
        occupancy["current"] -= 1

    def _read_by_id(_id):
        with state_lock:
            return _make_cost_code(
                id=150, qbo_id=state["qbo_id"], realm_id=state["realm_id"], name=state["name"],
            )

    def _update_by_id(row):
        with state_lock:
            state["name"] = row.name
        return row

    def _set_qbo_identity(*, id, qbo_id, realm_id):
        _enter_critical_section()
        try:
            time.sleep(0.05)  # widen the window so a non-excluded racer would reliably overlap
            with state_lock:
                state["qbo_id"] = qbo_id
                state["realm_id"] = realm_id
        finally:
            _exit_critical_section()

    cost_code_service.read_by_id.side_effect = _read_by_id
    cost_code_service.repo.update_by_id.side_effect = _update_by_id
    cost_code_service.repo.set_qbo_identity.side_effect = _set_qbo_identity

    real_lock = threading.Lock()
    requested_resources = set()
    resources_seen_lock = threading.Lock()

    @contextmanager
    def _real_lock(resource_name, timeout_ms=15000):
        with resources_seen_lock:
            requested_resources.add(resource_name)
        acquired = real_lock.acquire(timeout=timeout_ms / 1000)
        try:
            yield acquired
        finally:
            if acquired:
                real_lock.release()

    outcomes = {}

    def _racer(qbo_id):
        candidate = _make_cost_code(id=150)
        qbo_item = _make_qbo_item(qbo_id=qbo_id, realm_id="realm-1")
        try:
            outcomes[qbo_id] = ("won", connector._stamp_cost_code_identity(
                candidate, qbo_item, name=f"Name-from-{qbo_id}", description=None,
            ))
        except ValueError as e:
            outcomes[qbo_id] = ("lost", e)

    with patch(CC_STAMP_LOCK_TARGET, side_effect=_real_lock):
        t1 = threading.Thread(target=_racer, args=("ITEM-X",))
        t2 = threading.Thread(target=_racer, args=("ITEM-Y",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive(), "a racer thread hung — lock likely deadlocked"
    assert requested_resources == {"qbo_dbo_identity_stamp:CostCode:150"}
    assert occupancy["max"] == 1, (
        f"both racers were inside the critical section concurrently (max_occupants="
        f"{occupancy['max']}) — the lock did not actually exclude them"
    )
    kinds = sorted(kind for kind, _ in outcomes.values())
    assert kinds == ["lost", "won"], f"expected exactly one winner and one loser, got {outcomes}"
    winner_qbo_id = next(q for q, (kind, _) in outcomes.items() if kind == "won")
    # The final name must match the WINNER's incoming value -- the loser's
    # field write must never have landed, even transiently.
    assert state["name"] == f"Name-from-{winner_qbo_id}"
    assert state["qbo_id"] == winner_qbo_id


def test_cost_code_stamp_identity_lock_key_scoped_to_candidate():
    connector, cost_code_service, _ = _build_cost_code_connector()
    candidate = _make_cost_code(id=150)
    cost_code_service.read_by_id.return_value = _make_cost_code(id=150, qbo_id=None, realm_id=None)
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")
    recorded = []

    with patch(CC_STAMP_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector._stamp_cost_code_identity(candidate, qbo_item, name="X", description=None)

    assert recorded == ["qbo_dbo_identity_stamp:CostCode:150"]


def test_cost_code_stamp_identity_fails_closed_on_lock_timeout():
    connector, cost_code_service, _ = _build_cost_code_connector()
    candidate = _make_cost_code(id=150)
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(CC_STAMP_LOCK_TARGET, side_effect=_denied_lock):
        with pytest.raises(RuntimeError, match="Could not acquire identity-stamp lock"):
            connector._stamp_cost_code_identity(candidate, qbo_item, name="X", description=None)

    cost_code_service.read_by_id.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_not_called()


# --- Section 3: ItemSubCostCodeConnector dbo-only fast path ---


def _build_sub_cost_code_connector():
    sub_cost_code_service = Mock()
    sub_cost_code_service.repo = Mock()
    cost_code_service = Mock()
    reconciliation_repo = Mock()
    connector = ItemSubCostCodeConnector(
        sub_cost_code_service=sub_cost_code_service,
        cost_code_service=cost_code_service,
        reconciliation_repo=reconciliation_repo,
    )
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    return connector, sub_cost_code_service, cost_code_service, reconciliation_repo


def test_sub_cost_code_parent_not_dbo_stamped_raises_before_fast_path():
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    cost_code_service.read_by_qbo_identity.return_value = None
    qbo_item = _make_qbo_item(parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1")

    with pytest.raises(ValueError, match="not yet dbo-stamped"):
        connector.sync_from_qbo_item(qbo_item)

    cost_code_service.read_by_qbo_identity.assert_called_once_with("parent-item-1", realm_id="realm-1")
    sub_cost_code_service.read_by_qbo_identity.assert_not_called()


def test_sub_cost_code_direct_hit_uses_parent_resolved_cost_code_id_and_refreshes_active():
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1", active=False,
    )
    direct_hit = _make_sub_cost_code(id=200, qbo_id="ITEM-99", realm_id="realm-1", cost_code_id=999)
    sub_cost_code_service.read_by_qbo_identity.return_value = direct_hit
    updated = _make_sub_cost_code(id=200, cost_code_id=100)
    sub_cost_code_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    assert updated.cost_code_id == 100
    # QboActive mirror refreshed (U-275) with QboId/RealmId omitted (CASE WHEN no-op there).
    sub_cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=200, qbo_id=None, realm_id=None, active=False,
    )


def test_sub_cost_code_genuine_miss_creates_new_and_stamps_identity_with_active():
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1",
        name="13.01 Rough Carpentry", active=True,
    )
    sub_cost_code_service.read_by_qbo_identity.return_value = None
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    created = _make_sub_cost_code(id=500, qbo_id=None, realm_id=None)
    sub_cost_code_service.create.return_value = created
    stamped = _make_sub_cost_code(id=500, qbo_id="ITEM-99", realm_id="realm-1")
    sub_cost_code_service.read_by_id.side_effect = [created, stamped, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        SCC_STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is stamped
    sub_cost_code_service.create.assert_called_once_with(
        number="13.01", name="Rough Carpentry", description=None, cost_code_id=100,
    )
    # Stamped once inside _stamp_sub_cost_code_identity (real qbo_id/realm_id +
    # active), then again by the outer QboActive-refresh wrapper (harmless
    # redundant re-set, matching the pre-U-307c fast path's own uniform
    # refresh-on-every-hit shape).
    assert sub_cost_code_service.repo.set_qbo_identity.call_args_list == [
        call(id=500, qbo_id="ITEM-99", realm_id="realm-1", active=True),
        call(id=500, qbo_id=None, realm_id=None, active=True),
    ]


def test_sub_cost_code_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """Codex round-2 P1, SubCostCode side. See the CostCode test's docstring
    for the full rationale."""
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1", name="13.01 Rough",
    )
    existing = _make_sub_cost_code(
        id=201, number="13.01", cost_code_id=999, qbo_id=None, realm_id=None, name="Untouched",
    )
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = [existing]

    candidate = connector._resolve_sub_cost_code_candidate(
        qbo_item, number="13.01", name="Rough", description="new desc", cost_code_id=100,
    )

    assert candidate is existing
    assert existing.name == "Untouched"
    assert existing.description is None
    assert existing.cost_code_id == 999  # NOT re-parented here either
    sub_cost_code_service.repo.update_by_id.assert_not_called()


def test_sub_cost_code_candidate_scopes_number_match_by_parent_cost_code():
    """The same SubCostCode NUMBER can legitimately exist under two different
    parent CostCodes -- adopt must only match the sibling under THIS item's
    resolved parent, never a same-numbered row under a different parent."""
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1",
        name="01.1 Demo",
    )
    sub_cost_code_service.read_by_qbo_identity.return_value = None
    correct_parent_sibling = _make_sub_cost_code(id=201, number="01.1", cost_code_id=100, qbo_id=None)
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = [correct_parent_sibling]
    sub_cost_code_service.repo.update_by_id.side_effect = lambda s: s
    stamped = _make_sub_cost_code(id=201, qbo_id="ITEM-99", realm_id="realm-1")
    sub_cost_code_service.read_by_id.side_effect = [correct_parent_sibling, stamped, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        SCC_STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is stamped
    sub_cost_code_service.repo.read_by_cost_code_id.assert_called_once_with(100)
    sub_cost_code_service.create.assert_not_called()


def test_sub_cost_code_candidate_ignores_number_match_under_wrong_parent():
    """A same-numbered SubCostCode under a DIFFERENT parent CostCode must be
    ignored -- falls through to create fresh, not adopted across parents."""
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1",
        name="01.1 Demo",
    )
    sub_cost_code_service.read_by_qbo_identity.return_value = None
    # read_by_cost_code_id is already scoped server-side to cost_code_id=100 --
    # a sibling under a DIFFERENT parent simply never appears in this list.
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    created = _make_sub_cost_code(id=777, qbo_id=None, realm_id=None)
    sub_cost_code_service.create.return_value = created
    stamped = _make_sub_cost_code(id=777, qbo_id="ITEM-99", realm_id="realm-1")
    sub_cost_code_service.read_by_id.side_effect = [created, stamped, stamped]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock), patch(
        SCC_STAMP_LOCK_TARGET, side_effect=_granted_lock
    ):
        result = connector.sync_from_qbo_item(qbo_item)

    assert result is stamped
    sub_cost_code_service.create.assert_called_once()


def test_sub_cost_code_duplicate_qbo_id_guard_raises_and_records_issue():
    """Decision 2, SubCostCode side. Mutation target: deleting this guard
    makes the row get silently re-bound to a different QBO item's identity."""
    connector, sub_cost_code_service, cost_code_service, reconciliation_repo = (
        _build_sub_cost_code_connector()
    )
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1", name="13.01 Rough",
    )
    sub_cost_code_service.read_by_qbo_identity.return_value = None
    existing = _make_sub_cost_code(id=201, number="13.01", cost_code_id=100, qbo_id="ITEM-OTHER")
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = [existing]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_item(qbo_item)

    sub_cost_code_service.repo.update_by_id.assert_not_called()
    sub_cost_code_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_item"


def test_sub_cost_code_duplicate_guard_catches_same_qbo_id_different_realm():
    """Codex round-1 P1, SubCostCode side. See the CostCode test's docstring
    for the full rationale."""
    connector, sub_cost_code_service, cost_code_service, reconciliation_repo = (
        _build_sub_cost_code_connector()
    )
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1", name="13.01 Rough",
    )
    sub_cost_code_service.read_by_qbo_identity.return_value = None
    existing = _make_sub_cost_code(
        id=201, number="13.01", cost_code_id=100, qbo_id="ITEM-99", realm_id="realm-OTHER",
        name="Untouched",
    )
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = [existing]

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_item(qbo_item)

    assert existing.name == "Untouched"
    sub_cost_code_service.repo.update_by_id.assert_not_called()
    sub_cost_code_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()


def test_sub_cost_code_inactive_unmapped_raises_without_creating():
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(
        parent_ref_value="parent-item-1", qbo_id="ITEM-99", active=False,
    )
    sub_cost_code_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="inactive in QBO and has no local"):
            connector.sync_from_qbo_item(qbo_item)

    sub_cost_code_service.repo.read_by_cost_code_id.assert_not_called()
    sub_cost_code_service.create.assert_not_called()


def test_sub_cost_code_no_qbo_id_raises():
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(parent_ref_value="parent-item-1", qbo_id=None)

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_item(qbo_item)

    sub_cost_code_service.read_by_qbo_identity.assert_not_called()


def test_sub_cost_code_lock_resource_key_matches_dbo_only_namespace():
    connector, sub_cost_code_service, cost_code_service, _ = _build_sub_cost_code_connector()
    cost_code_service.read_by_qbo_identity.return_value = _make_cost_code(id=100)
    qbo_item = _make_qbo_item(parent_ref_value="parent-item-1", qbo_id="ITEM-99", realm_id="realm-1")
    sub_cost_code_service.read_by_qbo_identity.return_value = None
    sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    sub_cost_code_service.create.return_value = _make_sub_cost_code(id=500)
    sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code(
        id=500, qbo_id="ITEM-99", realm_id="realm-1"
    )
    recorded = []

    # FASTPATH_LOCK_TARGET and SCC_STAMP_LOCK_TARGET are now the SAME name
    # (both locks live in identity_fastpath.py, U-328/U-331) -- one recording
    # patch captures both acquisitions, in order.
    with patch(FASTPATH_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector.sync_from_qbo_item(qbo_item)

    assert recorded == [
        "qbo_dbo_identity_create:SubCostCode:ITEM-99:realm-1",
        "qbo_dbo_identity_stamp:SubCostCode:500",
    ]


def test_sub_cost_code_stamp_identity_refuses_to_overwrite_different_existing_identity():
    """Also proves the new Decision-2 on_conflict wiring (U-328/U-331) reuses
    `_raise_duplicate_qbo_item_issue` (the SAME recorder/DriftType
    resolve_candidate's own number-match guard already uses) on this
    stamp-time race — Codex xhigh P3 finding on the stamp-lock-helper diff."""
    connector, sub_cost_code_service, _, reconciliation_repo = _build_sub_cost_code_connector()
    candidate = _make_sub_cost_code(id=201)
    sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code(
        id=201, qbo_id="ITEM-OTHER", realm_id="realm-1"
    )
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(SCC_STAMP_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries QBO identity ITEM-OTHER"):
            connector._stamp_sub_cost_code_identity(
                candidate, qbo_item, name="X", description=None, cost_code_id=100,
            )

    sub_cost_code_service.repo.set_qbo_identity.assert_not_called()
    sub_cost_code_service.repo.update_by_id.assert_not_called()  # never mutated before the raise
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_item"


def test_sub_cost_code_stamp_identity_applies_field_write_atomically_with_stamp():
    """Codex round-2 P1 fix, SubCostCode side."""
    connector, sub_cost_code_service, _, _r = _build_sub_cost_code_connector()
    candidate = _make_sub_cost_code(id=201)
    unmapped = _make_sub_cost_code(id=201, qbo_id=None, realm_id=None, name="Old", cost_code_id=999)
    sub_cost_code_service.read_by_id.return_value = unmapped
    sub_cost_code_service.repo.update_by_id.side_effect = lambda s: s
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(SCC_STAMP_LOCK_TARGET, side_effect=_granted_lock):
        connector._stamp_sub_cost_code_identity(
            candidate, qbo_item, name="New", description="new desc", cost_code_id=100,
        )

    assert unmapped.name == "New"
    assert unmapped.description == "new desc"
    assert unmapped.cost_code_id == 100
    sub_cost_code_service.repo.update_by_id.assert_called_once_with(unmapped)
    sub_cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=201, qbo_id="ITEM-99", realm_id="realm-1", active=True,
    )


def test_sub_cost_code_stamp_identity_concurrent_update_race_raises_and_holds():
    """D1 (docs/design/stamp-lock-helper.md): before U-328/U-331, this
    connector's own `_stamp_sub_cost_code_identity` called `update_by_id` and
    discarded its return value entirely -- a concurrent ROWVERSION race on
    this exact write silently succeeded at `set_qbo_identity` anyway
    (TODO.md:44-51, U-316 follow-up). Migrating onto the shared
    `stamp_dbo_identity_with_lock` closes this structurally: `update_by_id`
    returning None (the race) must now raise and hold for retry, and
    set_qbo_identity must never fire on that path. Mutation target: reverting
    the shared helper's apply_fields None-guard reproduces the pre-fix
    silent-success bug and this test goes red."""
    connector, sub_cost_code_service, _, _r = _build_sub_cost_code_connector()
    candidate = _make_sub_cost_code(id=201)
    unmapped = _make_sub_cost_code(id=201, qbo_id=None, realm_id=None, name="Old", cost_code_id=999)
    sub_cost_code_service.read_by_id.return_value = unmapped
    sub_cost_code_service.repo.update_by_id.return_value = None  # race: row gone on write
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(SCC_STAMP_LOCK_TARGET, side_effect=_granted_lock):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector._stamp_sub_cost_code_identity(
                candidate, qbo_item, name="New", description="new desc", cost_code_id=100,
            )

    sub_cost_code_service.repo.set_qbo_identity.assert_not_called()


def test_sub_cost_code_stamp_identity_fails_closed_on_lock_timeout():
    connector, sub_cost_code_service, _, _r = _build_sub_cost_code_connector()
    candidate = _make_sub_cost_code(id=201)
    qbo_item = _make_qbo_item(qbo_id="ITEM-99", realm_id="realm-1")

    with patch(SCC_STAMP_LOCK_TARGET, side_effect=_denied_lock):
        with pytest.raises(RuntimeError, match="Could not acquire identity-stamp lock"):
            connector._stamp_sub_cost_code_identity(
                candidate, qbo_item, name="X", description=None, cost_code_id=100,
            )

    sub_cost_code_service.read_by_id.assert_not_called()
    sub_cost_code_service.repo.set_qbo_identity.assert_not_called()
