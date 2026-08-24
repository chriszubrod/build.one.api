"""Pure-logic tests for U-297: repoint `CustomerProjectConnector`'s own
parent-Customer lookup off the `qbo.Customer` -> `qbo.CustomerCustomer` two-hop
and onto `dbo.Customer`'s native QboId/RealmId (U-238c) — and for U-310, which
finished the job by swapping the verify step onto the dbo-only primitive and
DELETING the legacy two-hop fallback entirely.

The repointed branch resolves a QBO job/sub-customer's `ParentRefValue` to a
local `dbo.Customer.Id`, which is then WRITTEN to `dbo.Project.CustomerId`.
Before U-297 that branch had ZERO test coverage — every QboCustomer fixture
in the suite set `parent_ref_value=None`, so the connector's parent-lookup lines
never executed.

Covers:
  1. The shared `verify_customer_qbo_identity` wrapper
     (`base/identity_consistency.py`) — the third binding of the
     `_verify_dbo_qbo_identity` engine, alongside Project and Vendor. STILL
     LIVE and still tested here: Project's and Vendor's own reference resolvers
     use this mapping-table-reading engine until U-311/U-312 repoint them.
     `CustomerProjectConnector` itself no longer calls it (see 2).
  2. `CustomerProjectConnector._get_parent_customer_id` /
     `_resolve_parent_customer_id` post-U-310: a direct dbo hit is confirmed by
     `verify_identity_dbo_only` (a SECOND `read_by_qbo_identity`, keyed on the
     resolved row's own identity, compared by `.id`) and reads no `qbo.*` table
     at all; a verify refusal or a direct miss returns None OUTRIGHT — the
     legacy `qbo.Customer` -> `qbo.CustomerCustomer` hop that used to answer in
     those cases is deleted (`docs/design/wave5.md` §2's "consequence worth
     flagging": an advisory call site becomes hard-stop-equivalent by
     construction). Empty ref short-circuits; the result is memoized per
     (realm, ref), misses included.
  3. Integration through `sync_from_qbo_customer` with a TRUTHY
     `parent_ref_value` — that the resolved id actually reaches
     `_apply_project_fields_and_sync` (update paths) and `project_service.create`
     (create path), and that an unresolvable parent passes None through.
"""
from types import SimpleNamespace
from unittest.mock import Mock, call

from integrations.intuit.qbo.base.identity_consistency import (
    IdentityCheckResult,
    verify_customer_qbo_identity,
)
from integrations.intuit.qbo.customer.connector.project.business.service import (
    CustomerProjectConnector,
)


# --- Section 1: the verify_customer_qbo_identity wrapper ---


def _verify_repos(*, mapping_id=None, forward_external_qbo_id=None, reverse_mapped_local_id=None):
    """Bind the two repos the wrapper needs, with an EXPLICIT `IdentityCheckResult`.

    Never leave `read_identity_check` as a bare Mock: its default return is a
    truthy Mock whose `.mapping_id`/`.forward_external_qbo_id` are themselves
    Mocks, so the engine's `is not None` / `!=` comparisons would pass or fail
    by accident rather than by the case under test.
    """
    customer_customer_repo = Mock()
    customer_customer_repo.read_identity_check.return_value = IdentityCheckResult(
        mapping_id=mapping_id,
        forward_external_qbo_id=forward_external_qbo_id,
        reverse_mapped_local_id=reverse_mapped_local_id,
    )
    qbo_customer_repo = Mock()
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
    mapping_repo.read_identity_check.assert_not_called()
    assert qbo_repo.method_calls == []


def test_verify_trusts_when_customer_has_no_mapping_row():
    """No CustomerCustomer row AND no reverse conflict = nothing to disagree
    with = TRUST."""
    mapping_repo, qbo_repo = _verify_repos()
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result == "P-1"
    assert qbo_repo.method_calls == []  # U-306: folded into the one JOIN'd read, never touched


def test_verify_trusts_when_mapping_agrees():
    mapping_repo, qbo_repo = _verify_repos(
        mapping_id=1, forward_external_qbo_id="P-1", reverse_mapped_local_id=55,
    )
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result == "P-1"


def test_verify_refuses_when_mapping_binds_a_different_external_customer():
    """The whole point: dbo says P-1, the mapping table still says P-2 -> refuse."""
    mapping_repo, qbo_repo = _verify_repos(
        mapping_id=1, forward_external_qbo_id="P-2", reverse_mapped_local_id=55,
    )
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result is None


