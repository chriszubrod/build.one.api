"""Pure-logic tests for U-303: the CustomerProjectConnector name-match bind
path (no existing qbo.CustomerProject mapping, dbo-identity fast path misses,
an unmapped local Project name-matches) must write the resolved parent
CustomerId, same as the fast-path/legacy-update/create branches already do
(U-297).

Before this unit, that one branch (`sync_from_qbo_customer`'s "Local Project
exists with no QBO mapping — bind it" block) called only `create_mapping` +
`_sync_addresses`, never touching `Project.customer_id` — so the SAME
sub-customer landed WITH a parent via every other resolution branch but
WITHOUT one here, purely depending on which branch caught it first.

The parent-Customer RESOLVER itself (`_get_parent_customer_id` /
`_resolve_parent_customer_id`) is exhaustively covered by
`test_u297_customer_project_parent_customer_repoint.py` — these tests stub
its result directly rather than re-driving the dbo-identity / legacy two-hop
machinery, isolating the one thing U-303 changes: what the name-match branch
does with that resolved value.
"""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.customer.connector.project.business.service import (
    CustomerProjectConnector,
)
from conftest import mock_qbo_app_lock_granted, stub_qbo_identity_fastpath_miss

# U-311: sync_from_qbo_customer now routes every branch through
# run_identity_fastpath_dbo_only, which takes a create-lock on a genuine miss,
# and _stamp_project_identity takes its own separate stamp-lock -- both must
# be granted for these tests to reach the name-match-bind code they target.
FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
PROJECT_STAMP_LOCK_TARGET = (
    "integrations.intuit.qbo.customer.connector.project.business.service.qbo_app_lock"
)


@contextmanager
def _locks_granted():
    with ExitStack() as stack:
        stack.enter_context(patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted))
        stack.enter_context(patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted))
        yield


