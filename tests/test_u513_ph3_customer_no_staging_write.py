"""U-513 ph3a (CUSTOMER) — the readers are payload-fed; ONE fallback remains.

WHAT THIS FILE WAS, AND WHY IT CHANGED
--------------------------------------
ph3a was scoped as "delete `QboCustomerService._upsert_physical_address` and
stop passing `bill_addr_id` / `ship_addr_id` onto the `qbo.Customer` staging
row", on the stated premise that *projection no longer reads those rows*.

That premise held for exactly ONE of the customer family's two halves, so this
file was written as a BLOCKING GATE: it asserted, in the direction that was
correct at the time, that `CustomerProjectConnector` — the JOB half — still read
the FK columns in two live places the production pull reached, and that removing
the write would therefore kill the project SHIPPING slot outright and billing
Link 1 with it, SILENTLY (every read is blank-guarded and failure-isolated, and
`UpdateQboCustomerByQboId` COALESCES the FKs, so existing rows would keep
pointing at staging rows nothing refreshes and a QBO address edit would simply
stop propagating).

**U-513 ph2.5 discharged that gate by building the seam it asked for.** Both
halves are now payload-first:

    ✅ PARENT half — `CustomerCustomerConnector._own_billing_address` (ph1),
       threaded by `_sync_to_customers`. Its `bill_addr_id` read was a dead
       transitional fallback (Section 3), and ph3b deleted it.
    ✅ JOB half — `CustomerProjectConnector._own_billing_address_id` /
       `_own_shipping_address_id` (ph2.5), threaded by `_sync_to_projects`.
       Both project from the inline `BillAddr` / `ShipAddr`; the staging read
       survived as `_own_address_id_from_staging` until ph3b deleted that too.

So the file is no longer a blocker. It was then the ph3b inventory — recording
what the readers actually depended on, so the next phase could be checked
against something real rather than against prose — and ph3b has now emptied
that inventory (Section 2). What is left is this file's original and still-live
subject: the staging WRITE, and the proof that no pull path needs it.

WHAT THIS FILE DOES NOW
-----------------------
  * Section 1 proves the two job-half readers are PAYLOAD-fed, by running
    `_sync_addresses` with the FKs NULL (what ph3a produces) and the payload
    threaded, and asserting the `ProjectAddress` links still land. These are
    the same assertions as before, inverted — a regression to staging-only goes
    RED here.
  * Section 2 pinned the ONE remaining dependency, structurally. ph3b removed
    it; the section now records where the (stronger) successor assertions
    went — `test_u513_ph3b_customer_no_staging_read.py`.
  * Section 3 discharges the one check ph3a explicitly asked for: the parent
    half's transitional fallback really is unreachable from the pull.
  * Section 4 pins the payload -> `dbo.Address` path that ph1 built, under the
    synthetic identity ph3b depends on staying byte-identical.

The job half's own payload seam is covered in depth by
`test_u513_ph25_project_payload_seam.py` (blankness parity across both
branches, the shipping non-inheritance invariant, cross-wiring refusal). What
is here is only what bears on the STAGING WRITE decision.

Pure logic throughout: in-memory fakes, no live DB.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from integrations.intuit.qbo.customer.business import service as customer_service_module
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.connector.customer.business.service import (
    CustomerCustomerConnector,
    billing_address_qbo_id,
)
from integrations.intuit.qbo.customer.connector.project.business.service import (
    ADDRESS_TYPE_BILLING,
    ADDRESS_TYPE_SHIPPING,
    CustomerProjectConnector,
)

# Reuse the U-506 P1 harness rather than re-building a second set of fakes for
# the same connector. A divergent copy is how one of them quietly stops
# modelling the shipped shape -- and these tests turn on details that harness
# already gets right (staging vs dbo id keyspaces kept disjoint, realm scoping
# that fails closed, ProjectAddress as an observable in-memory table).
from tests.test_u506_p1_project_address_parent_fallback import (
    PARENT_BILL_ADDRESS_ID,
    PROJECT_ID,
    REAL_PARENT_BILL_ADDRESS,
    _build_connector,
    _qbo_customer,
)
# ph2.5's payload harness — the SAME connector, wired with the seam this file
# now asserts is live. Reused rather than re-faked for the same reason the U-506
# harness is: a second copy is how one of them stops modelling the shipped shape.
from tests.test_u513_ph25_project_payload_seam import (
    JOB_QBO_ID,
    PAYLOAD_BILL,
    PAYLOAD_SHIP,
    _build as _build_payload_connector,
    _external,
)
from tests.test_u269_qbo_staging_try_except import _client_cm

REALM = "9130353016965726"

OWNER_MAILING = {
    "Line1": "1539 Old Hillsboro Road",
    "Line2": "Suite 200",
    "City": "Franklin",
    "CountrySubDivisionCode": "TN",
    "PostalCode": "37064",
}


def _links_of_type(connector, address_type_id):
    return [
        address_id
        for type_id, address_id in connector.project_address_service.links()
        if type_id == address_type_id
    ]


# ===========================================================================
# Section 1 — DISCHARGED: the job half's two readers are PAYLOAD-fed
# ===========================================================================

def test_the_job_shipping_slot_survives_a_NULL_staging_fk():
    """✅ THE BLOCKER, shipping half — inverted by ph2.5.

    This asserted the opposite until ph2.5: `_sync_addresses`'s SHIPPING branch
    was `qbo_customer.ship_addr_id` and nothing else, so NULLing the FK killed
    the slot for every project forever (shipping deliberately never inherits
    from the parent, so there was no second source).

    Now the FK is NULL — the post-ph3a state — and the slot still resolves, from
    the inline `ShipAddr`. Both halves run here on purpose, exactly as before:
    asserting only the payload case would pass just as well against a connector
    that had stopped writing shipping links altogether.
    """
    after_ph3a = _build_payload_connector()
    after_ph3a._sync_addresses(
        _qbo_customer(ship_addr_id=None),
        PROJECT_ID,
        external_customer=_external(ship_addr=PAYLOAD_SHIP),
    )
    assert _links_of_type(after_ph3a, ADDRESS_TYPE_SHIPPING) == [
        after_ph3a.address_connector.address_id_for(f"{JOB_QBO_ID}_ship")
    ], (
        "a NULL ship_addr_id produced no shipping link even WITH the payload "
        "threaded -- the job half has regressed to staging-only and ph3a is "
        "blocked again"
    )

    no_payload = _build_payload_connector()
    no_payload._sync_addresses(_qbo_customer(ship_addr_id=None), PROJECT_ID)
    assert _links_of_type(no_payload, ADDRESS_TYPE_SHIPPING) == [], (
        "a link appeared with NEITHER an FK nor a payload -- the assertion "
        "above is not proving the payload is what fed the slot"
    )


def test_the_job_own_billing_link_survives_a_NULL_staging_fk():
    """✅ THE BLOCKER, billing half — inverted by ph2.5.

    Link 1 of the billing chain (`_own_billing_address_id`) used to resolve the
    JOB's own BillAddr through `qbo.PhysicalAddress`. Built with no parent
    address at all, so the chain has exactly one candidate and the assertion
    cannot be satisfied by the parent fallback standing in.

    Live measurement (2026-09-23, recorded in `_billing_address_candidates`):
    own-bill wins for 2 of 138 projects. Small, but those two would have lost
    their OWN street and silently fallen through to the owner's mailing address.
    """
    after_ph3a = _build_payload_connector(parent_address=None)
    after_ph3a._sync_addresses(
        _qbo_customer(bill_addr_id=None),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL),
    )
    assert _links_of_type(after_ph3a, ADDRESS_TYPE_BILLING) == [
        after_ph3a.address_connector.address_id_for(f"{JOB_QBO_ID}_bill")
    ], (
        "a NULL bill_addr_id produced no billing link even WITH the payload "
        "threaded -- billing Link 1 has regressed to staging-only"
    )

    no_payload = _build_payload_connector(parent_address=None)
    no_payload._sync_addresses(_qbo_customer(bill_addr_id=None), PROJECT_ID)
    assert _links_of_type(no_payload, ADDRESS_TYPE_BILLING) == []


def test_the_parent_fallback_covers_billing_but_CANNOT_cover_shipping():
    """The asymmetry that made the shipping loss unrecoverable WITHOUT a
    payload, and that ph2.5 did not soften.

    With BOTH FKs NULL and NO payload, but a parent whose `<ref>_bill`
    `dbo.Address` row is fully populated, billing still resolves -- the parent
    fallback is off staging already (ph1). Shipping resolves nothing, and by
    design never will: under property semantics a parent's address is a SIBLING
    project's street, wrong for 16 of the 61 projects U-506 P1 covered. That is
    why the shipping slot had to be given the payload rather than a wider
    fallback, and why `test_shipping_never_inherits_from_the_parent_on_the_
    payload_path` guards the new branch the same way.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    connector._sync_addresses(
        _qbo_customer(bill_addr_id=None, ship_addr_id=None), PROJECT_ID
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [PARENT_BILL_ADDRESS_ID]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == []


def test_the_job_projection_now_has_the_same_payload_seam_as_the_parent():
    """The structural gap, closed. Both halves take the payload as an optional
    second parameter, and both projection loops thread the map into it.

    Optional is load-bearing, not politeness: `heal_missing_mapping` and the
    other direct callers pass one argument, and `project_records`' 10 call sites
    across 8 other QBO families keep their strict one-argument `project_one`.
    """
    parent_params = list(
        inspect.signature(CustomerCustomerConnector.sync_from_qbo_customer).parameters
    )
    assert parent_params == ["self", "qbo_customer", "external_customer"]

    job_params = list(
        inspect.signature(CustomerProjectConnector.sync_from_qbo_customer).parameters
    )
    assert job_params == ["self", "qbo_customer", "external_customer"], (
        "CustomerProjectConnector lost its payload parameter -- the job half is "
        "back on staging for BillAddr and ShipAddr, and ph3a is blocked again"
    )
    assert (
        inspect.signature(
            CustomerProjectConnector.sync_from_qbo_customer
        ).parameters["external_customer"].default
        is None
    )

    projects_src = inspect.getsource(QboCustomerService._sync_to_projects)
    assert "external_by_id" in projects_src, (
        "the job projection no longer receives the payload map -- the connector "
        "seam exists but nothing feeds it, which is the silent-failure shape "
        "this file was built to catch"
    )


# ===========================================================================
# Section 2 — DISCHARGED: nothing depends on the staging write any more
# ===========================================================================
#
# ⚠️ EMPTIED BY ph3b, which is the outcome this section was built to produce.
# Two tests stood here:
#
#   * `test_the_staging_fallback_is_the_only_remaining_reader_of_the_address_
#     fks` — an AST inventory asserting the FK columns were read in exactly two
#     places, both of them the staging-arm dispatch. ph3b deleted that arm, so
#     the inventory is EMPTY. It did not simply lose its subject: "zero readers"
#     is a stronger claim than "these two", and it now spans the whole customer
#     package rather than one module. It lives in
#     `test_u513_ph3b_customer_no_staging_read.py`.
#
#   * `test_heal_missing_mapping_is_the_last_production_caller_of_the_staging_
#     path` — which priced ph3a by showing heal still resolved addresses
#     through staging. ph3b is where that price is actually paid, so the gap,
#     its bound and the proof that the next payload-bearing pull closes it are
#     pinned there too.
#
# What remains here is what this file is actually about: the staging WRITE.


def test_the_physical_address_staging_write_is_GONE():
    """ph3a, customer half. INVERTED from the gate this file used to be.

    Until ph2.5 this asserted the write was still wired, because deleting it
    silently broke the project SHIPPING slot: `CustomerProjectConnector` read
    `qbo_customer.ship_addr_id` and had no payload seam. The full suite stayed
    green and prod would not have raised -- `qbo.customer.sql:512` coalesces, so
    passing NULL PRESERVES the stale FK rather than clearing it, and every read
    is blank-guarded and failure-isolated.

    ph2.5 gave the job half the payload seam, so the precondition now holds and
    the write is deleted. Asserting its ABSENCE rather than deleting the test,
    because reinstating it would quietly recreate a writer for a table that no
    longer needs one -- and ph3b is about to drop that table.
    """
    assert not hasattr(QboCustomerService, "_upsert_physical_address"), (
        "the qbo.PhysicalAddress staging write is back on the customer service. "
        "Both address slots project from the inline payload now; a writer here "
        "recreates a dependency ph3b is dropping."
    )
    import inspect
    from integrations.intuit.qbo.customer.business import service as svc_mod
    src = inspect.getsource(svc_mod)
    for gone in ("QboPhysicalAddressService(", "physical_address_repo."):
        assert gone not in src, f"{gone} is back in the customer service"


def test_a_full_pull_stamps_NULL_into_both_address_fks():
    """The FKs are passed, and passed as None.

    Passed rather than omitted because `QboCustomerRepository.create` declares
    both keyword-only with NO default -- omitting is a TypeError (the vendor
    half hit exactly this).

    ⚠️ NULL does not CLEAR an existing FK: the UPDATE sproc coalesces. A row
    staged before ph3a keeps its id, which is what makes the rollout safe in
    both directions -- the outgoing container can still resolve addresses
    mid-deploy. The columns themselves go in ph3b.
    """
    import inspect
    from integrations.intuit.qbo.customer.business import service as svc_mod
    src = inspect.getsource(svc_mod.QboCustomerService._upsert_customer)
    assert "bill_addr_id = None" in src and "ship_addr_id = None" in src, (
        "the address FKs are no longer pinned to None in _upsert_customer"
    )
    assert "_upsert_physical_address" not in src



# ===========================================================================
# Section 3 — the check ph3a asked for: is the PARENT fallback really dead?
# ===========================================================================

def test_the_parent_staging_fallback_cannot_fire_from_the_pull():
    """✅ CONFIRMED DEAD -- for the parent half only.

    ⚠️ ph3b DELETED that fallback on the strength of this test, so what it
    proves has changed register: it is no longer "the branch is unreachable", it
    is "the payload is always there" — i.e. the deletion took nothing away. Kept
    verbatim for that reason. If it ever goes red, ph3b's parent half stops
    being a dead-branch removal and becomes address loss.

    `CustomerCustomerConnector._own_billing_address` used to fall back to the
    staging row when `external_customer is None`. That could not happen on the
    pull path:
    `sync_from_qbo` writes `external_by_id[qbo_customer.id]` and appends to
    `parent_customers` inside the SAME try block, after the same upsert, so a
    row reaches projection only if its payload was recorded first -- and the
    lookup key (`row.qbo_id`) is the value `_upsert_customer` passed as
    `qbo_id=qbo_customer.id`, so the map cannot miss.

    Asserted over a pull where one of two parents FAILS to stage: the survivor
    must arrive with its own payload, and the failed one must not arrive at all.
    Partitioning them into different branches is the realistic way this
    invariant would break.
    """
    repo = MagicMock()
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.side_effect = [
        RuntimeError("staging upsert failed for P-1"),
        SimpleNamespace(qbo_id="P-2", is_job=False),
    ]

    service = QboCustomerService(repo=repo)
    service.physical_address_service = MagicMock()
    service.physical_address_service.read_by_qbo_id.return_value = None
    service.physical_address_service.create.return_value = SimpleNamespace(id=910)

    externals = [
        customer_service_module.QboCustomerExternalSchema(
            Id="P-1", SyncToken="0", DisplayName="Owner One", Job=False,
            Active=True, BillAddr=OWNER_MAILING,
        ),
        customer_service_module.QboCustomerExternalSchema(
            Id="P-2", SyncToken="0", DisplayName="Owner Two", Job=False,
            Active=True, BillAddr=OWNER_MAILING,
        ),
    ]

    seen = []
    customer_connector = Mock()
    customer_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: seen.append((row.qbo_id, external))
        or SimpleNamespace(id=1)
    )

    with patch(
        f"{customer_service_module.__name__}.QboCustomerClient",
        return_value=_client_cm(externals),
    ), patch(
        f"{customer_service_module.__name__}.with_retry",
        side_effect=lambda fn, *a, **k: fn(*a),
    ), patch(
        f"{customer_service_module.__name__}.pace_batch",
    ), patch(
        f"{customer_service_module.__name__}.CustomerCustomerConnector",
        return_value=customer_connector,
    ):
        service.sync_from_qbo(realm_id=REALM, sync_to_modules=True)

    assert [qbo_id for qbo_id, _ in seen] == ["P-2"], (
        "a parent that failed to stage still reached projection"
    )
    assert seen[0][1] is externals[1], (
        "the surviving parent reached projection with external_customer=None -- "
        "the transitional staging fallback IS reachable, and deleting the write "
        "would break the parent half too"
    )


