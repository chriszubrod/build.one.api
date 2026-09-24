"""U-506 P1 — the WRITER half of the blank Draw-Request-"To:"-block fix.

P0 hardened the READER (`entities/invoice/api/router.py`: blank check, type
filter, deterministic order). It could only ever render what the writer had
stored, and the writer had stored almost nothing: only 2 of 139 `dbo.Project`
rows carried a non-blank address, while 191 of 799 `dbo.Address` rows were
completely blank. 1,012 invoices therefore rendered a name-only "To:" block.

Root cause — and what it is NOT:

  ⛔ NOT the `Job=false` gate at the top of `sync_from_qbo_customer`.
     `scripts/sync_qbo_customer.py` partitions the pull BEFORE the connector
     (`parent_customers` -> CustomerCustomerConnector, `job_customers` ->
     CustomerProjectConnector), so a Job=false customer never reaches that
     gate. It is a defensive assert on a path nothing takes, and lifting it
     would let a parent customer mint a `dbo.Project`.

  ✅ `_sync_addresses` keyed on ID PRESENCE, not CONTENT. 138 job customers
     reference 32 staging addresses of which only 3 have any content; the
     other 29 are QBO placeholder rows — an `Id` with empty
     Line1/City/PostalCode — which passed truthiness, were minted into
     `dbo.Address` as blank rows, and were linked.

The fix is a parent-address fallback on the BILLING slot, first NON-BLANK
wins: own bill -> own ship -> parent bill -> parent ship. Measured coverage:
2/139 projects and 64/1,012 invoices before; 63/139 and 706/1,012 (70% of
invoices) after. The residual 76 is irreducible — those parents' BillAddrId
points at a blank staging row too.

⚠️ The SHIPPING slot is the deliberate asymmetry and the reason
`test_shipping_slot_never_inherits_from_the_parent` exists: it stays job-only
(plus the new blank guard) and must NEVER inherit. Under PROPERTY semantics
the parent's address is a SIBLING project's street, which would be wrong for
16 of the 61 newly-covered projects (e.g. `BD - 4527 Beacon Dr.` inheriting
`1539 Old Hillsboro Road - OHR2`). Shipping is the only place a future
property-address feature belongs. The billing slot's OWNER-mailing reading is
stated once, at `PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING`, and
`test_semantics_flag_is_the_single_flip_point` pins that flipping it is a
one-line change rather than a hunt through the module.

These tests drive `_sync_addresses` directly with in-memory staging fakes —
the identity fast path, its locks and the name-match machinery are covered
exhaustively by test_u311/u303/u297/u276 and are not what this unit changes.

⚠️ RE-POINTED BY U-513. The BEHAVIOUR pinned here is unchanged, but the
PARENT link's source is not: it used to be the parent's `qbo.Customer`
STAGING row (`parent.bill_addr_id`), and it is now `dbo.Address` read by the
parent's synthetic `<parent QboId>_bill` identity. The old shape was circular
— `sync_from_qbo_to_address` on the parent's staging id is what MINTED the
very rows this fallback read — so with `qbo.PhysicalAddress` being sunset a
brand-new parent would have ended up with no address at all. The mint moved
to `CustomerCustomerConnector` (see test_u513_customer_address_from_payload),
and this side now only READS. Every assertion about WHICH address wins,
blankness, the 16-project shipping guard, stale-link clearing and memoization
is preserved verbatim in intent; only the seam being asserted changed.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.connector.project.business import service as svc
from integrations.intuit.qbo.customer.connector.project.business.service import (
    ADDRESS_TYPE_BILLING,
    ADDRESS_TYPE_SHIPPING,
    PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING,
    CustomerProjectConnector,
)
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress

REALM = "9130353016965726"
PARENT_REF = "P-1"
PARENT_BILL_QBO_ID = f"{PARENT_REF}_bill"
PROJECT_ID = 88
SIBLING_PROJECT_ID = 89

# Staging ids. The 9xx block is deliberately NOT the dbo.Address id block:
# `qbo.PhysicalAddress.Id` and `dbo.Address.Id` are separate keyspaces, and a
# test that conflated them would pass while production crossed them.
#
# U-513: the PARENT's staging ids are gone from this block — the parent link no
# longer touches qbo.PhysicalAddress at all. Only the JOB's own two slots still
# resolve through staging.
BLANK_OWN_BILL = 900
BLANK_OWN_SHIP = 901
REAL_OWN_BILL = 910
REAL_OWN_SHIP = 911
MISSING_STAGING_ROW = 999  # an id with no staging row behind it at all


def _staged(id, line1=None, city=None, postal_code=None, line2=None, state=None):
    """A real QboPhysicalAddress, not a SimpleNamespace — a renamed field must
    break these tests rather than silently diverge from the shipped dataclass
    (the blankness predicate reads line1/city/postal_code by attribute)."""
    return QboPhysicalAddress(
        id=id,
        public_id=f"pub-{id}",
        row_version="cm93dmVyMDE=",  # base64 of b"rowver01"
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-01 00:00:00",
        qbo_id=str(id),
        realm_id=REALM,
        line1=line1,
        line2=line2,
        city=city,
        country=None,
        country_sub_division_code=state,
        postal_code=postal_code,
    )


# The live shapes, verbatim in kind:
#   - the 29 placeholder rows: an Id, everything else empty
#   - the 3 real ones: a street + city + zip
STAGING_ROWS = {
    # QBO's placeholder: None-valued, and the whitespace-only variant.
    BLANK_OWN_BILL: _staged(BLANK_OWN_BILL),
    BLANK_OWN_SHIP: _staged(BLANK_OWN_SHIP, line1="   ", city="", postal_code=None),
    REAL_OWN_BILL: _staged(REAL_OWN_BILL, line1="4527 Beacon Dr.", city="Nashville", postal_code="37220"),
    REAL_OWN_SHIP: _staged(REAL_OWN_SHIP, line1="12 Job Site Rd.", city="Nashville", postal_code="37220"),
}


def _dbo_address_id(staging_id):
    """What `sync_from_qbo_to_address` mints/returns for a staging id. Offset on
    purpose so an assertion can never accidentally match the staging id."""
    return 1000 + staging_id


# ── The PARENT link's new source (U-513): dbo.Address, not qbo staging ───────
# A third id block again, deliberately disjoint from both the 9xx staging block
# and the 1xxx `_dbo_address_id` block, so no assertion can pass by collision.
PARENT_BILL_ADDRESS_ID = 2001
BLANK_PARENT_BILL_ADDRESS_ID = 2002


def _dbo_address(id, *, street_one=None, city=None, zip=None, qbo_id=PARENT_BILL_QBO_ID, realm_id=REALM):
    """A real `dbo.Address`, not a SimpleNamespace — same reasoning as `_staged`:
    `_is_blank_dbo_address` reads street_one/city/zip by attribute, and a renamed
    column must break these tests rather than silently read as blank."""
    from entities.address.business.model import Address, Country

    return Address(
        id=id,
        public_id=f"pub-a{id}",
        row_version="cm93dmVyMDE=",
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-01 00:00:00",
        street_one=street_one,
        street_two=None,
        city=city,
        state=None,
        zip=zip,
        country=Country.UNITED_STATES,
        qbo_id=qbo_id,
        realm_id=realm_id,
    )


# The owner's real remit-to, as it sits in dbo.Address after the parent
# customer's own projection minted it.
REAL_PARENT_BILL_ADDRESS = _dbo_address(
    PARENT_BILL_ADDRESS_ID, street_one="1539 Old Hillsboro Road", city="Franklin", zip="37064"
)
# ⚠️ One of the 191 blank rows the pre-U-506 connector minted. It EXISTS, so an
# identity read finds it — only the blankness test keeps it out of the slot.
BLANK_PARENT_BILL_ADDRESS = _dbo_address(BLANK_PARENT_BILL_ADDRESS_ID)

# ⚠️ THE TRAP. The parent's SHIP address — a SIBLING project's street — is
# present in dbo.Address under `<parent>_ship` and is wired into EVERY connector
# these tests build. It is non-blank and would resolve cleanly if the billing
# chain ever reached for it, which is precisely why it is always there: U-506 P2
# removed both ship links from the billing chain deliberately, and re-adding one
# must fail loudly rather than quietly start mailing draw requests to the
# neighbouring lot.
PARENT_SHIP_ADDRESS_ID = 2003
PARENT_SHIP_QBO_ID = f"{PARENT_REF}_ship"
SIBLING_STREET_ADDRESS = _dbo_address(
    PARENT_SHIP_ADDRESS_ID,
    street_one="99 Sibling Street",
    city="Franklin",
    zip="37064",
    qbo_id=PARENT_SHIP_QBO_ID,
)


class _FakeStagingAddressService:
    """Stands in for `QboPhysicalAddressService` — the read the blankness test
    needs. Returns None for an id with no row, which is the case
    `_is_blank_staged_address` must treat as blank rather than explode on."""

    def __init__(self):
        self.read_ids = []

    def read_by_id(self, id):
        self.read_ids.append(id)
        return STAGING_ROWS.get(id)


class _FakeAddressConnector:
    """Stands in for `PhysicalAddressAddressConnector`. Exposes the two seams
    this connector uses: the staging service (blankness) and the sync (mint)."""

    def __init__(self):
        self.qbo_physical_address_service = _FakeStagingAddressService()
        self.synced = []

    def sync_from_qbo_to_address(self, qbo_physical_address_id):
        if qbo_physical_address_id not in STAGING_ROWS:
            # The real connector raises on a missing staging row — a chain that
            # ever reaches here with one is a bug this fake makes loud.
            raise ValueError(f"QboPhysicalAddress with ID {qbo_physical_address_id} not found")
        self.synced.append(qbo_physical_address_id)
        return SimpleNamespace(id=_dbo_address_id(qbo_physical_address_id))


HAND_SET_ADDRESS_ID = 777  # a human-entered dbo.Address: qbo_id IS NULL


class _FakeAddressService:
    """Stands in for `AddressService`. Two seams:

    `read_by_id` models the ONE field the stale-link check turns on:
    `dbo.Address.qbo_id`. Connector-minted rows carry one because the identity
    fast path stamps it; a hand-entered address has none. Wiring this fake is
    load-bearing: with the real AddressService the connector attempts a live DB
    read, conftest blocks it, and `_sync_addresses`'s `except Exception`
    swallows the failure -- so the stale-link test would PASS for the wrong
    reason.

    `read_by_qbo_identity` is U-513's new parent seam, and it reproduces
    `ReadAddressByQboIdAndRealmId`'s FAIL-CLOSED realm scoping: a row is
    returned only when BOTH the synthetic qbo_id AND the realm match. A fake
    that ignored realm would let `test_parent_memo_is_keyed_by_realm_as_well_
    as_ref` pass while production served one realm's owner address to another
    realm's job.
    """

    def __init__(self, addresses=()):
        self.reads = []
        self.identity_reads = []
        self._by_identity = {a.qbo_id: a for a in addresses}

    def read_by_id(self, id):
        self.reads.append(id)
        if id == HAND_SET_ADDRESS_ID:
            return SimpleNamespace(id=id, qbo_id=None)
        return SimpleNamespace(id=id, qbo_id=f"A-{id}")

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        self.identity_reads.append((qbo_id, realm_id))
        found = self._by_identity.get(qbo_id)
        if found is None:
            return None
        if (realm_id or "") != (found.realm_id or ""):
            # Realm scoping fails CLOSED, exactly as the sproc's
            # `(RealmId = @RealmId) OR (both NULL)` predicate does.
            return None
        return found

    def identity_qbo_ids(self):
        return [qbo_id for qbo_id, _realm in self.identity_reads]


class _FakeProjectAddressService:
    """In-memory ProjectAddress table: enough for `_ensure_project_address`'s
    read -> create-or-repoint sequence, so "no link was written" is observable
    as an empty table rather than as an un-called mock."""

    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.deleted = []
        self.repo = SimpleNamespace(
            update_by_id=self._update_by_id, delete_by_id=self._delete_by_id
        )

    def read_by_project_id(self, project_id):
        return [r for r in self.rows if r.project_id == project_id]

    def create(self, *, project_id, address_id, address_type_id, tenant_id=None):
        row = SimpleNamespace(
            id=self._next_id, project_id=project_id, address_id=address_id,
            address_type_id=address_type_id,
        )
        self._next_id += 1
        self.rows.append(row)
        return row

    def _delete_by_id(self, id):
        self.deleted.append(id)
        self.rows = [r for r in self.rows if r.id != id]
        return None

    def _update_by_id(self, row):
        return row

    def links(self, project_id=PROJECT_ID):
        """(address_type_id, address_id) pairs for a project."""
        return [(r.address_type_id, r.address_id) for r in self.read_by_project_id(project_id)]


def _qbo_customer(
    *, id=4, bill_addr_id=None, ship_addr_id=None, parent_ref_value=PARENT_REF,
    display_name="BD - 4527 Beacon Dr.", job=True, realm_id=REALM,
):
    """A real QboCustomer — same reasoning as `_staged`."""
    return QboCustomer(
        id=id, public_id=f"pub-c{id}", row_version="cm93dmVyMDE=",
        created_datetime="2026-01-01 00:00:00", modified_datetime="2026-01-01 00:00:00",
        qbo_id=f"C-{id}", sync_token="0", realm_id=realm_id, display_name=display_name,
        title=None, given_name=None, middle_name=None, family_name=None, suffix=None,
        company_name=None, fully_qualified_name=None, level=1,
        parent_ref_value=parent_ref_value, parent_ref_name="Beacon Dr.", job=job, active=True,
        primary_email_addr=None, primary_phone=None, mobile=None, fax=None,
        bill_addr_id=bill_addr_id, ship_addr_id=ship_addr_id, balance=None,
        balance_with_jobs=None, taxable=None, notes=None, print_on_check_name=None,
    )


def _build_connector(*, parent_address=None):
    """`parent_address` is the `dbo.Address` the parent's `<ref>_bill` identity
    resolves to (U-513) — None models a parent whose own projection has never
    minted one, or no parent at all.

    `qbo_customer_repo` is still injected as a Mock, but purely so every test
    can assert it is NEVER touched: "the parent link is off qbo.Customer
    staging" is the point of the unit, and an un-called Mock is how that stays
    pinned rather than merely refactored."""
    qbo_customer_repo = Mock()
    addresses = [SIBLING_STREET_ADDRESS]
    if parent_address is not None:
        addresses.append(parent_address)
    connector = CustomerProjectConnector(
        project_service=Mock(),
        project_address_service=_FakeProjectAddressService(),
        address_connector=_FakeAddressConnector(),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=qbo_customer_repo,
        address_service=_FakeAddressService(addresses=addresses),
    )
    return connector


def _assert_off_staging(connector):
    """The unit's headline invariant: the PARENT link never reads qbo.Customer
    staging any more, and never reaches for a `_ship` identity."""
    connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    assert PARENT_SHIP_QBO_ID not in connector.address_service.identity_qbo_ids(), (
        "the billing chain reached for the parent's SHIP address -- that is a "
        "sibling project's street, not the owner's mailing address"
    )




# --------------------------------------------------------------------------
# The blankness predicate — presence is not content
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "staged, expected_blank",
    [
        (None, True),                                                  # no row at all
        (_staged(1), True),                                            # QBO's placeholder: Id only
        (_staged(2, line1="", city="", postal_code=""), True),
        (_staged(3, line1="  ", city="\t", postal_code=" \n"), True),  # whitespace is not content
        (_staged(4, line2="Suite 200", state="TN"), True),             # line2/state alone is unroutable
        (_staged(5, line1="1539 Old Hillsboro Road"), False),
        (_staged(6, city="Franklin"), False),
        (_staged(7, postal_code="37064"), False),
    ],
)
def test_blankness_is_line1_city_postal_only(staged, expected_blank):
    assert CustomerProjectConnector._is_blank_staged_address(staged) is expected_blank


@pytest.mark.parametrize(
    "address, expected_blank",
    [
        (_dbo_address(1), True),                                               # a minted-blank row
        (_dbo_address(2, street_one="", city="", zip=""), True),               # NOT NULL columns: ""
        (_dbo_address(3, street_one="  ", city="\t", zip=" \n"), True),        # whitespace is not content
        (_dbo_address(4, street_one="1539 Old Hillsboro Road"), False),
        (_dbo_address(5, city="Franklin"), False),
        (_dbo_address(6, zip="37064"), False),
    ],
)
def test_dbo_blankness_is_the_same_rule_on_the_dbo_column_names(address, expected_blank):
    """U-513 — the parent link now reads `dbo.Address`, whose columns are named
    `street_one`/`city`/`zip`, so the SAME blankness rule has to be applied
    through a second accessor.

    ⚠️ Load-bearing, not symmetry: 191 of the 799 existing dbo.Address rows are
    completely blank — minted by the pre-U-506 connector that keyed on id
    presence — so an identity read on a parent finds a row far more often than
    it finds an ADDRESS. Dropping this check would re-create the name-only "To:"
    block U-506 P1 fixed, and would additionally mask
    `_clear_stale_connector_billing_link` behind a link that merely LOOKS
    resolved.
    """
    assert CustomerProjectConnector._is_blank_dbo_address(address) is expected_blank


def test_both_blankness_accessors_share_one_rule():
    """MUTATION: re-inline either predicate with its own `or ""`/`.strip()` chain.

    Three copies of "line1 + city + postal, stripped" — staging, dbo, and the
    parent connector's own mint guard — is exactly how one of them quietly stops
    matching the others, and a mint/read pair that disagree on blankness is
    invisible: the row is written and then never chosen.
    """
    assert CustomerProjectConnector._is_blank_staged_address(
        _staged(1, line1=" ", city="", postal_code=None)
    ) is CustomerProjectConnector._is_blank_dbo_address(
        _dbo_address(1, street_one=" ", city="", zip=None)
    )


# --------------------------------------------------------------------------
# The fallback chain
# --------------------------------------------------------------------------

def test_blank_own_bill_falls_back_to_parent_bill():
    """MUTATION: revert the billing slot to `if qbo_customer.bill_addr_id:`.

    The pre-U-506 shape. The blank placeholder at BLANK_OWN_BILL passes
    truthiness, gets minted into dbo.Address as a blank row and linked — which
    is exactly how 191 of 799 dbo.Address rows became blank, and why the
    Draw Request "To:" block renders name-only.

    U-513: the parent's address now arrives from dbo.Address by its
    `<parent>_bill` identity, so nothing is synced through staging on this path
    at all — `address_connector.synced == []` is the new shape of "the parent
    link is off staging", and it is asserted rather than assumed.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_service.identity_reads == [(PARENT_BILL_QBO_ID, REALM)]
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, PARENT_BILL_ADDRESS_ID),
    ]
    _assert_off_staging(connector)


