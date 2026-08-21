"""
U-287 — shared QBO dbo-native identity fast path (base/identity_fastpath.py).

Pure-logic tests for the helper the six Phase-4 connectors now share. The six
families' own suites (test_u276/u277/u278/u279*) prove the bindings still behave;
THIS file pins the helper's own contract — above all the two guards that exist
because their absence shipped to prod:

  * conflict -> RAISE, unconditionally, with nothing written and nothing minted
    (the 2026-08-20 live-prod P0);
  * the self-heal create-race re-check that escalates a raced "missing" into a
    recorded conflict instead of a bare warning (U-276 round-4).
"""

# Python Standard Library Imports
from types import SimpleNamespace
from unittest.mock import Mock

# Third-party Imports
import pytest

# Local Imports
from integrations.intuit.qbo.base.identity_fastpath import (
    CONFLICT,
    CONSISTENT,
    MISSING,
    FastPathOutcome,
    resolve_mapping_state,
    run_identity_fastpath,
)


def _mapping(id_, external_id):
    """A mapping row carrying the external FK under the attr name used throughout."""
    return SimpleNamespace(id=id_, ext_id=external_id)


# --- resolve_mapping_state -------------------------------------------------


def test_resolve_mapping_state_consistent():
    by_local = _mapping(1, 42)
    state, local_side, external_side = resolve_mapping_state(
        local_id=55,
        external_id=42,
        read_by_local_id=lambda _: by_local,
        read_by_external_id=lambda _: pytest.fail("must not read the external side"),
        external_id_attr="ext_id",
    )
    assert state == CONSISTENT
    # Both slots are the SAME row — the external read is provably redundant here
    # (the external id is unique on every one of these mapping tables), and
    # skipping it is what keeps the steady-state path one round trip cheaper.
    assert local_side is by_local and external_side is by_local


def test_resolve_mapping_state_missing():
    state, local_side, external_side = resolve_mapping_state(
        local_id=55,
        external_id=42,
        read_by_local_id=lambda _: None,
        read_by_external_id=lambda _: None,
        external_id_attr="ext_id",
    )
    assert state == MISSING
    assert local_side is None and external_side is None


def test_resolve_mapping_state_qbo_side_conflict():
    """Nothing binds our local row, but the external id is bound elsewhere."""
    conflicting = _mapping(2, 42)
    state, local_side, external_side = resolve_mapping_state(
        local_id=55,
        external_id=42,
        read_by_local_id=lambda _: None,
        read_by_external_id=lambda _: conflicting,
        external_id_attr="ext_id",
    )
    assert state == CONFLICT
    assert local_side is None and external_side is conflicting


def test_resolve_mapping_state_local_side_conflict():
    """Our local row is bound, but to a DIFFERENT external id."""
    stale = _mapping(3, 7)
    state, local_side, external_side = resolve_mapping_state(
        local_id=55,
        external_id=42,
        read_by_local_id=lambda _: stale,
        read_by_external_id=lambda _: None,
        external_id_attr="ext_id",
    )
    assert state == CONFLICT
    assert local_side is stale and external_side is None


def test_resolve_mapping_state_two_row_crossed_conflict():
    """Both directions bound, to different partners — neither side may be dropped."""
    stale, conflicting = _mapping(3, 7), _mapping(2, 42)
    state, local_side, external_side = resolve_mapping_state(
        local_id=55,
        external_id=42,
        read_by_local_id=lambda _: stale,
        read_by_external_id=lambda _: conflicting,
        external_id_attr="ext_id",
    )
    assert state == CONFLICT
    assert local_side is stale and external_side is conflicting


# --- run_identity_fastpath: resolution ------------------------------------


