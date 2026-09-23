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
PROJECT_ID = 88
SIBLING_PROJECT_ID = 89

# Staging ids. The 9xx block is deliberately NOT the dbo.Address id block:
# `qbo.PhysicalAddress.Id` and `dbo.Address.Id` are separate keyspaces, and a
# test that conflated them would pass while production crossed them.
BLANK_OWN_BILL = 900
BLANK_OWN_SHIP = 901
BLANK_PARENT_BILL = 902
REAL_OWN_BILL = 910
REAL_OWN_SHIP = 911
REAL_PARENT_BILL = 920
REAL_PARENT_SHIP = 930
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
    BLANK_PARENT_BILL: _staged(BLANK_PARENT_BILL),
    REAL_OWN_BILL: _staged(REAL_OWN_BILL, line1="4527 Beacon Dr.", city="Nashville", postal_code="37220"),
    REAL_OWN_SHIP: _staged(REAL_OWN_SHIP, line1="12 Job Site Rd.", city="Nashville", postal_code="37220"),
    REAL_PARENT_BILL: _staged(
        REAL_PARENT_BILL, line1="1539 Old Hillsboro Road", city="Franklin", postal_code="37064"
    ),
    REAL_PARENT_SHIP: _staged(REAL_PARENT_SHIP, line1="99 Sibling Street", city="Franklin", postal_code="37064"),
}


def _dbo_address_id(staging_id):
    """What `sync_from_qbo_to_address` mints/returns for a staging id. Offset on
    purpose so an assertion can never accidentally match the staging id."""
    return 1000 + staging_id


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


class _FakeProjectAddressService:
    """In-memory ProjectAddress table: enough for `_ensure_project_address`'s
    read -> create-or-repoint sequence, so "no link was written" is observable
    as an empty table rather than as an un-called mock."""

    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.repo = SimpleNamespace(update_by_id=self._update_by_id)

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


def _parent(*, bill_addr_id=None, ship_addr_id=None):
    return _qbo_customer(
        id=1, bill_addr_id=bill_addr_id, ship_addr_id=ship_addr_id,
        parent_ref_value=None, display_name="Beacon Dr.", job=False,
    )


def _build_connector(*, parent=None):
    """`parent` is the qbo.Customer STAGING row the repo hands back — None
    models a parent that has no staging row (or no parent at all)."""
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = parent
    connector = CustomerProjectConnector(
        project_service=Mock(),
        project_address_service=_FakeProjectAddressService(),
        address_connector=_FakeAddressConnector(),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=qbo_customer_repo,
    )
    return connector


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


# --------------------------------------------------------------------------
# The fallback chain
# --------------------------------------------------------------------------

def test_blank_own_bill_falls_back_to_parent_bill():
    """MUTATION: revert the billing slot to `if qbo_customer.bill_addr_id:`.

    The pre-U-506 shape. The blank placeholder at BLANK_OWN_BILL passes
    truthiness, gets minted into dbo.Address as a blank row and linked — which
    is exactly how 191 of 799 dbo.Address rows became blank, and why the
    Draw Request "To:" block renders name-only.
    """
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_PARENT_BILL]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_PARENT_BILL)),
    ]


def test_blank_own_bill_and_ship_falls_back_through_the_full_chain():
    """own bill -> own ship -> parent bill -> parent ship, first NON-BLANK wins."""
    connector = _build_connector(
        parent=_parent(bill_addr_id=BLANK_PARENT_BILL, ship_addr_id=REAL_PARENT_SHIP)
    )
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=BLANK_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_PARENT_SHIP]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_PARENT_SHIP)),
    ]


def test_real_own_bill_wins_over_parent():
    """MUTATION: invert precedence (parent bill first).

    The job's own address is the most specific thing QBO knows; inheriting is
    strictly the fallback. A parent-first chain would overwrite every one of
    the 2 projects that already had a correct address of their own.

    Also pins LAZINESS: with its own bill non-blank the job must not read the
    parent staging row at all.
    """
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_OWN_BILL]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
    ]
    connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_no_parent_ref_value_never_reads_the_parent_staging_row():
    """MUTATION: drop `_get_parent_qbo_customer`'s empty-parent_ref_value guard.

    A top-level job has nothing to inherit from. Without the guard the repo is
    called with QboId=None — a pointless round trip per job, and one that would
    bind whatever a NULL-QboId staging row happened to return.
    """
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None, parent_ref_value=None)

    connector._sync_addresses(job, PROJECT_ID)

    connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == []


def test_missing_parent_staging_row_is_a_no_op_not_a_raise():
    """MUTATION: drop `_billing_address_candidates`'s `if parent is None` guard.

    The staging read is a plain miss when the parent has not been pulled yet
    (a job can arrive in a watermark window before its parent). Without the
    guard that miss is an AttributeError on None — swallowed by
    `_sync_addresses`'s failure isolation into a logged error, so the resolver
    is asserted DIRECTLY here: the miss must return None, not blow up.
    """
    connector = _build_connector(parent=None)
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    assert connector._resolve_billing_address_id(job) is None

    connector._sync_addresses(job, PROJECT_ID)
    connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(PARENT_REF, REALM)
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == []


def test_unreadable_staging_row_is_treated_as_blank_and_falls_through():
    """An id with no staging row behind it counts as blank rather than raising
    — `sync_from_qbo_to_address` would raise on it, aborting the whole slot and
    losing the parent address that was available all along."""
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=MISSING_STAGING_ROW, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_PARENT_BILL]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_PARENT_BILL)),
    ]