def test_the_parent_half_prefers_the_payload_over_the_staging_row():
    """Belt to Section 3's braces: even WITH a staging FK present, a threaded
    payload wins, so the parent half ignores the rows ph3b drops.

    The staging service is a Mock that must stay untouched -- an un-called seam
    is how "reads the payload" stays pinned rather than merely refactored. After
    ph3b nothing in the connector could call it, which is why the Mock is kept
    rather than removed: an assertion on a seam absent from the fake would be an
    assertion about the fake.
    """
    address_connector = Mock()
    address_connector.qbo_physical_address_service = Mock()
    connector = CustomerCustomerConnector(
        customer_service=Mock(),
        reconciliation_repo=Mock(),
        address_connector=address_connector,
    )

    staging_row = SimpleNamespace(qbo_id="P-1", bill_addr_id=902, realm_id=REALM)
    payload = SimpleNamespace(
        bill_addr=SimpleNamespace(
            line1="1539 Old Hillsboro Road", line2=None, city="Franklin",
            country_sub_division_code="TN", postal_code="37064",
        )
    )

    connector._project_own_billing_address(staging_row, payload)

    address_connector.qbo_physical_address_service.read_by_id.assert_not_called()
    assert address_connector.sync_address_from_external.call_args.kwargs["line1"] == (
        "1539 Old Hillsboro Road"
    )


