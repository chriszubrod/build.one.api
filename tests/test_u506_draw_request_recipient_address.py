"""U-506 — deterministic owner / remit-to address on the Draw Request "To" block.

`entities.invoice.api.router._resolve_draw_request_recipient` feeds three
client-facing surfaces — the Draw Request page, the Trend page, and the AIA G702
"TO OWNER:" block — and had ZERO coverage before this file
(`tests/test_invoice_draw_request.py` feeds literal `to_name`/`to_lines` straight
into the renderer, so it never exercises the resolution at all). It took
`pas[0]` out of a sproc with no `ORDER BY`, ignored the address-type slot
entirely, and never checked whether the `dbo.Address` it landed on was blank —
and most live ProjectAddress rows point at a fully blank Address (empty
StreetOne/City). Result: the owner block on a client-facing draw packet was
whatever row the engine happened to hand back, frequently rendering nothing.

Harness notes:
  * Pure-logic / no-live-DB. The four services the function lazy-imports are
    monkeypatched at their *module* attribute — the `from X import Y` inside the
    function body resolves at call time, so patching `X.Y` is exactly what the
    function sees.
  * Fixtures use the REAL `Address` / `ProjectAddress` dataclasses so a field
    rename cannot leave these tests passing against a shape that no longer exists.
  * The slot ids are imported from the QBO connector, NOT written as literals —
    see `test_slot_ids_are_the_writers_constants_not_the_addresstype_seed` for the
    trap that makes this load-bearing.
"""

import logging
import re

from entities.address.business.model import Address
from entities.invoice.api.router import _resolve_draw_request_recipient
from entities.project_address.business.model import ProjectAddress
from integrations.intuit.qbo.customer.connector.project.business.service import (
    ADDRESS_TYPE_BILLING,
    ADDRESS_TYPE_SHIPPING,
)
from tests.sproc_text import REPO_ROOT, sproc_body

OWNER = "Acme Owner LLC"
PROJECT_ID = 73

# Module paths patched below — the function imports from these at call time.
_PROJECT_MODULE = "entities.project.business.service"
_CUSTOMER_MODULE = "entities.customer.business.service"
_PROJECT_ADDRESS_MODULE = "entities.project_address.business.service"
_ADDRESS_MODULE = "entities.address.business.service"


# ── fixtures ────────────────────────────────────────────────────────────────

def _addr(id, *, street_one=None, street_two=None, city=None, state=None, postal=None,
          qbo_id=None):
    """`qbo_id` is the PROVENANCE discriminator, not decoration: every
    connector-written Address carries a synthetic `{qbo_id}_bill` / `_ship`, and a
    hand-entered one carries None. A slot-2 fixture without it models a HUMAN's
    billing address, not a QBO job site — which is the opposite of what a
    job-site test means to assert."""
    return Address(
        id=id,
        qbo_id=qbo_id,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        street_one=street_one,
        street_two=street_two,
        city=city,
        state=state,
        zip=postal,
        country=None,
    )


def _blank_addr(id):
    """The shape 29 of 33 live ProjectAddress rows point at: a row that exists
    and resolves, carrying nothing renderable."""
    return _addr(id, street_one="", street_two=None, city="", state=None, postal=None)


def _pa(id, *, address_id, address_type_id):
    return ProjectAddress(
        id=id,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        project_id=PROJECT_ID,
        address_id=address_id,
        address_type_id=address_type_id,
    )


class _FakeProject:
    customer_id = 9


class _FakeProjectService:
    def read_by_id(self, id):
        return _FakeProject()


class _FakeCustomer:
    def __init__(self, name):
        self.name = name


class _FakeCustomerService:
    def __init__(self, name):
        self._name = name

    def read_by_id(self, id):
        return _FakeCustomer(self._name)


class _FakeProjectAddressService:
    def __init__(self, rows):
        self._rows = rows

    def read_by_project_id(self, project_id):
        return list(self._rows)


class _FakeAddressService:
    def __init__(self, by_id, raises=False):
        self._by_id = by_id
        self._raises = raises
        self.calls = []

    def read_by_id(self, id):
        self.calls.append(id)
        if self._raises:
            raise RuntimeError("simulated address lookup failure")
        return self._by_id.get(id)


