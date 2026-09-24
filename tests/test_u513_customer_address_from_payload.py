"""U-513 (customer half) — the parent customer projects its OWN billing address.

THE DEFECT, stated plainly: `CustomerCustomerConnector.sync_from_qbo_customer`
wrote no `dbo.Address` at all. Every `<customer>_bill` row that exists today was
minted as a SIDE EFFECT of a CHILD project's billing fallback calling
`sync_from_qbo_to_address` on the PARENT's `qbo.PhysicalAddress` staging id
(`CustomerProjectConnector._billing_address_candidates`, U-506 P1).

That is circular — the fallback created the rows the fallback read. It survived
only because `qbo.PhysicalAddress` happened to be there to read. `qbo.Physical
Address` is being sunset (the addresses are, and always were, INLINE on the QBO
Customer payload as `BillAddr`), and the moment it goes:

  * a brand-new parent customer has no staging row, so nothing mints its
    address, so every project under it renders a name-only "To:" block — the
    exact 1,012-invoice defect U-506 P1 was built to end; and
  * an EXISTING parent whose address changes in QBO never gets refreshed,
    because nothing else writes that row.

So ownership moves to where it belongs: a customer projects its own address
under the synthetic identity `<qbo_id>_bill`, and the child project only READS
it (`test_u506_p1_project_address_parent_fallback.py` pins that half). The
identity string is byte-identical to the one `QboCustomerService._upsert_customer`
has always written onto the staging row, so no `dbo.Address` row is re-keyed.

`PhysicalAddressAddressConnector.sync_address_from_external` — the staging-free
write this codes against — is implemented by a sibling unit in the
`physical_address` package, so it is MOCKED here deliberately. What THIS unit
owns is WHEN it is called, WITH WHAT, and when it must NOT be called at all.

Pure-logic, no live DB: the repos/services are fakes, and the two address
"tables" are in-memory dicts shared between the writer and the reader so the
bootstrap can be proven END TO END rather than asserted on a mock.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from conftest import mock_qbo_app_lock_granted
from entities.address.business.model import Address, Country
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.connector.customer.business.service import (
    CustomerCustomerConnector,
    address_fields_are_blank,
    billing_address_qbo_id,
)
from integrations.intuit.qbo.customer.connector.project.business.service import (
    ADDRESS_TYPE_BILLING,
    CustomerProjectConnector,
)
from integrations.intuit.qbo.customer.external.schemas import (
    QboCustomer as QboCustomerExternalSchema,
)
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from tests.test_u269_qbo_staging_try_except import _client_cm

FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"

REALM = "9130353016965726"
PARENT_QBO_ID = "P-1"
PARENT_BILL_QBO_ID = "P-1_bill"
JOB_QBO_ID = "J-7"
PROJECT_ID = 88

OWNER_MAILING = {
    "Line1": "1539 Old Hillsboro Road",
    "Line2": "Suite 200",
    "City": "Franklin",
    "CountrySubDivisionCode": "TN",
    "PostalCode": "37064",
}
# What QBO actually returns for a customer with no address configured: an Id and
# nothing else. 29 of the 32 staging rows in prod are this shape.
QBO_PLACEHOLDER_ADDR = {"Id": "58"}


# --------------------------------------------------------------------------
# Fixtures for the two shapes an address arrives in
# --------------------------------------------------------------------------

def _external_customer(*, qbo_id=PARENT_QBO_ID, bill_addr=None, ship_addr=None, job=False):
    """The ORIGINAL QBO payload, built from an alias dict exactly as
    `QboCustomerQueryResponse.get_customers` builds it — so the `BillAddr` ->
    `bill_addr` alias mapping this unit depends on is exercised, not assumed."""
    payload = {
        "Id": qbo_id,
        "SyncToken": "0",
        "DisplayName": "Beacon Dr. Owner",
        "Job": job,
        "Active": True,
    }
    if bill_addr is not None:
        payload["BillAddr"] = bill_addr
    if ship_addr is not None:
        payload["ShipAddr"] = ship_addr
    return QboCustomerExternalSchema(**payload)


def _staging_customer(*, qbo_id=PARENT_QBO_ID, bill_addr_id=None, job=False, realm_id=REALM):
    """The `qbo.Customer` STAGING row — what the projection loop actually
    iterates. A real dataclass, so a renamed field breaks loudly."""
    return QboCustomer(
        id=4, public_id=f"pub-c{qbo_id}", row_version="cm93dmVyMDE=",
        created_datetime="2026-01-01 00:00:00", modified_datetime="2026-01-01 00:00:00",
        qbo_id=qbo_id, sync_token="0", realm_id=realm_id,
        display_name="Beacon Dr. Owner", title=None, given_name=None, middle_name=None,
        family_name=None, suffix=None, company_name=None, fully_qualified_name=None,
        level=0, parent_ref_value=None, parent_ref_name=None, job=job, active=True,
        primary_email_addr=None, primary_phone=None, mobile=None, fax=None,
        bill_addr_id=bill_addr_id, ship_addr_id=None, balance=None,
        balance_with_jobs=None, taxable=None, notes=None, print_on_check_name=None,
    )


def _staged(id, *, line1=None, city=None, postal_code=None, line2=None, state=None):
    """A `qbo.PhysicalAddress` staging row — the TRANSITIONAL content source."""
    return QboPhysicalAddress(
        id=id, public_id=f"pub-{id}", row_version="cm93dmVyMDE=",
        created_datetime="2026-01-01 00:00:00", modified_datetime="2026-01-01 00:00:00",
        qbo_id=PARENT_BILL_QBO_ID, realm_id=REALM,
        line1=line1, line2=line2, city=city, country=None,
        country_sub_division_code=state, postal_code=postal_code,
    )


STAGING_BILL_ADDR_ID = 902


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class _FakeStagingAddressService:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.read_ids = []

    def read_by_id(self, id):
        self.read_ids.append(id)
        return self.rows.get(id)


class _FakeAddressConnector:
    """Stands in for `PhysicalAddressAddressConnector`.

    `sync_address_from_external` is U-513's SHARED CONTRACT, implemented in the
    physical_address package by a sibling unit. Its parameters here are
    KEYWORD-ONLY on purpose: the real signature is, so a positional call from
    this connector must fail in these tests rather than at runtime in prod.

    It writes into `store`, an in-memory stand-in for `dbo.Address` keyed by
    (qbo_id, realm_id) — the same dict `_FakeAddressService` reads — which is
    what lets the bootstrap test observe the parent's mint and the child's
    inheritance as one causal chain instead of two mocks agreeing by luck.
    """

    def __init__(self, *, staging_rows=None, store=None, raises=None):
        self.calls = []
        self.store = {} if store is None else store
        self.qbo_physical_address_service = _FakeStagingAddressService(staging_rows)
        self.synced_staging_ids = []
        self._raises = raises
        self._next_id = 4001

    def sync_address_from_external(
        self, *, qbo_id, realm_id, line1, line2, city,
        country_sub_division_code, postal_code, source_ref=None,
    ):
        self.calls.append({
            "qbo_id": qbo_id, "realm_id": realm_id, "line1": line1, "line2": line2,
            "city": city, "country_sub_division_code": country_sub_division_code,
            "postal_code": postal_code, "source_ref": source_ref,
        })
        if self._raises is not None:
            raise self._raises
        existing = self.store.get((qbo_id, realm_id))
        address_id = existing.id if existing is not None else self._next_id
        if existing is None:
            self._next_id += 1
        address = Address(
            id=address_id, public_id=f"pub-a{address_id}", row_version="cm93dmVyMDE=",
            created_datetime="2026-01-01 00:00:00", modified_datetime="2026-01-01 00:00:00",
            street_one=line1 or "", street_two=line2 or "", city=city or "",
            state=country_sub_division_code or "", zip=postal_code or "",
            country=Country.UNITED_STATES, qbo_id=qbo_id, realm_id=realm_id,
        )
        self.store[(qbo_id, realm_id)] = address
        return address

    def sync_from_qbo_to_address(self, qbo_physical_address_id):
        """The JOB's own-address path, untouched by this unit."""
        self.synced_staging_ids.append(qbo_physical_address_id)
        return SimpleNamespace(id=5000 + qbo_physical_address_id)


