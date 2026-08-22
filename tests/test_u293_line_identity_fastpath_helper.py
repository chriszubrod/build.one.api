"""
U-293 — shared QBO dbo-native identity fast path for LINE-ITEM entities
(base/identity_fastpath.py::run_line_identity_fastpath).

Pure-logic tests, mirroring tests/test_u287_identity_fastpath_helper.py's harness
shape for the header helper. This is a deliberate SIBLING function, not a fork of
run_identity_fastpath: a line's QBO identity is unique only WITHIN its parent
transaction (confirmed against live prod at U-293's Gate-1 — real duplicate QboId
values are reused across different parents in every line family), so the direct-
read key here is (parent_local_id, qbo_line_id), never a bare (qbo_id, realm_id).

MISSING never self-heals here, UNLIKE the header helper — this is the one
deliberate divergence, not an oversight. A confirmed, adversarially-verified P1
(U-293 Gate-2, executable PoC) found that blindly self-healing a "direct hit, no
mapping either side" for a line is unsafe: QBO recycles a bill's line ids on edit
(stale-line cleanup deletes the old mapping+staging row but never clears the
orphaned dbo row's own QboId stamp), so a later, genuinely different new line can
reuse that same freed-up id and land on the stale orphan — silently overwriting
its real content with no error, no conflict record, nothing. So MISSING is now
treated as a plain miss (hit=False): only a CONSISTENT hit (an already-existing
mapping row confirms it) may ever write. These tests pin that contract, plus the
two guards shared unchanged with the header helper:

  * conflict -> RAISE, unconditionally, with nothing written and nothing minted
    (same class as the 2026-08-20 live-prod header P0);
  * apply_fields returning None (a ROWVERSION race) -> on_apply_returned_none
    fires, never a silent success.
"""

# Python Standard Library Imports
from types import SimpleNamespace
from unittest.mock import Mock

# Third-party Imports
import pytest

# Local Imports
from integrations.intuit.qbo.base.identity_fastpath import (
    FastPathOutcome,
    run_line_identity_fastpath,
)
from tests.test_u287_identity_fastpath_helper import _mapping


def _harness(*, direct=None, by_local=None, by_external=None, **overrides):
    """Build a run_line_identity_fastpath kwargs dict plus the Mocks it drives."""
    spy = SimpleNamespace(
        read_direct=Mock(return_value=direct),
        apply=Mock(side_effect=lambda e: e),
        record=Mock(),
        on_none=Mock(),
    )
    kwargs = dict(
        parent_local_id=900,
        qbo_line_id="1",
        external_id=42,
        entity_label="Widget",
        external_label="QboWidgetLine",
        read_direct_by_parent_and_qbo_line_id=spy.read_direct,
        read_by_local_id=Mock(return_value=by_local),
        read_by_external_id=Mock(return_value=by_external),
        external_id_attr="ext_id",
        record_conflict_issue=spy.record,
        conflict_message=lambda e: f"conflict on {e.id}",
        apply_fields=spy.apply,
    )
    kwargs.update(overrides)
    return kwargs, spy


# --- falsy short-circuit / miss ---------------------------------------------


def test_falsy_qbo_line_id_short_circuits_without_reading():
    kwargs, spy = _harness()
    kwargs["qbo_line_id"] = None
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome == FastPathOutcome(hit=False, entity=None)
    spy.read_direct.assert_not_called()
    spy.apply.assert_not_called()


def test_no_dbo_row_carrying_identity_is_a_miss():
    kwargs, spy = _harness(direct=None)
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome.hit is False
    spy.read_direct.assert_called_once_with(900, "1")
    spy.apply.assert_not_called()


def test_direct_read_is_keyed_on_parent_and_line_id_not_a_bare_qbo_id():
    """The whole point of the sibling function: the direct lookup takes the
    PARENT's local id as its first argument, never a bare global QboId — a line's
    QboId ("1", "2", ...) is only meaningful scoped to its own parent."""
    kwargs, spy = _harness(direct=None)
    kwargs["parent_local_id"] = 12345
    kwargs["qbo_line_id"] = "3"
    run_line_identity_fastpath(**kwargs)
    spy.read_direct.assert_called_once_with(12345, "3")


# --- the conflict hard stop --------------------------------------------------


def test_conflict_raises_records_and_writes_nothing():
    direct = SimpleNamespace(id=55)
    conflicting = _mapping(2, 42)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=conflicting)

    with pytest.raises(ValueError, match="conflict on 55"):
        run_line_identity_fastpath(**kwargs)

    spy.record.assert_called_once_with(direct, None, conflicting)
    spy.apply.assert_not_called()