def test_a_ship_address_is_never_a_billing_candidate():
    """U-506 P2 -- THE CHAIN IS own bill -> parent bill. NO ship link, either one.

    This test previously asserted the OPPOSITE: that the parent's ShipAddr
    ("99 Sibling Street") lands in the BILLING slot. Under the decided semantic
    -- the slot is the OWNER'S MAILING address -- that was wrong by
    construction, and it is the same "sibling project's street" error the
    SHIPPING slot has always been guarded against.

    U-513 makes the guard structural AND observable: `SIBLING_STREET_ADDRESS`
    sits in dbo.Address under `<parent>_ship`, non-blank and ready to resolve,
    so re-adding a parent-ship link would immediately fill the billing slot with
    it. Nothing may ask for that identity.
    """
    connector = _build_connector(parent_address=BLANK_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=BLANK_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert PARENT_SHIP_ADDRESS_ID not in [a for _t, a in connector.project_address_service.links()]
    assert connector.project_address_service.links() == [], (
        "nothing should occupy the BILLING slot: every remaining candidate is a "
        "job-site address"
    )
    _assert_off_staging(connector)


def test_own_ship_does_not_outrank_the_parents_billing_address():
    """U-506 P2 -- the ordering bug, isolated.

    own ship used to sit at link 2, AHEAD of parent bill. So a job with a site
    address and an owner with a real remit-to rendered the SITE under
    "TO OWNER:", while the owner's mailing address sat unused in staging. The
    owner's address must win; the site must not appear in this slot at all.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, PARENT_BILL_ADDRESS_ID),
        (ADDRESS_TYPE_SHIPPING, _dbo_address_id(REAL_OWN_SHIP)),
    ], "the owner's mailing address must fill BILLING; the site belongs in SHIPPING only"
    _assert_off_staging(connector)


def test_a_reparented_job_does_not_keep_the_former_owners_address():
    """U-506 P2 -- THE P0. New owner's NAME beside the OLD owner's STREET.

    `_apply_project_fields_and_sync` repoints `project.customer_id`
    unconditionally, so moving a job under a new owner whose slots are blank
    left the BILLING link pointing at the FORMER owner's address forever. The
    packet then addressed the new owner at the previous owner's house.

    Specific to inheritance: before the parent fallback the slot could only hold
    the job's OWN address, so "stale" was at worst the same party's out-of-date
    address. Once a PARENT's address can occupy the slot, stale and
    someone-else's are the same state.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)
    connector._sync_addresses(job, PROJECT_ID)
    former = PARENT_BILL_ADDRESS_ID
    assert connector.project_address_service.links() == [(ADDRESS_TYPE_BILLING, former)]

    # Re-parent onto an owner with NOTHING -- the chain now resolves None.
    reparented = _build_connector(parent_address=None)
    reparented.project_address_service.create(
        project_id=PROJECT_ID, address_id=former, address_type_id=ADDRESS_TYPE_BILLING,
    )
    reparented._sync_addresses(job, PROJECT_ID)

    assert reparented.project_address_service.links() == [], (
        f"the former owner's address {former} survived the re-parent. The Draw "
        f"Request would render the NEW owner's name beside the OLD owner's street."
    )
    assert reparented.project_address_service.deleted, "the stale link was never cleared"


def test_a_blank_parent_address_row_is_not_linked():
    """U-513 -- the dbo-side blank guard, isolated.

    A parent whose `<parent>_bill` row EXISTS but is blank (one of the 191) must
    read as ABSENT: the chain resolves nothing and the slot is cleared, exactly
    as if no row existed at all. Without `_is_blank_dbo_address` the identity
    read's truthy row would be linked and the project would render an empty
    "To:" block that LOOKS configured.
    """
    connector = _build_connector(parent_address=BLANK_PARENT_BILL_ADDRESS)
    connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=PARENT_BILL_ADDRESS_ID,
        address_type_id=ADDRESS_TYPE_BILLING,
    )
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == []
    assert connector.project_address_service.deleted == [1]