class _FakeAddressService:
    """Reads the SAME in-memory `dbo.Address` store the connector writes, with
    `ReadAddressByQboIdAndRealmId`'s fail-closed realm scoping."""

    def __init__(self, store):
        self.store = store
        self.identity_reads = []

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        self.identity_reads.append((qbo_id, realm_id))
        return self.store.get((qbo_id, realm_id))

    def read_by_id(self, id):
        return SimpleNamespace(id=id, qbo_id=f"A-{id}")


class _FakeProjectAddressService:
    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.repo = SimpleNamespace(
            update_by_id=lambda row: row, delete_by_id=self._delete_by_id
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
        self.rows = [r for r in self.rows if r.id != id]

    def links(self, project_id=PROJECT_ID):
        return [(r.address_type_id, r.address_id) for r in self.read_by_project_id(project_id)]


def _customer_row(id=55, qbo_id=None, realm_id=None):
    """A `dbo.Customer` stand-in. A SimpleNamespace on purpose: the Customer
    entity's own shape is exhaustively covered by test_u276/u287/u310 and is not
    what this unit changes — the ADDRESS shapes are the real dataclasses here."""
    return SimpleNamespace(
        id=id, public_id=f"pub-{id}", name="Beacon Dr. Owner", email="", phone="",
        qbo_id=qbo_id, realm_id=realm_id,
    )


def _build_connector(*, hit=None, staging_rows=None, store=None, address_raises=None):
    """`hit` is the dbo.Customer a direct identity read resolves to — None drives
    the MISS/create path (a brand-new parent)."""
    customer_service = Mock()
    customer_service.repo = Mock()
    customer_service.read_by_qbo_identity.return_value = hit
    customer_service.read_by_name.return_value = None
    created = _customer_row()
    customer_service.create.return_value = created
    customer_service.read_by_id.side_effect = lambda _id: created
    customer_service.repo.update_by_id.return_value = hit if hit is not None else created

    address_connector = _FakeAddressConnector(
        staging_rows=staging_rows, store=store, raises=address_raises,
    )
    connector = CustomerCustomerConnector(
        customer_service=customer_service,
        reconciliation_repo=Mock(),
        address_connector=address_connector,
    )
    return connector


def _sync(connector, staging_row, external=None):
    """Run the projection with the identity fast path's locks granted."""
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        return connector.sync_from_qbo_customer(staging_row, external)


# --------------------------------------------------------------------------
# The identity string — no dbo.Address may be re-keyed
# --------------------------------------------------------------------------

def test_the_synthetic_identity_is_the_string_staging_already_carried():
    """⚠️ MIGRATION SAFETY. `QboCustomerService._upsert_customer` has always
    written `f"{qbo_customer.id}_bill"` as the staging row's QboId, and
    `SetAddressQboIdentity` stamped that same string onto `dbo.Address`. U-513
    removes the staging hop but must keep the STRING, or all 799 existing
    dbo.Address rows silently stop resolving and every project degrades to
    name-only on the next pull.
    """
    assert billing_address_qbo_id(PARENT_QBO_ID) == f"{PARENT_QBO_ID}_bill"
    assert billing_address_qbo_id("123") == "123_bill"


# --------------------------------------------------------------------------
# ⭐ THE BOOTSTRAP — the circularity fix
# --------------------------------------------------------------------------

def test_bootstrap_a_brand_new_parent_mints_its_own_billing_address():
    """⭐ THE POINT OF THE UNIT.

    MUTATION: delete the `_project_own_billing_address` call from
    `sync_from_qbo_customer`.

    A parent customer QBO has never handed us before: no `dbo.Customer` row
    holds its identity (the fast path takes the MISS/create branch), and
    crucially NO `qbo.PhysicalAddress` staging row exists for it — the fake's
    staging table is empty. Pre-U-513 there was nothing on this path that could
    mint an address at all; the only writer was a child project's fallback,
    which could not run until a child existed and which read the very staging
    row that is gone.

    The address must be minted from the INLINE payload, with no staging read.
    """
    connector = _build_connector(hit=None, staging_rows={})
    external = _external_customer(bill_addr=OWNER_MAILING)

    _sync(connector, _staging_customer(bill_addr_id=None), external)

    assert len(connector.address_connector.calls) == 1
    call = connector.address_connector.calls[0]
    assert call["qbo_id"] == PARENT_BILL_QBO_ID
    assert call["realm_id"] == REALM
    assert call["line1"] == "1539 Old Hillsboro Road"
    assert call["city"] == "Franklin"
    assert call["postal_code"] == "37064"
    assert connector.address_connector.qbo_physical_address_service.read_ids == [], (
        "the payload path must not read qbo.PhysicalAddress at all"
    )


def test_bootstrap_end_to_end_a_new_parent_then_its_first_job():
    """⭐ THE CIRCULARITY, BROKEN — writer and reader in one causal chain.

    MUTATION: revert `_parent_billing_address_id` to the staging read, or drop
    the parent's own mint. Either way this goes red, because the two halves are
    wired to ONE in-memory dbo.Address store: the parent's projection is the
    only thing that puts a row in it, and the job's fallback is the only thing
    that reads it. Nothing in this test can pass by two mocks agreeing.

    The job carries no address of its own (the overwhelmingly common shape: 29
    of 32 staging rows are placeholders), so its BILLING slot can only be filled
    by inheriting — and the address it inherits must be the row the parent just
    minted, by id.
    """
    store = {}
    parent_connector = _build_connector(hit=None, staging_rows={}, store=store)
    _sync(
        parent_connector,
        _staging_customer(bill_addr_id=None),
        _external_customer(bill_addr=OWNER_MAILING),
    )
    minted = store[(PARENT_BILL_QBO_ID, REALM)]

    project_connector = CustomerProjectConnector(
        project_service=Mock(),
        project_address_service=_FakeProjectAddressService(),
        address_connector=_FakeAddressConnector(staging_rows={}),
        reconciliation_repo=Mock(),
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
        address_service=_FakeAddressService(store),
    )
    job = QboCustomer(
        id=9, public_id="pub-j", row_version="cm93dmVyMDE=",
        created_datetime="2026-01-01 00:00:00", modified_datetime="2026-01-01 00:00:00",
        qbo_id=JOB_QBO_ID, sync_token="0", realm_id=REALM,
        display_name="BD - 4527 Beacon Dr.", title=None, given_name=None,
        middle_name=None, family_name=None, suffix=None, company_name=None,
        fully_qualified_name=None, level=1, parent_ref_value=PARENT_QBO_ID,
        parent_ref_name="Beacon Dr. Owner", job=True, active=True,
        primary_email_addr=None, primary_phone=None, mobile=None, fax=None,
        bill_addr_id=None, ship_addr_id=None, balance=None, balance_with_jobs=None,
        taxable=None, notes=None, print_on_check_name=None,
    )

    project_connector._sync_addresses(job, PROJECT_ID)

    assert project_connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, minted.id)
    ], "the new job did not inherit the address its parent had just minted"
    assert project_connector.address_service.identity_reads == [(PARENT_BILL_QBO_ID, REALM)]
    project_connector.qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