def test_verify_refuses_when_unmapped_but_reverse_bound_to_a_different_customer():
    """U-297's H1, closed by U-306: no CustomerCustomer mapping of its own, but
    the mapping table already binds this exact QboId to a DIFFERENT Customer."""
    mapping_repo, qbo_repo = _verify_repos(mapping_id=None, reverse_mapped_local_id=999)
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    result = verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    assert result is None


def test_verify_binds_the_customer_familys_accessors():
    """Guards against a copy/paste of the Project or Vendor wrapper's bindings."""
    mapping_repo, qbo_repo = _verify_repos(
        mapping_id=1, forward_external_qbo_id="P-1", reverse_mapped_local_id=55,
    )
    customer = SimpleNamespace(id=55, qbo_id="P-1")

    verify_customer_qbo_identity(
        customer, customer_customer_repo=mapping_repo, qbo_customer_repo=qbo_repo
    )

    # read_identity_check (not a Project/Vendor-shaped wrapper), keyed on the
    # LOCAL id and this entity's own qbo_id.
    mapping_repo.read_identity_check.assert_called_once_with(local_id=55, qbo_id="P-1")


# --- Section 2: the _get_parent_customer_id / _resolve_parent_customer_id resolver ---
#
# U-310 repointed this resolver onto Option A (`verify_identity_dbo_only`) and
# DELETED the legacy `qbo.Customer` -> `qbo.CustomerCustomer` two-hop that used
# to catch a verify refusal. Two consequences these tests now pin:
#
#   1. The verify step is a SECOND `customer_service.read_by_qbo_identity` call,
#      keyed on the already-resolved row's OWN (qbo_id, realm_id), trusted only
#      when it reads back the SAME local id. So "verify agrees" is a second read
#      returning the same entity, and "verify refuses" is a second read
#      returning a different row (or nothing).
#   2. A refusal or a direct miss now returns None OUTRIGHT — there is no legacy
#      answer left to fall through to. Per `docs/design/wave5.md` §2's
#      "consequence worth flagging", this advisory resolver becomes
#      hard-stop-equivalent by construction. The underlying property is
#      unchanged and still proven below: a disagreeing/refused identity is NEVER
#      trusted — only the outcome of not trusting it moved from "use the legacy
#      hop's answer" to "resolve to no parent at all".


def _build_connector(*, direct=None):
    """A connector whose parent-lookup dependency is explicitly pinned.

    `direct` -> customer_service.read_by_qbo_identity. Post-U-310 that ONE
    collaborator serves both steps: the resolver's own direct read and the
    verify step's re-read. Returning the same object from both (a plain
    `return_value`) is the "verify agrees" case, since the verify compares
    `.id`. A test that needs a DISAGREEING verify overrides
    `read_by_qbo_identity.side_effect` directly afterward — the same idiom the
    pre-U-310 form of this file used to override `read_identity_check`.

    `customer_mapping_repo` / `qbo_customer_repo` are still injected because
    the connector still accepts them (constructor back-compat, U-310), and
    pinning them as Mocks keeps the "never read any more" assertions honest.
    """
    customer_service = Mock()
    customer_service.read_by_qbo_identity.return_value = direct

    return CustomerProjectConnector(
        mapping_repo=Mock(),
        project_service=Mock(),
        project_address_service=Mock(),
        address_connector=Mock(),
        customer_mapping_repo=Mock(),
        reconciliation_repo=Mock(),
        customer_service=customer_service,
        qbo_customer_repo=Mock(),
    )


def _parent(id=42, qbo_id="P-1", realm_id="realm-1"):
    """A dbo.Customer-shaped parent. `realm_id` is load-bearing post-U-310:
    `verify_identity_dbo_only` re-reads by the entity's OWN (qbo_id, realm_id)."""
    return SimpleNamespace(id=id, qbo_id=qbo_id, realm_id=realm_id)


def test_resolver_direct_hit_returns_dbo_id_and_reads_no_mapping_table():
    connector = _build_connector(direct=_parent())

    result = connector._get_parent_customer_id("P-1", "realm-1")

    assert result == 42
    # U-310: neither staging nor the mapping table is touched at all any more.
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()
    connector.customer_mapping_repo.read_by_qbo_customer_id.assert_not_called()
    connector.customer_mapping_repo.read_identity_check.assert_not_called()