def _install(monkeypatch, rows, addresses, *, customer_name=OWNER, address_raises=False):
    """Wire the four lazy-imported services and hand back the address fake."""
    address_service = _FakeAddressService(addresses, raises=address_raises)
    monkeypatch.setattr(f"{_PROJECT_MODULE}.ProjectService", lambda: _FakeProjectService())
    monkeypatch.setattr(f"{_CUSTOMER_MODULE}.CustomerService", lambda: _FakeCustomerService(customer_name))
    monkeypatch.setattr(
        f"{_PROJECT_ADDRESS_MODULE}.ProjectAddressService",
        lambda: _FakeProjectAddressService(rows),
    )
    monkeypatch.setattr(f"{_ADDRESS_MODULE}.AddressService", lambda: address_service)
    return address_service


# ── behaviour ───────────────────────────────────────────────────────────────

def test_skips_blank_address_and_takes_the_real_one(monkeypatch):
    """A ProjectAddress row pointing at a fully blank Address is not an address.

    Mutation target: the `if lines:` content check.
    """
    rows = [
        _pa(1, address_id=10, address_type_id=ADDRESS_TYPE_BILLING),
        _pa(2, address_id=20, address_type_id=ADDRESS_TYPE_BILLING),
    ]
    addresses = {
        10: _blank_addr(10),
        20: _addr(20, street_one="100 Cedar Ln", city="Nashville", state="TN", postal="37201"),
    }
    _install(monkeypatch, rows, addresses)

    to_name, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_name == OWNER
    assert to_lines == ["100 Cedar Ln", "Nashville, TN 37201"]


def test_ignores_shipping_slot_when_billing_has_content(monkeypatch):
    """Billing wins even when a populated shipping row sorts first.

    Mutation target: the address-type slot filter.
    """
    rows = [
        _pa(1, address_id=10, address_type_id=ADDRESS_TYPE_SHIPPING),
        _pa(2, address_id=20, address_type_id=ADDRESS_TYPE_BILLING),
    ]
    addresses = {
        10: _addr(10, street_one="9 Jobsite Rd", city="Franklin", state="TN", postal="37064"),
        20: _addr(20, street_one="100 Cedar Ln", city="Nashville", state="TN", postal="37201"),
    }
    _install(monkeypatch, rows, addresses)

    _, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_lines == ["100 Cedar Ln", "Nashville, TN 37201"]


def test_never_falls_back_to_shipping_even_when_billing_is_blank(monkeypatch):
    """A blank billing slot degrades to NAME-ONLY. It must never reach shipping.

    CONTRACT CHANGE, 2026-09-25 (U-506 follow-up). This test previously asserted
    the opposite — that shipping is a fallback — and the fixture it used to prove
    it was called "9 Jobsite Rd", which is the whole problem: the shipping slot
    holds the SITE, and the connector says so ("SHIPPING: job-only … a job's
    ShipAddr is the SITE"). Rendering it under "TO OWNER:" on a client-facing
    G702 is exactly what U-506 P2 removed both ShipAddr links from the billing
    chain to prevent — a missing address is visible, a plausible-but-wrong one
    is not.

    The reachable path was real, not theoretical: `_clear_stale_connector_billing_link`
    deletes the BILLING link when the chain resolves nothing and never touches
    SHIPPING, so the connector manufactures this exact row shape. Measured
    2026-09-25: one project (30, OHR2) holds a populated shipping slot carrying
    its own street, outranked today only by its billing link.
    """
    rows = [
        _pa(1, address_id=10, address_type_id=ADDRESS_TYPE_BILLING),   # blank
        _pa(2, address_id=20, address_type_id=ADDRESS_TYPE_SHIPPING),  # the job site
    ]
    addresses = {
        10: _blank_addr(10),
        20: _addr(20, street_one="9 Jobsite Rd", city="Franklin", state="TN", postal="37064",
                  qbo_id="382_ship"),
    }
    _install(monkeypatch, rows, addresses)

    _, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_lines == []
    assert "9 Jobsite Rd" not in " ".join(to_lines)


def test_shipping_only_project_renders_name_only(monkeypatch):
    """No billing row AT ALL (not merely blank) still must not reach shipping.

    The sibling above covers a blank billing Address; this covers the shape the
    connector's delete path actually produces — the billing LINK removed
    entirely, leaving a lone populated shipping row.
    """
    rows = [_pa(1, address_id=20, address_type_id=ADDRESS_TYPE_SHIPPING)]
    addresses = {
        20: _addr(20, street_one="9 Jobsite Rd", city="Franklin", state="TN", postal="37064",
                  qbo_id="382_ship"),
    }
    _install(monkeypatch, rows, addresses)

    to_name, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_lines == []
    assert to_name == OWNER