# --------------------------------------------------------------------------
# The mint, on an already-known parent
# --------------------------------------------------------------------------

def test_an_existing_parent_has_its_address_refreshed_on_every_pull():
    """The HIT branch. QBO is source of truth, and an owner who MOVES edits only
    the address — nothing about the customer changes. Pre-U-513 the refresh
    happened (if at all) on the child's next pull; now it happens here, which is
    the only reason the child can be allowed to stop re-syncing it."""
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM))

    result = _sync(
        connector,
        _staging_customer(bill_addr_id=None),
        _external_customer(bill_addr=OWNER_MAILING),
    )

    assert result is not None
    assert [c["qbo_id"] for c in connector.address_connector.calls] == [PARENT_BILL_QBO_ID]


def test_the_minted_address_carries_every_payload_field_and_the_realm():
    """The full field map, including `line2` and the state code — neither counts
    as CONTENT for blankness, but both must still be written when the address is
    non-blank on other grounds, or a suite number is silently dropped off a
    payment request."""
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM))

    _sync(
        connector,
        _staging_customer(bill_addr_id=None),
        _external_customer(bill_addr=OWNER_MAILING),
    )

    call = connector.address_connector.calls[0]
    assert call["line2"] == "Suite 200"
    assert call["country_sub_division_code"] == "TN"
    assert call["realm_id"] == REALM
    assert PARENT_QBO_ID in (call["source_ref"] or "")


