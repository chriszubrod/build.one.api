"""Pure-logic tests for U-276 (Phase-4 pilot): repoint the `customer` connector
family's identity resolution off qbo.Customer / qbo.CustomerCustomer / qbo.CustomerProject
onto dbo.Customer / dbo.Project's native QboId/RealmId (U-238a/c).

Covers:
  1. CustomerRepository.read_by_qbo_identity / ProjectRepository.read_by_qbo_identity
     (sproc call shape).
  2. ProjectService.read_by_qbo_identity threads RBAC actor scope like its siblings.
  3. CustomerCustomerConnector / CustomerProjectConnector's new direct-identity fast
     path: hit updates without the mapping-table hop + self-heals a missing mapping
     row; miss falls through to the pre-existing mapping-table path unchanged.
  4. The Bill / Purchase / Invoice `_get_qbo_customer_ref` push helpers now read
     dbo.Project.Name/.QboId directly instead of qbo.Customer.DisplayName.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.customer.connector.customer.business.service import (
    CustomerCustomerConnector,
)
from integrations.intuit.qbo.customer.connector.project.business.service import (
    CustomerProjectConnector,
)


def _make_qbo_customer(**overrides):
    defaults = dict(
        id=4,
        qbo_id="C-99",
        realm_id="realm-1",
        display_name="Proj",
        company_name=None,
        is_job=False,
        active=True,
        notes="",
        parent_ref_value=None,
        bill_addr_id=None,
        ship_addr_id=None,
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo-level sproc call shape ---


def test_customer_repo_read_by_qbo_identity_calls_sproc():
    from entities.customer.persistence.repo import CustomerRepository

    repo = CustomerRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.customer.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.customer.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("C-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadCustomerByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "C-99", "RealmId": "realm-1"}


def test_project_repo_read_by_qbo_identity_calls_sproc():
    from entities.project.persistence.repo import ProjectRepository

    repo = ProjectRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.project.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.project.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("P-1", "realm-1", actor_user_id=7, actor_is_system_admin=True)

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadProjectByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {
        "QboId": "P-1",
        "RealmId": "realm-1",
        "ActorUserId": 7,
        "ActorIsSystemAdmin": 1,
    }


def test_project_service_read_by_qbo_identity_threads_actor_scope():
    """Mirrors read_by_id/read_by_name — must NOT bypass RBAC scoping."""
    from entities.project.business.service import ProjectService
    from shared.authz import current_is_system_admin, current_user_id

    repo = Mock()
    service = ProjectService(repo=repo)

    tok_u = current_user_id.set(7)
    tok_a = current_is_system_admin.set(True)
    try:
        service.read_by_qbo_identity("P-1", "realm-1")
    finally:
        current_user_id.reset(tok_u)
        current_is_system_admin.reset(tok_a)

    repo.read_by_qbo_identity.assert_called_once_with(
        "P-1", "realm-1", actor_user_id=7, actor_is_system_admin=True
    )


def test_customer_service_read_by_qbo_identity_is_a_thin_passthrough():
    from entities.customer.business.service import CustomerService

    repo = Mock()
    service = CustomerService(repo=repo)
    service.read_by_qbo_identity("C-1", "realm-1")
    repo.read_by_qbo_identity.assert_called_once_with("C-1", "realm-1")


# --- Section 2: CustomerCustomerConnector fast path ---
#
# The mapping-conflict cases are unit-tested directly against
# _resolve_mapping_state / _raise_identity_mapping_conflict_issue rather than
# through the full sync_from_qbo_customer(), because a detected conflict now
# falls through to the pre-existing (complex, multi-branch) legacy path
# instead of returning early (round-3 review finding: checking-then-mutating
# was itself the bug, so the fast path must not write on conflict — and what
# the legacy fallback then does with a globally-unmapped QboCustomer is
# already covered by test_qbo_customer_project_heal.py / test_u219, not this
# file's concern). What THIS file must prove: (a) the conflict is correctly
# detected and recorded, (b) the dbo-identity-matched row is never written to
# on that path.


def _build_customer_connector():
    mapping_repo = Mock()
    customer_service = Mock()
    customer_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = CustomerCustomerConnector(
        mapping_repo=mapping_repo,
        customer_service=customer_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, customer_service, reconciliation_repo


def test_customer_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_customer_id.return_value = SimpleNamespace(id=1, qbo_customer_id=4)
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=1, customer_id=55)

    state, _, _ = connector._resolve_mapping_state(customer_id=55, qbo_customer=qbo_customer)

    assert state == "consistent"


def test_customer_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_customer_id.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(customer_id=55, qbo_customer=qbo_customer)

    assert state == "missing"


def test_customer_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_customer_id.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=2, customer_id=9)

    state, by_customer, by_qbo_customer = connector._resolve_mapping_state(
        customer_id=55, qbo_customer=qbo_customer
    )

    assert state == "conflict"
    assert by_customer is None
    assert by_qbo_customer.customer_id == 9


def test_customer_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_customer_id.return_value = SimpleNamespace(id=3, qbo_customer_id=5)
    mapping_repo.read_by_qbo_customer_id.return_value = None

    state, by_customer, by_qbo_customer = connector._resolve_mapping_state(
        customer_id=55, qbo_customer=qbo_customer
    )

    assert state == "conflict"
    assert by_customer.qbo_customer_id == 5
    assert by_qbo_customer is None


def test_customer_raise_identity_mapping_conflict_issue_names_both_sides():
    """Codex-confirmed P1 (round 1) + P1/P3 (round 2/3): the recorded issue
    must name the dbo-identity-matched Customer AND whichever conflicting
    mapping(s) exist — never silently dropping one side, and never reusing a
    differently-shaped helper whose message wouldn't mention the right rows."""
    connector, _, _, reconciliation_repo = _build_customer_connector()
    qbo_customer = _make_qbo_customer(id=4, qbo_id="C-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, customer_id=9, qbo_customer_id=4)
    local_side = SimpleNamespace(id=3, customer_id=55, qbo_customer_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_customer=qbo_customer, dbo_customer_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "customer_identity_conflict"
    assert "55" in kwargs["details"]  # the dbo-identity-matched Customer
    assert "9" in kwargs["details"]   # the qbo-side conflicting Customer
    assert "5" in kwargs["details"]   # the local-side conflicting QboCustomer


def test_customer_fast_path_hit_updates_without_writing_on_conflict():
    """Integration-level check: on a detected conflict, sync_from_qbo_customer
    must NOT write to the dbo-identity-matched Customer (55) — the ordering
    bug round 3 found (write first, detect after) would corrupt it. The
    fallback path (exercised elsewhere) is free to do whatever it normally
    does with a globally-unmapped QboCustomer; only the non-write on 55 is
    this test's concern."""
    connector, mapping_repo, customer_service, reconciliation_repo = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", email="", phone="")
    customer_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_customer_id.return_value = None
    conflicting = SimpleNamespace(id=2, customer_id=9, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = conflicting
    # Safe terminal state for the legacy fallback this falls through to.
    customer_service.read_by_name.return_value = None
    customer_service.create.return_value = SimpleNamespace(id=77)

    connector.sync_from_qbo_customer(qbo_customer)

    reconciliation_repo.create.assert_called_once()
    # Customer 55 (the dbo-identity match) must never be written to.
    for call in customer_service.repo.update_by_id.call_args_list:
        written = call.args[0] if call.args else call.kwargs.get("customer")
        assert getattr(written, "id", None) != 55


def test_customer_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", email="", phone="")
    customer_service.read_by_qbo_identity.return_value = direct_hit
    customer_service.repo.update_by_id.side_effect = lambda c: c
    mapping_repo.read_by_customer_id.return_value = None  # mapping missing on this side...
    mapping_repo.read_by_qbo_customer_id.return_value = None  # ...and no conflicting mapping either

    connector.sync_from_qbo_customer(qbo_customer)

    mapping_repo.create.assert_called_once_with(customer_id=55, qbo_customer_id=qbo_customer.id)


def test_customer_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """Codex round-4 P2: a concurrent sync can turn 'missing' into 'conflict'
    between the pre-check and the create() call (no sp_getapplock serializes
    this — a known, pre-existing gap, see TODO.md). The create() failure must
    not just be a bare warning — re-check and record a real conflict issue
    when that's what actually happened."""
    connector, mapping_repo, customer_service, reconciliation_repo = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    direct_hit = SimpleNamespace(id=55, name="Acme", email="", phone="")
    customer_service.read_by_qbo_identity.return_value = direct_hit
    customer_service.repo.update_by_id.side_effect = lambda c: c
    # Pre-check (inside _resolve_mapping_state, called once before the write)
    # sees "missing"; the create() call itself fails (the race); a SECOND
    # _resolve_mapping_state call (the re-check) now sees a real conflict.
    mapping_repo.read_by_customer_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_customer_id.side_effect = [
        None, SimpleNamespace(id=9, customer_id=3, qbo_customer_id=qbo_customer.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    connector.sync_from_qbo_customer(qbo_customer)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "customer_identity_conflict"


def test_customer_fast_path_hit_consistent_skips_mapping_table_write():
    connector, mapping_repo, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    direct_hit = SimpleNamespace(id=55, name="", email="", phone="")
    customer_service.read_by_qbo_identity.return_value = direct_hit
    customer_service.repo.update_by_id.side_effect = lambda c: c
    mapping_repo.read_by_customer_id.return_value = SimpleNamespace(id=1, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=1, customer_id=55)

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result.name == "Acme"
    mapping_repo.create.assert_not_called()
    customer_service.create.assert_not_called()


def test_customer_fast_path_miss_falls_back_to_mapping_table_path():
    """No qbo_id on the incoming record (or no dbo row carries it yet) -> the
    pre-existing mapping-table-based logic must still run, untouched."""
    connector, mapping_repo, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = None
    customer_service.read_by_name = Mock(return_value=None)
    created = SimpleNamespace(id=77)
    customer_service.create.return_value = created
    mapping_repo.read_by_customer_id.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = None

    result = connector.sync_from_qbo_customer(qbo_customer)

    customer_service.read_by_qbo_identity.assert_called_once_with("C-99", "realm-1")
    assert result is created
    customer_service.create.assert_called_once()


def test_customer_fast_path_skipped_entirely_when_no_qbo_id():
    """A record with no external qbo_id can't possibly have a dbo-native identity
    match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id=None)
    mapping_repo.read_by_qbo_customer_id.return_value = None
    customer_service.read_by_name = Mock(return_value=None)
    customer_service.create.return_value = SimpleNamespace(id=1)
    mapping_repo.read_by_customer_id.return_value = None

    connector.sync_from_qbo_customer(qbo_customer)

    customer_service.read_by_qbo_identity.assert_not_called()


# --- Section 3: CustomerProjectConnector fast path ---
# Same testing shape as Section 2 — see its header comment.


def _build_project_connector():
    mapping_repo = Mock()
    project_service = Mock()
    project_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = CustomerProjectConnector(
        mapping_repo=mapping_repo,
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        customer_mapping_repo=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._sync_addresses = Mock()
    return connector, mapping_repo, project_service, reconciliation_repo


def test_project_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_project_id.return_value = SimpleNamespace(id=2, qbo_customer_id=4)
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=2, project_id=88)

    state, _, _ = connector._resolve_mapping_state(project_id=88, qbo_customer=qbo_customer)

    assert state == "consistent"


def test_project_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_project_id.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(project_id=88, qbo_customer=qbo_customer)

    assert state == "missing"


def test_project_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_project_id.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=2, project_id=9)

    state, by_project, by_qbo_customer = connector._resolve_mapping_state(
        project_id=88, qbo_customer=qbo_customer
    )

    assert state == "conflict"
    assert by_project is None
    assert by_qbo_customer.project_id == 9


def test_project_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_project_id.return_value = SimpleNamespace(id=3, qbo_customer_id=5)
    mapping_repo.read_by_qbo_customer_id.return_value = None

    state, by_project, by_qbo_customer = connector._resolve_mapping_state(
        project_id=88, qbo_customer=qbo_customer
    )

    assert state == "conflict"
    assert by_project.qbo_customer_id == 5
    assert by_qbo_customer is None


def test_project_resolve_mapping_state_two_row_crossed_conflict():
    """Codex round-3 P3: Project 88's own mapping points at QboCustomer 5
    WHILE QboCustomer 4's mapping points at a different Project 9 — both
    conflict signals present at once. Both must survive into the recorded
    issue (checked in the message test below), not just whichever direction
    is checked first."""
    connector, mapping_repo, _, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(id=4)
    mapping_repo.read_by_project_id.return_value = SimpleNamespace(id=3, qbo_customer_id=5)
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=2, project_id=9)

    state, by_project, by_qbo_customer = connector._resolve_mapping_state(
        project_id=88, qbo_customer=qbo_customer
    )

    assert state == "conflict"
    assert by_project.qbo_customer_id == 5
    assert by_qbo_customer.project_id == 9


def test_project_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_project_connector()
    qbo_customer = _make_qbo_customer(id=4, qbo_id="P-1", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, project_id=9, qbo_customer_id=4)
    local_side = SimpleNamespace(id=3, project_id=88, qbo_customer_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_customer=qbo_customer, dbo_project_id=88,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "project_identity_conflict"
    assert "88" in kwargs["details"]  # the dbo-identity-matched Project
    assert "9" in kwargs["details"]   # the qbo-side conflicting Project
    assert "5" in kwargs["details"]   # the local-side conflicting QboCustomer


def test_project_fast_path_hit_updates_without_writing_on_conflict():
    """Integration-level check mirroring the Customer one above: on a
    detected conflict, sync_from_qbo_customer must NOT write to the
    dbo-identity-matched Project (88)."""
    connector, mapping_repo, project_service, reconciliation_repo = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="Proj X", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_project_id.return_value = None
    conflicting = SimpleNamespace(id=2, project_id=9, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = conflicting
    # Safe terminal state for the legacy fallback this falls through to (it
    # finds `conflicting`'s own mapping and updates Project 9 through it —
    # unrelated to Project 88, which is this test's actual concern).
    project_service.read_by_id.return_value = SimpleNamespace(
        id=9, name="Other Proj", description="", status="active", customer_id=None
    )
    project_service.repo.update_by_id.side_effect = lambda p: p

    connector.sync_from_qbo_customer(qbo_customer)

    reconciliation_repo.create.assert_called_once()
    for call in project_service.repo.update_by_id.call_args_list:
        written = call.args[0] if call.args else call.kwargs.get("project")
        assert getattr(written, "id", None) != 88


def test_project_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="Proj X", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    project_service.repo.update_by_id.side_effect = lambda p: p
    mapping_repo.read_by_project_id.return_value = None  # mapping missing on this side...
    mapping_repo.read_by_qbo_customer_id.return_value = None  # ...and no conflicting mapping either

    connector.sync_from_qbo_customer(qbo_customer)

    mapping_repo.create.assert_called_once_with(project_id=88, qbo_customer_id=qbo_customer.id)


def test_project_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """Codex round-4 P2, Project mirror of the Customer test above."""
    connector, mapping_repo, project_service, reconciliation_repo = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="Proj X", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    project_service.repo.update_by_id.side_effect = lambda p: p
    mapping_repo.read_by_project_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_customer_id.side_effect = [
        None, SimpleNamespace(id=9, project_id=3, qbo_customer_id=qbo_customer.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    connector.sync_from_qbo_customer(qbo_customer)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "project_identity_conflict"


def test_project_fast_path_hit_consistent_skips_mapping_table_write():
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", display_name="Proj X", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    project_service.repo.update_by_id.side_effect = lambda p: p
    mapping_repo.read_by_project_id.return_value = SimpleNamespace(id=2, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=2, project_id=88)

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result.name == "Proj X"
    mapping_repo.create.assert_not_called()
    project_service.create.assert_not_called()
    # Identity is already correct by construction on the fast path — must not re-stamp.
    project_service.repo.set_qbo_identity.assert_not_called()


def test_project_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    project_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = None
    project_service.read_by_name.return_value = None
    created = SimpleNamespace(id=99)
    project_service.create.return_value = created
    mapping_repo.read_by_project_id.return_value = None

    result = connector.sync_from_qbo_customer(qbo_customer)

    project_service.read_by_qbo_identity.assert_called_once_with("P-1", "realm-1")
    assert result is created
    project_service.create.assert_called_once()


# --- Section 4: push-helper repoints (Bill / Purchase / Invoice) ---
#
# Round-4 review: dbo.Project.QboId alone isn't enough to trust for an
# outbound push (dbo-internal uniqueness ≠ mapping-table agreement) — every
# push helper now runs it through verify_project_qbo_identity() first. That
# helper's own logic (consistent / no-mapping-yet / disagreement) is unit
# tested directly; the push-helper tests below just confirm each one wires
# it in and respects the result.


def test_verify_project_qbo_identity_trusts_when_no_mapping_yet():
    from integrations.intuit.qbo.base.identity_consistency import verify_project_qbo_identity

    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    customer_project_repo = Mock()
    customer_project_repo.read_by_project_id.return_value = None  # not migrated yet — nothing to disagree with
    qbo_customer_repo = Mock()

    result = verify_project_qbo_identity(
        project, customer_project_repo=customer_project_repo, qbo_customer_repo=qbo_customer_repo
    )

    assert result == "QBO-P-42"
    qbo_customer_repo.read_by_id.assert_not_called()


def test_verify_project_qbo_identity_trusts_when_mapping_agrees():
    from integrations.intuit.qbo.base.identity_consistency import verify_project_qbo_identity

    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    customer_project_repo = Mock()
    customer_project_repo.read_by_project_id.return_value = SimpleNamespace(qbo_customer_id=10)
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QBO-P-42")

    result = verify_project_qbo_identity(
        project, customer_project_repo=customer_project_repo, qbo_customer_repo=qbo_customer_repo
    )

    assert result == "QBO-P-42"


def test_verify_project_qbo_identity_refuses_when_mapping_disagrees():
    """Codex round-4 P1: a stale/'stolen' dbo QboId must NOT be trusted for an
    outbound push when the mapping table still binds a DIFFERENT external
    customer to this Project — that's the financial-misrouting risk."""
    from integrations.intuit.qbo.base.identity_consistency import verify_project_qbo_identity

    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    customer_project_repo = Mock()
    customer_project_repo.read_by_project_id.return_value = SimpleNamespace(qbo_customer_id=10)
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QBO-P-OTHER")

    result = verify_project_qbo_identity(
        project, customer_project_repo=customer_project_repo, qbo_customer_repo=qbo_customer_repo
    )

    assert result is None


def test_verify_project_qbo_identity_none_when_no_qbo_id():
    from integrations.intuit.qbo.base.identity_consistency import verify_project_qbo_identity

    project = SimpleNamespace(id=42, qbo_id=None)
    assert verify_project_qbo_identity(project, customer_project_repo=Mock(), qbo_customer_repo=Mock()) is None


def test_bill_get_qbo_customer_ref_reads_project_directly():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
    from integrations.intuit.qbo.bill.external.schemas import QboReferenceType

    connector = BillBillConnector(
        mapping_repo=Mock(), bill_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(), bill_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(), qbo_item_repo=Mock(),
        customer_project_repo=Mock(), qbo_customer_repo=Mock(),
        project_service=Mock(), qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(), qbo_term_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = SimpleNamespace(
        id=42, name="TB3 - 917 Tyne Blvd", qbo_id="QBO-P-42"
    )
    connector.customer_project_repo.read_by_project_id.return_value = None  # nothing to disagree with

    ref = connector._get_qbo_customer_ref(42)

    connector.project_service.read_by_id.assert_called_once_with(42)
    assert ref == QboReferenceType(value="QBO-P-42", name="TB3 - 917 Tyne Blvd")


def test_bill_get_qbo_customer_ref_none_when_project_never_synced():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(
        mapping_repo=Mock(), bill_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(), bill_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(), qbo_item_repo=Mock(),
        customer_project_repo=Mock(), qbo_customer_repo=Mock(),
        project_service=Mock(), qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(), qbo_term_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = SimpleNamespace(id=42, name="Manual Proj", qbo_id=None)

    assert connector._get_qbo_customer_ref(42) is None


def test_bill_get_qbo_customer_ref_none_when_mapping_disagrees():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(
        mapping_repo=Mock(), bill_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(), bill_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(), qbo_item_repo=Mock(),
        customer_project_repo=Mock(), qbo_customer_repo=Mock(),
        project_service=Mock(), qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(), qbo_term_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = SimpleNamespace(id=42, name="Proj", qbo_id="QBO-P-42")
    connector.customer_project_repo.read_by_project_id.return_value = SimpleNamespace(qbo_customer_id=10)
    connector.qbo_customer_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QBO-P-OTHER")

    assert connector._get_qbo_customer_ref(42) is None


def test_purchase_get_qbo_customer_ref_reads_project_directly():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        PurchaseExpenseConnector,
    )
    from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

    connector = PurchaseExpenseConnector(
        mapping_repo=Mock(), expense_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_purchase_repo=Mock(),
        qbo_purchase_line_repo=Mock(),
    )
    fake_project_service = Mock()
    fake_project_service.read_by_id.return_value = SimpleNamespace(
        id=7, name="OL-14 - Overton Lea", qbo_id="QBO-P-7"
    )
    fake_customer_project_repo = Mock()
    fake_customer_project_repo.read_by_project_id.return_value = None

    with patch(
        "entities.project.business.service.ProjectService", return_value=fake_project_service
    ), patch(
        "integrations.intuit.qbo.customer.connector.project.persistence.repo.CustomerProjectRepository",
        return_value=fake_customer_project_repo,
    ):
        ref = connector._get_qbo_customer_ref(7)

    fake_project_service.read_by_id.assert_called_once_with(7)
    assert ref == QboReferenceType(value="QBO-P-7", name="OL-14 - Overton Lea")


def test_invoice_get_qbo_customer_ref_reads_project_directly():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )
    from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

    connector = InvoiceInvoiceConnector(
        mapping_repo=Mock(), line_mapping_repo=Mock(), invoice_service=Mock(),
        project_service=Mock(), qbo_customer_repo=Mock(), customer_project_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = SimpleNamespace(
        id=9, name="HA - 206 Haverford Ave", qbo_id="QBO-P-9"
    )
    connector.customer_project_repo.read_by_project_id.return_value = None

    ref = connector._get_qbo_customer_ref(9)

    connector.project_service.read_by_id.assert_called_once_with(9)
    assert ref == QboReferenceType(value="QBO-P-9", name="HA - 206 Haverford Ave")


def test_invoice_get_qbo_customer_ref_none_when_project_never_synced():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    connector = InvoiceInvoiceConnector(
        mapping_repo=Mock(), line_mapping_repo=Mock(), invoice_service=Mock(),
        project_service=Mock(), qbo_customer_repo=Mock(), customer_project_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = None

    assert connector._get_qbo_customer_ref(9) is None