# ===========================================================================
# Section 4 — the payload -> dbo.Address path ph3b depends on
# ===========================================================================

def test_dbo_address_is_minted_from_the_payload_under_the_synthetic_identity():
    """The identity string is the whole reason ph3 needs no data migration:
    `<customer QboId>_bill` is byte-identical to what the staging hop used to
    stamp, so the 799 existing `dbo.Address` rows are not re-keyed.

    Also pins the realm, which `_parent_billing_address_id` reads back through a
    fail-closed sproc -- a mint under the wrong realm would be invisible (the
    row exists, the lookup simply never finds it).
    """
    address_connector = Mock()
    connector = CustomerCustomerConnector(
        customer_service=Mock(),
        reconciliation_repo=Mock(),
        address_connector=address_connector,
    )
    staging_row = SimpleNamespace(qbo_id="P-1", bill_addr_id=None, realm_id=REALM)
    payload = SimpleNamespace(
        bill_addr=SimpleNamespace(
            line1="1539 Old Hillsboro Road", line2="Suite 200", city="Franklin",
            country_sub_division_code="TN", postal_code="37064",
        )
    )

    connector._project_own_billing_address(staging_row, payload)

    kwargs = address_connector.sync_address_from_external.call_args.kwargs
    assert kwargs["qbo_id"] == billing_address_qbo_id("P-1") == "P-1_bill"
    assert kwargs["realm_id"] == REALM
    assert kwargs["source_ref"] == "QboCustomer:P-1"


def test_a_blank_payload_address_mints_nothing():
    """Blank means ABSENT. 191 of 799 `dbo.Address` rows are blank precisely
    because the pre-U-506 writer keyed on presence; the payload path must not
    re-open that door now that it, not staging, is the writer.
    """
    address_connector = Mock()
    connector = CustomerCustomerConnector(
        customer_service=Mock(),
        reconciliation_repo=Mock(),
        address_connector=address_connector,
    )
    staging_row = SimpleNamespace(qbo_id="P-1", bill_addr_id=None, realm_id=REALM)
    blank = SimpleNamespace(
        bill_addr=SimpleNamespace(
            line1="   ", line2="Suite 200", city="", country_sub_division_code="TN",
            postal_code=None,
        )
    )

    connector._project_own_billing_address(staging_row, blank)

    address_connector.sync_address_from_external.assert_not_called()