def test_the_realm_travels_with_the_identity():
    """QBO ids are unique only WITHIN a realm, and the synthetic `<id>_bill`
    string inherits that. A mint that dropped the realm would collide two
    companies' owners onto one dbo.Address row — and the child's read is
    realm-scoped and fails closed, so it would then find nothing."""
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id="other-realm"))

    _sync(
        connector,
        _staging_customer(bill_addr_id=None, realm_id="other-realm"),
        _external_customer(bill_addr=OWNER_MAILING),
    )

    assert connector.address_connector.calls[0]["realm_id"] == "other-realm"


# --------------------------------------------------------------------------
# Blank and absent — never mint a blank dbo.Address
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bill_addr, why",
    [
        (None, "no BillAddr on the payload at all"),
        (QBO_PLACEHOLDER_ADDR, "QBO's placeholder: an Id and nothing else"),
        ({"Line1": "", "City": "", "PostalCode": ""}, "empty strings"),
        ({"Line1": "   ", "City": "\t", "PostalCode": " "}, "whitespace is not content"),
        ({"Line2": "Suite 200", "CountrySubDivisionCode": "TN"}, "line2/state alone is unroutable"),
    ],
)
def test_a_blank_or_absent_bill_addr_mints_nothing(bill_addr, why):
    """MUTATION: drop the blank guard and mint whatever the payload carries.

    That is precisely the pre-U-506 defect, re-introduced one layer up: keying
    on PRESENCE rather than CONTENT minted 191 of 799 blank `dbo.Address` rows.
    Worse here than before — a blank row minted under `<parent>_bill` would be
    FOUND by the child's identity read, and only the child's own blank check
    would keep it out of the slot. Do not create the problem and then rely on
    the guard downstream.
    """
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM))

    _sync(
        connector,
        _staging_customer(bill_addr_id=None),
        _external_customer(bill_addr=bill_addr),
    )

    assert connector.address_connector.calls == [], why


