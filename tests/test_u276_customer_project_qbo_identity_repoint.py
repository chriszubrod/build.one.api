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
  4. CustomerProjectConnector's OWN pull -- as of U-311 this is ALSO the
     DBO-ONLY fast path (`run_identity_fastpath_dbo_only`), mirroring Section
     2's CustomerCustomerConnector one-for-one: no qbo.CustomerProject read or
     write of any kind on this path, no mapping-table fallback, no self-heal,
     no mapping-vs-dbo conflict state. One deliberate divergence: the MISS
     branch's name-match adopt writes ONLY CustomerId (U-303's pre-existing
     rule preserving a possibly hand-authored Project's other fields), not a
     full field refresh.
  5. The Bill / Purchase / Invoice `_get_qbo_customer_ref` push helpers now read
     dbo.Project.Name/.QboId directly and verify via `verify_identity_dbo_only`
     (U-311, Wave-5 Option A) -- no qbo.CustomerProject mapping-table read.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted
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
# _stamp_customer_identity's own lock now lives in the shared
# stamp_dbo_identity_with_lock (U-328/U-331) inside identity_fastpath.py --
# same target as the create lock above, not a separate connector-module import.
CUST_STAMP_LOCK_TARGET = FASTPATH_LOCK_TARGET


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

    # FASTPATH_LOCK_TARGET and CUST_STAMP_LOCK_TARGET are now the SAME name
    # (both locks live in identity_fastpath.py, U-328/U-331) -- one recording
    # patch captures both acquisitions, in order.
    with patch(FASTPATH_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector.sync_from_qbo_customer(qbo_customer)

    assert recorded == [
        "qbo_dbo_identity_create:Customer:C-99:realm-1",
        "qbo_dbo_identity_stamp:Customer:300",
    ]


def test_customer_stamp_identity_refuses_to_overwrite_different_existing_identity():
    connector, customer_service, _ = _build_customer_connector()
    candidate = _make_customer(id=150)
    customer_service.read_by_id.return_value = _make_customer(
        id=150, qbo_id="C-OTHER", realm_id="realm-1"
    )
    qbo_customer = _make_qbo_customer(qbo_id="C-99", realm_id="realm-1")

    with patch(CUST_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries QBO identity C-OTHER"):
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
        with pytest.raises(ValueError, match=r"already carries QBO identity C-OTHER"):
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


# --- Section 3: CustomerProjectConnector dbo-only fast path (U-311) ---
#
# U-311 retired `qbo.CustomerProject` from THIS connector's own pull entirely
# (Wave-5 "trust dbo alone", `docs/design/wave5.md` §2/§4, mirroring U-310's
# CustomerCustomerConnector one section up): no mapping-table read/write of
# any kind on the sync_from_qbo_customer path, so the pre-U-311
# _resolve_mapping_state / _raise_identity_mapping_conflict_issue /
# mapping-table hit/miss/heal-in-place tests this section used to hold are
# gone with the machinery they covered (`_resolve_mapping_state` itself is
# UNCHANGED and still tested directly where it's actually exercised --
# nowhere in production now, it's a retained test-seam, see the method's own
# docstring). What THIS section proves is the SAME dbo-only contract Section 2
# proves for Customer, adapted for Project's extra CustomerId (parent) field
# and its address sync: a direct or race-discovered hit updates fields (name/
# description/status/CustomerId) and syncs addresses; a genuine miss (re-
# confirmed under the create lock) adopts an existing unmapped Project by NAME
# or creates fresh, then stamps identity under the candidate's OWN lock; a
# name-matched row already carrying a DIFFERENT identity raises + records a
# `project_identity_conflict` issue. One deliberate divergence from Customer's
# shape: the MISS branch writes ONLY CustomerId, never name/description/
# status -- U-303's pre-existing rule that adopting a possibly hand-authored
# local Project by name must preserve its other fields untouched (see
# `_resolve_project_candidate`'s own comment).


PROJECT_CONNECTOR_MODULE = (
    "integrations.intuit.qbo.customer.connector.project.business.service"
)
# _stamp_project_identity's own lock now lives in the shared
# stamp_dbo_identity_with_lock (U-328/U-331) inside identity_fastpath.py --
# same target as the create lock (FASTPATH_LOCK_TARGET, Section 2), not a
# separate connector-module import.
PROJECT_STAMP_LOCK_TARGET = FASTPATH_LOCK_TARGET


def _make_project(**overrides):
    defaults = dict(
        id=88, public_id="proj-pub-88", qbo_id=None, realm_id=None,
        name="Proj", description="", status="active", customer_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_project_connector():
    project_service = Mock()
    project_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = CustomerProjectConnector(
        project_service=project_service,
        reconciliation_repo=reconciliation_repo,
    )
    connector._sync_addresses = Mock()
    return connector, project_service, reconciliation_repo


def test_project_direct_hit_updates_fields_no_create_or_stamp():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", display_name="Proj X", is_job=True)
    direct_hit = _make_project(id=88, qbo_id="P-1", realm_id="realm-1")
    project_service.read_by_qbo_identity.return_value = direct_hit
    updated = _make_project(id=88, qbo_id="P-1", realm_id="realm-1", name="Proj X")
    project_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is updated
    project_service.repo.update_by_id.assert_called_once()
    project_service.create.assert_not_called()
    project_service.repo.set_qbo_identity.assert_not_called()
    project_service.read_by_name.assert_not_called()
    connector._sync_addresses.assert_called_once_with(qbo_customer, 88)


def test_project_direct_hit_preserves_non_blank_local_name_and_takes_qbo_fields():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(
        qbo_id="P-1", realm_id="realm-1", display_name="Proj (renamed in QBO)",
        notes="new notes", active=True, is_job=True,
    )
    direct_hit = _make_project(
        id=88, qbo_id="P-1", realm_id="realm-1",
        name="Curated Local Name", description="stale", status="inactive", customer_id=5,
    )
    project_service.read_by_qbo_identity.return_value = direct_hit
    project_service.repo.update_by_id.side_effect = lambda p: p

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result.name == "Curated Local Name"  # preserve_human_edited_name
    assert result.description == "new notes"  # QBO-owned, always overwritten
    assert result.status == "active"


def test_project_genuine_miss_creates_new_and_stamps_identity():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(
        qbo_id="P-1", realm_id="realm-1", display_name="Proj X", notes="desc", active=True, is_job=True,
    )
    project_service.read_by_qbo_identity.return_value = None
    project_service.read_by_name.return_value = None
    created = _make_project(id=300, qbo_id=None, realm_id=None, customer_id=None)
    project_service.create.return_value = created
    stamped = _make_project(id=300, qbo_id="P-1", realm_id="realm-1")
    project_service.read_by_id.side_effect = [created, stamped]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is stamped
    project_service.create.assert_called_once_with(
        name="Proj X", description="desc", status="active", customer_id=None,
    )
    project_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="P-1", realm_id="realm-1"
    )
    connector._sync_addresses.assert_called_once_with(qbo_customer, 300)


def test_project_genuine_miss_adopts_existing_unmapped_by_name_and_preserves_other_fields():
    """U-303: adopting a possibly hand-authored local Project by name match
    must NOT overwrite its name/description/status -- only CustomerId gets
    bound. This is the one deliberate divergence from Customer's own (U-310)
    raw-name-overwrite adopt shape (Project has no such "trust QBO's name on
    adopt" precedent -- U-303 predates this repoint and this unit must not
    silently regress it)."""
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(
        qbo_id="P-1", realm_id="realm-1", display_name="Proj X", notes="new notes",
        active=True, is_job=True,
    )
    project_service.read_by_qbo_identity.return_value = None
    existing = _make_project(
        id=150, qbo_id=None, realm_id=None,
        name="Old Curated Name", description="Old Desc", status="inactive", customer_id=None,
    )
    project_service.read_by_name.return_value = existing
    project_service.repo.update_by_id.side_effect = lambda p: p
    stamped = _make_project(id=150, qbo_id="P-1", realm_id="realm-1", name="Old Curated Name")
    project_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is stamped
    assert existing.name == "Old Curated Name"  # never overwritten
    assert existing.description == "Old Desc"   # never overwritten
    assert existing.status == "inactive"        # never overwritten
    project_service.create.assert_not_called()
    project_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="P-1", realm_id="realm-1"
    )
    connector._sync_addresses.assert_called_once_with(qbo_customer, 150)


def test_project_blank_incoming_name_skips_the_adopt_lookup_and_creates():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(
        qbo_id="P-1", realm_id="realm-1", display_name=None, company_name=None, is_job=True,
    )
    project_service.read_by_qbo_identity.return_value = None
    created = _make_project(id=300, name="")
    project_service.create.return_value = created
    project_service.read_by_id.side_effect = [created, created]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        connector.sync_from_qbo_customer(qbo_customer)

    project_service.read_by_name.assert_not_called()
    project_service.create.assert_called_once_with(name="", description="", status="active", customer_id=None)


def test_project_resolve_candidate_does_not_mutate_or_persist_the_adopted_row():
    """Same purity requirement as Customer's own equivalent (Section 2): no
    field write, no update_by_id call inside resolve_candidate itself -- the
    write (CustomerId only, here) happens exclusively in
    _stamp_project_identity, atomically with the identity stamp under the
    candidate's own lock."""
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", display_name="Proj X", is_job=True)
    existing = _make_project(
        id=150, qbo_id=None, realm_id=None,
        name="Untouched Name", description="untouched desc", status="inactive", customer_id=None,
    )
    project_service.read_by_name.return_value = existing

    candidate = connector._resolve_project_candidate(
        qbo_customer, name="Proj X", description="new desc", status="active", customer_id=7,
    )

    assert candidate is existing
    assert existing.name == "Untouched Name"
    assert existing.description == "untouched desc"
    assert existing.status == "inactive"
    assert existing.customer_id is None
    project_service.repo.update_by_id.assert_not_called()


def test_project_duplicate_qbo_id_guard_raises_and_records_issue():
    connector, project_service, reconciliation_repo = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", display_name="Proj X", is_job=True)
    project_service.read_by_qbo_identity.return_value = None
    existing = _make_project(id=150, qbo_id="P-OTHER", realm_id="realm-1")
    project_service.read_by_name.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_customer(qbo_customer)

    project_service.repo.update_by_id.assert_not_called()
    project_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "project_identity_conflict"


def test_project_duplicate_guard_catches_same_qbo_id_different_realm():
    connector, project_service, reconciliation_repo = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", display_name="Proj X", is_job=True)
    project_service.read_by_qbo_identity.return_value = None
    existing = _make_project(id=150, qbo_id="P-1", realm_id="realm-OTHER", name="Untouched")
    project_service.read_by_name.return_value = existing

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
            connector.sync_from_qbo_customer(qbo_customer)

    assert existing.name == "Untouched"  # never mutated before the raise
    project_service.repo.update_by_id.assert_not_called()
    project_service.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_called_once()


def test_project_resolve_candidate_allows_reresolve_to_same_qbo_id():
    """A benign re-resolve (existing.qbo_id already equals the incoming qbo_id)
    must proceed normally -- the guard only blocks a DIFFERENT identity."""
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", display_name="Proj X", is_job=True)
    project_service.read_by_qbo_identity.return_value = None
    existing = _make_project(id=150, qbo_id="P-1", realm_id="realm-1")
    project_service.read_by_name.return_value = existing
    project_service.repo.update_by_id.side_effect = lambda p: p
    stamped = _make_project(id=150, qbo_id="P-1", realm_id="realm-1")
    project_service.read_by_id.side_effect = [existing, stamped]

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted), patch(
        PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted
    ):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is stamped


