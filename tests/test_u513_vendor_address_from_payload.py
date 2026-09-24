"""U-513 (vendor half) — the vendor projection reads its billing address off the
INLINE QBO payload, not out of `qbo.PhysicalAddress`.

What changed
------------
`qbo.PhysicalAddress` is a pure write-then-read-back cache: the pull stages the
vendor's `BillAddr` into it (`QboVendorService._upsert_physical_address`), stores
the new row's LOCAL id on the staging vendor row, and the projection then reads
that row straight back to build `dbo.Address`. The address content was in hand
the whole time — it is right there on the external record as
`QboVendor.BillAddr`. This unit cuts the round trip: the pull now hands the
EXTERNAL record to the connector, which projects `dbo.Address` from the inline
object via `PhysicalAddressAddressConnector.sync_address_from_external`.

The staging WRITE deliberately stays (the table is dropped by a later step, once
every consumer is off it); what goes away is the DEPENDENCE on reading it back.

How the payload reaches the connector — a closure, not a wider primitive
-----------------------------------------------------------------------
`sync_outcome.project_records` is shared by ten call sites across eight QBO
families (seven besides vendor). Widening it to carry a per-record side value
would push this family's concern into all of them. Instead
`QboVendorService._sync_to_vendors` closes over a `{qbo_id: external record}`
dict built in the very loop that stages the rows:

    project_one=lambda row: connector.sync_from_qbo_vendor(row, by_id.get(row.qbo_id))

`test_closure_pairs_each_staging_row_with_its_own_external_record` is the guard
that matters there: the closure is the ONLY thing keeping vendor A's address off
vendor B's identity, and a page of vendors is the normal case, not the edge one.

Identity is preserved, not re-keyed
-----------------------------------
`sync_address_from_external` is called with the same synthetic
`f"{qbo_vendor_id}_bill"` string the staging row carries today, so every
`dbo.Address` already minted through the old path is matched by identity rather
than duplicated.

Blankness
---------
QBO emits a `BillAddr` object shell for vendors with no address on file. The old
round trip staged that shell and then minted a `dbo.Address` of empty strings and
linked it — 191 such rows exist. An address with no `Line1`, no `City` and no
`PostalCode` is now ABSENT: nothing minted, nothing linked.

No live DB: the connector's collaborators are mocked, as is the shared
`PhysicalAddressAddressConnector` — `sync_address_from_external` is being built
in a sibling worktree, so these tests pin the exact keyword contract it must
land with rather than binding to an implementation that is not here yet.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.address.business.model import Address
from entities.vendor.business.model import Vendor
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.customer.external.schemas import QboPhysicalAddress
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from integrations.intuit.qbo.vendor.connector.vendor.business.service import (
    ADDRESS_TYPE_BILLING,
    VendorVendorConnector,
    _inline_address_is_blank,
)
from integrations.intuit.qbo.vendor.external.schemas import QboVendor as QboVendorExternal

REALM = "9130353016965726"

SERVICE_MODULE = "integrations.intuit.qbo.vendor.business.service"
CONNECTOR_MODULE = "integrations.intuit.qbo.vendor.connector.vendor.business.service"


# --------------------------------------------------------------------------
# Builders — real models, not SimpleNamespace, so a renamed field breaks these
# tests instead of silently diverging from the shipped dataclasses/schemas.
# --------------------------------------------------------------------------


def _external(qbo_id="1246", *, bill_addr=None, display_name="Acme Supply"):
    """One QBO Vendor as the API returns it, with its inline BillAddr."""
    return QboVendorExternal(
        Id=qbo_id,
        SyncToken="0",
        DisplayName=display_name,
        Active=True,
        BillAddr=bill_addr,
    )


def _addr(**overrides):
    defaults = dict(
        line1="PO Box 594",
        line2="Suite 200",
        city="Brentwood",
        country_sub_division_code="TN",
        postal_code="37024",
    )
    defaults.update(overrides)
    return QboPhysicalAddress(**defaults)


def _staging(qbo_id="1246", *, realm_id=REALM, bill_addr_id=None, display_name="Acme Supply"):
    """The `qbo.Vendor` row the staging upsert produced for that payload."""
    return QboVendor(
        id=77,
        public_id="qbo-vendor-pub-77",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=realm_id,
        display_name=display_name,
        title=None,
        given_name=None,
        middle_name=None,
        family_name=None,
        suffix=None,
        company_name=None,
        print_on_check_name=None,
        tax_identifier=None,
        vendor_1099=None,
        active=True,
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
        fax=None,
        bill_addr_id=bill_addr_id,
        balance=None,
        acct_num=None,
        web_addr=None,
    )


def _vendor(id=55, name="Acme Supply"):
    return Vendor(
        id=id,
        public_id=f"vendor-pub-{id}",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        name=name,
        abbreviation=None,
        taxpayer_id=None,
        vendor_type_id=None,
        is_draft=False,
        qbo_id="1246",
        realm_id=REALM,
    )


def _address(id=900):
    return Address(
        id=id,
        public_id=f"address-pub-{id}",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        street_one="PO Box 594",
        street_two="Suite 200",
        city="Brentwood",
        state="TN",
        zip="37024",
        country=None,
        qbo_id="1246_bill",
        realm_id=REALM,
    )


def _build_connector(*, local_vendor=None, minted_address=None):
    """A connector on the DIRECT-hit branch of the dbo-only fast path: an
    existing dbo.Vendor already holds this identity, so no app lock is reached
    and the address sync is the only interesting side effect left.

    `address_connector` is a bare Mock, not an autospec: the shared
    `sync_address_from_external` lands in the physical_address worktree, so
    autospec here would bind these tests to a method that does not exist yet.
    The call-kwarg assertions below are what pin the contract instead.
    """
    local_vendor = local_vendor or _vendor()

    vendor_service = Mock()
    vendor_service.repo = Mock()
    vendor_service.read_by_qbo_identity.return_value = local_vendor
    vendor_service.read_deleted_by_qbo_identity.return_value = None

    vendor_address_service = Mock()
    # No pre-existing VendorAddress rows -> _ensure_vendor_address takes its
    # create branch, which is the one a "was anything linked?" assertion reads.
    vendor_address_service.read_all_by_vendor_id.return_value = []

    address_connector = Mock()
    address_connector.sync_address_from_external.return_value = (
        minted_address if minted_address is not None else _address()
    )
    address_connector.sync_from_qbo_to_address.return_value = _address(id=901)

    connector = VendorVendorConnector(
        vendor_service=vendor_service,
        vendor_address_service=vendor_address_service,
        address_connector=address_connector,
        reconciliation_repo=Mock(),
    )
    return connector, vendor_service, vendor_address_service, address_connector


# --------------------------------------------------------------------------
# 1. The projection mints dbo.Address from the INLINE payload
# --------------------------------------------------------------------------


def test_projection_mints_address_from_inline_payload_not_from_staging():
    """The headline behavior: with the external record in hand, the address is
    projected from `BillAddr` under the synthetic `{vendor_id}_bill` identity,
    and `qbo.PhysicalAddress` is never read back."""
    connector, _, vendor_address_service, address_connector = _build_connector()
    # bill_addr_id IS populated -- the staging write still happens during the
    # transition. If the connector still preferred it, this test would pass
    # vacuously, so the staging read is asserted absent below.
    staging = _staging(bill_addr_id=555)

    connector.sync_from_qbo_vendor(staging, _external(bill_addr=_addr()))

    address_connector.sync_address_from_external.assert_called_once_with(
        qbo_id="1246_bill",
        realm_id=REALM,
        line1="PO Box 594",
        line2="Suite 200",
        city="Brentwood",
        country_sub_division_code="TN",
        postal_code="37024",
    )
    address_connector.sync_from_qbo_to_address.assert_not_called()
    vendor_address_service.create.assert_called_once_with(
        vendor_id="55", address_id="900", address_type_id=str(ADDRESS_TYPE_BILLING),
    )


def test_synthetic_identity_matches_the_string_the_staging_row_carries_today():
    """`_upsert_physical_address` stages the address under `f"{vendor.id}_bill"`.
    Reusing that exact string is what keeps the 190-odd already-minted
    `dbo.Address` rows MATCHED rather than duplicated under a new key."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(qbo_id="329"), _external("329", bill_addr=_addr()))

    kwargs = address_connector.sync_address_from_external.call_args.kwargs
    assert kwargs["qbo_id"] == "329_bill"