def _make_qbo_customer(**overrides):
    defaults = dict(
        id=4,
        qbo_id="C-99",
        realm_id="realm-1",
        display_name="Sub Unit A",
        company_name=None,
        is_job=True,
        active=True,
        notes="",
        parent_ref_value="P-1",
        bill_addr_id=None,
        ship_addr_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _echo_as_fresh_object(project):
    """Stand-in for the real `ProjectRepository.update_by_id` contract: it
    always returns a NEW `Project` built from the sproc's OUTPUT row, never
    the same object passed in. A bare `lambda p: p` echo can't distinguish
    the connector actually reassigning `existing_local = updated` from a
    regression that silently keeps using the pre-write object — see
    `test_name_match_bind_writes_the_resolved_parent`, which asserts on
    object identity specifically to catch that."""
    return SimpleNamespace(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        customer_id=project.customer_id,
    )


def _build_connector_for_name_match_bind(*, existing_local, resolved_customer_id=42):
    """A connector pinned to land on the no-mapping/name-match bind branch:
    dbo-identity fast path misses, no qbo.CustomerProject mapping exists yet,
    and `existing_local` name-matches an unmapped local Project.

    U-311: `_stamp_project_identity` re-reads via `project_service.read_by_id`
    TWICE -- once as `current` (the row it may mutate+write), once more at
    the very end (the real repo's re-read-after-write contract, mirroring
    `_stamp_customer_identity`). The first call must return `existing_local`
    itself (so mutating `current.customer_id` actually lands on the object
    these tests hold a reference to); the second call returns a FRESH object
    (via `_echo_as_fresh_object`) only if an actual write happened -- exactly
    mirroring what a real DB round-trip would produce, and letting
    `test_name_match_bind_writes_the_resolved_parent`'s object-identity
    assertion and `test_name_match_bind_skips_the_write_when_parent_unresolvable`'s
    `is existing_local` assertion both hold for the write and no-write cases
    respectively.
    """
    project_service = Mock()
    stub_qbo_identity_fastpath_miss(project_service)
    project_service.read_by_name.return_value = existing_local

    write_happened = {"flag": False}

    def _update_by_id(project):
        write_happened["flag"] = True
        return _echo_as_fresh_object(project)

    project_service.repo.update_by_id.side_effect = _update_by_id

    read_by_id_calls = {"n": 0}

    def _read_by_id(_id):
        read_by_id_calls["n"] += 1
        if read_by_id_calls["n"] == 1 or not write_happened["flag"]:
            return existing_local
        return _echo_as_fresh_object(existing_local)

    project_service.read_by_id.side_effect = _read_by_id

    connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    connector._sync_addresses = Mock()
    connector._get_parent_customer_id = Mock(return_value=resolved_customer_id)
    return connector


def _build_rowversion_realistic_connector(*, existing_local, resolved_customer_id=42):
    """Models real SQL Server ROWVERSION optimistic-concurrency semantics well
    enough to catch an ordering regression between the new CustomerId write
    and `create_mapping`'s internal `set_qbo_identity` call.

    Regression context (U-303's own P1, caught by adversarial review, not the
    author): an EARLIER draft of this fix wrote CustomerId AFTER calling
    `create_mapping`. `SetProjectQboIdentity`'s UPDATE unconditionally bumps
    this row's RowVersion first (its WHERE guard is guaranteed true here,
    since reaching this branch means the top-of-method identity fast path
    already MISSED on this exact (qbo_id, realm_id) pair — so the row can't
    already carry it). Writing CustomerId afterward then sent a stale
    RowVersion into `UpdateProjectById` and spuriously raised on every real
    invocation. A plain `lambda p: p` / `lambda *a, **k: None` mock can't see
    this — it doesn't model the DB row's version advancing between the two
    calls — so this fake tracks one server-side row-version cell that BOTH
    `set_qbo_identity` and `update_by_id` check/bump, exactly like the real
    sprocs' contracts.
    """
    server_row_version = {"value": 1}
    existing_local.row_version = 1

    def _set_qbo_identity(*, id, qbo_id, realm_id):
        server_row_version["value"] += 1

    def _update_by_id(project):
        if project.row_version != server_row_version["value"]:
            return None  # UpdateProjectById's WHERE RowVersion=@RowVersion missed
        server_row_version["value"] += 1
        return SimpleNamespace(
            id=project.id,
            name=project.name,
            description=project.description,
            status=project.status,
            customer_id=project.customer_id,
            row_version=server_row_version["value"],
        )

    project_service = Mock()
    stub_qbo_identity_fastpath_miss(project_service)
    project_service.read_by_name.return_value = existing_local
    project_service.read_by_id.return_value = existing_local
    project_service.repo.set_qbo_identity.side_effect = _set_qbo_identity
    project_service.repo.update_by_id.side_effect = _update_by_id

    connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    connector._sync_addresses = Mock()
    connector._get_parent_customer_id = Mock(return_value=resolved_customer_id)
    return connector


def _make_project(**overrides):
    defaults = dict(id=88, name="Sub Unit A", description="", status="active", customer_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_name_match_bind_writes_the_resolved_parent():
    existing_local = _make_project()
    connector = _build_connector_for_name_match_bind(existing_local=existing_local, resolved_customer_id=42)

    with _locks_granted():
        result = connector.sync_from_qbo_customer(_make_qbo_customer())

    # NOT `is existing_local`: the post-stamp re-read returns a fresh object
    # (real repo contract, see _echo_as_fresh_object) — this asserts the
    # connector actually returns that fresh state rather than silently
    # keeping the pre-write object.
    assert result is not existing_local
    assert result.customer_id == 42
    connector.project_service.repo.update_by_id.assert_called_once_with(existing_local)


def test_name_match_bind_survives_create_mappings_identity_stamp_rowversion_bump():
    """Regression for U-303's own P1 (caught by adversarial review before ship,
    not by the author): see `_build_rowversion_realistic_connector`'s docstring.
    The fix must write CustomerId using a RowVersion that is still valid at the
    moment `update_by_id` actually runs — i.e. it must not be sent stale by a
    RowVersion-bumping write (`_stamp_project_identity`'s own `set_qbo_identity`
    stamp) that already landed first. U-311 carries this ordering forward:
    the CustomerId write still happens BEFORE set_qbo_identity."""
    existing_local = _make_project()
    connector = _build_rowversion_realistic_connector(existing_local=existing_local, resolved_customer_id=42)

    with _locks_granted():
        result = connector.sync_from_qbo_customer(_make_qbo_customer())

    assert result.customer_id == 42
    connector.project_service.repo.set_qbo_identity.assert_called_once()
    connector.project_service.repo.update_by_id.assert_called_once()


def test_name_match_bind_writes_even_when_already_equal_to_the_resolved_value():
    """No equality short-circuit exists (by design, to keep the branch simple) —
    lock that down so a future 'skip the no-op write' optimization can't
    silently start also skipping a case where the resolution just changed."""
    existing_local = _make_project(customer_id=42)
    connector = _build_connector_for_name_match_bind(existing_local=existing_local, resolved_customer_id=42)

    with _locks_granted():
        connector.sync_from_qbo_customer(_make_qbo_customer())

    connector.project_service.repo.update_by_id.assert_called_once()


def test_name_match_bind_preserves_description_and_status():
    """`_stamp_project_identity` writes ONLY CustomerId — Name/Description/
    Status are deliberately left untouched. `_apply_project_fields_and_sync`
    would apply the QBO-derived Description/Status unconditionally, which is
    why this branch must NOT reuse it: it deliberately ADOPTS a pre-existing
    local Project without touching its other, possibly hand-authored, fields."""
    existing_local = _make_project(description="hand-written notes", status="on_hold")
    connector = _build_connector_for_name_match_bind(existing_local=existing_local, resolved_customer_id=42)

    with _locks_granted():
        result = connector.sync_from_qbo_customer(_make_qbo_customer())

    assert result.description == "hand-written notes"
    assert result.status == "on_hold"


def test_name_match_bind_skips_the_write_when_parent_unresolvable():
    """No resolvable parent must not trigger a no-op UPDATE (and its ROWVERSION churn)."""
    existing_local = _make_project()
    connector = _build_connector_for_name_match_bind(existing_local=existing_local, resolved_customer_id=None)

    with _locks_granted():
        result = connector.sync_from_qbo_customer(_make_qbo_customer())

    assert result is existing_local
    assert result.customer_id is None
    connector.project_service.repo.update_by_id.assert_not_called()


def test_name_match_bind_raises_concurrent_write_race_on_rowversion_conflict():
    """Mirrors the fast-path's U-291 guard: a None return from update_by_id
    means a concurrent edit/delete raced this bind and must raise loud, not
    silently return the pre-write (stale) Project."""
    existing_local = _make_project()
    connector = _build_connector_for_name_match_bind(existing_local=existing_local, resolved_customer_id=42)
    connector.project_service.repo.update_by_id.side_effect = None
    connector.project_service.repo.update_by_id.return_value = None

    with _locks_granted():
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector.sync_from_qbo_customer(_make_qbo_customer())


def test_name_match_bind_still_syncs_addresses():
    """Non-regression baseline: address-sync must survive unchanged alongside
    the CustomerId write. U-311: there is no more mapping row to create on
    this path (Wave-5 Option B retired qbo.CustomerProject from this
    connector's own pull entirely) — the pre-existing `mapping_repo.create`
    assertion this test used to carry is gone with that machinery."""
    existing_local = _make_project()
    connector = _build_connector_for_name_match_bind(existing_local=existing_local, resolved_customer_id=42)

    with _locks_granted():
        connector.sync_from_qbo_customer(_make_qbo_customer())

    connector._sync_addresses.assert_called_once()
    assert connector._sync_addresses.call_args.args[1] == existing_local.id


def test_equivalence_same_parent_regardless_of_which_branch_binds_it():
    """Correctness-prove: whether a sub-customer lands via the dbo-identity fast
    path (U-297's steady-state branch) or this name-match bind path (U-303's
    fix), it resolves to the SAME parent CustomerId — branch-independent."""
    resolved_parent_id = 42

    fast_path_project = _make_project(id=10)
    fast_connector = CustomerProjectConnector(
        project_service=Mock(),
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    fast_connector._sync_addresses = Mock()
    fast_connector._get_parent_customer_id = Mock(return_value=resolved_parent_id)
    fast_connector.project_service.read_by_qbo_identity.return_value = fast_path_project
    fast_connector.project_service.repo.update_by_id.side_effect = lambda p: p

    name_match_project = _make_project(id=88)
    name_match_connector = _build_connector_for_name_match_bind(
        existing_local=name_match_project, resolved_customer_id=resolved_parent_id
    )

    # The dbo-identity HIT branch returns before ever taking a lock (U-311 --
    # run_identity_fastpath_dbo_only only locks on a genuine MISS), so
    # fast_connector's call needs no lock patching; name_match_connector's
    # does (it's a genuine miss -> name-match-adopt).
    fast_result = fast_connector.sync_from_qbo_customer(_make_qbo_customer(id=4))
    with _locks_granted():
        name_match_result = name_match_connector.sync_from_qbo_customer(_make_qbo_customer(id=5))

    assert fast_result.customer_id == name_match_result.customer_id == resolved_parent_id
