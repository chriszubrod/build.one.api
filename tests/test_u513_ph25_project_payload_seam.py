"""U-513 ph2.5 — the JOB half moves onto the inline QBO payload.

WHAT THIS UNIT CHANGED
----------------------
`CustomerProjectConnector` was the last live reader of `qbo.PhysicalAddress` in
the customer family, in two places the production pull actually reaches:

    `_own_billing_address_id`  -> `qbo_customer.bill_addr_id`   (billing Link 1)
    `_sync_addresses`          -> `qbo_customer.ship_addr_id`   (the WHOLE
                                                                 shipping slot)

Both now project from the ORIGINAL QBO Customer payload's inline `BillAddr` /
`ShipAddr`, threaded through `QboCustomerService._sync_to_projects` by the same
closure `_sync_to_customers` has used since ph1. The staging read survives as a
fallback for the two callers that genuinely have no payload (a bare
`sync_from_qbo_customer(row)` and `heal_missing_mapping`) and dies with the
table in ph3b.

WHY IT HAD TO BE PINNED RATHER THAN DESCRIBED
---------------------------------------------
Every read on this path is blank-guarded and failure-isolated, and
`UpdateQboCustomerByQboId` COALESCES the address FKs
(`CASE WHEN @BillAddrId IS NULL THEN [BillAddrId] ELSE @BillAddrId END`), so a
half-finished version of this change fails SILENTLY: job rows keep pointing at
staging rows nothing refreshes, a QBO address edit stops propagating, and the
suite stays green. The assertions below are written against the OUTCOME (which
`dbo.Address` id reaches which `ProjectAddress` slot) rather than against a
called mock, for exactly that reason.

THE ASYMMETRY, restated because it is the half most likely to rot:
BILLING inherits (own bill -> parent bill). SHIPPING NEVER DOES, in either
branch. A parent's address is a SIBLING project's street — wrong for 16 of the
61 projects U-506 P1 covered — and the payload move does not soften that: the
threaded payload is THIS job's, and there is no parent payload in scope to fall
back to even if someone wanted one.

Pure logic throughout: the U-506 P1 harness's in-memory fakes, no live DB.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.customer.business import service as customer_service_module
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.connector.customer.business.service import (
    billing_address_qbo_id,
)
from integrations.intuit.qbo.customer.connector.project.business.service import (
    ADDRESS_TYPE_BILLING,
    ADDRESS_TYPE_SHIPPING,
    CustomerProjectConnector,
    shipping_address_qbo_id,
)
from integrations.intuit.qbo.customer.external.schemas import (
    QboCustomer as QboCustomerExternalSchema,
)

# Reuse the U-506 P1 harness rather than building a second set of fakes for the
# same connector -- a divergent copy is how one of them quietly stops modelling
# the shipped shape. It already gets the details this unit turns on right:
# staging vs dbo id keyspaces kept disjoint, realm scoping that fails closed,
# ProjectAddress as an observable in-memory table, and the parent's `_ship`
# address permanently wired in as a trap.
from tests.test_u506_p1_project_address_parent_fallback import (
    HAND_SET_ADDRESS_ID,
    PARENT_BILL_ADDRESS_ID,
    PARENT_SHIP_QBO_ID,
    PROJECT_ID,
    REAL_OWN_BILL,
    REAL_OWN_SHIP,
    REAL_PARENT_BILL_ADDRESS,
    SIBLING_STREET_ADDRESS,
    _FakeAddressConnector,
    _FakeAddressService,
    _FakeProjectAddressService,
    _FakeStagingAddressService,
    _dbo_address_id,
    _qbo_customer,
    _staged,
)

REALM = "9130353016965726"

# What `_qbo_customer()` stamps. Derived, not hand-copied: the payload's `Id`
# must equal the staging row's `QboId` or the cross-wiring guard refuses it, and
# a drifting literal would silently turn every payload test into a staging test.
JOB_QBO_ID = _qbo_customer().qbo_id

# ⚠️ A FOURTH disjoint id block. 9xx = qbo.PhysicalAddress staging,
# 1xxx = `_dbo_address_id` (what the STAGING branch mints), 2xxx = the parent's
# dbo.Address. 3xxx is what the PAYLOAD branch mints, so no assertion in this
# file can pass by collision with the branch it is meant to rule out.
PAYLOAD_ADDRESS_ID_BASE = 3000

# The job's own two addresses as QBO hands them back INLINE. Deliberately
# DIFFERENT streets from the staging rows behind REAL_OWN_BILL / REAL_OWN_SHIP,
# so "came from the payload" is observable in the content and not only in which
# seam was touched.
PAYLOAD_BILL = {
    "Line1": "4527 Beacon Dr. (payload)",
    "Line2": "Unit 2",
    "City": "Nashville",
    "CountrySubDivisionCode": "TN",
    "PostalCode": "37220",
}
PAYLOAD_SHIP = {
    "Line1": "12 Job Site Rd. (payload)",
    "City": "Nashville",
    "CountrySubDivisionCode": "TN",
    "PostalCode": "37220",
}
# QBO's placeholder: the object shell for a customer with no address on file.
BLANK_ADDR = {"Line1": "   ", "Line2": "Suite 200", "City": "", "CountrySubDivisionCode": "TN"}


# --------------------------------------------------------------------------
# Harness: the U-506 fakes plus the seam ph2.5 added
# --------------------------------------------------------------------------

def _external(*, qbo_id=JOB_QBO_ID, bill_addr=None, ship_addr=None):
    """The ORIGINAL QBO payload, built from an alias dict exactly as
    `QboCustomerQueryResponse.get_customers` builds it -- so the
    `BillAddr`/`ShipAddr` -> `bill_addr`/`ship_addr` alias mapping this unit
    depends on is exercised, not assumed."""
    payload = {
        "Id": qbo_id, "SyncToken": "0", "DisplayName": "BD - 4527 Beacon Dr.",
        "Job": True, "Active": True,
    }
    if bill_addr is not None:
        payload["BillAddr"] = bill_addr
    if ship_addr is not None:
        payload["ShipAddr"] = ship_addr
    return QboCustomerExternalSchema(**payload)


class _ExtendableStagingService(_FakeStagingAddressService):
    """The U-506 staging fake, plus rows a single test supplies. Used only by
    the shared-blankness test, which has to drive the STAGING branch over the
    same arbitrary field values it drives the PAYLOAD branch over."""

    def __init__(self, extra):
        super().__init__()
        self.extra = extra

    def read_by_id(self, id):
        if id in self.extra:
            self.read_ids.append(id)
            return self.extra[id]
        return super().read_by_id(id)


class _PayloadAwareAddressConnector(_FakeAddressConnector):
    """`_FakeAddressConnector` + `sync_address_from_external`, the staging-free
    write the payload branch calls.

    Implemented by the `physical_address` package (a sibling unit), so it is a
    fake here deliberately: what THIS unit owns is WHEN it is called, with WHAT
    identity/realm/content, and when it must NOT be called at all. The staging
    seams are inherited untouched so "the payload branch did not read staging"
    is observable on the real thing rather than on a re-implementation.
    """

    def __init__(self, extra_staging_rows=None):
        super().__init__()
        self.minted = []
        self._ids = {}
        self._extra = dict(extra_staging_rows or {})
        if self._extra:
            self.qbo_physical_address_service = _ExtendableStagingService(self._extra)

    def sync_address_from_external(self, **kwargs):
        self.minted.append(kwargs)
        qbo_id = kwargs["qbo_id"]
        if qbo_id not in self._ids:
            self._ids[qbo_id] = PAYLOAD_ADDRESS_ID_BASE + len(self._ids) + 1
        return SimpleNamespace(id=self._ids[qbo_id])

    def sync_from_qbo_to_address(self, qbo_physical_address_id):
        if qbo_physical_address_id in self._extra:
            self.synced.append(qbo_physical_address_id)
            return SimpleNamespace(id=_dbo_address_id(qbo_physical_address_id))
        return super().sync_from_qbo_to_address(qbo_physical_address_id)

    # -- observables -------------------------------------------------------
    def minted_qbo_ids(self):
        return [m["qbo_id"] for m in self.minted]

    def address_id_for(self, qbo_id):
        return self._ids[qbo_id]

    def staging_reads(self):
        return list(self.qbo_physical_address_service.read_ids)


def _build(*, parent_address=None, extra_staging_rows=None):
    """`CustomerProjectConnector` wired the U-506 way, with the payload-aware
    address connector swapped in. `parent_address` is the `dbo.Address` the
    parent's `<ref>_bill` identity resolves to; the parent's `_ship` row
    (`SIBLING_STREET_ADDRESS`) is ALWAYS present, as the trap it is."""
    addresses = [SIBLING_STREET_ADDRESS]
    if parent_address is not None:
        addresses.append(parent_address)
    return CustomerProjectConnector(
        project_service=Mock(),
        project_address_service=_FakeProjectAddressService(),
        address_connector=_PayloadAwareAddressConnector(extra_staging_rows),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
        address_service=_FakeAddressService(addresses=addresses),
    )


def _links_of_type(connector, address_type_id, project_id=PROJECT_ID):
    return [
        address_id
        for type_id, address_id in connector.project_address_service.links(project_id)
        if type_id == address_type_id
    ]


# ===========================================================================
# Section 1 — the two readers now resolve from the payload
# ===========================================================================

def test_the_jobs_own_billing_address_is_projected_from_the_payload_not_staging():
    """Billing Link 1.

    Built with the staging FK POPULATED and pointing at a real, non-blank row,
    and with NO parent address -- so the chain has exactly one candidate and the
    assertion cannot be satisfied by the parent fallback standing in, nor by an
    absent staging row making "didn't read staging" vacuous. If Link 1 had been
    left on staging, the link would be `_dbo_address_id(REAL_OWN_BILL)`.
    """
    connector = _build(parent_address=None)
    connector._sync_addresses(
        _qbo_customer(bill_addr_id=REAL_OWN_BILL),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL),
    )

    minted_id = connector.address_connector.address_id_for(f"{JOB_QBO_ID}_bill")
    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [minted_id]
    assert minted_id != _dbo_address_id(REAL_OWN_BILL)
    assert connector.address_connector.synced == [], (
        "the payload branch still pushed a staging id through "
        "sync_from_qbo_to_address -- Link 1 did not move"
    )
    assert REAL_OWN_BILL not in connector.address_connector.staging_reads(), (
        "the payload branch still read qbo.PhysicalAddress for its blank check"
    )
    assert connector.address_connector.minted[0]["line1"] == PAYLOAD_BILL["Line1"]


def test_the_shipping_slot_is_projected_from_the_payload_not_staging():
    """The SHIPPING slot -- the half with no vendor precedent to copy.

    Same shape as the billing test: staging FK populated and non-blank, so a
    connector that never moved would link `_dbo_address_id(REAL_OWN_SHIP)`.
    """
    connector = _build()
    connector._sync_addresses(
        _qbo_customer(ship_addr_id=REAL_OWN_SHIP),
        PROJECT_ID,
        external_customer=_external(ship_addr=PAYLOAD_SHIP),
    )

    minted_id = connector.address_connector.address_id_for(f"{JOB_QBO_ID}_ship")
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [minted_id]
    assert minted_id != _dbo_address_id(REAL_OWN_SHIP)
    assert connector.address_connector.synced == []
    assert REAL_OWN_SHIP not in connector.address_connector.staging_reads()
    assert connector.address_connector.minted[0]["line1"] == PAYLOAD_SHIP["Line1"]


def test_both_slots_resolve_from_one_payload_in_a_single_pass():
    """The real production shape: one `QboCustomer` payload carrying both
    objects, both slots linked, neither staging FK touched."""
    connector = _build()
    connector._sync_addresses(
        _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL, ship_addr=PAYLOAD_SHIP),
    )

    ac = connector.address_connector
    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [
        ac.address_id_for(f"{JOB_QBO_ID}_bill")
    ]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [
        ac.address_id_for(f"{JOB_QBO_ID}_ship")
    ]
    assert ac.synced == []
    assert ac.staging_reads() == []


# ===========================================================================
# Section 2 — ⚠️ SHIPPING NEVER INHERITS (invariant 1)
# ===========================================================================

def test_shipping_never_inherits_from_the_parent_on_the_payload_path():
    """⚠️ THE INVARIANT THIS UNIT WAS MOST LIKELY TO BREAK.

    The parent HAS a fully populated `<ref>_bill` `dbo.Address` and a non-blank
    `<ref>_ship` one, and the job's payload carries NO ShipAddr. BILLING must
    inherit (proving the parent really is reachable and non-blank here -- an
    assertion of "shipping is empty" against an unreachable parent would pass
    for the wrong reason). SHIPPING must stay empty.

    Under property semantics the parent's address is a SIBLING project's street,
    wrong for 16 of the 61 projects U-506 P1 covered.
    """
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(ship_addr=None),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [PARENT_BILL_ADDRESS_ID], (
        "the parent's billing address was NOT reachable in this test -- the "
        "shipping assertion below would pass for the wrong reason"
    )
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [], (
        "the SHIPPING slot inherited from the parent -- that is a sibling "
        "project's street on 16 of 61 projects"
    )
    assert PARENT_SHIP_QBO_ID not in connector.address_service.identity_qbo_ids()


def test_a_blank_payload_ship_address_does_not_fall_back_to_the_parent():
    """The narrower shape of the same defect: a job whose ShipAddr is QBO's
    placeholder. "Blank" must mean ABSENT and STOP, not "try the parent next"."""
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL, ship_addr=BLANK_ADDR),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == []
    assert f"{JOB_QBO_ID}_ship" not in connector.address_connector.minted_qbo_ids(), (
        "a blank inline ShipAddr minted a dbo.Address -- 191 of 799 rows are "
        "blank precisely because a writer keyed on presence"
    )
    assert PARENT_SHIP_QBO_ID not in connector.address_service.identity_qbo_ids()


# ===========================================================================
# Section 3 — the billing CHAIN is unchanged (invariant 2)
# ===========================================================================

def test_the_payloads_ship_addr_is_never_a_billing_candidate():
    """⚠️ The trap the payload path newly creates.

    On the staging path `own ship` was a separate FK; on the payload path it
    sits in the SAME object as `bill_addr`, one attribute away. U-506 P2 removed
    both ship links from the billing chain deliberately -- a job's ShipAddr is
    the SITE, and the slot means the OWNER'S MAILING address.

    Blank BillAddr + real ShipAddr + a real parent: billing must inherit the
    PARENT's address, never the site sitting right next to it in the payload.
    """
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=BLANK_ADDR, ship_addr=PAYLOAD_SHIP),
    )

    ship_id = connector.address_connector.address_id_for(f"{JOB_QBO_ID}_ship")
    billing = _links_of_type(connector, ADDRESS_TYPE_BILLING)
    assert billing == [PARENT_BILL_ADDRESS_ID]
    assert ship_id not in billing, (
        "the job's SITE address was linked into the OWNER-mailing billing slot"
    )


def test_a_blank_payload_bill_with_no_parent_links_no_billing_address():
    """The same trap with the parent removed, so nothing else can absorb the
    ShipAddr: the chain must resolve NOTHING rather than reach sideways."""
    connector = _build(parent_address=None)
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=BLANK_ADDR, ship_addr=PAYLOAD_SHIP),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == []
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [
        connector.address_connector.address_id_for(f"{JOB_QBO_ID}_ship")
    ]


def test_the_jobs_own_payload_bill_outranks_the_parent():
    """Chain ORDER, on the payload path: own bill -> parent bill."""
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [
        connector.address_connector.address_id_for(f"{JOB_QBO_ID}_bill")
    ]


# ===========================================================================
# Section 4 — ONE blankness predicate across BOTH branches (invariant 3)
# ===========================================================================

BLANKNESS_CASES = [
    pytest.param(None, None, None, False, id="empty"),
    pytest.param("", "", "", False, id="empty-strings"),
    pytest.param("  ", "\t", " \n", False, id="whitespace-is-not-content"),
    pytest.param("1539 Old Hillsboro Road", None, None, True, id="line1-only"),
    pytest.param(None, "Franklin", None, True, id="city-only"),
    pytest.param(None, None, "37064", True, id="postal-only"),
]

CUSTOM_STAGING_ID = 950  # outside STAGING_ROWS; supplied per-test


@pytest.mark.parametrize("line1, city, postal, expect_link", BLANKNESS_CASES)
def test_blankness_is_the_same_decision_in_both_branches(line1, city, postal, expect_link):
    """⚠️ Both branches over the SAME field values, asserted EQUAL.

    A payload branch that hand-rolled its own blank test -- say by adding
    `line2` or the state code as content -- would diverge here on the
    `line2`/state cases below, and production behaviour would then depend on
    which branch happened to run. The single shared rule is
    `address_fields_are_blank`; this is what keeps it single.
    """
    payload_conn = _build(parent_address=None)
    payload_conn._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(
            bill_addr={"Line1": line1, "City": city, "PostalCode": postal}
        ),
    )
    payload_linked = bool(_links_of_type(payload_conn, ADDRESS_TYPE_BILLING))

    staging_conn = _build(
        parent_address=None,
        extra_staging_rows={
            CUSTOM_STAGING_ID: _staged(
                CUSTOM_STAGING_ID, line1=line1, city=city, postal_code=postal
            )
        },
    )
    staging_conn._sync_addresses(_qbo_customer(bill_addr_id=CUSTOM_STAGING_ID), PROJECT_ID)
    staging_linked = bool(_links_of_type(staging_conn, ADDRESS_TYPE_BILLING))

    assert payload_linked == staging_linked == expect_link, (
        f"the two branches disagree on whether "
        f"(line1={line1!r}, city={city!r}, postal={postal!r}) is an address: "
        f"payload={payload_linked}, staging={staging_linked}"
    )


@pytest.mark.parametrize("line2, state", [("Suite 200", "TN"), (None, "TN"), ("Suite 200", None)])
def test_line2_and_state_are_not_content_in_either_branch(line2, state):
    """The specific divergence the shared predicate exists to prevent: neither
    `line2` nor the state code is routable on its own, and BOTH branches must
    say so. Split out from the parametrize above because these are the fields a
    hand-rolled payload check is most likely to include by accident."""
    payload_conn = _build(parent_address=None)
    payload_conn._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(
            bill_addr={"Line2": line2, "CountrySubDivisionCode": state}
        ),
    )
    staging_conn = _build(
        parent_address=None,
        extra_staging_rows={
            CUSTOM_STAGING_ID: _staged(CUSTOM_STAGING_ID, line2=line2, state=state)
        },
    )
    staging_conn._sync_addresses(_qbo_customer(bill_addr_id=CUSTOM_STAGING_ID), PROJECT_ID)

    assert _links_of_type(payload_conn, ADDRESS_TYPE_BILLING) == []
    assert _links_of_type(staging_conn, ADDRESS_TYPE_BILLING) == []
    assert payload_conn.address_connector.minted == []


def test_a_blank_payload_address_mints_nothing_in_either_slot():
    """Blank means ABSENT. Never mint a `dbo.Address`, never link one -- 191 of
    799 rows are blank because the pre-U-506 writer keyed on presence."""
    connector = _build(parent_address=None)
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=BLANK_ADDR, ship_addr=BLANK_ADDR),
    )

    assert connector.address_connector.minted == []
    assert connector.project_address_service.links() == []


# ===========================================================================
# Section 5 — degradation: no payload, partial map, mis-paired payload
# ===========================================================================

def test_a_missing_payload_entry_degrades_to_the_staging_read():
    """`external_by_id.get(row.qbo_id)` returning None must not crash and must
    not silently blank both slots -- it must take the transitional staging path,
    which is the whole reason that path is still standing."""
    connector = _build(parent_address=None)
    connector._sync_addresses(
        _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP),
        PROJECT_ID,
        external_customer=None,
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [_dbo_address_id(REAL_OWN_BILL)]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [_dbo_address_id(REAL_OWN_SHIP)]
    assert connector.address_connector.minted == [], (
        "the staging branch went through the payload writer"
    )


def test_heal_missing_mapping_still_reaches_the_staging_path():
    """`heal_missing_mapping` binds a name-matched Project from an INVOICE pull
    and genuinely has no Customer payload in scope. It is the second of the two
    remaining staging callers, and it must keep resolving addresses until ph3b
    drops the table."""
    connector = _build(parent_address=None)
    project = SimpleNamespace(id=PROJECT_ID, public_id="pub-p88", qbo_id=None, realm_id=None)
    connector.project_service.read_by_name.return_value = project
    connector.project_service.read_by_id.return_value = project
    connector.project_service.read_by_qbo_identity.return_value = None

    connector.heal_missing_mapping(
        _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [_dbo_address_id(REAL_OWN_BILL)]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [_dbo_address_id(REAL_OWN_SHIP)]


def test_a_payload_for_a_DIFFERENT_customer_is_refused_and_falls_back_to_staging():
    """Defense in depth against a mis-keyed map. Taking the ADDRESS off one
    customer's payload while writing it under ANOTHER customer's synthetic
    identity is silent cross-wiring -- a project would render a stranger's
    street on a payment request with nothing raised. Mirrors
    `VendorVendorConnector._bill_address_from_payload`'s refusal, and it refuses
    BOTH slots, not just the one the vendor connector has.
    """
    connector = _build(parent_address=None)
    connector._sync_addresses(
        _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP),
        PROJECT_ID,
        external_customer=_external(
            qbo_id="SOMEONE-ELSE", bill_addr=PAYLOAD_BILL, ship_addr=PAYLOAD_SHIP
        ),
    )

    assert connector.address_connector.minted == [], (
        "a payload belonging to another customer was projected under THIS job's "
        "synthetic identity"
    )
    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [_dbo_address_id(REAL_OWN_BILL)]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [_dbo_address_id(REAL_OWN_SHIP)]


def test_a_payload_failure_cannot_fail_the_project_projection():
    """Failure isolation is unchanged, and now also covers the payload branch.
    An address problem must never hold the pull watermark -- that would block
    EVERY customer and project projection over one address."""
    connector = _build(parent_address=None)
    connector.address_connector.sync_address_from_external = Mock(
        side_effect=RuntimeError("dbo.Address write failed")
    )

    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL, ship_addr=PAYLOAD_SHIP),
    )  # must not raise

    assert connector.project_address_service.links() == []


# ===========================================================================
# Section 6 — identity is unchanged (invariant 5) + realm scoping
# ===========================================================================

def test_identity_is_still_the_synthetic_bill_and_ship_strings():
    """The whole reason ph2.5 needs no data migration: `<job QboId>_bill` and
    `<job QboId>_ship` are byte-identical to what the staging hop stamped, so
    the existing `dbo.Address` rows are MATCHED, not re-keyed.

    Realm is pinned alongside: it comes from the staging row and is passed
    through unmodified, and a mint under the wrong realm would be INVISIBLE --
    the row exists, the fail-closed identity read simply never finds it.
    """
    connector = _build()
    connector._sync_addresses(
        _qbo_customer(),
        PROJECT_ID,
        external_customer=_external(bill_addr=PAYLOAD_BILL, ship_addr=PAYLOAD_SHIP),
    )

    minted = {m["qbo_id"]: m for m in connector.address_connector.minted}
    assert set(minted) == {f"{JOB_QBO_ID}_bill", f"{JOB_QBO_ID}_ship"}
    assert billing_address_qbo_id(JOB_QBO_ID) == f"{JOB_QBO_ID}_bill"
    assert shipping_address_qbo_id(JOB_QBO_ID) == f"{JOB_QBO_ID}_ship"
    for m in minted.values():
        assert m["realm_id"] == REALM
        assert m["source_ref"] == f"QboCustomer:{JOB_QBO_ID}"


def test_the_payload_fields_map_onto_the_address_columns_verbatim():
    """`line2` and the state code are not CONTENT for the blankness rule, but
    they are still DATA: dropping them on the payload path would quietly
    downgrade every address to street/city/zip."""
    connector = _build()
    connector._sync_addresses(
        _qbo_customer(), PROJECT_ID, external_customer=_external(bill_addr=PAYLOAD_BILL),
    )

    m = connector.address_connector.minted[0]
    assert m["line1"] == PAYLOAD_BILL["Line1"]
    assert m["line2"] == PAYLOAD_BILL["Line2"]
    assert m["city"] == PAYLOAD_BILL["City"]
    assert m["country_sub_division_code"] == PAYLOAD_BILL["CountrySubDivisionCode"]
    assert m["postal_code"] == PAYLOAD_BILL["PostalCode"]


# ===========================================================================
# Section 7 — stale-link clearing survives the move (invariant 4)
# ===========================================================================

def test_a_payload_chain_that_resolves_nothing_clears_a_connector_minted_link():
    """A re-parented job whose new owner has no address: the chain resolves
    None while the BILLING link still points at the FORMER owner's address.
    Pinned on the STAGING path by U-506 P2; pinned here on the PAYLOAD path,
    because that is the branch production now takes."""
    connector = _build(parent_address=None)
    stale = connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=4242, address_type_id=ADDRESS_TYPE_BILLING,
    )

    connector._sync_addresses(
        _qbo_customer(), PROJECT_ID, external_customer=_external(bill_addr=BLANK_ADDR),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == []
    assert connector.project_address_service.deleted == [stale.id]


def test_a_hand_set_billing_address_is_never_cleared_on_the_payload_path():
    """The other half, and the one that records a real defect: a human-entered
    `dbo.Address` carries no `qbo_id`, and the connector must never clobber it."""
    connector = _build(parent_address=None)
    connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=HAND_SET_ADDRESS_ID,
        address_type_id=ADDRESS_TYPE_BILLING,
    )

    connector._sync_addresses(
        _qbo_customer(), PROJECT_ID, external_customer=_external(bill_addr=BLANK_ADDR),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [HAND_SET_ADDRESS_ID]
    assert connector.project_address_service.deleted == []


# ===========================================================================
# Section 8 — the seam: the pull threads the map into the JOB connector
# ===========================================================================

def test_sync_to_projects_threads_the_payload_through_the_closure():
    """The closure is the whole mechanism. `project_records` is shared by 10
    call sites across 8 other QBO families and takes a strict one-argument
    `project_one`, so the extra argument is bound HERE -- exactly as
    `_sync_to_customers` has done since ph1 -- rather than by widening a
    signature every other family depends on.
    """
    from integrations.intuit.qbo.base.sync_outcome import SyncOutcome

    job = _qbo_customer()
    payload = _external(bill_addr=PAYLOAD_BILL, ship_addr=PAYLOAD_SHIP)
    outcome = SyncOutcome.for_service_pull(synced=[job], fetched=1)

    seen = []
    project_connector = Mock()
    project_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: seen.append((row.qbo_id, external))
        or SimpleNamespace(id=1)
    )

    with patch(
        f"{customer_service_module.__name__}.with_retry", side_effect=lambda fn, *a, **k: fn(*a)
    ), patch(
        f"{customer_service_module.__name__}.pace_batch"
    ), patch(
        f"{customer_service_module.__name__}.CustomerProjectConnector",
        return_value=project_connector,
    ):
        QboCustomerService(repo=MagicMock())._sync_to_projects(
            [job], outcome, {job.qbo_id: payload}
        )

    assert seen == [(job.qbo_id, payload)]


def test_sync_to_projects_without_a_map_still_projects():
    """`_sync_to_projects(rows, outcome)` -- the two-argument call other suites
    make, and the shape `project_records`' 10 other call sites rely on staying
    valid. It must degrade to `external=None`, not raise."""
    from integrations.intuit.qbo.base.sync_outcome import SyncOutcome

    job = _qbo_customer()
    outcome = SyncOutcome.for_service_pull(synced=[job], fetched=1)

    seen = []
    project_connector = Mock()
    project_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: seen.append(external) or SimpleNamespace(id=1)
    )

    with patch(
        f"{customer_service_module.__name__}.with_retry", side_effect=lambda fn, *a, **k: fn(*a)
    ), patch(
        f"{customer_service_module.__name__}.pace_batch"
    ), patch(
        f"{customer_service_module.__name__}.CustomerProjectConnector",
        return_value=project_connector,
    ):
        QboCustomerService(repo=MagicMock())._sync_to_projects([job], outcome)
        QboCustomerService(repo=MagicMock())._sync_to_projects([job], outcome, None)

    assert seen == [None, None]


def test_project_records_signature_is_untouched():
    """⛔ The seam that must NOT have moved. `project_records` is shared by 10
    call sites across 8 other QBO families; widening it to carry this family's
    payload is the change this unit deliberately did not make, and the closure
    above is what made that possible."""
    import inspect

    from integrations.intuit.qbo.base.sync_outcome import project_records

    params = list(inspect.signature(project_records).parameters)
    assert params == ["records", "outcome", "label", "project_one", "logger"], (
        "project_records' signature changed -- 10 call sites across 8 other QBO "
        "families depend on it; the payload belongs in the caller's closure"
    )