def test_realm_is_passed_through_untouched_including_none():
    """Realm scoping must fail CLOSED downstream, which it cannot do if this
    connector launders a NULL realm into `""` on the way in (an empty string is
    a value that can MATCH; None is the absence the callee must refuse)."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(realm_id=None), _external(bill_addr=_addr()))

    kwargs = address_connector.sync_address_from_external.call_args.kwargs
    assert kwargs["realm_id"] is None


def test_vendor_address_link_still_routes_through_vendor_address_with_billing_type():
    """The dbo.Vendor -> dbo.Address relationship is unchanged by this unit: the
    link is a `dbo.VendorAddress` row carrying the BILLING address type."""
    connector, _, vendor_address_service, _ = _build_connector()

    connector.sync_from_qbo_vendor(_staging(), _external(bill_addr=_addr()))

    assert vendor_address_service.create.call_args.kwargs["address_type_id"] == "1"
    assert ADDRESS_TYPE_BILLING == 1


# --------------------------------------------------------------------------
# 2. Blankness — a blank inline address is ABSENT
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blank_addr",
    [
        pytest.param(_addr(line1=None, line2=None, city=None,
                           country_sub_division_code=None, postal_code=None),
                     id="all-null"),
        pytest.param(_addr(line1="", line2="", city="",
                           country_sub_division_code="", postal_code=""),
                     id="all-empty-strings"),
        pytest.param(_addr(line1="   ", line2="   ", city="  ",
                           country_sub_division_code="  ", postal_code=" "),
                     id="whitespace-only"),
        pytest.param(_addr(line1=None, line2="Suite 200", city=None,
                           country_sub_division_code="TN", postal_code=None),
                     id="line2-and-state-only"),
    ],
)
def test_blank_inline_address_mints_nothing_and_links_nothing(blank_addr):
    """A `BillAddr` with no Line1, no City and no PostalCode is the shell QBO
    emits for a vendor with no address on file. Minting a `dbo.Address` of empty
    strings for it -- and linking it -- is what produced the 191 blank rows this
    guard exists to stop growing. line2/state alone do not rescue it: a state
    code with no street, city or ZIP is not an address."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(bill_addr_id=555), _external(bill_addr=blank_addr))

    address_connector.sync_address_from_external.assert_not_called()
    # ...and it must not quietly fall back to the staging read either -- that is
    # the very path that mints the blank row.
    address_connector.sync_from_qbo_to_address.assert_not_called()
    vendor_address_service.create.assert_not_called()
    vendor_address_service.repo.update_by_id.assert_not_called()


