"""U-513 ph3a (CUSTOMER) — ⛔ THE STAGING WRITE WAS **NOT** REMOVED.

WHY THIS FILE EXISTS
--------------------
ph3a was scoped as "delete `QboCustomerService._upsert_physical_address` and
stop passing `bill_addr_id` / `ship_addr_id` onto the `qbo.Customer` staging
row", on the stated premise that *projection no longer reads those rows*.

That premise holds for exactly ONE of the customer family's two halves.

    ✅ PARENT half — `CustomerCustomerConnector` is payload-first. ph1 gave it
       `_own_billing_address`, ph2 made `scripts/sync_qbo_customer.py` ask for
       `sync_to_modules=True` so the service threads the inline `BillAddr`, and
       its `bill_addr_id` read is now a dead transitional fallback (pinned by
       `test_the_parent_staging_fallback_cannot_fire_from_the_pull` below).

    ⛔ JOB half — `CustomerProjectConnector` was NEVER converted. It has no
       payload seam at all (`_sync_to_projects` passes one argument on
       purpose), and it reads the staging FK columns in TWO live places:

         `_own_billing_address_id`  (service.py:697)  -> `qbo_customer.bill_addr_id`
         `_sync_addresses`          (service.py:625)  -> `qbo_customer.ship_addr_id`

       Its own docstrings say so out loud: *"The job's OWN link still goes
       through staging"* and *"the one half of this connector U-513 does NOT
       move off staging"*.

Removing the write as scoped would therefore have:

  1. killed the project SHIPPING slot outright — it is job-only, deliberately
     never inherits from the parent, and has no other source;
  2. killed billing Link 1 (the job's own BillAddr), leaving only the parent
     fallback;
  3. done both SILENTLY — every read is blank-guarded and failure-isolated, so
     a NULL FK degrades to "no address" with nothing raised and nothing logged
     above debug.

And it would NOT have failed loudly on deploy either. The UPDATE sproc coalesces
(`[BillAddrId] = CASE WHEN @BillAddrId IS NULL THEN [BillAddrId] ELSE @BillAddrId END`),
so existing job rows keep their FK pointing at a `qbo.PhysicalAddress` row that
is no longer refreshed — a QBO address edit would stop propagating to a project's
billing/shipping slot with no symptom at all. Only brand-new job customers would
show the total loss. Then ph3b drops the table and the columns and the rest goes.

WHAT THIS FILE DOES
-------------------
It is a GATE, not a regression suite for a change that shipped:

  * Section 1 proves the two job-half readers are live and staging-fed, by
    running `_sync_addresses` with the FK populated (today) and NULL (what ph3a
    would produce) and comparing the `ProjectAddress` links written.
  * Section 2 pins the write itself, so deleting it goes RED **here** instead of
    going quiet in prod.
  * Section 3 discharges the one check ph3a explicitly asked for: the parent
    half's transitional fallback really is unreachable from the pull.
  * Section 4 pins the payload -> `dbo.Address` path that ph1 built, under the
    synthetic identity ph3b depends on staying byte-identical.

TO UNBLOCK: give `CustomerProjectConnector` a payload seam the way
`VendorVendorConnector` already has one (`_bill_address_from_payload` /
`_bill_address_from_staging`, chosen on `external is not None`), thread the map
through `_sync_to_projects`, and cover `ShipAddr` as well as `BillAddr` — the
vendor family has no shipping slot, so that half has no precedent to copy.
Then Section 1 inverts and Section 2 flips.

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
    REAL_OWN_BILL,
    REAL_OWN_SHIP,
    REAL_PARENT_BILL_ADDRESS,
    _build_connector,
    _dbo_address_id,
    _qbo_customer,
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
# Section 1 — THE BLOCKER: the job half reads the FK columns ph3a would NULL
# ===========================================================================

def test_the_job_shipping_slot_is_fed_ONLY_by_the_staging_write():
    """⛔ THE BLOCKER, shipping half.

    `_sync_addresses`'s SHIPPING branch is `qbo_customer.ship_addr_id` and
    nothing else. Stop writing that FK and the slot is dead for every project,
    forever -- no payload reaches this connector and shipping deliberately never
    inherits from the parent, so there is no second source to fall back on.

    Both halves run here on purpose: asserting only the NULL case would pass
    just as well against a connector that never wrote a shipping link at all.
    """
    live = _build_connector()
    live._sync_addresses(_qbo_customer(ship_addr_id=REAL_OWN_SHIP), PROJECT_ID)
    assert _links_of_type(live, ADDRESS_TYPE_SHIPPING) == [_dbo_address_id(REAL_OWN_SHIP)], (
        "the shipping slot is not staging-fed today -- re-read _sync_addresses "
        "before trusting the NULL half of this test"
    )

    after_ph3a = _build_connector()
    after_ph3a._sync_addresses(_qbo_customer(ship_addr_id=None), PROJECT_ID)
    assert _links_of_type(after_ph3a, ADDRESS_TYPE_SHIPPING) == [], (
        "a NULL ship_addr_id still produced a shipping link -- if this ever "
        "fires, the job half has gained a non-staging source and ph3a is unblocked"
    )


def test_the_job_own_billing_link_is_fed_ONLY_by_the_staging_write():
    """⛔ THE BLOCKER, billing half.

    Link 1 of the billing chain (`_own_billing_address_id`) resolves the JOB's
    own BillAddr through `qbo.PhysicalAddress` by `qbo_customer.bill_addr_id`.
    Built with no parent address at all, so the chain has exactly one candidate
    and the assertion cannot be satisfied by the parent fallback standing in.

    Live measurement (2026-09-23, recorded in `_billing_address_candidates`):
    own-bill wins for 2 of 138 projects. Small, but those two lose their OWN
    street and silently fall through to the owner's mailing address.
    """
    live = _build_connector(parent_address=None)
    live._sync_addresses(_qbo_customer(bill_addr_id=REAL_OWN_BILL), PROJECT_ID)
    assert _links_of_type(live, ADDRESS_TYPE_BILLING) == [_dbo_address_id(REAL_OWN_BILL)]

    after_ph3a = _build_connector(parent_address=None)
    after_ph3a._sync_addresses(_qbo_customer(bill_addr_id=None), PROJECT_ID)
    assert _links_of_type(after_ph3a, ADDRESS_TYPE_BILLING) == [], (
        "a NULL bill_addr_id still produced a billing link from the job's own "
        "address -- the job half has a non-staging source and ph3a is unblocked"
    )


def test_the_parent_fallback_covers_billing_but_CANNOT_cover_shipping():
    """The asymmetry that makes the shipping loss unrecoverable.

    With BOTH FKs NULL (the post-ph3a state) and a parent whose `<ref>_bill`
    `dbo.Address` row is fully populated, billing still resolves -- the parent
    fallback is off staging already (ph1). Shipping resolves nothing, and by
    design never will: under property semantics a parent's address is a SIBLING
    project's street, wrong for 16 of the 61 projects U-506 P1 covered. So the
    shipping slot cannot be rescued by widening the fallback; it needs the
    payload.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    connector._sync_addresses(
        _qbo_customer(bill_addr_id=None, ship_addr_id=None), PROJECT_ID
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [PARENT_BILL_ADDRESS_ID]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == []


def test_the_job_projection_has_no_payload_seam_to_replace_staging():
    """Why the job half cannot simply be repointed the way the parent half was.

    `CustomerCustomerConnector.sync_from_qbo_customer` grew a second parameter
    in ph1 and `_sync_to_customers` threads the map into it. Its job-side twin
    has neither, and `_sync_to_projects` says so in prose. This is the
    structural gap, asserted rather than described -- it goes GREEN the moment
    someone builds the seam, which is exactly when ph3a becomes shippable.
    """
    parent_params = list(
        inspect.signature(CustomerCustomerConnector.sync_from_qbo_customer).parameters
    )
    assert parent_params == ["self", "qbo_customer", "external_customer"]

    job_params = list(
        inspect.signature(CustomerProjectConnector.sync_from_qbo_customer).parameters
    )
    assert job_params == ["self", "qbo_customer"], (
        "CustomerProjectConnector grew a payload parameter -- if it is now "
        "threaded and covers BOTH BillAddr and ShipAddr, ph3a is unblocked"
    )

    projects_src = inspect.getsource(QboCustomerService._sync_to_projects)
    assert "external_by_id" not in projects_src, (
        "the job projection now receives the payload map -- re-check whether "
        "the connector consumes it for both address slots"
    )


# ===========================================================================
# Section 2 — THE GATE: the write stays until the job half is converted
# ===========================================================================

def test_the_physical_address_staging_write_is_still_wired():
    """RED-on-deletion guard.

    ph3a's own spec asked for the inverse of this assertion. It is written in
    the direction that is CORRECT TODAY so that removing the write trips a test
    instead of trickling out as stale project addresses. Flip this test (and the
    next) in the same commit that converts `CustomerProjectConnector`.
    """
    assert hasattr(QboCustomerService, "_upsert_physical_address"), (
        "_upsert_physical_address was deleted. If CustomerProjectConnector is "
        "now payload-fed for BOTH BillAddr and ShipAddr, delete this test and "
        "invert Section 1. If it is not, this deletion silently breaks project "
        "shipping addresses -- revert it."
    )

    service = QboCustomerService(repo=MagicMock())
    service.physical_address_service = MagicMock()
    service.physical_address_service.read_by_qbo_id.return_value = None
    service.physical_address_service.create.return_value = SimpleNamespace(id=902)

    addr = SimpleNamespace(
        line1="1539 Old Hillsboro Road", line2=None, city="Franklin",
        country=None, country_sub_division_code="TN", postal_code="37064",
    )
    assert service._upsert_physical_address(
        qbo_address=addr, qbo_id="J-7_ship", realm_id=REALM
    ) == 902
    assert service.physical_address_service.create.called


def test_a_full_pull_still_stamps_both_address_fks_onto_the_staging_row():
    """The end-to-end shape ph3a would have removed.

    Drives `sync_from_qbo` over one job customer carrying both a BillAddr and a
    ShipAddr, and asserts the two FKs reach `repo.create`. These are the exact
    values `CustomerProjectConnector` reads back; NULLing them is what breaks it.
    """
    repo = MagicMock()
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.return_value = SimpleNamespace(qbo_id="J-7", is_job=True)

    service = QboCustomerService(repo=repo)
    service.physical_address_service = MagicMock()
    service.physical_address_service.read_by_qbo_id.return_value = None
    service.physical_address_service.create.side_effect = [
        SimpleNamespace(id=910),  # _bill
        SimpleNamespace(id=911),  # _ship
    ]

    job = customer_service_module.QboCustomerExternalSchema(
        Id="J-7", SyncToken="0", DisplayName="BD - 4527 Beacon Dr.", Job=True,
        Active=True, BillAddr=OWNER_MAILING,
        ShipAddr={"Line1": "12 Job Site Rd.", "City": "Nashville", "PostalCode": "37220"},
    )

    with patch(
        f"{customer_service_module.__name__}.QboCustomerClient",
        return_value=_client_cm([job]),
    ):
        service.sync_from_qbo(realm_id=REALM, sync_to_modules=False)

    kwargs = repo.create.call_args.kwargs
    assert kwargs["bill_addr_id"] == 910, (
        "the job's BillAddr FK no longer reaches the staging row -- "
        "CustomerProjectConnector._own_billing_address_id reads exactly this"
    )
    assert kwargs["ship_addr_id"] == 911, (
        "the job's ShipAddr FK no longer reaches the staging row -- the project "
        "SHIPPING slot has no other source"
    )

    minted = [
        call.kwargs["qbo_id"]
        for call in service.physical_address_service.create.call_args_list
    ]
    assert minted == ["J-7_bill", "J-7_ship"]


# ===========================================================================
# Section 3 — the check ph3a asked for: is the PARENT fallback really dead?
# ===========================================================================

def test_the_parent_staging_fallback_cannot_fire_from_the_pull():
    """✅ CONFIRMED DEAD -- for the parent half only.

    `CustomerCustomerConnector._own_billing_address` falls back to the staging
    row when `external_customer is None`. It cannot happen on the pull path:
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
    payload wins, so the parent half already ignores the rows ph3b will drop.

    The staging service is a Mock that must stay untouched -- an un-called seam
    is how "reads the payload" stays pinned rather than merely refactored.
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