def test_resolver_direct_hit_is_the_prod_steady_state_at_two_dbo_reads():
    """The branch every real parent takes, and the round-trip budget the repoint
    is justified by.

    Post-U-310 both reads are against dbo.Customer: the direct read plus the
    verify re-read = 2 reads per DISTINCT parent, and no `qbo.*` read at all.
    71 distinct parents x 2 = 142, versus the pre-repoint legacy 136 x 2 = 272.
    A 3rd read here (71 x 3 = 213) would silently give the budget back.
    """
    connector = _build_connector(direct=_parent())

    assert connector._get_parent_customer_id("P-1", "realm-1") == 42

    assert connector.customer_service.read_by_qbo_identity.call_count == 2
    assert connector.qbo_customer_repo.method_calls == []
    assert connector.customer_mapping_repo.method_calls == []


def test_resolver_direct_miss_returns_none_with_no_legacy_hop_left():
    """Pre-U-310 a direct miss fell through to the qbo.Customer ->
    qbo.CustomerCustomer hop and could still answer. That hop is deleted, so a
    miss is now the final answer."""
    connector = _build_connector(direct=None)

    assert connector._get_parent_customer_id("P-1", "realm-1") is None
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()
    connector.customer_mapping_repo.read_by_qbo_customer_id.assert_not_called()
    # A miss short-circuits before the verify step — nothing to verify.
    assert connector.customer_service.read_by_qbo_identity.call_count == 1


def test_resolver_verify_failure_returns_none_and_never_the_direct_hit():
    """A direct hit whose identity no longer reads back to it is refused. The
    resolver returns None — NOT the unverified direct hit, and (post-U-310) not
    a legacy-hop answer either, because there is no legacy hop. This is the
    assertion that fails if the verify call is dropped."""
    connector = _build_connector(direct=_parent())
    # The fresh read by this row's own identity resolves to a DIFFERENT row.
    connector.customer_service.read_by_qbo_identity.side_effect = [
        _parent(id=42), _parent(id=999),
    ]

    result = connector._get_parent_customer_id("P-1", "realm-1")

    assert result is None  # refused outright
    assert result != 42  # NOT the unverified direct hit
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_resolver_verify_refusal_on_a_vanished_identity_returns_none():
    """The other refusal shape: the fresh read finds NOBODY holding the identity
    any more (it was stolen/cleared between the two reads). Must be refused
    exactly like a disagreement — never trusted just because nothing contradicts
    it. (Pre-U-310 this was U-297's H1: the mapping table binding the same QboId
    to a DIFFERENT Customer; dbo-only, both collapse to the same `.id` compare.)"""
    connector = _build_connector(direct=_parent())
    connector.customer_service.read_by_qbo_identity.side_effect = [_parent(id=42), None]

    assert connector._get_parent_customer_id("P-1", "realm-1") is None
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_resolver_returns_none_when_the_direct_read_misses():
    connector = _build_connector(direct=None)
    assert connector._get_parent_customer_id("P-1", "realm-1") is None


def test_resolver_short_circuits_on_empty_parent_ref_without_any_read():
    connector = _build_connector(direct=_parent())

    for empty in (None, ""):
        assert connector._get_parent_customer_id(empty, "realm-1") is None

    # A `WHERE QboId = NULL` round trip for every top-level job would be pure waste.
    connector.customer_service.read_by_qbo_identity.assert_not_called()
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_resolver_threads_realm_positionally_including_none():
    connector = _build_connector(direct=_parent())

    connector._get_parent_customer_id("P-1", "realm-1")
    # The resolver's OWN read is the first one — the second is the verify
    # re-read, keyed on the resolved row's own identity (asserted separately).
    assert connector.customer_service.read_by_qbo_identity.call_args_list[0] == call(
        "P-1", "realm-1"
    )

    # A None realm is still passed through, not guarded away.
    connector.customer_service.read_by_qbo_identity.reset_mock()
    connector._get_parent_customer_id("P-2", None)
    assert connector.customer_service.read_by_qbo_identity.call_args_list[0] == call("P-2", None)


def test_resolver_verify_reads_by_the_resolved_rows_own_identity():
    """Replaces the pre-U-310 'the legacy hop stays realm-UNSCOPED' test: there
    is no legacy hop left to keep unscoped, but the same question — what key the
    SECOND read uses — still matters. `verify_identity_dbo_only` must re-read by
    the RESOLVED ROW's own (qbo_id, realm_id), not by the child's realm argument:
    a row whose stored realm differs from the child's is exactly what the verify
    exists to catch, and passing the child's realm through would mask it."""
    connector = _build_connector(direct=_parent(qbo_id="P-1", realm_id="realm-stored"))

    connector._get_parent_customer_id("P-1", "realm-child")

    assert connector.customer_service.read_by_qbo_identity.call_args_list == [
        call("P-1", "realm-child"),   # the resolver's own read: the ref + child realm
        call("P-1", "realm-stored"),  # the verify re-read: the row's OWN identity
    ]