def test_project_inactive_unmapped_raises_without_creating():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(active=False, qbo_id="P-1", is_job=True)
    project_service.read_by_qbo_identity.return_value = None

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match="inactive in QBO and has no local"):
            connector.sync_from_qbo_customer(qbo_customer)

    project_service.read_by_name.assert_not_called()
    project_service.create.assert_not_called()


def test_project_race_discovered_hit_adopts_racer_without_create():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    racer_row = _make_project(id=400, qbo_id="P-1", realm_id="realm-1")
    project_service.read_by_qbo_identity.side_effect = [None, racer_row]
    project_service.repo.update_by_id.side_effect = lambda p: p

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is racer_row
    project_service.create.assert_not_called()
    project_service.repo.set_qbo_identity.assert_not_called()
    assert project_service.read_by_qbo_identity.call_args_list == [
        call("P-1", "realm-1"),
        call("P-1", "realm-1"),
    ]


def test_project_update_returning_none_raises_runtime_error_not_value_error():
    """U-287/U-291 discipline, carried through the repoint: a ROWVERSION race
    on the HIT branch (update_by_id affected 0 rows) must raise RuntimeError,
    NOT ValueError, and must not sync addresses for a Project whose fields
    were never written."""
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    project_service.read_by_qbo_identity.return_value = _make_project(id=88)
    project_service.repo.update_by_id.return_value = None  # race: row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_customer(qbo_customer)

    project_service.create.assert_not_called()
    project_service.repo.set_qbo_identity.assert_not_called()
    connector._sync_addresses.assert_not_called()


