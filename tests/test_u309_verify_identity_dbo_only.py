"""
U-309 — the dbo-only VERIFY primitive (base/identity_consistency.py::verify_identity_dbo_only).

Wave-5 "trust dbo alone" plan (memory `project_qbo_trust_dbo_identity_alone`, `docs/design/wave5.md`
§2): once a family's qbo.* mapping table is retired, the `verify_*_qbo_identity` wrappers' mapping-
table read has nothing left to read. `verify_identity_dbo_only` is the dbo-only replacement — it
re-reads `dbo.<Entity>` fresh by `(qbo_id, realm_id)` and confirms the caller's already-resolved
entity is still the current holder of that identity. No lock (see the function's own docstring for
why); UNWIRED as of this unit — U-310/U-311/U-312 wire it into the 12 verify/reference-resolver call
sites `docs/design/wave5.md` §4 enumerates.

These tests pin the helper's own contract in isolation — no DB I/O, `SimpleNamespace` stand-ins for
entities/rows, per the house convention `test_u300a_identity_fastpath_dbo_only.py` established for
this module's sibling (create-shaped) primitive.
"""
from types import SimpleNamespace
from unittest.mock import Mock

from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only


def _entity(*, id=42, qbo_id="Q-42", realm_id="realm-1"):
    return SimpleNamespace(id=id, qbo_id=qbo_id, realm_id=realm_id)


# --- falsy-entity / falsy-qbo_id short-circuit ------------------------------


def test_none_entity_short_circuits_without_reading():
    read_direct = Mock()
    assert verify_identity_dbo_only(None, read_direct_by_qbo_identity=read_direct) is None
    read_direct.assert_not_called()


def test_none_qbo_id_short_circuits_without_reading():
    read_direct = Mock()
    entity = _entity(qbo_id=None)
    assert verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_direct) is None
    read_direct.assert_not_called()


# --- reads direct by the entity's own (qbo_id, realm_id) --------------------


def test_reads_direct_by_the_entitys_own_qbo_id_and_realm_id():
    entity = _entity(id=42, qbo_id="Q-42", realm_id="realm-7")
    read_direct = Mock(return_value=SimpleNamespace(id=42))
    verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_direct)
    read_direct.assert_called_once_with("Q-42", "realm-7")


def test_missing_realm_id_is_passed_through_as_none():
    entity = _entity(id=42, qbo_id="Q-42", realm_id=None)
    read_direct = Mock(return_value=SimpleNamespace(id=42))
    verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_direct)
    read_direct.assert_called_once_with("Q-42", None)


# --- the verified case: fresh read agrees with the caller's entity ----------


def test_matching_fresh_read_returns_the_qbo_id():
    entity = _entity(id=42, qbo_id="Q-42")
    read_direct = Mock(return_value=SimpleNamespace(id=42))
    assert verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_direct) == "Q-42"


# --- the anomaly this primitive exists to catch ------------------------------
#
# Mutation target: if the `direct.id == entity.id` comparison were ever
# dropped (any non-None `direct` treated as a match), these two tests would
# go green on broken code — this is the fix these tests must prove RED against.


def test_no_row_found_refuses():
    entity = _entity(id=42, qbo_id="Q-42")
    read_direct = Mock(return_value=None)
    assert verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_direct) is None


def test_fresh_read_bound_to_a_different_local_id_refuses():
    """The core anomaly this primitive exists to catch: (qbo_id, realm_id) now
    resolves to a DIFFERENT local row than the one the caller already
    trusted — the identity moved out from under it. Must refuse, never
    silently vouch for the stale reference."""
    entity = _entity(id=42, qbo_id="Q-42")
    reassigned_to = SimpleNamespace(id=999)
    read_direct = Mock(return_value=reassigned_to)
    assert verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_direct) is None