def test_a_hand_set_billing_address_is_never_cleared():
    """The half of the old 'leave it untouched' guard that records a REAL defect.

    The connector must not clobber human data. `dbo.Address.qbo_id IS NULL`
    means a person entered it, so a blank chain leaves it exactly alone -- only
    CONNECTOR-MINTED links (qbo_id set) are ever cleared.
    """
    connector = _build_connector(parent_address=BLANK_PARENT_BILL_ADDRESS)
    connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=HAND_SET_ADDRESS_ID,
        address_type_id=ADDRESS_TYPE_BILLING,
    )
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, HAND_SET_ADDRESS_ID)
    ], "a hand-set address was cleared -- the connector must never clobber human data"
    assert connector.project_address_service.deleted == []


def test_real_own_bill_wins_over_parent():
    """MUTATION: invert precedence (parent bill first).

    The job's own address is the most specific thing QBO knows; inheriting is
    strictly the fallback. A parent-first chain would overwrite every one of
    the 2 projects that already had a correct address of their own.

    Also pins LAZINESS: with its own bill non-blank the job must not read the
    parent's dbo.Address at all.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_OWN_BILL]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
    ]
    assert connector.address_service.identity_reads == []
    _assert_off_staging(connector)


def test_no_parent_ref_value_never_reads_the_parent_address():
    """MUTATION: drop `_parent_billing_address_id`'s empty-parent_ref_value guard.

    A top-level job has nothing to inherit from. Without the guard the address
    service is asked for the identity `"None_bill"` — a pointless round trip per
    job, and one that would bind whatever a row carrying that literal returned.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None, parent_ref_value=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_service.identity_reads == []
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == []
    _assert_off_staging(connector)