def test_blankness_here_is_the_shared_rule_not_a_local_copy():
    """The mint guard, the staging blank check and the dbo blank check must be
    ONE rule. `address_fields_are_blank` is that rule; this pins that the parent
    connector exports and uses it rather than re-spelling the strip chain."""
    assert address_fields_are_blank(None, None, None) is True
    assert address_fields_are_blank("  ", "", None) is True
    assert address_fields_are_blank(None, "Franklin", None) is False


# --------------------------------------------------------------------------
# Failure isolation and ordering
# --------------------------------------------------------------------------

def test_an_address_failure_does_not_fail_the_customer_projection():
    """MUTATION: let `_project_own_billing_address` raise.

    Matching `CustomerProjectConnector._sync_addresses`'s own convention. A
    raise here is classified by `SyncOutcome.record_projection_error`: a
    transient DB error would HOLD the customer watermark — blocking EVERY
    customer and project projection in the realm — over one address, and a
    ValueError would SKIP the customer entirely. Degrading one project to
    name-only is the cheaper failure by a wide margin, and it is visible to
    whoever sends the packet.
    """
    connector = _build_connector(
        hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM),
        address_raises=RuntimeError("address service is down"),
    )

    result = _sync(
        connector,
        _staging_customer(bill_addr_id=None),
        _external_customer(bill_addr=OWNER_MAILING),
    )

    assert result is not None, "an address failure swallowed the Customer projection"
    assert len(connector.address_connector.calls) == 1


def test_a_failed_customer_projection_mints_no_address():
    """Ordering: the address write sits AFTER the identity fast path, so a
    customer that never resolved cannot leave an orphan `<qbo_id>_bill` row
    behind pointing at nothing."""
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM))
    # A ROWVERSION race: the HIT branch's update affects 0 rows.
    connector.customer_service.repo.update_by_id.return_value = None

    with pytest.raises(RuntimeError):
        _sync(
            connector,
            _staging_customer(bill_addr_id=None),
            _external_customer(bill_addr=OWNER_MAILING),
        )

    assert connector.address_connector.calls == []


def test_a_job_customer_is_refused_before_any_address_is_minted():
    """The Job=true gate still fires first — a sub-customer's address belongs to
    its Project's slots, resolved by `CustomerProjectConnector`, and must never
    be minted under the parent-customer identity."""
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM))

    with pytest.raises(ValueError, match="Job=true"):
        _sync(
            connector,
            _staging_customer(bill_addr_id=None, job=True),
            _external_customer(bill_addr=OWNER_MAILING, job=True),
        )

    assert connector.address_connector.calls == []