def _harness(*, direct=None, by_local=None, by_external=None, **overrides):
    """Build a run_identity_fastpath kwargs dict plus the Mocks it drives."""
    spy = SimpleNamespace(
        read_direct=Mock(return_value=direct),
        apply=Mock(side_effect=lambda e: e),
        create_mapping=Mock(),
        record=Mock(),
        on_none=Mock(),
    )
    kwargs = dict(
        qbo_id="Q-99",
        realm_id="realm-1",
        external_id=42,
        entity_label="Widget",
        external_label="QboWidget",
        mapping_label="WidgetWidget",
        read_direct_by_qbo_identity=spy.read_direct,
        read_by_local_id=Mock(return_value=by_local),
        read_by_external_id=Mock(return_value=by_external),
        external_id_attr="ext_id",
        record_conflict_issue=spy.record,
        conflict_message=lambda e: f"conflict on {e.id}",
        create_mapping=spy.create_mapping,
        apply_fields=spy.apply,
    )
    kwargs.update(overrides)
    return kwargs, spy


def test_falsy_qbo_id_short_circuits_without_reading():
    kwargs, spy = _harness()
    kwargs["qbo_id"] = None
    outcome = run_identity_fastpath(**kwargs)
    assert outcome == FastPathOutcome(hit=False, entity=None)
    spy.read_direct.assert_not_called()
    spy.apply.assert_not_called()


def test_no_dbo_row_carrying_identity_is_a_miss():
    kwargs, spy = _harness(direct=None)
    outcome = run_identity_fastpath(**kwargs)
    assert outcome.hit is False
    spy.read_direct.assert_called_once_with("Q-99", "realm-1")
    spy.apply.assert_not_called()


# --- run_identity_fastpath: the conflict hard stop -------------------------


def test_conflict_raises_records_and_writes_nothing():
    """THE guard. A conflict must record the issue and RAISE — never apply fields,
    never create a mapping, never return an outcome the caller could act on."""
    direct = SimpleNamespace(id=55)
    conflicting = _mapping(2, 42)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=conflicting)

    with pytest.raises(ValueError, match="conflict on 55"):
        run_identity_fastpath(**kwargs)

    spy.record.assert_called_once_with(direct, None, conflicting)
    spy.apply.assert_not_called()
    spy.create_mapping.assert_not_called()


def test_conflict_raises_on_local_side_shape_too():
    direct = SimpleNamespace(id=55)
    stale = _mapping(3, 7)
    kwargs, spy = _harness(direct=direct, by_local=stale, by_external=None)

    with pytest.raises(ValueError):
        run_identity_fastpath(**kwargs)

    spy.record.assert_called_once_with(direct, stale, None)
    spy.apply.assert_not_called()
    spy.create_mapping.assert_not_called()


def test_conflict_records_before_it_raises():
    """The reconciliation issue is the durable follow-up — it must be written even
    though the call is about to blow up, not skipped by an early raise."""
    direct = SimpleNamespace(id=55)
    order = []
    kwargs, _ = _harness(direct=direct, by_local=None, by_external=_mapping(2, 42))
    kwargs["record_conflict_issue"] = lambda *a: order.append("recorded")
    kwargs["conflict_message"] = lambda e: (order.append("raised"), "boom")[1]

    with pytest.raises(ValueError):
        run_identity_fastpath(**kwargs)

    assert order == ["recorded", "raised"]


# --- run_identity_fastpath: consistent / missing ---------------------------


def test_consistent_applies_fields_and_skips_mapping_create():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=_mapping(1, 42))
    outcome = run_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.apply.assert_called_once_with(direct)
    spy.create_mapping.assert_not_called()
    spy.record.assert_not_called()


def test_missing_applies_fields_then_self_heals_the_mapping():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=None)
    outcome = run_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.apply.assert_called_once_with(direct)
    spy.create_mapping.assert_called_once_with(55)
    spy.record.assert_not_called()


def test_mapping_create_is_keyed_on_the_post_apply_row_not_the_pre_apply_one():
    """apply_fields may return a re-read row; the mapping must bind THAT id."""
    direct = SimpleNamespace(id=55)
    reread = SimpleNamespace(id=56)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=None)
    kwargs["apply_fields"] = Mock(return_value=reread)
    run_identity_fastpath(**kwargs)
    spy.create_mapping.assert_called_once_with(56)