def test_missing_parent_address_is_a_no_op_not_a_raise():
    """MUTATION: drop `_parent_billing_address_id`'s `if address is None` guard.

    The identity read is a plain miss when the parent has never been projected
    (a job can arrive in a watermark window before its parent). Without the
    guard that miss is an AttributeError on None — swallowed by
    `_sync_addresses`'s failure isolation into a logged error, so the resolver
    is asserted DIRECTLY here: the miss must return None, not blow up.
    """
    connector = _build_connector(parent_address=None)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    assert connector._resolve_billing_address_id(job) is None

    connector._sync_addresses(job, PROJECT_ID)
    # Exactly one read across BOTH calls — the miss is memoized (see below).
    assert connector.address_service.identity_reads == [(PARENT_BILL_QBO_ID, REALM)]
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == []
    _assert_off_staging(connector)


def test_unreadable_staging_row_is_treated_as_blank_and_falls_through():
    """An id with no staging row behind it counts as blank rather than raising
    — `sync_from_qbo_to_address` would raise on it, aborting the whole slot and
    losing the parent address that was available all along."""
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=MISSING_STAGING_ROW, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, PARENT_BILL_ADDRESS_ID),
    ]


def test_blank_everywhere_creates_no_address_and_no_project_address_link():
    """MUTATION: drop the do-nothing branch (sync `bill_addr_id` regardless).

    The irreducible residual — the projects whose parent has no usable address
    either. QBO holds nothing more for them, so the only correct action is none:
    minting a blank dbo.Address and linking it is what produced the 191 blank
    rows, and it actively harms the reader — P0's blank check would have to skip
    it anyway, but the SHIPPING slot behind it could never be reached.
    """
    connector = _build_connector(parent_address=None)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=BLANK_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == []
    assert connector.project_address_service.rows == []