def test_a_falsy_qbo_id_never_mints_a_none_bill_identity():
    """Unreachable through `sync_from_qbo_customer` (the fast path raises on a
    falsy qbo_id first), but `<None>_bill` is an identity string that would be
    SHARED by every such customer — a collision, not a miss. Asserted directly
    on the private method so the guard is pinned rather than merely present."""
    connector = _build_connector(hit=_customer_row())
    row = _staging_customer(bill_addr_id=None)
    row.qbo_id = None

    connector._project_own_billing_address(row, _external_customer(bill_addr=OWNER_MAILING))

    assert connector.address_connector.calls == []


# --------------------------------------------------------------------------
# ⚠️ The TRANSITIONAL staging source
# --------------------------------------------------------------------------
# `scripts/sync_qbo_customer.py` — the path the scheduler actually runs — USED
# to call `sync_from_qbo(sync_to_modules=False)` and run its OWN projection
# loop, so the external payloads never reached it. Without a content fallback,
# U-513's repointed child lookup would have read a `dbo.Address` nothing had
# refreshed. U-513 ph2 converted that script (`sync_to_modules=True`), so no
# production caller reaches the fallback any more — it now only covers a direct
# one-argument `sync_from_qbo_customer(row)` call. These four tests pin it until
# the sibling vendor half is converted and the EM deletes the branch; deleting
# it should turn them red, which is the signal to delete them too.

def test_without_the_payload_the_staging_row_supplies_the_content():
    connector = _build_connector(
        hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM),
        staging_rows={
            STAGING_BILL_ADDR_ID: _staged(
                STAGING_BILL_ADDR_ID, line1="1539 Old Hillsboro Road",
                city="Franklin", postal_code="37064", state="TN",
            )
        },
    )

    _sync(connector, _staging_customer(bill_addr_id=STAGING_BILL_ADDR_ID), None)

    assert connector.address_connector.qbo_physical_address_service.read_ids == [
        STAGING_BILL_ADDR_ID
    ]
    call = connector.address_connector.calls[0]
    assert call["qbo_id"] == PARENT_BILL_QBO_ID, (
        "the transitional path must write the SAME synthetic identity as the "
        "payload path, or the two sources would mint two different rows"
    )
    assert call["line1"] == "1539 Old Hillsboro Road"


def test_without_the_payload_a_blank_staging_row_still_mints_nothing():
    """The blank guard is applied to BOTH sources — a placeholder row cannot
    sneak a blank dbo.Address in through the transitional door."""
    connector = _build_connector(
        hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM),
        staging_rows={STAGING_BILL_ADDR_ID: _staged(STAGING_BILL_ADDR_ID)},
    )

    _sync(connector, _staging_customer(bill_addr_id=STAGING_BILL_ADDR_ID), None)

    assert connector.address_connector.calls == []


def test_without_the_payload_and_without_a_staging_id_nothing_is_read():
    connector = _build_connector(hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM))

    _sync(connector, _staging_customer(bill_addr_id=None), None)

    assert connector.address_connector.qbo_physical_address_service.read_ids == []
    assert connector.address_connector.calls == []


def test_the_payload_wins_over_staging_and_staging_is_not_even_read():
    """Preference, not merge. When both are available the payload is what QBO
    just said; the staging row is a copy of what QBO said last time, and re-
    reading it would be a round trip whose only possible effect is to be
    ignored."""
    connector = _build_connector(
        hit=_customer_row(qbo_id=PARENT_QBO_ID, realm_id=REALM),
        staging_rows={
            STAGING_BILL_ADDR_ID: _staged(
                STAGING_BILL_ADDR_ID, line1="STALE STREET", city="Nowhere", postal_code="00000",
            )
        },
    )

    _sync(
        connector,
        _staging_customer(bill_addr_id=STAGING_BILL_ADDR_ID),
        _external_customer(bill_addr=OWNER_MAILING),
    )

    assert connector.address_connector.qbo_physical_address_service.read_ids == []
    assert connector.address_connector.calls[0]["line1"] == "1539 Old Hillsboro Road"


# --------------------------------------------------------------------------
# The closure that threads the payload — WITHOUT touching project_records
# --------------------------------------------------------------------------

