"""Pure-logic tests for U-276 (Phase-4 pilot): repoint the `customer` connector
family's identity resolution off qbo.Customer / qbo.CustomerCustomer / qbo.CustomerProject
onto dbo.Customer / dbo.Project's native QboId/RealmId (U-238a/c).

Covers:
  1. CustomerRepository.read_by_qbo_identity / ProjectRepository.read_by_qbo_identity
     (sproc call shape).
  2. ProjectService.read_by_qbo_identity threads RBAC actor scope like its siblings.
  3. CustomerCustomerConnector's identity resolution -- as of U-310 this is the
     DBO-ONLY fast path (`run_identity_fastpath_dbo_only`): no qbo.CustomerCustomer
     read or write of any kind, so there is no mapping-table fallback, no
     self-heal, and no mapping-vs-dbo conflict state left to test. A hit updates
     fields and writes nothing else; a genuine miss adopts by NAME or creates,
     then stamps identity under the candidate's own lock. See Section 2's header.
  4. CustomerProjectConnector's direct-identity fast path -- still the U-276
     mapping-table shape (hit updates without the mapping hop + self-heals a
     missing mapping row; miss falls through to the pre-existing mapping-table
     path unchanged). Project's own dbo-only repoint is U-311, not U-310.
  5. The Bill / Purchase / Invoice `_get_qbo_customer_ref` push helpers now read
     dbo.Project.Name/.QboId directly instead of qbo.Customer.DisplayName.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted, stub_identity_check_trusts
from integrations.intuit.qbo.base.identity_consistency import IdentityCheckResult
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


# --- Section 2: CustomerCustomerConnector dbo-only fast path (U-310) ---
#
# U-310 retired `qbo.CustomerCustomer` from this connector entirely (Wave-5
# "trust dbo alone", `docs/design/wave5.md` §2/§4): there is no mapping table
# left to read, write, self-heal, or conflict against, so the pre-U-310
# _resolve_mapping_state / _raise_identity_mapping_conflict_issue /
# create_mapping tests this section used to hold are gone with the machinery
# they covered. What THIS section must now prove is the dbo-only contract,
# mirroring `test_u289_item_qbo_identity_repoint.py`'s ItemCostCodeConnector
# section one-for-one (Customer is the same "parent, business-key-adoptable"
# shape as CostCode, with `name` as the business key where CostCode uses
# `number`): a direct or race-discovered hit updates fields and writes nothing
# else; a genuine miss (re-confirmed under the create lock) adopts an existing
# unmapped Customer by NAME (RAW name overwrite, U-219) or creates fresh, then
# stamps identity under the candidate's OWN lock; a name-matched row already
# carrying a DIFFERENT identity raises + records a `customer_identity_conflict`
# issue instead of being silently re-pointed (Decision 2's duplicate-QboId
# guard -- the one genuinely new, correctness-critical piece of this repoint).


CUST_CONNECTOR_MODULE = (
    "integrations.intuit.qbo.customer.connector.customer.business.service"
)
FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
# _stamp_customer_identity acquires its OWN app lock directly (not through
# run_identity_fastpath_dbo_only's create lock) -- a separate import in the
# connector module, so it needs its own patch target.
CUST_STAMP_LOCK_TARGET = f"{CUST_CONNECTOR_MODULE}.qbo_app_lock"


def _make_customer(**overrides):
    defaults = dict(
        id=55, public_id="cust-pub-55", qbo_id=None, realm_id=None,
        name="Acme", email="", phone="",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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


def _build_customer_connector():
    customer_service = Mock()
    customer_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = CustomerCustomerConnector(
        customer_service=customer_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, customer_service, reconciliation_repo


def test_customer_direct_hit_updates_fields_no_create_or_stamp():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    direct_hit = _make_customer(id=55, qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_qbo_identity.return_value = direct_hit
    updated = _make_customer(id=55, qbo_id="C-99", realm_id="realm-1")
    customer_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is updated
    customer_service.repo.update_by_id.assert_called_once()
    customer_service.create.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_not_called()
    customer_service.read_by_name.assert_not_called()


def test_customer_direct_hit_preserves_non_blank_local_name_and_takes_qbo_contact_fields():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(
        display_name="Acme (deleted)", primary_email_addr="a@x.com", primary_phone="555-1000",
    )
    direct_hit = _make_customer(name="Curated Local Name", email="stale@x.com", phone="old")
    customer_service.read_by_qbo_identity.return_value = direct_hit
    customer_service.repo.update_by_id.side_effect = lambda c: c

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result.name == "Curated Local Name"  # preserve_human_edited_name
    assert result.email == "a@x.com"  # QBO-owned, always overwritten
    assert result.phone == "555-1000"


def test_customer_genuine_miss_creates_new_and_stamps_identity():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(
        qbo_id="C-99", realm_id="realm-1", display_name="Acme",
        primary_email_addr="a@x.com", primary_phone="555-1000",
    )
    customer_service.read_by_qbo_identity.return_value = None
    customer_service.read_by_name.return_value = None
    created = _make_customer(id=300, qbo_id=None, realm_id=None)
    customer_service.create.return_value = created
    stamped = _make_customer(id=300, qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_id.side_effect = [created, stamped]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is stamped
    customer_service.create.assert_called_once_with(
        name="Acme", email="a@x.com", phone="555-1000"
    )
    customer_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="C-99", realm_id="realm-1"
    )


def test_customer_genuine_miss_adopts_existing_unmapped_by_name_raw_name():
    """U-219: adopt-by-name is a RAW name overwrite, bypassing preserve_human_edited_name."""
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    customer_service.read_by_qbo_identity.return_value = None
    existing = _make_customer(id=150, qbo_id=None, name="Old Curated Name")
    customer_service.read_by_name.return_value = existing
    customer_service.repo.update_by_id.side_effect = lambda c: c
    stamped = _make_customer(id=150, qbo_id="C-99", realm_id="realm-1", name="Acme")
    customer_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is stamped
    assert existing.name == "Acme"  # raw overwrite, not preserved
    customer_service.create.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="C-99", realm_id="realm-1"
    )


def test_customer_blank_incoming_name_skips_the_adopt_lookup_and_creates():
    """Customer-specific vs. CostCode's always-present `number`: a QboCustomer
    with neither DisplayName nor CompanyName yields an empty business key.
    `read_by_name("")` would match whatever a blank-name lookup happens to
    return, so the adopt step must be skipped entirely, not fed an empty key."""
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(
        qbo_id="C-99", realm_id="realm-1", display_name=None, company_name=None,
    )
    customer_service.read_by_qbo_identity.return_value = None
    created = _make_customer(id=300, name="")
    customer_service.create.return_value = created
    customer_service.read_by_id.side_effect = [created, created]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_customer(qbo_customer)

    customer_service.read_by_name.assert_not_called()
    customer_service.create.assert_called_once_with(name="", email="", phone="")


def test_customer_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """U-307c's Codex round-2 P1, inherited: resolve_candidate must be PURE for
    the adopt-by-name case -- no field write, no update_by_id call. The field
    write happens only in _stamp_customer_identity, atomically with the identity
    stamp under the candidate's own lock, or two concurrent QboCustomers
    name-matching the same row could each mutate it before either acquires that
    lock. Direct unit test on resolve_candidate itself (not the full
    sync_from_qbo_customer integration) so a regression that moves the write
    back here is caught even if the integration-level assertions happen to
    still read correctly."""
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    existing = _make_customer(
        id=150, qbo_id=None, realm_id=None,
        name="Untouched Name", email="old@x.com", phone="555-old",
    )
    customer_service.read_by_name.return_value = existing

    candidate = connector._resolve_customer_candidate(
        qbo_customer, name="Acme", email="a@x.com", phone="555-1000",
    )

    assert candidate is existing
    assert existing.name == "Untouched Name"
    assert existing.email == "old@x.com"
    assert existing.phone == "555-old"
    customer_service.repo.update_by_id.assert_not_called()


def test_customer_duplicate_qbo_id_guard_raises_and_records_issue():
    """Decision 2 (the one genuinely new, correctness-critical piece of this
    repoint): a name-matched Customer already carrying a DIFFERENT QboId must
    NOT be returned as the candidate -- stamp_identity's theft-clear would
    silently re-point it. Must raise + record a customer_identity_conflict
    issue instead, mirroring the mapping-table-era contract this replaces.
    Mutation target: deleting this guard makes the row get silently re-bound."""
    connector, customer_service, reconciliation_repo = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    customer_service.read_by_qbo_identity.return_value = None
    existing = _make_customer(id=150, qbo_id="C-OTHER", realm_id="realm-1")
    customer_service.read_by_name.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_customer(qbo_customer)

    customer_service.repo.update_by_id.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "customer_identity_conflict"


def test_customer_duplicate_guard_catches_same_qbo_id_different_realm():
    """QBO ids are only unique WITHIN a realm, so a QboId-only check would let a
    same-QboId-different-realm row through and overwrite its name/email/phone
    before _stamp_customer_identity's own (qbo_id AND realm_id) check ever runs.
    Must raise from resolve_candidate BEFORE any field mutation, matching
    _stamp_customer_identity's exact comparison."""
    connector, customer_service, reconciliation_repo = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    customer_service.read_by_qbo_identity.return_value = None
    existing = _make_customer(id=150, qbo_id="C-99", realm_id="realm-OTHER", name="Untouched")
    customer_service.read_by_name.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_customer(qbo_customer)

    assert existing.name == "Untouched"  # never mutated before the raise
    customer_service.repo.update_by_id.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()