def test_conflict_raises_on_local_side_shape_too():
    direct = SimpleNamespace(id=55)
    stale = _mapping(3, 7)
    kwargs, spy = _harness(direct=direct, by_local=stale, by_external=None)

    with pytest.raises(ValueError):
        run_line_identity_fastpath(**kwargs)

    spy.record.assert_called_once_with(direct, stale, None)
    spy.apply.assert_not_called()


def test_conflict_raises_on_both_sides_crossed_shape():
    direct = SimpleNamespace(id=55)
    stale, conflicting = _mapping(3, 7), _mapping(2, 42)
    kwargs, spy = _harness(direct=direct, by_local=stale, by_external=conflicting)

    with pytest.raises(ValueError):
        run_line_identity_fastpath(**kwargs)

    spy.record.assert_called_once_with(direct, stale, conflicting)
    spy.apply.assert_not_called()


def test_conflict_records_before_it_raises():
    direct = SimpleNamespace(id=55)
    order = []
    kwargs, _ = _harness(direct=direct, by_local=None, by_external=_mapping(2, 42))
    kwargs["record_conflict_issue"] = lambda *a: order.append("recorded")
    kwargs["conflict_message"] = lambda e: (order.append("raised"), "boom")[1]

    with pytest.raises(ValueError):
        run_line_identity_fastpath(**kwargs)

    assert order == ["recorded", "raised"]


# --- missing: never self-heals, unlike the header helper ---------------------


def test_missing_is_a_plain_miss_nothing_read_or_written():
    """THE guard this unit's Gate-2 review exists to pin: a direct hit with no
    mapping on either side must NOT self-heal for a line (a stale orphan whose
    QboId was never cleared by stale-line cleanup could otherwise be silently
    overwritten by an unrelated new line reusing its recycled id — a confirmed
    P1). apply_fields must never even be called."""
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=None)
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome == FastPathOutcome(hit=False, entity=None)
    spy.apply.assert_not_called()
    spy.record.assert_not_called()


def test_missing_with_apply_fields_omitted_is_still_a_plain_miss():
    kwargs, spy = _harness(direct=SimpleNamespace(id=55), by_local=None, by_external=None)
    kwargs.pop("apply_fields")
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome == FastPathOutcome(hit=False, entity=None)


def test_signature_has_no_create_mapping_parameter():
    """MISSING never reaches a create-mapping step any more — the parameter
    itself was removed, not just unused, so a caller can't silently pass a
    dead callback and believe self-heal still happens."""
    import inspect

    params = inspect.signature(run_line_identity_fastpath).parameters
    assert "create_mapping" not in params
    assert "mapping_label" not in params


# --- consistent: the only state that writes ----------------------------------


def test_consistent_applies_fields():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=_mapping(1, 42))
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.apply.assert_called_once_with(direct)
    spy.record.assert_not_called()


def test_local_id_is_coerced_from_str_for_the_mapping_check():
    """coerce_id keeps a str PK from reaching the mapping repo as a str —
    proven via the by_local/by_external calls, since there's no create_mapping
    call any more to observe it through."""
    direct = SimpleNamespace(id="55")
    kwargs, spy = _harness(direct=direct, by_local=_mapping(1, 42))
    run_line_identity_fastpath(**kwargs)
    assert kwargs["read_by_local_id"].call_args.args[0] == 55


# --- apply returned None (ROWVERSION race) -----------------------------------


def test_apply_returning_none_on_consistent_notifies():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=_mapping(1, 42))
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = spy.on_none

    outcome = run_line_identity_fastpath(**kwargs)

    spy.on_none.assert_called_once_with(direct)
    assert outcome.hit is True and outcome.entity is None


def test_apply_returning_none_without_a_callback_is_safe():
    kwargs, spy = _harness(direct=SimpleNamespace(id=55), by_local=_mapping(1, 42))
    kwargs["apply_fields"] = Mock(return_value=None)
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is None


# --- the resolution-only shape ------------------------------------------------


def test_apply_fields_omitted_on_consistent_resolves_identity_without_writing():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=_mapping(1, 42))
    kwargs.pop("apply_fields")
    outcome = run_line_identity_fastpath(**kwargs)
    assert outcome.hit is True and outcome.entity is direct


def test_apply_fields_omitted_still_hard_stops_on_conflict():
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct, by_local=None, by_external=_mapping(2, 42))
    kwargs.pop("apply_fields")
    with pytest.raises(ValueError):
        run_line_identity_fastpath(**kwargs)
    spy.record.assert_called_once()


# --- no realm_id parameter at all --------------------------------------------


def test_signature_has_no_realm_id_parameter():
    """Unlike the header helper, realm is not part of this function's direct-
    read key at all — the parent already pins it. Pin the contract so a future
    edit can't silently reintroduce an unused/misleading realm_id param."""
    import inspect

    params = inspect.signature(run_line_identity_fastpath).parameters
    assert "realm_id" not in params
    assert "parent_local_id" in params
    assert "qbo_line_id" in params