def test_blank_chain_leaves_an_existing_link_untouched():
    """NARROWED by U-506 P2. Originally this covered BOTH a hand-set address and
    one "from an earlier pull when QBO still had content". The second half was
    WRONG and is now inverted: a connector-minted link whose chain resolves
    nothing is a FORMER owner's address and IS cleared (see
    test_a_reparented_job_does_not_keep_the_former_owners_address). The real
    defect this guard records -- the connector must not clobber human data --
    is kept, and is pinned harder by test_a_hand_set_billing_address_is_never_cleared:
    address 777 has no qbo_id, so it survives."""
    connector = _build_connector(parent_address=BLANK_PARENT_BILL_ADDRESS)
    connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=777, address_type_id=ADDRESS_TYPE_BILLING,
    )
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [(ADDRESS_TYPE_BILLING, 777)]


# --------------------------------------------------------------------------
# ⚠️ The 16-project guard
# --------------------------------------------------------------------------

def test_shipping_slot_never_inherits_from_the_parent():
    """MUTATION: add the same fallback chain to the SHIPPING slot.

    ⚠️ THE 16-PROJECT ERROR PATH. Billing inherits because the parent's
    BillAddr is the OWNER's mailing address (see
    PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING). Shipping has no such reading:
    for 16 of the 61 newly-covered projects the parent's address is a SIBLING
    project's street, so inheriting it would stamp `BD - 4527 Beacon Dr.` with
    `1539 Old Hillsboro Road - OHR2`. Shipping stays job-only and is the only
    place a future property-address feature belongs.

    Doubles as the shipping slot's own blank guard: the job's blank ship must
    not be minted either.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=BLANK_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_OWN_BILL]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
    ]
    assert PARENT_BILL_ADDRESS_ID not in [a for _t, a in connector.project_address_service.links()]
    _assert_off_staging(connector)


def test_shipping_slot_still_syncs_the_jobs_own_non_blank_ship_address():
    """The other half of the guard — "never inherit" must not degrade into
    "never write", or the shipping slot would be silently dead."""
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
        (ADDRESS_TYPE_SHIPPING, _dbo_address_id(REAL_OWN_SHIP)),
    ]


# --------------------------------------------------------------------------
# Memoization
# --------------------------------------------------------------------------

def test_parent_address_read_is_memoized_across_sibling_jobs():
    """MUTATION: drop the `_parent_billing_address_cache` read/write.

    ONE connector instance serves a whole pull run and sub-units of one
    property share a parent — 138 job customers resolve to 73 distinct parents
    — so an unmemoized read turns the fallback into ~one extra address
    round trip per job instead of per parent.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job_a = _qbo_customer(id=4, bill_addr_id=BLANK_OWN_BILL, display_name="BD - Unit A")
    job_b = _qbo_customer(id=5, bill_addr_id=BLANK_OWN_BILL, display_name="BD - Unit B")

    connector._sync_addresses(job_a, PROJECT_ID)
    connector._sync_addresses(job_b, SIBLING_PROJECT_ID)

    assert connector.address_service.identity_reads == [(PARENT_BILL_QBO_ID, REALM)]
    assert connector.project_address_service.links(PROJECT_ID) == [
        (ADDRESS_TYPE_BILLING, PARENT_BILL_ADDRESS_ID),
    ]
    assert connector.project_address_service.links(SIBLING_PROJECT_ID) == [
        (ADDRESS_TYPE_BILLING, PARENT_BILL_ADDRESS_ID),
    ]