def test_each_parent_row_is_paired_with_its_OWN_external_record():
    """MUTATION: key the map on anything but `qbo_id`, or bind the loop variable
    instead of the row.

    `project_records` is shared by 10 call sites across 8 other QBO families and
    takes a strict one-argument `project_one`; binding the payload in a closure
    at the call site is exactly how this family threads a second argument
    without changing a signature every other family depends on. The failure mode
    a closure invites is late binding — every row receiving the LAST payload —
    so the pairing is asserted per row, with deliberately distinguishable
    addresses.
    """
    service = QboCustomerService(repo=MagicMock())
    rows = [_staging_customer(qbo_id="P-1"), _staging_customer(qbo_id="P-2")]
    externals = {
        "P-1": _external_customer(qbo_id="P-1", bill_addr=OWNER_MAILING),
        "P-2": _external_customer(qbo_id="P-2", bill_addr={"Line1": "2 Second St.", "City": "Nashville"}),
    }
    seen = []

    fake_connector = Mock()
    fake_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: seen.append((row.qbo_id, external)) or SimpleNamespace(id=1)
    )

    with patch(
        "integrations.intuit.qbo.customer.business.service.CustomerCustomerConnector",
        return_value=fake_connector,
    ):
        service._sync_to_customers(rows, MagicMock(), externals)

    assert [qbo_id for qbo_id, _e in seen] == ["P-1", "P-2"]
    assert seen[0][1].bill_addr.line1 == "1539 Old Hillsboro Road"
    assert seen[1][1].bill_addr.line1 == "2 Second St."


def test_a_row_with_no_matching_payload_gets_none_not_a_crash():
    """A staging row whose external record is missing from the map (a partial
    map, a re-projection of an older row) must degrade to the None default, not
    KeyError out of the whole projection loop."""
    service = QboCustomerService(repo=MagicMock())
    seen = []
    fake_connector = Mock()
    fake_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: seen.append(external) or SimpleNamespace(id=1)
    )

    with patch(
        "integrations.intuit.qbo.customer.business.service.CustomerCustomerConnector",
        return_value=fake_connector,
    ):
        service._sync_to_customers([_staging_customer(qbo_id="P-9")], MagicMock(), {})
        service._sync_to_customers([_staging_customer(qbo_id="P-9")], MagicMock(), None)

    assert seen == [None, None]


def test_the_pull_builds_the_payload_map_and_projects_parents_before_jobs():
    """End-to-end over `sync_from_qbo(sync_to_modules=True)`.

    Two assertions in one run because they are one guarantee: the payload map is
    built from the SAME loop that writes staging (the last scope that still has
    the external records), and parents project BEFORE jobs — load-bearing, since
    a job's billing fallback reads the `dbo.Address` its parent's projection
    mints. Reverse the two calls and a new property's first job renders
    name-only until the next pull.
    """
    repo = MagicMock()
    service = QboCustomerService(repo=repo)
    service.physical_address_service = MagicMock()
    parent = _external_customer(qbo_id="P-1", bill_addr=OWNER_MAILING, job=False)
    job = _external_customer(qbo_id="J-7", job=True)
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.side_effect = [
        _staging_customer(qbo_id="P-1", job=False),
        _staging_customer(qbo_id="J-7", job=True),
    ]

    order = []
    customer_connector = Mock()
    customer_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: order.append(("parent", row.qbo_id, external))
        or SimpleNamespace(id=1)
    )
    project_connector = Mock()
    project_connector.sync_from_qbo_customer.side_effect = (
        lambda row: order.append(("job", row.qbo_id, None)) or SimpleNamespace(id=2)
    )

    with patch(
        "integrations.intuit.qbo.customer.business.service.QboCustomerClient",
        return_value=_client_cm([parent, job]),
    ), patch(
        "integrations.intuit.qbo.customer.business.service.CustomerCustomerConnector",
        return_value=customer_connector,
    ), patch(
        "integrations.intuit.qbo.customer.business.service.CustomerProjectConnector",
        return_value=project_connector,
    ):
        outcome = service.sync_from_qbo(realm_id=REALM, sync_to_modules=True)

    assert outcome.fetched == 2
    assert [kind for kind, _id, _e in order] == ["parent", "job"], (
        "jobs projected before parents -- a new property's first job cannot "
        "inherit an address its parent has not minted yet"
    )
    assert order[0][2] is parent, "the parent's own external payload was not threaded"