def test_resolver_returns_a_local_int_not_a_public_id():
    """Type parity with what it feeds: the result is WRITTEN to
    dbo.Project.CustomerId, so it must be a local dbo.Customer.Id int."""
    hit = _build_connector(direct=_parent(id=42))
    miss = _build_connector(direct=None)

    a = hit._get_parent_customer_id("P-1", "realm-1")
    b = miss._get_parent_customer_id("P-1", "realm-1")

    assert a == 42
    assert isinstance(a, int)
    assert b is None


def test_resolver_memoizes_per_connector_lifetime():
    connector = _build_connector(direct=_parent())

    first = connector._get_parent_customer_id("P-1", "realm-1")
    reads_after_first = connector.customer_service.read_by_qbo_identity.call_count
    second = connector._get_parent_customer_id("P-1", "realm-1")

    assert first == second == 42
    assert reads_after_first == 2  # direct + verify, once
    assert connector.customer_service.read_by_qbo_identity.call_count == reads_after_first


def test_resolver_memoizes_misses_too():
    """Caching only hits would leave a miss re-paying its round trip per job.

    Pre-U-310 this proved the point with `qbo_customer_repo.read_by_qbo_id.call_count
    == 1` (the legacy hop, which a miss also had to pay). That hop is gone, so
    the equivalent proof is that the ONE remaining read — the dbo direct read —
    also fires only once across two lookups of the same key."""
    connector = _build_connector(direct=None)

    assert connector._get_parent_customer_id("P-1", "realm-1") is None
    assert connector._get_parent_customer_id("P-1", "realm-1") is None

    assert connector.customer_service.read_by_qbo_identity.call_count == 1
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_resolver_cache_is_keyed_by_realm_too():
    connector = _build_connector(direct=_parent())

    connector._get_parent_customer_id("P-1", "realm-1")
    connector._get_parent_customer_id("P-1", "realm-2")

    # Same ref in a different realm is a different question: 2 resolutions x
    # (direct + verify).
    assert connector.customer_service.read_by_qbo_identity.call_count == 4


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


def _sync_connector(*, direct=None, existing_project=None):
    connector = _build_connector(direct=direct)
    connector._sync_addresses = Mock()
    # Force the header identity fast path to a miss so these tests land on the
    # mapping-table branches (conftest.stub_qbo_identity_fastpath_miss's rule).
    # NB Project's OWN identity path is still the mapping-table shape — U-310
    # repointed only the PARENT-CUSTOMER lookup; U-311 owns the rest.
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
    connector = _sync_connector(direct=_parent())
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
    connector = _sync_connector(direct=_parent(), existing_project=project)

    result = connector.sync_from_qbo_customer(_make_qbo_customer())

    assert result.customer_id == 42


def test_sync_passes_the_resolved_parent_to_project_create():
    connector = _sync_connector(direct=_parent())
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer())

    assert connector.project_service.create.call_args.kwargs["customer_id"] == 42


def test_sync_passes_none_when_the_parent_cannot_be_resolved():
    """An unresolvable parent must not raise or skip — it passes None through,
    which UpdateProjectById's CASE WHEN guard then treats as preserve-existing.
    Post-U-310 "unresolvable" also covers a REFUSED verify, which no longer has
    a legacy hop to fall back to."""
    connector = _sync_connector(direct=None)
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer())

    assert connector.project_service.create.call_args.kwargs["customer_id"] is None


def test_sync_with_no_parent_ref_never_attempts_a_parent_lookup():
    """Regression baseline for the 16 pre-existing tests, all of which use
    parent_ref_value=None."""
    connector = _sync_connector(direct=_parent())
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer(parent_ref_value=None))

    assert connector.project_service.create.call_args.kwargs["customer_id"] is None
    connector.customer_service.read_by_qbo_identity.assert_not_called()
    connector.qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_sync_resolves_the_parent_once_across_sibling_sub_units():
    """The cache's reason to exist: 136 prod job customers share 71 parents."""
    connector = _sync_connector(direct=_parent())
    connector.project_service.create.return_value = SimpleNamespace(id=77, public_id="p-77")

    connector.sync_from_qbo_customer(_make_qbo_customer(id=4, display_name="Sub A"))
    connector.sync_from_qbo_customer(_make_qbo_customer(id=5, display_name="Sub B"))

    # One resolution only: direct + verify, then the memo answers the sibling.
    assert connector.customer_service.read_by_qbo_identity.call_count == 2