def test_hand_entered_slot_two_address_is_honoured_as_the_owner(monkeypatch):
    """Slot 2 means TWO different things, and provenance is what separates them.

    The connector calls id 2 SHIPPING, but `dbo.AddressType` seeds it as
    *Billing* — and `POST /create/project_address` accepts the raw id, so a
    person entering an owner's remit-to address through the API lands here
    meaning the opposite of what the connector means. Dropping slot 2 outright
    to kill the job-site fall-through would have silently stranded that person's
    address and rendered name-only instead.

    A connector row always carries a synthetic `Address.QboId`; a hand-entered
    one carries none. Measured 2026-09-25 there are zero hand-entered rows, so
    this path is latent today — which is exactly why it needs a test rather than
    a measurement to keep it correct.
    """
    rows = [_pa(1, address_id=20, address_type_id=ADDRESS_TYPE_SHIPPING)]
    addresses = {
        20: _addr(20, street_one="77 Owner Way", city="Nashville", state="TN",
                  postal="37205", qbo_id=None),   # hand-entered: no QBO identity
    }
    _install(monkeypatch, rows, addresses)

    to_name, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_lines == ["77 Owner Way", "Nashville, TN 37205"]
    assert to_name == OWNER


def test_connector_billing_still_outranks_a_hand_entered_slot_two_row(monkeypatch):
    """Tier order survives the provenance gate: slot 1 wins even when a valid
    hand-entered slot-2 row sorts first."""
    rows = [
        _pa(1, address_id=20, address_type_id=ADDRESS_TYPE_SHIPPING),
        _pa(2, address_id=10, address_type_id=ADDRESS_TYPE_BILLING),
    ]
    addresses = {
        20: _addr(20, street_one="77 Owner Way", city="Nashville", state="TN",
                  postal="37205", qbo_id=None),
        10: _addr(10, street_one="100 Cedar Ln", city="Nashville", state="TN",
                  postal="37201", qbo_id="382_bill"),
    }
    _install(monkeypatch, rows, addresses)

    _, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_lines == ["100 Cedar Ln", "Nashville, TN 37201"]


def test_deterministic_under_reversed_input_row_order(monkeypatch):
    """The sproc had no ORDER BY, so row arrival order is an engine detail. Two
    populated billing rows must always resolve to the SAME one (lowest Id).

    Mutation target: the `sorted(...)` by Id.
    """
    low = _pa(3, address_id=30, address_type_id=ADDRESS_TYPE_BILLING)
    high = _pa(7, address_id=70, address_type_id=ADDRESS_TYPE_BILLING)
    addresses = {
        30: _addr(30, street_one="3 Low St", city="Nashville", state="TN", postal="37201"),
        70: _addr(70, street_one="7 High St", city="Nashville", state="TN", postal="37201"),
    }

    _install(monkeypatch, [low, high], addresses)
    forward = _resolve_draw_request_recipient(PROJECT_ID)

    _install(monkeypatch, [high, low], addresses)
    reversed_ = _resolve_draw_request_recipient(PROJECT_ID)

    assert forward == reversed_
    assert forward[1] == ["3 Low St", "Nashville, TN 37201"]


def test_tolerates_duplicate_billing_rows(monkeypatch):
    """Project-73 shape: two identical type-1 rows pointing at the same Address.
    The block must render once, not twice."""
    rows = [
        _pa(41, address_id=10, address_type_id=ADDRESS_TYPE_BILLING),
        _pa(42, address_id=10, address_type_id=ADDRESS_TYPE_BILLING),
    ]
    addresses = {10: _addr(10, street_one="100 Cedar Ln", city="Nashville", state="TN", postal="37201")}
    address_service = _install(monkeypatch, rows, addresses)

    to_name, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_name == OWNER
    assert to_lines == ["100 Cedar Ln", "Nashville, TN 37201"]
    # Resolution short-circuits on the first row that yields content — the
    # duplicate is never even fetched, let alone appended a second time.
    assert address_service.calls == [10]