def test_project_no_qbo_id_raises():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id=None, is_job=True)

    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_customer(qbo_customer)

    project_service.read_by_qbo_identity.assert_not_called()


def test_project_non_job_customer_still_refused_before_any_identity_read():
    """Job=false belongs to CustomerCustomerConnector, not this one -- the
    guard must fire before the fast path touches dbo.Project at all."""
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", is_job=False)

    with pytest.raises(ValueError, match="Job=false"):
        connector.sync_from_qbo_customer(qbo_customer)

    project_service.read_by_qbo_identity.assert_not_called()
    project_service.create.assert_not_called()


def test_project_lock_resource_key_matches_dbo_only_namespace():
    connector, project_service, _ = _build_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    project_service.read_by_qbo_identity.return_value = None
    project_service.read_by_name.return_value = None
    project_service.create.return_value = _make_project(id=300)
    project_service.read_by_id.return_value = _make_project(id=300, qbo_id="P-1", realm_id="realm-1")
    recorded = []

    # FASTPATH_LOCK_TARGET and PROJECT_STAMP_LOCK_TARGET are now the SAME name
    # (both locks live in identity_fastpath.py, U-328/U-331) -- one recording
    # patch captures both acquisitions, in order.
    with patch(FASTPATH_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector.sync_from_qbo_customer(qbo_customer)

    assert recorded == [
        "qbo_dbo_identity_create:Project:P-1:realm-1",
        "qbo_dbo_identity_stamp:Project:300",
    ]


def test_project_stamp_identity_refuses_to_overwrite_different_existing_identity():
    connector, project_service, _ = _build_project_connector()
    candidate = _make_project(id=150)
    project_service.read_by_id.return_value = _make_project(id=150, qbo_id="P-OTHER", realm_id="realm-1")
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries QBO identity P-OTHER"):
            connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    project_service.repo.set_qbo_identity.assert_not_called()
    project_service.repo.update_by_id.assert_not_called()  # never mutated before the raise
    connector._sync_addresses.assert_not_called()


def test_project_stamp_identity_records_duplicate_issue_even_when_resolve_candidate_missed_it():
    """Mirrors Customer's Codex round-1 P2 fix: `read_by_id`'s re-read is the
    ONE that reliably carries QboId/RealmId against a real DB read (a
    read_by_name-sourced candidate may not project it), so this is the guard
    that actually protects production."""
    connector, project_service, reconciliation_repo = _build_project_connector()
    candidate = _make_project(id=150)
    project_service.read_by_id.return_value = _make_project(id=150, qbo_id="P-OTHER", realm_id="realm-1")
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError, match=r"already carries QBO identity P-OTHER"):
            connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "project_identity_conflict"
    assert "P-OTHER" in kwargs["details"]


