"""Pure-logic tests for U-297: repoint `CustomerProjectConnector`'s own
parent-Customer lookup off the `qbo.Customer` -> `qbo.CustomerCustomer` two-hop
and onto `dbo.Customer`'s native QboId/RealmId (U-238c).

The repointed branch resolves a QBO job/sub-customer's `ParentRefValue` to a
local `dbo.Customer.Id`, which is then WRITTEN to `dbo.Project.CustomerId`.
Before this unit that branch had ZERO test coverage — every QboCustomer fixture
in the suite set `parent_ref_value=None`, so lines 92-100 of the connector never
executed.

Covers:
  1. The new shared `verify_customer_qbo_identity` wrapper
     (`base/identity_consistency.py`) — the third binding of the
     `_verify_dbo_qbo_identity` engine, alongside Project and Vendor.
  2. `CustomerProjectConnector._get_parent_customer_id` /
     `_resolve_parent_customer_id`: direct dbo hit skips the legacy hop, verify
     failure and direct miss both FALL THROUGH to it unchanged, empty ref
     short-circuits, and the result is memoized per (realm, ref).
  3. Integration through `sync_from_qbo_customer` with a TRUTHY
     `parent_ref_value` — that the resolved id actually reaches
     `_apply_project_fields_and_sync` (update paths) and `project_service.create`
     (create path).
"""
from types import SimpleNamespace
from unittest.mock import Mock

from integrations.intuit.qbo.base.identity_consistency import verify_customer_qbo_identity
from integrations.intuit.qbo.customer.connector.project.business.service import (
    CustomerProjectConnector,
)


# --- Section 1: the verify_customer_qbo_identity wrapper ---


def _verify_repos(*, mapping=None, external=None):
    """Bind the two repos the wrapper needs, with EXPLICIT return values.

    Never leave these as bare Mocks: a Mock's `read_by_customer_id(...)` returns a
    truthy Mock whose `.qbo_customer_id` is itself a Mock, and
    `read_by_id(...).qbo_id` is a Mock too — so the engine's `!=` comparison
    would pass or fail by accident rather than by the case under test.
    """
    customer_customer_repo = Mock()
    customer_customer_repo.read_by_customer_id.return_value = mapping
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_id.return_value = external
    return customer_customer_repo, qbo_customer_repo


def test_verify_returns_none_for_missing_entity_or_qbo_id():
    """No entity, or an entity with no dbo-native QboId, is never trustworthy."""
    mapping_repo, qbo_repo = _verify_repos()

    for entity in (None, SimpleNamespace(id=7, qbo_id=None), SimpleNamespace(id=7, qbo_id="")):
        assert (
            verify_customer_qbo_identity(
                entity, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
            )
            is None
        )
    # Short-circuits before touching either repo.
    mapping_repo.read_by_customer_id.assert_not_called()
    qbo_repo.read_by_id.assert_not_called()


def test_verify_trusts_when_customer_has_no_mapping_row():
    """No CustomerCustomer row = nothing to disagree with = TRUST (engine line 57-58)."""
    mapping_repo, qbo_repo = _verify_repos(mapping=None)
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result == "P-1"
    qbo_repo.read_by_id.assert_not_called()


def test_verify_trusts_when_mapping_agrees():
    mapping_repo, qbo_repo = _verify_repos(
        mapping=SimpleNamespace(id=1, qbo_customer_id=9),
        external=SimpleNamespace(id=9, qbo_id="P-1"),
    )
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result == "P-1"
    # Resolved the external row through the mapping's OWN FK, not the customer id.
    qbo_repo.read_by_id.assert_called_once_with(9)


def test_verify_refuses_when_mapping_binds_a_different_external_customer():
    """The whole point: dbo says P-1, the mapping table still says P-2 -> refuse."""
    mapping_repo, qbo_repo = _verify_repos(
        mapping=SimpleNamespace(id=1, qbo_customer_id=9),
        external=SimpleNamespace(id=9, qbo_id="P-2"),
    )
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result is None


def test_verify_binds_the_customer_familys_accessors():
    """Guards against a copy/paste of the Project or Vendor wrapper's bindings."""
    mapping_repo, qbo_repo = _verify_repos(
        mapping=SimpleNamespace(id=1, qbo_customer_id=9),
        external=SimpleNamespace(id=9, qbo_id="P-1"),
    )
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    # read_by_customer_id (not read_by_project_id / read_by_vendor_id), keyed on
    # the LOCAL id, and the external id read through the `qbo_customer_id` attr.
    mapping_repo.read_by_customer_id.assert_called_once_with(55)
    qbo_repo.read_by_id.assert_called_once_with(9)