def test_customer_resolve_candidate_allows_reresolve_to_same_qbo_id():
    """A benign re-resolve (existing.qbo_id already equals the incoming qbo_id)
    must proceed normally -- the guard only blocks a DIFFERENT identity."""
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1", display_name="Acme")
    customer_service.read_by_qbo_identity.return_value = None
    existing = _make_customer(id=150, qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_name.return_value = existing
    customer_service.repo.update_by_id.side_effect = lambda c: c
    stamped = _make_customer(id=150, qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is stamped


def test_customer_inactive_unmapped_raises_without_creating():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(active=False, qbo_id="C-99")
    customer_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="inactive in QBO and has no local"):
            connector.sync_from_qbo_customer(qbo_customer)

    customer_service.read_by_name.assert_not_called()
    customer_service.create.assert_not_called()


def test_customer_race_discovered_hit_adopts_racer_without_create():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    racer_row = _make_customer(id=400, qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_qbo_identity.side_effect = [None, racer_row]
    customer_service.repo.update_by_id.side_effect = lambda c: c

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is racer_row
    customer_service.create.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_not_called()
    assert customer_service.read_by_qbo_identity.call_args_list == [
        call("C-99", "realm-1"),
        call("C-99", "realm-1"),
    ]


def test_customer_update_returning_none_raises_runtime_error_not_value_error():
    """U-287/U-291, carried through the repoint: a ROWVERSION race on the HIT
    branch (update_by_id affected 0 rows) must raise RuntimeError, NOT
    ValueError -- record_projection_error classifies a plain ValueError as a
    permanent SKIP that advances the watermark past a Customer whose fields
    were never written. RuntimeError holds it for retry."""
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_qbo_identity.return_value = _make_customer(id=55)
    customer_service.repo.update_by_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_customer(qbo_customer)

    customer_service.create.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_not_called()


def test_customer_no_qbo_id_raises():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id=None)

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_customer(qbo_customer)

    customer_service.read_by_qbo_identity.assert_not_called()


def test_customer_job_customer_still_refused_before_any_identity_read():
    """Job=true belongs to CustomerProjectConnector, not this one -- the guard
    must fire before the fast path touches dbo.Customer at all."""
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", is_job=True)

    with pytest.raises(ValueError, match="Job=true"):
        connector.sync_from_qbo_customer(qbo_customer)

    customer_service.read_by_qbo_identity.assert_not_called()
    customer_service.create.assert_not_called()


def test_customer_lock_resource_key_matches_dbo_only_namespace():
    connector, customer_service, _ = _build_customer_connector()
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    customer_service.read_by_qbo_identity.return_value = None
    customer_service.read_by_name.return_value = None
    customer_service.create.return_value = _make_customer(id=300)
    customer_service.read_by_id.return_value = _make_customer(
        id=300, qbo_id="C-99", realm_id="realm-1"
    )
    recorded = []

    with patch(FASTPATH_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)), patch(
        CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_customer(qbo_customer)

    assert recorded == ["qbo_dbo_identity_create:Customer:C-99:realm-1"]


def test_customer_stamp_identity_refuses_to_overwrite_different_existing_identity():
    connector, customer_service, _ = _build_customer_connector()
    candidate = _make_customer(id=150)
    customer_service.read_by_id.return_value = _make_customer(
        id=150, qbo_id="C-OTHER", realm_id="realm-1"
    )
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries a DIFFERENT identity \(QboId=C-OTHER"):
            connector._stamp_customer_identity(
                candidate, qbo_customer, name="X", email="x@x.com", phone="555",
            )

    customer_service.repo.set_qbo_identity.assert_not_called()
    customer_service.repo.update_by_id.assert_not_called()  # never mutated before the raise


def test_customer_stamp_identity_records_duplicate_issue_even_when_resolve_candidate_missed_it():
    """Codex round-1 P2: `ReadCustomerByName` does not project QboId/RealmId
    (entities/customer/sql/dbo.customer.sql), so `_resolve_customer_candidate`'s
    own duplicate-QboId guard never actually sees a populated qbo_id against a
    REAL DB read -- only `read_by_id` (this method's own re-read) reliably
    carries it. This is the guard that actually protects production; it must
    record the same `customer_identity_conflict` reconciliation issue the
    resolve_candidate-side guard does, not just raise silently. Mutation
    target: deleting the `_raise_duplicate_qbo_customer_issue` call here drops
    the conflict record for every real-world hit of this guard."""
    connector, customer_service, reconciliation_repo = _build_customer_connector()
    candidate = _make_customer(id=150)
    customer_service.read_by_id.return_value = _make_customer(
        id=150, qbo_id="C-OTHER", realm_id="realm-1"
    )
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries a DIFFERENT identity \(QboId=C-OTHER"):
            connector._stamp_customer_identity(
                candidate, qbo_customer, name="X", email="x@x.com", phone="555",
            )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "customer_identity_conflict"
    assert "C-OTHER" in kwargs["details"]


def test_customer_stamp_identity_duplicate_issue_wording_is_realm_aware_on_same_qbo_id():
    """Codex round-1 P3: a same-QboId-different-realm collision must not be
    described as a DIFFERENT QboId in the recorded issue -- misleading for
    whoever reads the reconciliation queue."""
    connector, customer_service, reconciliation_repo = _build_customer_connector()
    candidate = _make_customer(id=150)
    customer_service.read_by_id.return_value = _make_customer(
        id=150, qbo_id="C-99", realm_id="realm-OTHER"
    )
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError):
            connector._stamp_customer_identity(
                candidate, qbo_customer, name="X", email="x@x.com", phone="555",
            )

    details = reconciliation_repo.create.call_args.kwargs["details"]
    assert "SAME QboId" in details
    assert "DIFFERENT RealmId" in details
    assert "DIFFERENT QboId" not in details


def test_customer_stamp_identity_update_returning_none_raises_runtime_error():
    """Codex round-1 P2: a ROWVERSION race between the pre-stamp read and the
    field-write update_by_id call must not silently proceed to stamp identity
    on a row whose write never took -- same discipline as `_on_update_empty`
    (U-287). Mutation target: dropping the `updated is None` check makes this
    fall through to set_qbo_identity with stale fields left on the row."""
    connector, customer_service, _ = _build_customer_connector()
    candidate = _make_customer(id=150)
    customer_service.read_by_id.return_value = _make_customer(
        id=150, qbo_id=None, realm_id=None, name="Old Name",
    )
    customer_service.repo.update_by_id.return_value = None  # race: row gone on write
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector._stamp_customer_identity(
                candidate, qbo_customer, name="New Name", email="new@x.com", phone="555",
            )

    customer_service.repo.set_qbo_identity.assert_not_called()


def test_customer_stamp_identity_applies_field_write_atomically_with_stamp():
    """The field write happens INSIDE this method, under the candidate lock, not
    in resolve_candidate -- confirms it's actually applied."""
    connector, customer_service, _ = _build_customer_connector()
    candidate = _make_customer(id=150)
    unmapped = _make_customer(
        id=150, qbo_id=None, realm_id=None, name="Old Name", email="old@x.com", phone="old",
    )
    customer_service.read_by_id.return_value = unmapped
    customer_service.repo.update_by_id.side_effect = lambda c: c
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_customer_identity(
            candidate, qbo_customer, name="New Name", email="new@x.com", phone="555-new",
        )

    assert unmapped.name == "New Name"
    assert unmapped.email == "new@x.com"
    assert unmapped.phone == "555-new"
    customer_service.repo.update_by_id.assert_called_once_with(unmapped)
    customer_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="C-99", realm_id="realm-1"
    )


def test_two_racers_name_matching_the_same_customer_serialize_and_the_loser_never_mutates_fields():
    """The side-channel-candidate race, reproduced with REAL threads (not
    sequential calls dressed up as a race): two genuinely concurrent
    QboCustomers with DIFFERENT qbo_ids (so no contention on
    run_identity_fastpath_dbo_only's own qbo_id-keyed create lock) that both
    name-match the SAME unmapped local Customer. A real threading.Lock stands
    in for sp_getapplock's cross-connection mutual exclusion.

    Proves two things directly, not just the final outcome: (1) mutual
    exclusion actually held during the read-guard-write-stamp sequence (an
    occupancy probe, not an inference from who "won"), and (2) the LOSER's
    incoming field values never landed on the row -- only the winner's did,
    matching whichever qbo_id the row ended up stamped with. Mutation target:
    this is exactly what breaks if the field write is moved back into
    resolve_candidate (outside this lock) or the lock is removed/keyed wrong."""
    import threading
    import time

    connector, customer_service, _ = _build_customer_connector()

    state_lock = threading.Lock()
    state = {"qbo_id": None, "realm_id": None, "name": None, "email": None}

    occupancy = {"current": 0, "max": 0}

    def _enter_critical_section():
        occupancy["current"] += 1
        occupancy["max"] = max(occupancy["max"], occupancy["current"])

    def _exit_critical_section():
        occupancy["current"] -= 1

    def _read_by_id(_id):
        with state_lock:
            return _make_customer(
                id=150, qbo_id=state["qbo_id"], realm_id=state["realm_id"],
                name=state["name"], email=state["email"],
            )

    def _update_by_id(row):
        with state_lock:
            state["name"] = row.name
            state["email"] = row.email
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

    customer_service.read_by_id.side_effect = _read_by_id
    customer_service.repo.update_by_id.side_effect = _update_by_id
    customer_service.repo.set_qbo_identity.side_effect = _set_qbo_identity

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
        candidate = _make_customer(id=150)
        qbo_customer = _make_qbo_customer(qbo_id=qbo_id, realm_id="realm-1")
        try:
            outcomes[qbo_id] = ("won", connector._stamp_customer_identity(
                candidate, qbo_customer,
                name=f"Name-from-{qbo_id}", email=f"{qbo_id}@x.com", phone="555",
            ))
        except ValueError as e:
            outcomes[qbo_id] = ("lost", e)

    with patch(CUST_STAMP_LOCK_TARGET, side_effect=_real_lock):
        t1 = threading.Thread(target=_racer, args=("C-X",))
        t2 = threading.Thread(target=_racer, args=("C-Y",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive(), "a racer thread hung — lock likely deadlocked"
    assert requested_resources == {"qbo_dbo_identity_stamp:Customer:150"}
    assert occupancy["max"] == 1, (
        f"both racers were inside the critical section concurrently (max_occupants="
        f"{occupancy['max']}) — the lock did not actually exclude them"
    )
    kinds = sorted(kind for kind, _ in outcomes.values())
    assert kinds == ["lost", "won"], f"expected exactly one winner and one loser, got {outcomes}"
    winner_qbo_id = next(q for q, (kind, _) in outcomes.items() if kind == "won")
    # The final name/email must match the WINNER's incoming values -- the
    # loser's field write must never have landed, even transiently.
    assert state["name"] == f"Name-from-{winner_qbo_id}"
    assert state["email"] == f"{winner_qbo_id}@x.com"
    assert state["qbo_id"] == winner_qbo_id


def test_customer_stamp_identity_lock_key_scoped_to_candidate():
    connector, customer_service, _ = _build_customer_connector()
    candidate = _make_customer(id=150)
    customer_service.read_by_id.return_value = _make_customer(id=150, qbo_id=None, realm_id=None)
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")
    recorded = []

    with patch(CUST_STAMP_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector._stamp_customer_identity(
            candidate, qbo_customer, name="X", email="x@x.com", phone="555",
        )

    assert recorded == ["qbo_dbo_identity_stamp:Customer:150"]


def test_customer_stamp_identity_fails_closed_on_lock_timeout():
    connector, customer_service, _ = _build_customer_connector()
    candidate = _make_customer(id=150)
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, side_effect=_denied_lock):
        with pytest.raises(RuntimeError, match="Could not acquire identity-stamp lock"):
            connector._stamp_customer_identity(
                candidate, qbo_customer, name="X", email="x@x.com", phone="555",
            )

    customer_service.read_by_id.assert_not_called()
    customer_service.repo.set_qbo_identity.assert_not_called()


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
        # U-297: never used here (every fixture sets parent_ref_value=None) —
        # injected so a truthy-parent test can't default to live-DB collaborators.
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
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


def test_project_fast_path_hit_conflict_raises_and_never_repoints_or_mints():
    """Mirrors the Customer hotfix test: on a detected conflict, sync_from_qbo_customer
    must RAISE — never fall through, which would set_qbo_identity on a DIFFERENT Project
    (identity theft, e.g. Project 9 here) or mint a duplicate. U-276 hotfix (2026-08-20)."""
    connector, mapping_repo, project_service, reconciliation_repo = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="Proj X", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_project_id.return_value = None
    conflicting = SimpleNamespace(id=2, project_id=9, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = conflicting
    # If the fall-through bug were still present, these would let it repoint Project 9.
    project_service.read_by_id.return_value = SimpleNamespace(
        id=9, name="Other Proj", description="", status="active", customer_id=None
    )
    project_service.repo.update_by_id.side_effect = lambda p: p

    with pytest.raises(ValueError):
        connector.sync_from_qbo_customer(qbo_customer)

    reconciliation_repo.create.assert_called_once()  # conflict recorded (durable follow-up)
    project_service.repo.update_by_id.assert_not_called()  # NO write to ANY Project (no theft)


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


def test_project_fast_path_hit_missing_update_returns_none_raises_runtime_error():
    """U-291: before this fix, ProjectConnector passed no on_apply_returned_none
    at all — a ROWVERSION race on this branch accidentally held only because
    `_apply_project_fields_and_sync` crashed with AttributeError on `.id`
    access (record_projection_error's rule 3, correct by accident not design).
    This is now an explicit, designed RuntimeError raise instead — and
    `_apply_project_fields_and_sync`'s own None-guard means `_sync_addresses`
    (this connector's proof apply_fields actually ran, see the vendor sibling's
    equivalent comment) must NOT fire on a race."""
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="Proj X", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    project_service.repo.update_by_id.return_value = None  # race: row gone on write
    mapping_repo.read_by_project_id.return_value = None  # state == "missing"
    mapping_repo.read_by_qbo_customer_id.return_value = None

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_customer(qbo_customer)

    mapping_repo.create.assert_not_called()
    connector._sync_addresses.assert_not_called()


def test_project_fast_path_hit_consistent_update_returns_none_raises_runtime_error():
    """U-291: the far more common steady-state case for an already-mapped
    Project — before this fix, `run_identity_fastpath` only invoked
    on_apply_returned_none when state == MISSING, so a race here (state ==
    "consistent", exercised via an existing mapping row) fell through with NO
    callback and NO exception at all, regardless of ProjectConnector having one
    wired."""
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    direct_hit = SimpleNamespace(id=88, name="Proj X", description="", status="active", customer_id=None)
    project_service.read_by_qbo_identity.return_value = direct_hit
    project_service.repo.update_by_id.return_value = None  # race: row gone on write
    mapping_repo.read_by_project_id.return_value = SimpleNamespace(id=2, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(id=2, project_id=88)

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_customer(qbo_customer)

    mapping_repo.create.assert_not_called()
    connector._sync_addresses.assert_not_called()


def test_project_legacy_existing_mapping_update_returns_none_raises_runtime_error():
    """The legacy "mapping found, Project resolved" branch calls the SAME
    shared `_apply_project_fields_and_sync` helper the fast path uses. Before
    U-291's None-guard on that helper, a None return crashed on `.id` access
    one line later inside this branch (an accidental, not designed, hold) —
    now an explicit raise."""
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    project_service.read_by_qbo_identity.return_value = None  # fast path misses
    existing_mapping = SimpleNamespace(id=2, project_id=88, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = existing_mapping
    existing_project = SimpleNamespace(
        id=88, name="Proj X", description="", status="active", customer_id=None
    )
    project_service.read_by_id.return_value = existing_project
    project_service.repo.update_by_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_customer(qbo_customer)

    project_service.repo.set_qbo_identity.assert_not_called()


def test_project_legacy_healed_repoint_update_returns_none_raises_runtime_error():
    """The legacy "mapping exists but bound Project missing, healed by name
    match" branch also calls the shared `_apply_project_fields_and_sync`
    helper as its own RETURN statement — before U-291 this would have returned
    None straight through to the caller as a silent success (project_records
    counts a None return as a projected SUCCESS), not even the accidental
    crash-to-hold the OTHER legacy branch got."""
    connector, mapping_repo, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True, display_name="Proj X")
    project_service.read_by_qbo_identity.return_value = None  # fast path misses
    stale_mapping = SimpleNamespace(id=2, project_id=88, qbo_customer_id=qbo_customer.id)
    mapping_repo.read_by_qbo_customer_id.return_value = stale_mapping
    project_service.read_by_id.return_value = None  # bound Project missing
    replacement = SimpleNamespace(
        id=88, name="Proj X", description="", status="active", customer_id=None
    )
    project_service.read_by_name.return_value = replacement
    mapping_repo.read_by_project_id.return_value = None  # replacement unbound
    project_service.repo.update_by_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_customer(qbo_customer)


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
    # not migrated yet, and the mapping table doesn't bind this QboId to any
    # OTHER Project either (U-306's reverse check) — nothing to disagree with.
    stub_identity_check_trusts(customer_project_repo)
    qbo_customer_repo = Mock()

    result = verify_project_qbo_identity(
        project, customer_project_repo=customer_project_repo, qbo_customer_repo=qbo_customer_repo
    )

    assert result == "QBO-P-42"
    assert qbo_customer_repo.method_calls == []  # U-306: folded into the one JOIN'd read, never touched


def test_verify_project_qbo_identity_trusts_when_mapping_agrees():
    from integrations.intuit.qbo.base.identity_consistency import verify_project_qbo_identity

    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    customer_project_repo = Mock()
    customer_project_repo.read_identity_check.return_value = IdentityCheckResult(
        mapping_id=1, forward_external_qbo_id="QBO-P-42", reverse_mapped_local_id=42
    )
    qbo_customer_repo = Mock()

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
    customer_project_repo.read_identity_check.return_value = IdentityCheckResult(
        mapping_id=1, forward_external_qbo_id="QBO-P-OTHER", reverse_mapped_local_id=42
    )
    qbo_customer_repo = Mock()

    result = verify_project_qbo_identity(
        project, customer_project_repo=customer_project_repo, qbo_customer_repo=qbo_customer_repo
    )

    assert result is None


def test_verify_project_qbo_identity_refuses_when_unmapped_but_reverse_bound_elsewhere():
    """U-297's H1, closed by U-306: no CustomerProject mapping of its own, but
    the mapping table already binds this exact QboId to a DIFFERENT Project —
    must refuse, not blindly trust (the pre-U-306 behavior)."""
    from integrations.intuit.qbo.base.identity_consistency import verify_project_qbo_identity

    project = SimpleNamespace(id=42, qbo_id="QBO-P-42")
    customer_project_repo = Mock()
    customer_project_repo.read_identity_check.return_value = IdentityCheckResult(
        mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=999
    )
    qbo_customer_repo = Mock()

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
    stub_identity_check_trusts(connector.customer_project_repo)  # nothing to disagree with

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
    connector.customer_project_repo.read_identity_check.return_value = IdentityCheckResult(
        mapping_id=1, forward_external_qbo_id="QBO-P-OTHER", reverse_mapped_local_id=42
    )

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
    stub_identity_check_trusts(fake_customer_project_repo)

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
    stub_identity_check_trusts(connector.customer_project_repo)

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