def test_absent_bill_addr_mints_nothing_and_does_not_fall_back_to_staging():
    """`BillAddr` omitted entirely is likewise absent. With a payload in hand the
    payload is authoritative -- falling back to a stale staging row here would
    relink an address QBO no longer reports."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(bill_addr_id=555), _external(bill_addr=None))

    address_connector.sync_address_from_external.assert_not_called()
    address_connector.sync_from_qbo_to_address.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_a_single_populated_field_is_enough_to_be_a_real_address():
    """The blankness rule is ALL of (line1, city, postal_code) empty -- so a
    city-only address is real and must still project. Without this the guard
    could tighten into a silent address-dropper."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(),
        _external(bill_addr=_addr(line1=None, city="Brentwood", postal_code=None)),
    )

    address_connector.sync_address_from_external.assert_called_once()


@pytest.mark.parametrize(
    "line1,city,postal_code,expected",
    [
        (None, None, None, True),
        ("", "", "", True),
        ("  ", "\t", "\n", True),
        ("PO Box 594", None, None, False),
        (None, "Brentwood", None, False),
        (None, None, "37024", False),
    ],
)
def test_inline_address_is_blank_truth_table(line1, city, postal_code, expected):
    """The predicate itself, independent of the connector wiring."""
    assert _inline_address_is_blank(
        line1=line1, city=city, postal_code=postal_code,
    ) is expected


# --------------------------------------------------------------------------
# 3. The closure pairs each staging row with its OWN external record
# --------------------------------------------------------------------------


class _FakeVendorRepo:
    """In-memory `QboVendorRepository`: `create` echoes a staging row built from
    the kwargs it was handed, so the rows the projection loop sees are the rows
    THIS page produced -- which is what makes a cross-wired pairing detectable."""

    def __init__(self):
        self.created = []
        self._next_id = 100

    def read_by_qbo_id_and_realm_id(self, *, qbo_id, realm_id):
        return None

    def create(self, **kwargs):
        self._next_id += 1
        row = _staging(
            qbo_id=kwargs.get("qbo_id"),
            realm_id=kwargs.get("realm_id"),
            bill_addr_id=kwargs.get("bill_addr_id"),
            display_name=kwargs.get("display_name"),
        )
        row.id = self._next_id
        self.created.append(row)
        return row


def _service_with_fakes():
    service = QboVendorService(repo=_FakeVendorRepo())
    # Staging address writes still happen during the transition; they are not
    # what this test is about, so they are stubbed rather than removed.
    service.physical_address_service = Mock()
    service.physical_address_service.read_by_qbo_id.return_value = None
    service.physical_address_service.create.return_value = SimpleNamespace(id=555)
    return service


def _client_returning(*externals):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.query_all_vendors.return_value = list(externals)
    return client