# --- Section 2: the _get_parent_customer_id / _resolve_parent_customer_id resolver ---


def _build_connector(*, direct=None, staging=None, parent_mapping=None, own_mapping=None):
    """A connector whose parent-lookup dependencies are all explicitly pinned.

    `direct`         -> customer_service.read_by_qbo_identity (the dbo fast path)
    `own_mapping`    -> customer_mapping_repo.read_by_customer_id (the verify step)
    `staging`        -> qbo_customer_repo.read_by_qbo_id (legacy hop 1)
    `parent_mapping` -> customer_mapping_repo.read_by_qbo_customer_id (legacy hop 2)
    """
    customer_service = Mock()
    customer_service.read_by_qbo_identity.return_value = direct

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = staging
    # The verify step resolves the mapped external row by its FK.
    qbo_customer_repo.read_by_id.return_value = SimpleNamespace(
        id=9, qbo_id=getattr(direct, "qbo_id", None)
    )

    customer_mapping_repo = Mock()
    customer_mapping_repo.read_by_customer_id.return_value = own_mapping
    customer_mapping_repo.read_by_qbo_customer_id.return_value = parent_mapping

    return CustomerProjectConnector(
        mapping_repo=Mock(),
        project_service=Mock(),
        project_address_service=Mock(),
        address_connector=Mock(),
        customer_mapping_repo=customer_mapping_repo,
        reconciliation_repo=Mock(),
        customer_service=customer_service,
        qbo_customer_repo=qbo_customer_repo,
    )


def test_resolver_direct_hit_returns_dbo_id_and_skips_the_legacy_hop():
    connector = _build_connector(
        direct=SimpleNamespace(id=42, qbo_id="P-1"),
        own_mapping=None,  # no mapping -> verify TRUSTS
        staging=SimpleNamespace(id=9),
        parent_mapping=SimpleNamespace(id=1, customer_id=999),
    )

    result = connector._get_parent_customer_id("P-1", "realm-1")

    assert result == 42
    # The legacy two-hop must not run at all on a verified direct hit.
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()
    connector.customer_mapping_repo.read_by_qbo_customer_id.assert_not_called()


def test_resolver_direct_hit_with_an_agreeing_mapping_is_the_prod_steady_state():
    """The branch every real parent takes, and the round-trip budget the repoint
    is justified by.

    The test above uses `own_mapping=None` — the verify's permissive
    "nothing to disagree with" arm, whose LIVE population is zero (0 stamped
    dbo.Customer rows lack a CustomerCustomer row). All 136 prod job/sub-customers
    go through this stricter arm instead: direct + mapping + external = 3 reads,
    and the 2-read legacy hop must stay unpaid. 71 distinct parents x 3 = 213 vs
    the legacy 136 x 2 = 272. A 4th read here (71 x 4 = 284) would silently turn
    this unit into a net loss.
    """
    connector = _build_connector(
        direct=SimpleNamespace(id=42, qbo_id="P-1"),
        own_mapping=SimpleNamespace(id=1, qbo_customer_id=9),  # mapping EXISTS and AGREES
        staging=SimpleNamespace(id=9),
        parent_mapping=SimpleNamespace(id=1, customer_id=999),
    )

    assert connector._get_parent_customer_id("P-1", "realm-1") == 42

    # Exactly three reads, one per step.
    assert connector.customer_service.read_by_qbo_identity.call_count == 1
    assert connector.customer_mapping_repo.read_by_customer_id.call_count == 1
    assert connector.qbo_customer_repo.read_by_id.call_count == 1
    # And the legacy two-hop stays unpaid.
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()
    connector.customer_mapping_repo.read_by_qbo_customer_id.assert_not_called()


def test_resolver_direct_miss_falls_back_to_the_legacy_two_hop():
    connector = _build_connector(
        direct=None,
        staging=SimpleNamespace(id=9),
        parent_mapping=SimpleNamespace(id=1, customer_id=999),
    )

    assert connector._get_parent_customer_id("P-1", "realm-1") == 999
    connector.qbo_customer_repo.read_by_qbo_id.assert_called_once_with("P-1")