def test_project_stamp_identity_duplicate_issue_wording_is_realm_aware_on_same_qbo_id():
    connector, project_service, reconciliation_repo = _build_project_connector()
    candidate = _make_project(id=150)
    project_service.read_by_id.return_value = _make_project(id=150, qbo_id="P-1", realm_id="realm-OTHER")
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(ValueError):
            connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    details = reconciliation_repo.create.call_args.kwargs["details"]
    assert "SAME QboId" in details
    assert "DIFFERENT RealmId" in details
    assert "DIFFERENT QboId" not in details


def test_project_stamp_identity_update_returning_none_raises_runtime_error():
    """A ROWVERSION race between the pre-stamp read and the CustomerId-write
    update_by_id call must not silently proceed to stamp identity on a row
    whose write never took."""
    connector, project_service, _ = _build_project_connector()
    candidate = _make_project(id=150)
    project_service.read_by_id.return_value = _make_project(id=150, qbo_id=None, realm_id=None, customer_id=None)
    project_service.repo.update_by_id.return_value = None  # race: row gone on write
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    project_service.repo.set_qbo_identity.assert_not_called()


def test_project_stamp_identity_writes_customer_id_only_and_syncs_addresses():
    """The CustomerId write happens INSIDE this method, under the candidate
    lock -- confirms it's actually applied, and that name/description/status
    are NOT touched here (U-303, see _resolve_project_candidate's comment)."""
    connector, project_service, _ = _build_project_connector()
    candidate = _make_project(id=150)
    unmapped = _make_project(
        id=150, qbo_id=None, realm_id=None,
        name="Untouched Name", description="untouched desc", status="inactive", customer_id=None,
    )
    project_service.read_by_id.return_value = unmapped
    project_service.repo.update_by_id.side_effect = lambda p: p
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    assert unmapped.customer_id == 7
    assert unmapped.name == "Untouched Name"
    assert unmapped.description == "untouched desc"
    assert unmapped.status == "inactive"
    project_service.repo.update_by_id.assert_called_once_with(unmapped)
    project_service.repo.set_qbo_identity.assert_called_once_with(id=150, qbo_id="P-1", realm_id="realm-1")
    connector._sync_addresses.assert_called_once_with(qbo_customer, 150)