def test_closure_pairs_each_staging_row_with_its_own_external_record():
    """Two vendors in one page must not cross-wire. Taking vendor B's BillAddr
    while writing under vendor A's `{id}_bill` identity is silent, permanent
    address corruption on both rows, and a page of vendors is the ORDINARY
    case -- a one-vendor test would never see it."""
    service = _service_with_fakes()
    ext_a = _external("1246", display_name="Alpha Supply", bill_addr=_addr(city="Brentwood"))
    ext_b = _external("329", display_name="Beta Supply", bill_addr=_addr(city="Nashville"))
    connector = Mock()

    with patch(f"{SERVICE_MODULE}.QboVendorClient", return_value=_client_returning(ext_a, ext_b)), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector):
        outcome = service.sync_from_qbo(realm_id=REALM, sync_to_modules=True)

    assert outcome.projected_count == 2
    pairs = {
        call.args[0].qbo_id: call.args[1]
        for call in connector.sync_from_qbo_vendor.call_args_list
    }
    assert pairs["1246"] is ext_a
    assert pairs["329"] is ext_b


def test_projection_is_skipped_entirely_when_sync_to_modules_is_false():
    """`sync_to_modules=False` is a staging-only pull (the QBO sync router
    exposes it as a request flag); the service must not project on it. Note
    `scripts/sync_qbo_vendor.py` no longer uses it — U-513 ph2 moved it to
    `sync_to_modules=True` and deleted its own projection loop."""
    service = _service_with_fakes()
    connector = Mock()

    with patch(f"{SERVICE_MODULE}.QboVendorClient", return_value=_client_returning(_external())), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector):
        outcome = service.sync_from_qbo(realm_id=REALM, sync_to_modules=False)

    connector.sync_from_qbo_vendor.assert_not_called()
    assert outcome.synced_count == 1


def test_sync_to_vendors_without_a_payload_map_still_projects_every_row():
    """`_sync_to_vendors`'s payload map is optional, so a caller that has no
    external records (or a row whose id is missing from the map) still projects
    -- it just falls back to the staging address path."""
    service = _service_with_fakes()
    connector = Mock()
    rows = [_staging("1246"), _staging("329")]
    outcome = SyncOutcome.for_service_pull()

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector):
        service._sync_to_vendors(rows, outcome)

    assert outcome.projected_count == 2
    assert [c.args[1] for c in connector.sync_from_qbo_vendor.call_args_list] == [None, None]


# --------------------------------------------------------------------------
# 4. Fallbacks and defenses
# --------------------------------------------------------------------------


def test_no_payload_keeps_the_pre_u513_staging_read_path():
    """The staging read is still the address source for a caller that re-projects
    rows it did not fetch. No PULL takes that branch any more (U-513 ph2
    converted `scripts/sync_qbo_vendor.py`, the last one), but the branch stays
    until `qbo.PhysicalAddress` itself is dropped — deleting it early would
    blind any remaining payload-less caller to addresses entirely."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(bill_addr_id=555))

    address_connector.sync_from_qbo_to_address.assert_called_once_with(555)
    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_called_once_with(
        vendor_id="55", address_id="901", address_type_id="1",
    )


def test_no_payload_and_no_staging_address_links_nothing():
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(bill_addr_id=None))

    address_connector.sync_from_qbo_to_address.assert_not_called()
    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_mispaired_payload_is_refused_rather_than_written_under_the_wrong_identity():
    """Defense in depth behind the closure: if a payload ever arrives whose Id is
    not this staging row's, taking its address content would write ONE vendor's
    address under ANOTHER's identity. Refuse the payload; fall back to this
    row's own staging address (here: none, so nothing is written at all)."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(qbo_id="1246"), _external("329", bill_addr=_addr()))

    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_mispaired_payload_falls_back_to_this_rows_own_staging_address():
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(qbo_id="1246", bill_addr_id=555), _external("329", bill_addr=_addr()),
    )

    address_connector.sync_address_from_external.assert_not_called()
    address_connector.sync_from_qbo_to_address.assert_called_once_with(555)


def test_address_failure_does_not_fail_the_vendor_projection():
    """Unchanged failure isolation: an address error is logged and swallowed, so
    a bad address never holds the pull watermark over a vendor that synced fine."""
    connector, _, _, address_connector = _build_connector()
    address_connector.sync_address_from_external.side_effect = RuntimeError("address exploded")

    result = connector.sync_from_qbo_vendor(_staging(), _external(bill_addr=_addr()))

    assert result.id == 55


def test_connector_returning_no_address_links_nothing():
    """Defensive: if the shared connector ever answers `None` (its declared
    return type is `Address`, but a blank-guard of its own could change that),
    do not call `coerce_id(None)` and do not write a link."""
    connector, _, vendor_address_service, address_connector = _build_connector()
    address_connector.sync_address_from_external.return_value = None

    connector.sync_from_qbo_vendor(_staging(), _external(bill_addr=_addr()))

    vendor_address_service.create.assert_not_called()