def test_resolver_verify_failure_falls_back_and_does_not_return_the_direct_hit():
    """A direct hit whose own mapping binds a DIFFERENT external customer is
    refused — the resolver returns the legacy hop's answer, NOT the unverified
    direct hit. This is the assertion that fails if the verify call is dropped."""
    connector = _build_connector(
        direct=SimpleNamespace(id=42, qbo_id="P-1"),
        own_mapping=SimpleNamespace(id=1, qbo_customer_id=9),
        staging=SimpleNamespace(id=9),
        parent_mapping=SimpleNamespace(id=1, customer_id=999),
    )
    # The mapped external row disagrees with the dbo row's own QboId.
    connector.qbo_customer_repo.read_by_id.return_value = SimpleNamespace(id=9, qbo_id="P-2")

    result = connector._get_parent_customer_id("P-1", "realm-1")

    assert result == 999  # legacy hop's answer
    assert result != 42  # NOT the unverified direct hit


def test_resolver_returns_none_when_both_paths_miss():
    connector = _build_connector(direct=None, staging=None)
    assert connector._get_parent_customer_id("P-1", "realm-1") is None


def test_resolver_short_circuits_on_empty_parent_ref_without_any_read():
    connector = _build_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"))

    for empty in (None, ""):
        assert connector._get_parent_customer_id(empty, "realm-1") is None

    # A `WHERE QboId = NULL` round trip for every top-level job would be pure waste.
    connector.customer_service.read_by_qbo_identity.assert_not_called()
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_resolver_threads_realm_positionally_including_none():
    connector = _build_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"), own_mapping=None)

    connector._get_parent_customer_id("P-1", "realm-1")
    connector.customer_service.read_by_qbo_identity.assert_called_once_with("P-1", "realm-1")

    # A None realm is still passed through, not guarded away.
    connector.customer_service.read_by_qbo_identity.reset_mock()
    connector._get_parent_customer_id("P-2", None)
    connector.customer_service.read_by_qbo_identity.assert_called_once_with("P-2", None)


def test_resolver_legacy_hop_stays_realm_unscoped():
    """Realm-scoping the fallback would be a behavior change beyond this repoint."""
    connector = _build_connector(
        direct=None, staging=SimpleNamespace(id=9), parent_mapping=SimpleNamespace(customer_id=999)
    )

    connector._get_parent_customer_id("P-1", "realm-1")

    connector.qbo_customer_repo.read_by_qbo_id.assert_called_once_with("P-1")
    connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_resolver_returns_a_local_int_on_both_branches():
    """Type parity: the dbo branch must return the same shape as the legacy
    branch (a local dbo.Customer.Id), never a public_id."""
    direct_hit = _build_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"), own_mapping=None)
    legacy_hit = _build_connector(
        direct=None,
        staging=SimpleNamespace(id=9),
        parent_mapping=SimpleNamespace(id=1, customer_id=999),
    )

    a = direct_hit._get_parent_customer_id("P-1", "realm-1")
    b = legacy_hit._get_parent_customer_id("P-1", "realm-1")

    assert (a, b) == (42, 999)
    assert isinstance(a, int) and isinstance(b, int)


def test_resolver_memoizes_per_connector_lifetime():
    connector = _build_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"), own_mapping=None)

    first = connector._get_parent_customer_id("P-1", "realm-1")
    second = connector._get_parent_customer_id("P-1", "realm-1")

    assert first == second == 42
    assert connector.customer_service.read_by_qbo_identity.call_count == 1


def test_resolver_memoizes_misses_too():
    """Caching only hits would leave a miss re-paying both round trips per job."""
    connector = _build_connector(direct=None, staging=None)

    assert connector._get_parent_customer_id("P-1", "realm-1") is None
    assert connector._get_parent_customer_id("P-1", "realm-1") is None

    assert connector.customer_service.read_by_qbo_identity.call_count == 1
    assert connector.qbo_customer_repo.read_by_qbo_id.call_count == 1


def test_resolver_cache_is_keyed_by_realm_too():
    connector = _build_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"), own_mapping=None)

    connector._get_parent_customer_id("P-1", "realm-1")
    connector._get_parent_customer_id("P-1", "realm-2")

    # Same ref in a different realm is a different question.
    assert connector.customer_service.read_by_qbo_identity.call_count == 2