def test_never_raises_when_the_address_lookup_explodes(monkeypatch, caplog):
    """Isolation is load-bearing: this runs OUTSIDE the packet generator's try and
    `generate_invoice_packet_router` converts any escape into a 500, so letting it
    raise fails the entire draw packet. Isolated, but logged LOUD and distinctly.

    Mutation target: the `except Exception` around the address block.
    """
    rows = [_pa(1, address_id=10, address_type_id=ADDRESS_TYPE_BILLING)]
    _install(monkeypatch, rows, {}, address_raises=True)

    caplog.set_level(logging.WARNING, logger="entities.invoice.api.router")
    to_name, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert (to_name, to_lines) == (OWNER, [])
    assert "draw_request.recipient_address_failed" in caplog.text
    # "exploded" must never be reported as the clean "absent" outcome.
    assert "draw_request.recipient_address_missing" not in caplog.text
    failed = [r for r in caplog.records if "draw_request.recipient_address_failed" in r.getMessage()]
    assert failed and failed[0].levelno >= logging.ERROR
    assert failed[0].exc_info is not None


def test_returns_name_only_when_there_is_no_address_anywhere(monkeypatch, caplog):
    """Pins the degraded path for the ~306 invoices whose project has no address
    on file: name-only, ("Name", []) — never ("", []) and never an exception."""
    _install(monkeypatch, [], {})

    caplog.set_level(logging.WARNING, logger="entities.invoice.api.router")
    result = _resolve_draw_request_recipient(PROJECT_ID)

    assert result == (OWNER, [])
    assert "draw_request.recipient_address_missing" in caplog.text
    assert "draw_request.recipient_address_failed" not in caplog.text


def test_slot_ids_are_the_writers_constants_not_the_addresstype_seed(monkeypatch):
    """CONSTANT TRAP. `dbo.AddressType` is seeded 1=Legal / 2=Billing / 3=Shipping,
    but both QBO address writers hard-code BILLING=1 / SHIPPING=2 — so every
    "billing" address in the system is physically stamped Legal. Filtering on the
    seed values would match nothing at all. This pins the writer's values and
    proves a row carrying the seed's Shipping id (3) is not picked up."""
    assert (ADDRESS_TYPE_BILLING, ADDRESS_TYPE_SHIPPING) == (1, 2)

    rows = [_pa(1, address_id=10, address_type_id=3)]
    addresses = {10: _addr(10, street_one="3 Seed Shipping Rd", city="Franklin", state="TN", postal="37064")}
    _install(monkeypatch, rows, addresses)

    assert _resolve_draw_request_recipient(PROJECT_ID) == (OWNER, [])


def test_no_project_id_returns_empty_pair(monkeypatch):
    """No project => no lookups at all; the caller still gets an unpackable pair."""
    address_service = _install(monkeypatch, [_pa(1, address_id=10, address_type_id=ADDRESS_TYPE_BILLING)], {})

    assert _resolve_draw_request_recipient(None) == ("", [])
    assert address_service.calls == []


def test_row_with_null_address_id_is_skipped(monkeypatch):
    """`ProjectAddress.AddressId` is nullable — a dangling row must not shadow a
    real one nor blow up the lookup."""
    rows = [
        _pa(1, address_id=None, address_type_id=ADDRESS_TYPE_BILLING),
        _pa(2, address_id=20, address_type_id=ADDRESS_TYPE_BILLING),
    ]
    addresses = {20: _addr(20, street_one="100 Cedar Ln", city="Nashville", state="TN", postal="37201")}
    address_service = _install(monkeypatch, rows, addresses)

    _, to_lines = _resolve_draw_request_recipient(PROJECT_ID)

    assert to_lines == ["100 Cedar Ln", "Nashville, TN 37201"]
    assert address_service.calls == [20]


# ── sproc contract ──────────────────────────────────────────────────────────

def test_read_project_address_by_project_id_is_ordered():
    """The client-side sort above is only half the fix — without a server-side
    ORDER BY, `read_by_project_id` is non-deterministic for every other caller too.

    Mutation target: the `ORDER BY [Id] ASC` in the .sql file.
    """
    body = sproc_body(
        REPO_ROOT / "entities" / "project_address" / "sql" / "dbo.project_address.sql",
        "ReadProjectAddressByProjectId",
    )
    assert re.search(r"ORDER\s+BY\s+\[?Id\]?\s+ASC", body, re.IGNORECASE), body