def test_project_stamp_identity_skips_customer_id_write_when_none():
    """No parent resolved (customer_id=None) -- the write is skipped
    entirely, not sent as an explicit NULL clobber."""
    connector, project_service, _ = _build_project_connector()
    candidate = _make_project(id=150)
    unmapped = _make_project(id=150, qbo_id=None, realm_id=None, customer_id=9)
    project_service.read_by_id.return_value = unmapped
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector._stamp_project_identity(candidate, qbo_customer, customer_id=None)

    assert unmapped.customer_id == 9  # untouched
    project_service.repo.update_by_id.assert_not_called()
    project_service.repo.set_qbo_identity.assert_called_once_with(id=150, qbo_id="P-1", realm_id="realm-1")


def test_two_racers_name_matching_the_same_project_serialize_and_the_loser_never_mutates_customer_id():
    """Project mirror of Section 2's real-threading race proof: two genuinely
    concurrent QboCustomers with DIFFERENT qbo_ids (no contention on
    run_identity_fastpath_dbo_only's own qbo_id-keyed create lock) that both
    name-match the SAME unmapped local Project. Proves mutual exclusion
    actually held AND the loser's CustomerId never landed on the row."""
    import threading
    import time

    connector, project_service, _ = _build_project_connector()

    state_lock = threading.Lock()
    state = {"qbo_id": None, "realm_id": None, "customer_id": None}

    occupancy = {"current": 0, "max": 0}

    def _enter_critical_section():
        occupancy["current"] += 1
        occupancy["max"] = max(occupancy["max"], occupancy["current"])

    def _exit_critical_section():
        occupancy["current"] -= 1

    def _read_by_id(_id):
        with state_lock:
            return _make_project(
                id=150, qbo_id=state["qbo_id"], realm_id=state["realm_id"], customer_id=state["customer_id"],
            )

    def _update_by_id(row):
        with state_lock:
            state["customer_id"] = row.customer_id
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

    project_service.read_by_id.side_effect = _read_by_id
    project_service.repo.update_by_id.side_effect = _update_by_id
    project_service.repo.set_qbo_identity.side_effect = _set_qbo_identity

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

    def _racer(qbo_id, customer_id):
        candidate = _make_project(id=150)
        qbo_customer = _make_qbo_customer(qbo_id=qbo_id, realm_id="realm-1", is_job=True)
        try:
            outcomes[qbo_id] = ("won", connector._stamp_project_identity(
                candidate, qbo_customer, customer_id=customer_id,
            ))
        except ValueError as e:
            outcomes[qbo_id] = ("lost", e)

    with patch(PROJECT_STAMP_LOCK_TARGET, side_effect=_real_lock):
        t1 = threading.Thread(target=_racer, args=("P-X", 1001))
        t2 = threading.Thread(target=_racer, args=("P-Y", 1002))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive(), "a racer thread hung — lock likely deadlocked"
    assert requested_resources == {"qbo_dbo_identity_stamp:Project:150"}
    assert occupancy["max"] == 1, (
        f"both racers were inside the critical section concurrently (max_occupants="
        f"{occupancy['max']}) — the lock did not actually exclude them"
    )
    kinds = sorted(kind for kind, _ in outcomes.values())
    assert kinds == ["lost", "won"], f"expected exactly one winner and one loser, got {outcomes}"
    winner_qbo_id = next(q for q, (kind, _) in outcomes.items() if kind == "won")
    winner_customer_id = 1001 if winner_qbo_id == "P-X" else 1002
    # The final CustomerId must match the WINNER's incoming value -- the
    # loser's write must never have landed, even transiently.
    assert state["customer_id"] == winner_customer_id
    assert state["qbo_id"] == winner_qbo_id