def test_local_id_is_coerced_from_str():
    """coerce_id keeps a str PK from reaching the mapping repo as a str."""
    kwargs, spy = _harness(direct=SimpleNamespace(id="55"), by_local=None, by_external=None)
    run_identity_fastpath(**kwargs)
    spy.create_mapping.assert_called_once_with(55)
    assert kwargs["read_by_local_id"].call_args.args[0] == 55


# --- run_identity_fastpath: the self-heal create race ----------------------


def test_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the pre-check
    and create(). The failure must re-check and record a REAL conflict issue —
    not leave a bare warning (U-276 round-4)."""
    direct = SimpleNamespace(id=55)
    raced = _mapping(9, 42)
    kwargs, spy = _harness(direct=direct)
    # Pre-check sees "missing"; the re-check sees the raced row.
    kwargs["read_by_local_id"] = Mock(side_effect=[None, None])
    kwargs["read_by_external_id"] = Mock(side_effect=[None, raced])
    spy.create_mapping.side_effect = Exception("UNIQUE constraint violation")

    outcome = run_identity_fastpath(**kwargs)

    spy.record.assert_called_once_with(direct, None, raced)
    # It records, but does NOT raise — the field write already landed and the
    # entity is still the right answer for the caller.
    assert outcome.hit is True and outcome.entity is direct


def test_self_heal_recheck_asks_about_the_post_apply_row():
    """The re-check must interrogate the row we actually wrote, not the one we read.

    Found by the U-287 mutation matrix: swapping the re-check's `coerce_id(updated.id)`
    for `coerce_id(direct.id)` passed every other test, because they all let apply_fields
    return the same object it was handed, making the two ids coincide. Here apply_fields
    returns a re-read row with a DIFFERENT id, so the distinction is observable — which
    matters because a race re-check keyed on a stale id would clear a conflict that the
    row we just wrote is actually in.
    """
    direct, reread = SimpleNamespace(id=55), SimpleNamespace(id=56)
    raced = _mapping(9, 42)
    kwargs, spy = _harness(direct=direct)
    kwargs["apply_fields"] = Mock(return_value=reread)
    kwargs["read_by_local_id"] = Mock(side_effect=[None, None])
    kwargs["read_by_external_id"] = Mock(side_effect=[None, raced])
    spy.create_mapping.side_effect = Exception("UNIQUE constraint violation")

    run_identity_fastpath(**kwargs)

    # pre-check asked about the row we read; re-check about the row we wrote
    assert [c.args[0] for c in kwargs["read_by_local_id"].call_args_list] == [55, 56]
    spy.record.assert_called_once_with(reread, None, raced)


def test_self_heal_race_that_resolves_benignly_records_nothing():
    """If the re-check shows the race resolved consistently, the create() failure
    was benign — log it, but do not manufacture a conflict issue."""
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct)
    kwargs["read_by_local_id"] = Mock(side_effect=[None, _mapping(9, 42)])
    kwargs["read_by_external_id"] = Mock(side_effect=[None])
    spy.create_mapping.side_effect = Exception("UNIQUE constraint violation")

    outcome = run_identity_fastpath(**kwargs)

    spy.record.assert_not_called()
    assert outcome.hit is True


def test_mapping_create_failure_is_swallowed_not_propagated():
    """The field write already succeeded; a mapping-create hiccup must not turn a
    successful sync into a raised error."""
    kwargs, spy = _harness(direct=SimpleNamespace(id=55))
    kwargs["read_by_local_id"] = Mock(side_effect=[None, None])
    kwargs["read_by_external_id"] = Mock(side_effect=[None, None])
    spy.create_mapping.side_effect = Exception("transient")
    assert run_identity_fastpath(**kwargs).hit is True


# --- run_identity_fastpath: apply returned None ---------------------------


def test_apply_returning_none_on_missing_skips_create_and_notifies():
    """A concurrent delete between the identity read and the write leaves nothing
    to map or stamp — the caller is told, and no mapping row is invented."""
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=None)
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = spy.on_none

    outcome = run_identity_fastpath(**kwargs)

    spy.on_none.assert_called_once_with(direct)
    spy.create_mapping.assert_not_called()
    assert outcome.hit is True and outcome.entity is None


def test_apply_returning_none_without_a_callback_is_safe():
    """Five of the six families pass no on_apply_returned_none — the helper must
    not require one (and must not crash reaching for .id on a None row)."""
    kwargs, spy = _harness(direct=SimpleNamespace(id=55), by_local=None, by_external=None)
    kwargs["apply_fields"] = Mock(return_value=None)
    outcome = run_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is None
    spy.create_mapping.assert_not_called()


def test_apply_returning_none_on_consistent_never_notifies():
    """on_apply_returned_none is a 'missing'-path concern only — the consistent
    path has no mapping row to create, so there is nothing to tell the caller."""
    kwargs, spy = _harness(direct=SimpleNamespace(id=55), by_local=_mapping(1, 42))
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = spy.on_none
    outcome = run_identity_fastpath(**kwargs)
    spy.on_none.assert_not_called()
    assert outcome.entity is None


# --- run_identity_fastpath: the resolution-only (attachable) shape ---------


def test_apply_fields_omitted_resolves_identity_without_writing():
    """AttachableAttachmentConnector uses the helper for identity resolution only
    and does its field work downstream — omitting apply_fields must write nothing
    and hand back `direct` untouched."""
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=_mapping(1, 42))
    kwargs.pop("apply_fields")
    outcome = run_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.create_mapping.assert_not_called()


# --- regression: the one confirmed finding from U-287's Pass-1 hunt ----------


def test_customer_connector_update_race_holds_the_watermark():
    """U-287 Pass-1 finding (confirmed by 2 of 5 lenses, traced directly).

    CustomerCustomerConnector was the only one of the six families whose
    apply_fields could return None with no handler. Before U-287 that path blew up
    with an AttributeError (the old inline block had no None guard and its own
    except-handler f-string re-raised), which record_projection_error classifies
    under rule 3 -> failure/HOLD, so the record self-healed on the next tick. The
    extraction's uniform None guard turned that into a silent `return None`, which
    project_records counts as a projected SUCCESS -> watermark ADVANCES past a
    Customer whose fields were never written and whose mapping row was never made.

    The raise must NOT be a plain ValueError: rule 2 classifies that as a permanent
    SKIP, which advances the watermark too — the very outcome being fixed.
    """
    from integrations.intuit.qbo.customer.connector.customer.business.service import (
        CustomerCustomerConnector,
    )

    mapping_repo, customer_service = Mock(), Mock()
    customer_service.repo = Mock()
    connector = CustomerCustomerConnector(
        mapping_repo=mapping_repo,
        customer_service=customer_service,
        reconciliation_repo=Mock(),
    )
    qbo_customer = SimpleNamespace(
        id=100, qbo_id="C-99", realm_id="realm-1", is_job=False,
        display_name="Acme", company_name=None, primary_email_addr=None,
        primary_phone=None, mobile=None, active=True,
    )
    customer_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=55, name="Acme", email="", phone=""
    )
    customer_service.repo.update_by_id.return_value = None  # ROWVERSION race
    mapping_repo.read_by_customer_id.return_value = None    # state == "missing"
    mapping_repo.read_by_qbo_customer_id.return_value = None

    with pytest.raises(RuntimeError):
        connector.sync_from_qbo_customer(qbo_customer)

    # A plain ValueError here would be classified as a permanent skip.
    mapping_repo.create.assert_not_called()


def test_apply_fields_omitted_still_self_heals_and_still_hard_stops():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=None)
    kwargs.pop("apply_fields")
    assert run_identity_fastpath(**kwargs).entity is direct
    spy.create_mapping.assert_called_once_with(55)

    kwargs, spy = _harness(direct=direct, by_local=None, by_external=_mapping(2, 42))
    kwargs.pop("apply_fields")
    with pytest.raises(ValueError):
        run_identity_fastpath(**kwargs)
    spy.create_mapping.assert_not_called()