def test_parent_address_miss_is_memoized_too():
    """Caches misses as well as hits, per `_parent_customer_cache`'s canonical
    shape — otherwise a parent that is genuinely absent costs a read per job,
    which is the worst case rather than the cheap one.

    U-513 widened what "miss" covers: a parent whose row exists but is BLANK
    memoizes as None too, so the blankness verdict is paid once per parent
    rather than once per job."""
    connector = _build_connector(parent_address=BLANK_PARENT_BILL_ADDRESS)
    job_a = _qbo_customer(id=4, bill_addr_id=BLANK_OWN_BILL)
    job_b = _qbo_customer(id=5, bill_addr_id=BLANK_OWN_BILL)

    connector._sync_addresses(job_a, PROJECT_ID)
    connector._sync_addresses(job_b, SIBLING_PROJECT_ID)

    assert connector.address_service.identity_reads == [(PARENT_BILL_QBO_ID, REALM)]


def test_parent_memo_is_keyed_by_realm_as_well_as_ref():
    """QBO ids are unique only WITHIN a realm — a memo keyed on the ref alone
    would serve one realm's parent address to another's job.

    Doubles as the FAIL-CLOSED realm check (U-513): the second job's realm does
    not match the row's, so `ReadAddressByQboIdAndRealmId` matches nothing and
    that project gets NO billing link — it must not inherit across realms just
    because the synthetic `<ref>_bill` string happens to collide.
    """
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job_a = _qbo_customer(id=4, bill_addr_id=BLANK_OWN_BILL, realm_id=REALM)
    job_b = _qbo_customer(id=5, bill_addr_id=BLANK_OWN_BILL, realm_id="other-realm")

    connector._sync_addresses(job_a, PROJECT_ID)
    connector._sync_addresses(job_b, SIBLING_PROJECT_ID)

    assert connector.address_service.identity_reads == [
        (PARENT_BILL_QBO_ID, REALM),
        (PARENT_BILL_QBO_ID, "other-realm"),
    ]
    assert connector.project_address_service.links(PROJECT_ID) == [
        (ADDRESS_TYPE_BILLING, PARENT_BILL_ADDRESS_ID),
    ]
    assert connector.project_address_service.links(SIBLING_PROJECT_ID) == [], (
        "a job in another realm inherited this realm's owner address -- realm "
        "scoping must fail closed"
    )


# --------------------------------------------------------------------------
# The semantics assumption lives in exactly one place
# --------------------------------------------------------------------------

def test_semantics_flag_is_the_single_flip_point(monkeypatch):
    """REQUIREMENT: the owner-mailing-vs-property reading is ONE named
    module-level constant, so rejecting it is a one-line change rather than a
    hunt through the module.

    Shipped True (owner mailing: g702 renders this block under "TO OWNER:" and
    renders the property separately as "PROJECT:"). Flipped False, the billing
    chain degrades to own-bill only — pre-U-506 behavior plus the blank
    guard — and reads NO parent address, which is the behavior that would be
    correct if these were property addresses.
    """
    assert PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING is True

    monkeypatch.setattr(svc, "PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING", False)
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_service.identity_reads == []
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == []


def test_flipped_flag_still_honours_the_jobs_own_addresses(monkeypatch):
    """The flip removes INHERITANCE, not the slot: a job's own non-blank
    address is a property address under either reading."""
    monkeypatch.setattr(svc, "PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING", False)
    connector = _build_connector(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
    ]