def test_project_stamp_identity_lock_key_scoped_to_candidate():
    connector, project_service, _ = _build_project_connector()
    candidate = _make_project(id=150)
    project_service.read_by_id.return_value = _make_project(id=150, qbo_id=None, realm_id=None)
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)
    recorded = []

    with patch(PROJECT_STAMP_LOCK_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    assert recorded == ["qbo_dbo_identity_stamp:Project:150"]


def test_project_stamp_identity_fails_closed_on_lock_timeout():
    connector, project_service, _ = _build_project_connector()
    candidate = _make_project(id=150)
    qbo_customer = _make_qbo_customer(qbo_id="P-1", realm_id="realm-1", is_job=True)

    with patch(PROJECT_STAMP_LOCK_TARGET, side_effect=_denied_lock):
        with pytest.raises(RuntimeError, match="Could not acquire identity-stamp lock"):
            connector._stamp_project_identity(candidate, qbo_customer, customer_id=7)

    project_service.read_by_id.assert_not_called()
    project_service.repo.set_qbo_identity.assert_not_called()


# --- Section 4: push-helper repoints (Bill / Purchase / Invoice) ---
#
# Round-4 review: dbo.Project.QboId alone isn't enough to trust for an
# outbound push (dbo-internal uniqueness ≠ still-being-the-current-holder).
# U-311 (Wave-5 Option A) repointed all 3 push helpers onto
# `verify_identity_dbo_only`: a plain re-read of dbo.Project by the resolved
# row's own (qbo_id, realm_id), no mapping-table read at all. The push-helper
# tests below confirm each one wires THAT primitive in and respects the
# result. (The mapping-table-reading `verify_project_qbo_identity()` this
# section used to also keep test coverage on, purely as a still-tested,
# no-longer-wired primitive, is gone — U-314 dropped qbo.CustomerProject and
# deleted it outright.)


def test_bill_get_qbo_customer_ref_reads_project_directly():
    """U-311 (Wave-5 Option A): verification is now `verify_identity_dbo_only`
    -- a second call to `read_by_qbo_identity` keyed on the resolved row's own
    (qbo_id, realm_id), not a qbo.CustomerProject mapping-table read."""
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
    from integrations.intuit.qbo.bill.external.schemas import QboReferenceType

    connector = BillBillConnector(
        mapping_repo=Mock(), bill_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(), bill_line_item_service=Mock(),
        customer_project_repo=Mock(), qbo_customer_repo=Mock(),
        project_service=Mock(), qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(), qbo_term_repo=Mock(),
    )
    project = SimpleNamespace(id=42, name="TB3 - 917 Tyne Blvd", qbo_id="QBO-P-42", realm_id="realm-1")
    connector.project_service.read_by_id.return_value = project
    connector.project_service.read_by_qbo_identity.return_value = project  # verify re-read: same row

    ref = connector._get_qbo_customer_ref(42)

    connector.project_service.read_by_id.assert_called_once_with(42)
    assert ref == QboReferenceType(value="QBO-P-42", name="TB3 - 917 Tyne Blvd")


def test_bill_get_qbo_customer_ref_none_when_project_never_synced():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(
        mapping_repo=Mock(), bill_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(), bill_line_item_service=Mock(),
        customer_project_repo=Mock(), qbo_customer_repo=Mock(),
        project_service=Mock(), qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(), qbo_term_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = SimpleNamespace(id=42, name="Manual Proj", qbo_id=None)

    assert connector._get_qbo_customer_ref(42) is None


def test_bill_get_qbo_customer_ref_none_when_verification_fails():
    """The direct hit exists, but a fresh re-read by its OWN (qbo_id,
    realm_id) no longer resolves back to the SAME row (a stale/"stolen"
    identity) -- must not trust it; U-311 has no legacy hop left, so this
    returns None."""
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(
        mapping_repo=Mock(), bill_service=Mock(), vendor_service=Mock(),
        vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock(), qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(), bill_line_item_service=Mock(),
        customer_project_repo=Mock(), qbo_customer_repo=Mock(),
        project_service=Mock(), qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(), qbo_term_repo=Mock(),
    )
    connector.project_service.read_by_id.return_value = SimpleNamespace(
        id=42, name="Proj", qbo_id="QBO-P-42", realm_id="realm-1"
    )
    connector.project_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=99, name="Other", qbo_id="QBO-P-42", realm_id="realm-1"
    )  # stolen by a different Project

    assert connector._get_qbo_customer_ref(42) is None


def test_purchase_get_qbo_customer_ref_reads_project_directly():
    """U-311 (Wave-5 Option A): verification is now `verify_identity_dbo_only`
    -- a second call to `read_by_qbo_identity` keyed on the resolved row's own
    (qbo_id, realm_id), not a qbo.CustomerProject mapping-table read."""
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
    project = SimpleNamespace(id=7, name="OL-14 - Overton Lea", qbo_id="QBO-P-7", realm_id="realm-1")
    fake_project_service.read_by_id.return_value = project
    fake_project_service.read_by_qbo_identity.return_value = project  # verify re-read: same row

    with patch("entities.project.business.service.ProjectService", return_value=fake_project_service):
        ref = connector._get_qbo_customer_ref(7)

    fake_project_service.read_by_id.assert_called_once_with(7)
    assert ref == QboReferenceType(value="QBO-P-7", name="OL-14 - Overton Lea")


def test_invoice_get_qbo_customer_ref_reads_project_directly():
    """U-311 (Wave-5 Option A): verification is now `verify_identity_dbo_only`
    -- a second call to `read_by_qbo_identity` keyed on the resolved row's own
    (qbo_id, realm_id), not a qbo.CustomerProject mapping-table read."""
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )
    from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

    connector = InvoiceInvoiceConnector(
        mapping_repo=Mock(), line_mapping_repo=Mock(), invoice_service=Mock(),
        project_service=Mock(), qbo_customer_repo=Mock(), customer_project_repo=Mock(),
    )
    project = SimpleNamespace(id=9, name="HA - 206 Haverford Ave", qbo_id="QBO-P-9", realm_id="realm-1")
    connector.project_service.read_by_id.return_value = project
    connector.project_service.read_by_qbo_identity.return_value = project  # verify re-read: same row

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