# --- Section 3: integration through sync_from_qbo_customer (truthy parent_ref_value) ---


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
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _sync_connector(*, direct=None, staging=None, parent_mapping=None, existing_project=None):
    connector = _build_connector(
        direct=direct, staging=staging, parent_mapping=parent_mapping, own_mapping=None
    )
    connector._sync_addresses = Mock()
    # Force the header identity fast path to a miss so these tests land on the
    # mapping-table branches (conftest.stub_qbo_identity_fastpath_miss's rule).
    connector.project_service.read_by_qbo_identity.return_value = None
    connector.project_service.read_by_name.return_value = None
    connector.mapping_repo.read_by_qbo_customer_id.return_value = None
    # Pin BOTH 1:1 guards in create_mapping — a bare Mock returns a truthy row
    # here and the create path raises "already mapped".
    connector.mapping_repo.read_by_project_id.return_value = None
    connector.mapping_repo.create.return_value = SimpleNamespace(id=1)
    connector.project_service.repo.update_by_id.side_effect = lambda p: p
    if existing_project is not None:
        connector.mapping_repo.read_by_qbo_customer_id.return_value = SimpleNamespace(
            id=1, project_id=existing_project.id
        )
        connector.project_service.read_by_id.return_value = existing_project
    return connector


def test_sync_writes_the_resolved_parent_on_the_dbo_identity_fast_path():
    """The branch prod actually takes.

    Every Project that has synced even once carries dbo.Project.QboId/RealmId, so
    all 136 live job/sub-customers resolve through `run_identity_fastpath`, NOT
    the legacy mapping-table branch the other Section-3 tests exercise. The fast
    path hands `customer_id` through its own `apply_fields` lambda — a separate
    call site from the legacy branch's — so without this test a change that
    stopped writing the parent on the fast path would leave the whole suite
    green while breaking every sub-customer in production.
    """
    project = SimpleNamespace(
        id=10, name="Sub Unit A", description="", status="active", customer_id=None
    )
    connector = _sync_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"))
    # Header identity resolves directly -> fast path HITS ...
    connector.project_service.read_by_qbo_identity.return_value = project
    # ... and the mapping table agrees, so resolve_mapping_state is CONSISTENT
    # (qbo_customer_id must equal the QboCustomer's id, 4).
    connector.mapping_repo.read_by_project_id.return_value = SimpleNamespace(
        id=1, project_id=10, qbo_customer_id=4
    )

    result = connector.sync_from_qbo_customer(_make_qbo_customer())

    assert result is project
    assert result.customer_id == 42


def test_sync_writes_the_resolved_parent_onto_an_existing_project():
    project = SimpleNamespace(
        id=10, name="Sub Unit A", description="", status="active", customer_id=None
    )
    connector = _sync_connector(
        direct=SimpleNamespace(id=42, qbo_id="P-1"), existing_project=project
    )

    result = connector.sync_from_qbo_customer(_make_qbo_customer())

    assert result.customer_id == 42


def test_sync_passes_the_resolved_parent_to_project_create():
    connector = _sync_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"))
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer())

    assert connector.project_service.create.call_args.kwargs["customer_id"] == 42


def test_sync_passes_none_when_both_resolution_paths_miss():
    """An unresolvable parent must not raise or skip — it passes None through,
    which UpdateProjectById's CASE WHEN guard then treats as preserve-existing."""
    connector = _sync_connector(direct=None, staging=None)
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer())

    assert connector.project_service.create.call_args.kwargs["customer_id"] is None


def test_sync_with_no_parent_ref_never_attempts_a_parent_lookup():
    """Regression baseline for the 16 pre-existing tests, all of which use
    parent_ref_value=None."""
    connector = _sync_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"))
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer(parent_ref_value=None))

    assert connector.project_service.create.call_args.kwargs["customer_id"] is None
    connector.customer_service.read_by_qbo_identity.assert_not_called()
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_sync_resolves_the_parent_once_across_sibling_sub_units():
    """The cache's reason to exist: 136 prod job customers share 71 parents."""
    connector = _sync_connector(direct=SimpleNamespace(id=42, qbo_id="P-1"))
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer(id=4, display_name="Sub A"))
    connector.sync_from_qbo_customer(_make_qbo_customer(id=5, display_name="Sub B"))

    assert connector.customer_service.read_by_qbo_identity.call_count == 1