def test_blank_everywhere_creates_no_address_and_no_project_address_link():
    """MUTATION: drop the do-nothing branch (sync `bill_addr_id` regardless).

    The irreducible residual — 76 of 139 projects whose parent's BillAddrId
    ALSO points at a blank staging row. QBO holds nothing more for them, so the
    only correct action is none: minting a blank dbo.Address and linking it is
    what produced the 191 blank rows, and it actively harms the reader — P0's
    blank check would have to skip it anyway, but the SHIPPING slot behind it
    could never be reached.
    """
    connector = _build_connector(
        parent=_parent(bill_addr_id=BLANK_PARENT_BILL, ship_addr_id=None)
    )
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=BLANK_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == []
    assert connector.project_address_service.rows == []


def test_blank_chain_leaves_an_existing_link_untouched():
    """Do-nothing means DO NOTHING: an address already on the project (hand-set,
    or from an earlier pull when QBO still had content) must not be repointed
    or cleared by a run that resolves nothing."""
    connector = _build_connector(parent=_parent(bill_addr_id=BLANK_PARENT_BILL))
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
    connector = _build_connector(
        parent=_parent(bill_addr_id=REAL_PARENT_BILL, ship_addr_id=REAL_PARENT_SHIP)
    )
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=BLANK_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.address_connector.synced == [REAL_OWN_BILL]
    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
    ]
    assert REAL_PARENT_SHIP not in connector.address_connector.synced


def test_shipping_slot_still_syncs_the_jobs_own_non_blank_ship_address():
    """The other half of the guard — "never inherit" must not degrade into
    "never write", or the shipping slot would be silently dead."""
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
        (ADDRESS_TYPE_SHIPPING, _dbo_address_id(REAL_OWN_SHIP)),
    ]


# --------------------------------------------------------------------------
# Memoization
# --------------------------------------------------------------------------

def test_parent_staging_read_is_memoized_across_sibling_jobs():
    """MUTATION: drop the `_parent_qbo_customer_cache` read/write.

    ONE connector instance serves a whole pull run and sub-units of one
    property share a parent — 138 job customers resolve to 73 distinct parents
    — so an unmemoized read turns the fallback into ~one extra staging
    round trip per job instead of per parent.
    """
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job_a = _qbo_customer(id=4, bill_addr_id=BLANK_OWN_BILL, display_name="BD - Unit A")
    job_b = _qbo_customer(id=5, bill_addr_id=BLANK_OWN_BILL, display_name="BD - Unit B")

    connector._sync_addresses(job_a, PROJECT_ID)
    connector._sync_addresses(job_b, SIBLING_PROJECT_ID)

    assert connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.call_count == 1
    assert connector.project_address_service.links(PROJECT_ID) == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_PARENT_BILL)),
    ]
    assert connector.project_address_service.links(SIBLING_PROJECT_ID) == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_PARENT_BILL)),
    ]


def test_parent_staging_miss_is_memoized_too():
    """Caches misses as well as hits, per `_parent_customer_cache`'s canonical
    shape — otherwise a parent that is genuinely absent costs a read per job,
    which is the worst case rather than the cheap one."""
    connector = _build_connector(parent=None)
    job_a = _qbo_customer(id=4, bill_addr_id=BLANK_OWN_BILL)
    job_b = _qbo_customer(id=5, bill_addr_id=BLANK_OWN_BILL)

    connector._sync_addresses(job_a, PROJECT_ID)
    connector._sync_addresses(job_b, SIBLING_PROJECT_ID)

    assert connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.call_count == 1


def test_parent_memo_is_keyed_by_realm_as_well_as_ref():
    """QBO ids are unique only WITHIN a realm — a memo keyed on the ref alone
    would serve one realm's parent address to another's job."""
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job_a = _qbo_customer(id=4, bill_addr_id=BLANK_OWN_BILL, realm_id=REALM)
    job_b = _qbo_customer(id=5, bill_addr_id=BLANK_OWN_BILL, realm_id="other-realm")

    connector._sync_addresses(job_a, PROJECT_ID)
    connector._sync_addresses(job_b, SIBLING_PROJECT_ID)

    assert connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.call_count == 2


# --------------------------------------------------------------------------
# The semantics assumption lives in exactly one place
# --------------------------------------------------------------------------

def test_semantics_flag_is_the_single_flip_point(monkeypatch):
    """REQUIREMENT: the owner-mailing-vs-property reading is ONE named
    module-level constant, so rejecting it is a one-line change rather than a
    hunt through the module.

    Shipped True (owner mailing: g702 renders this block under "TO OWNER:" and
    renders the property separately as "PROJECT:"). Flipped False, the billing
    chain degrades to own-bill -> own-ship — pre-U-506 behavior plus the blank
    guard — and reads NO parent row, which is the behavior that would be
    correct if these were property addresses.
    """
    assert PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING is True

    monkeypatch.setattr(svc, "PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING", False)
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=BLANK_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    assert connector.address_connector.synced == []
    assert connector.project_address_service.links() == []


def test_flipped_flag_still_honours_the_jobs_own_addresses(monkeypatch):
    """The flip removes INHERITANCE, not the slot: a job's own non-blank
    address is a property address under either reading."""
    monkeypatch.setattr(svc, "PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING", False)
    connector = _build_connector(parent=_parent(bill_addr_id=REAL_PARENT_BILL))
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=None)

    connector._sync_addresses(job, PROJECT_ID)

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, _dbo_address_id(REAL_OWN_BILL)),
    ]
