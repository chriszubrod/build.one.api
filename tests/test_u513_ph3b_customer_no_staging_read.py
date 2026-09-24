"""U-513 ph3b (CUSTOMER) — the last `qbo.PhysicalAddress` READERS are deleted.

WHAT CHANGED
------------
Two readers, both in the customer family:

    `CustomerProjectConnector._own_address_id_from_staging`
        the JOB half's shared staging arm, reached from billing Link 1
        (`_own_billing_address_id`) and from the whole SHIPPING slot
        (`_own_shipping_address_id`), plus its two private helpers
        `_staged_address_is_blank` / `_is_blank_staged_address`.

    `CustomerCustomerConnector._own_billing_address`'s staging fallback
        the PARENT half's, already proven unreachable from the pull.

Both slots are now payload-ONLY. Nothing in this package reads
`qbo.Customer.BillAddrId` / `ShipAddrId`, `qbo.PhysicalAddress`, or
`sync_from_qbo_to_address`.

⚠️ THE ONE PLACE THIS CHANGES BEHAVIOUR, AND IT IS NOT HIDDEN
-------------------------------------------------------------
`CustomerProjectConnector.heal_missing_mapping` reaches `_sync_addresses` with
NO payload. It is driven by the INVOICE pull
(`integrations/intuit/qbo/invoice/connector/invoice/business/service.py`), which
has a QBO Invoice in scope and no QBO Customer payload at all — there is none to
thread. After ph3b it therefore resolves no own billing address and no shipping
address.

THE PRICE, MEASURED — narrower than "no address", and that is load-bearing:

  * billing Link 1 (the job's OWN BillAddr) — live measurement 2026-09-23:
    own-bill wins for 2 of the 63 projects the chain covers.
  * the SHIPPING slot, entirely — it has no chain behind it, by design
    (invariant 1: a parent's address is a SIBLING project's street).
  * billing Link 2 is UNAFFECTED. `_parent_billing_address_id` reads
    `dbo.Address` by the parent's synthetic identity and never touched staging.
    It is the winner for 61 of those 63 projects, so the heal path still fills
    the slot that renders on a Draw Request for nearly every job it reaches.
    `test_the_heal_path_still_inherits_the_parents_mailing_address` pins this,
    and it is the reason the gap is a gap and not an outage.

WHY IT IS BOUNDED, AND WHERE THE BOUND ENDS
-------------------------------------------
Nothing is deleted and QBO still holds the address. The next customer pull that
carries this job threads the payload and mints it — and a pull whose projection
FAILED holds its watermark (`SyncOutcome.should_hold`;
`integrations/intuit/qbo/base/watermark.py`), so it re-pulls the same window on
the next tick. `test_the_next_payload_bearing_pull_mints_what_the_heal_path_
could_not` proves that end to end on ONE connector rather than asserting it.

⚠️ The residual that is NOT tick-bounded, recorded so nobody re-derives it under
pressure: if a Project loses its dbo identity AFTER a successful pull — the
`SetProjectQboIdentity` theft-clear on a name collision is the realistic way —
the customer watermark has already advanced past that job, so heal re-binds it
with no own address and the gap persists until QBO next touches that customer
(or the watermark is replayed). Bounded by "QBO edits the job", not by one tick.
It is NOT fixable inside the connector: on that path the payload does not exist
at all, so any "fallback" would be inventing a source. The fix, if it is ever
wanted, is upstream — thread a payload into heal, or re-project on identity
loss — and it is a separate unit.

⚠️ Part of the gap was ALREADY LIVE before ph3b. ph3a stopped writing the FKs,
so every `qbo.Customer` row staged since carries NULL `bill_addr_id` /
`ship_addr_id` and the deleted arm already resolved nothing for it. ph3b extends
that to the pre-ph3a rows whose FK `UpdateQboCustomerByQboId` coalesced into
place.

Pure logic throughout: the U-506 P1 harness's in-memory fakes, no live DB.
"""
import ast
import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from conftest import mock_qbo_app_lock_granted
from integrations.intuit.qbo.customer.business import service as customer_service_module
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.connector.customer.business import (
    service as customer_connector_module,
)
from integrations.intuit.qbo.customer.connector.customer.business.service import (
    CustomerCustomerConnector,
    address_fields_are_blank,
    billing_address_qbo_id,
)
from integrations.intuit.qbo.customer.connector.project.business import (
    service as project_connector_module,
)
from integrations.intuit.qbo.customer.connector.project.business import service as svc
from integrations.intuit.qbo.customer.connector.project.business.service import (
    ADDRESS_TYPE_BILLING,
    ADDRESS_TYPE_SHIPPING,
    PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING,
    CustomerProjectConnector,
    shipping_address_qbo_id,
)

# The U-506 P1 harness, reused rather than re-faked: a divergent copy is how one
# of them quietly stops modelling the shipped shape, and this file turns on
# details that harness already gets right — staging vs dbo id keyspaces kept
# disjoint, realm scoping that fails CLOSED, `ProjectAddress` as an observable
# in-memory table, and the parent's `_ship` row permanently wired in as a trap.
from tests.test_u506_p1_project_address_parent_fallback import (
    BLANK_OWN_BILL,
    BLANK_OWN_SHIP,
    HAND_SET_ADDRESS_ID,
    JOB_BILL_QBO_ID,
    JOB_QBO_ID,
    JOB_SHIP_QBO_ID,
    PARENT_BILL_ADDRESS_ID,
    PARENT_REF,
    PARENT_SHIP_QBO_ID,
    PROJECT_ID,
    REAL_OWN_BILL,
    REAL_OWN_SHIP,
    REAL_PARENT_BILL_ADDRESS,
    REALM,
    SIBLING_STREET_ADDRESS,
    _FakeAddressConnector,
    _FakeAddressService,
    _FakeProjectAddressService,
    _dbo_address_id,
    _own_addresses_payload,
    _qbo_customer,
)
from tests.test_u269_qbo_staging_try_except import _client_cm

FASTPATH_LOCK_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"

# The two staging FK columns ph3b orphaned, and the two `physical_address`
# staging seams that went with them. Named once so every structural assertion
# below is driven off the SAME list.
STAGING_FK_ATTRS = {"bill_addr_id", "ship_addr_id"}
STAGING_SEAM_ATTRS = {"qbo_physical_address_service", "sync_from_qbo_to_address"}
DELETED_HELPERS = (
    "_own_address_id_from_staging",
    "_staged_address_is_blank",
    "_is_blank_staged_address",
)

# The customer PACKAGE, both halves. Scanning only the project connector — which
# is what the ph3a inventory did — would miss a staging read reinstated on the
# parent side, and the parent side is where the `dbo.Address` row the whole
# billing fallback depends on is MINTED.
CONNECTOR_MODULES = (project_connector_module, customer_connector_module)


# --------------------------------------------------------------------------
# A fake dbo.Project, so heal-then-pull is ONE sequence and not two mocks
# --------------------------------------------------------------------------

class _FakeProjectService:
    """In-memory `dbo.Project` for exactly one row.

    ⚠️ Load-bearing for the self-healing claim: `set_qbo_identity` really
    stamps, so after `heal_missing_mapping` binds the project, the SECOND step
    reaches `run_identity_fastpath_dbo_only`'s HIT branch the same way the next
    customer pull would. Staging that with two independently-configured Mocks
    would let the test assert the conclusion it is supposed to be proving.
    """

    def __init__(self, project):
        self.project = project
        self.repo = SimpleNamespace(
            set_qbo_identity=self._set_qbo_identity,
            update_by_id=self._update_by_id,
        )

    def read_by_name(self, name):
        return self.project if self.project.name == name else None

    def read_by_id(self, id):
        return self.project if int(id) == int(self.project.id) else None

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        """Realm-scoped and FAILS CLOSED, mirroring `ReadProjectByQboIdentity`
        — a fake that ignored realm would let invariant 6 pass while production
        served one realm's project to another realm's invoice."""
        if not self.project.qbo_id or self.project.qbo_id != qbo_id:
            return None
        if (self.project.realm_id or "") != (realm_id or ""):
            return None
        return self.project

    def _set_qbo_identity(self, *, id, qbo_id, realm_id):
        self.project.qbo_id = qbo_id
        self.project.realm_id = realm_id

    def _update_by_id(self, project):
        return project


def _project(name="BD - 4527 Beacon Dr."):
    return SimpleNamespace(
        id=PROJECT_ID, public_id="pub-p88", name=name, description="",
        status="active", customer_id=None, qbo_id=None, realm_id=None,
    )


def _build(*, parent_address=None, project=None):
    """`CustomerProjectConnector` on the U-506 fakes, with a real-ish Project
    store so the heal -> pull sequence can be driven end to end.

    `customer_service.read_by_qbo_identity` returns None deliberately: the
    parent-Customer resolution is not this unit's subject, and a bare Mock's
    truthy return would drag `verify_identity_dbo_only` into every test here.
    """
    addresses = [SIBLING_STREET_ADDRESS]
    if parent_address is not None:
        addresses.append(parent_address)
    customer_service = Mock()
    customer_service.read_by_qbo_identity.return_value = None
    return CustomerProjectConnector(
        project_service=_FakeProjectService(project if project is not None else _project()),
        project_address_service=_FakeProjectAddressService(),
        address_connector=_FakeAddressConnector(),
        reconciliation_repo=Mock(),
        customer_service=customer_service,
        qbo_customer_repo=Mock(),
        address_service=_FakeAddressService(addresses=addresses),
    )


def _links_of_type(connector, address_type_id):
    return [
        address_id
        for type_id, address_id in connector.project_address_service.links()
        if type_id == address_type_id
    ]


def _assert_touched_no_staging(connector):
    """The headline claim of the whole unit, as one reusable assertion: the
    `qbo.PhysicalAddress` seams on the address-connector fake came back
    UNTOUCHED. Both are kept on the fake precisely so this is an assertion about
    the shipped code rather than about a fake that lacks the method."""
    assert connector.address_connector.staging_reads() == [], (
        "a qbo.PhysicalAddress row was read -- ph3b deleted every reader"
    )
    assert connector.address_connector.synced == [], (
        "sync_from_qbo_to_address was called -- ph3b deleted every caller"
    )


# ===========================================================================
# Section 0 — STRUCTURAL: zero readers left, package-wide
# ===========================================================================

def _attribute_readers(module, attrs):
    """Every function in `module` whose body accesses one of `attrs`, found by
    AST rather than by grepping source text -- so the several deliberate
    mentions in docstrings and comments (ph3b left a lot of history behind)
    cannot make this pass or fail for the wrong reason."""
    tree = ast.parse(inspect.getsource(module))
    readers = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in attrs:
                readers.add(f"{module.__name__.rsplit('.', 3)[0]}:{node.name}")
    return readers


@pytest.mark.parametrize(
    "module, control_attr",
    [
        # An attribute each module certainly DOES read, and would be broken
        # without: the job's link to its owner, and the payload's inline address.
        (project_connector_module, "parent_ref_value"),
        (customer_connector_module, "bill_addr"),
    ],
)
def test_the_ast_scanner_actually_finds_things(module, control_attr):
    """⚠️ ANTI-TAUTOLOGY CONTROL, and the first thing to check if the three
    tests below ever look suspiciously easy.

    `_attribute_readers` is the instrument every structural assertion in this
    file depends on. A typo in the walk, a wrong node type, or a module that
    fails to re-parse would make it return an empty set for ANYTHING, and every
    "no reader survived" assertion would pass vacuously and forever.

    Run PER MODULE, not once across both: the scans below span the whole
    package, so a control that only proved the scanner works on ONE module would
    leave the other's "zero readers" result unverified — which is exactly the
    half a reinstated staging read would hide in.
    """
    assert _attribute_readers(module, {control_attr}), (
        f"the AST scanner found no reader of {control_attr} in {module.__name__} "
        "-- it is broken there, and every 'no staging reader survived' "
        "assertion covering that module is vacuous"
    )


def test_no_slot_reads_the_staging_fk_columns_any_more():
    """⛔ THE ph3b GATE. Section 1 proves the payload path WORKS; it cannot
    prove that no OTHER reader of the FK columns survived elsewhere in the
    package — and a surviving reader is precisely the shape that fails
    SILENTLY, because every read on this path is blank-guarded and
    failure-isolated and `UpdateQboCustomerByQboId` COALESCES the FKs (a row
    staged before ph3a keeps its id, so a reinstated read would find real data
    and quietly serve a value nothing refreshes).

    Asserted as ZERO readers across BOTH halves of the package, by AST, so a new
    reader ANYWHERE in either file fails here — not just one re-added to the two
    functions that used to have it.
    """
    readers = set()
    for module in CONNECTOR_MODULES:
        readers |= _attribute_readers(module, STAGING_FK_ATTRS)
    assert readers == set(), (
        f"a reader of {sorted(STAGING_FK_ATTRS)} is back: {sorted(readers)}. "
        "ph3b is dropping those columns and the table behind them; this read "
        "will keep the dependency alive, and it will do it silently"
    )


def test_no_staging_seam_of_the_physical_address_package_is_touched():
    """The sibling structural claim, and the one that survives the FK columns
    being dropped: even with no FK to read, a connector could reach
    `qbo.PhysicalAddress` through the address connector's staging service or its
    `sync_from_qbo_to_address` mint. Neither may be referenced.

    This is what makes the package safe for the sibling unit that DELETES the
    `physical_address` staging code outright — an unreferenced seam cannot break
    when it disappears.
    """
    users = set()
    for module in CONNECTOR_MODULES:
        users |= _attribute_readers(module, STAGING_SEAM_ATTRS)
    assert users == set(), (
        f"a qbo.PhysicalAddress seam is referenced again: {sorted(users)}"
    )


@pytest.mark.parametrize("helper", DELETED_HELPERS)
def test_the_deleted_staging_helpers_stay_deleted(helper):
    """Asserting ABSENCE rather than deleting the test: these three are the
    whole of what came out of `CustomerProjectConnector`, and re-adding any one
    of them is how the staging dependency comes back wearing its old name."""
    assert not hasattr(CustomerProjectConnector, helper), (
        f"{helper} is back on CustomerProjectConnector -- ph3b removed it with "
        "the last reader of qbo.PhysicalAddress"
    )


# ===========================================================================
# Section 1 — both own slots STILL project from the payload, same identities
# ===========================================================================

def test_both_own_slots_still_project_from_the_payload():
    """The other half of the gate. "No staging reader" must not have been
    achieved by breaking the slots.

    Driven with the staging FKs deliberately POPULATED and pointing at real,
    non-blank rows — the fixture that used to make the staging arm succeed — so
    a regression to staging is distinguishable from the payload working: the ids
    below are the PAYLOAD block (4xxx), never `_dbo_address_id` (1xxx).
    """
    connector = _build(parent_address=None)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    connector._sync_addresses(
        job, PROJECT_ID,
        external_customer=_own_addresses_payload(
            job, bill=REAL_OWN_BILL, ship=REAL_OWN_SHIP
        ),
    )

    bill_id = connector.address_connector.address_id_for(JOB_BILL_QBO_ID)
    ship_id = connector.address_connector.address_id_for(JOB_SHIP_QBO_ID)
    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [bill_id]
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [ship_id]
    assert bill_id != _dbo_address_id(REAL_OWN_BILL), "this is the staging id"
    assert ship_id != _dbo_address_id(REAL_OWN_SHIP), "this is the staging id"
    _assert_touched_no_staging(connector)


def test_invariant_5_the_synthetic_identities_are_unchanged():
    """⚠️ INVARIANT 5 — MIGRATION SAFETY, and the reason ph3b needs no data
    migration at all. `<job QboId>_bill` / `<job QboId>_ship` are byte-identical
    to what the staging hop stamped, so the existing `dbo.Address` rows are
    MATCHED, not re-keyed.

    Realm is pinned alongside: a mint under the wrong realm would be INVISIBLE —
    the row exists, and `_parent_billing_address_id`'s fail-closed identity read
    simply never finds it.
    """
    connector = _build(parent_address=None)
    job = _qbo_customer()

    connector._sync_addresses(
        job, PROJECT_ID,
        external_customer=_own_addresses_payload(
            job, bill=REAL_OWN_BILL, ship=REAL_OWN_SHIP
        ),
    )

    minted = {m["qbo_id"]: m for m in connector.address_connector.minted}
    assert sorted(minted) == sorted([
        billing_address_qbo_id(JOB_QBO_ID), shipping_address_qbo_id(JOB_QBO_ID),
    ])
    assert billing_address_qbo_id(JOB_QBO_ID) == f"{JOB_QBO_ID}_bill"
    assert shipping_address_qbo_id(JOB_QBO_ID) == f"{JOB_QBO_ID}_ship"
    for call in minted.values():
        assert call["realm_id"] == REALM
        assert call["source_ref"] == f"QboCustomer:{JOB_QBO_ID}"


# ===========================================================================
# Section 2 — ⚠️ THE HEAL-PATH GAP: what it costs, and that it self-heals
# ===========================================================================

def _heal(connector, job):
    """Run `heal_missing_mapping` the way the INVOICE pull does: one argument,
    no Customer payload, because on that path there is none to thread."""
    return connector.heal_missing_mapping(job)


def test_the_heal_path_mints_no_address_when_no_payload_is_threaded():
    """⚠️ THE GAP, asserted rather than described — the deliberate, accepted,
    bounded cost of ph3b.

    Built the worst way on purpose: BOTH staging FKs populated and pointing at
    real non-blank rows, AND no parent address, so every source the chain has
    ever had is either present-but-forbidden or absent. Nothing may resolve.

    Note what is NOT lost: the heal itself still succeeds and the Project is
    still bound. This is an address gap, not a mapping failure — which is what
    keeps the invoice pull's own no-invoice window closed.
    """
    connector = _build(parent_address=None)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    healed = _heal(connector, job)

    assert healed is not None, (
        "heal stopped binding the Project -- ph3b was supposed to cost an "
        "ADDRESS, not the mapping the invoice pull is calling heal for"
    )
    assert healed.qbo_id == job.qbo_id, "the identity stamp is the heal's actual job"
    assert connector.address_connector.minted == [], (
        "an address was minted with no payload in hand -- from where? the gap "
        "has been 'fixed' by inventing a source"
    )
    assert connector.project_address_service.links() == []
    _assert_touched_no_staging(connector)


def test_the_heal_path_still_inherits_the_parents_mailing_address():
    """The gap is NARROWER than "no address", and that is load-bearing.

    `_parent_billing_address_id` reads `dbo.Address` by the parent's synthetic
    identity — it never touched staging, so ph3b did not touch it. Live
    measurement 2026-09-23: parent-bill wins for 61 of the 63 projects the chain
    covers, so the heal path still fills the slot that actually renders under
    "TO OWNER:" on a Draw Request for nearly every job it reaches.

    SHIPPING still resolves nothing, and must (invariant 1).
    """
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    _heal(connector, job)

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [PARENT_BILL_ADDRESS_ID], (
        "the PARENT link went down with the staging arm -- it reads dbo.Address "
        "and must be unaffected; the gap would then be an outage"
    )
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == []
    _assert_touched_no_staging(connector)


def test_the_next_payload_bearing_pull_mints_what_the_heal_path_could_not():
    """⚠️ THE SELF-HEALING CLAIM, PROVEN — the companion this unit is not
    allowed to ship without.

    "It fills a tick later" is only acceptable if a later pull actually fills
    it. So: ONE connector, ONE in-memory `ProjectAddress` table, ONE Project
    row, two steps.

      1. `heal_missing_mapping` with no payload -> the Project is bound and NO
         address link exists. (Same state as the test above.)
      2. `sync_from_qbo_customer(job, payload)` -> the identity heal stamped in
         step 1 puts this on the fast path's HIT branch, exactly where the next
         customer pull lands, and BOTH slots fill.

    Step 2 is not a re-staged fixture: it works because step 1's
    `set_qbo_identity` really stamped, which is why `_FakeProjectService`
    exists.
    """
    connector = _build(parent_address=None)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    # --- tick N: the invoice pull heals the mapping, with no payload ---------
    _heal(connector, job)
    assert connector.project_address_service.links() == [], (
        "precondition broken: the heal path resolved an address after all"
    )

    # --- tick N+1: the customer pull carries this job, payload and all -------
    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector.sync_from_qbo_customer(
            job,
            _own_addresses_payload(job, bill=REAL_OWN_BILL, ship=REAL_OWN_SHIP),
        )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [
        connector.address_connector.address_id_for(JOB_BILL_QBO_ID)
    ], "the gap did NOT self-heal -- it is permanent data loss, not a bounded cost"
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == [
        connector.address_connector.address_id_for(JOB_SHIP_QBO_ID)
    ], "the SHIPPING gap did NOT self-heal -- and shipping has no second source"
    _assert_touched_no_staging(connector)


def test_a_direct_one_argument_sync_is_the_other_payload_less_caller():
    """The second of the two payload-less callers, and the harmless one: a bare
    `sync_from_qbo_customer(row)` — a test/console shape, never the pull
    (`QboCustomerService._sync_to_projects` always threads the payload map).

    Pinned so that "no production caller lands here" stays a claim about the
    call sites and not about the connector: the connector must not crash, and it
    must not invent an address.
    """
    connector = _build(parent_address=None)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)
    connector.project_service.project.qbo_id = job.qbo_id
    connector.project_service.project.realm_id = job.realm_id

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector.sync_from_qbo_customer(job)  # must not raise

    assert connector.address_connector.minted == []
    assert connector.project_address_service.links() == []
    _assert_touched_no_staging(connector)


def test_the_heal_path_can_clear_a_connector_minted_link_and_a_later_pull_restores_it():
    """⚠️ THE EDGE THIS UNIT REFUSED TO PAPER OVER.

    `_sync_addresses` clears a CONNECTOR-MINTED billing link whenever the chain
    resolves nothing (U-506 P2: a stale link mails a financial document to a
    FORMER owner). On the payload-less heal path that decision is now made
    without the job's own address in hand, so a link the job's own BillAddr was
    holding up can be dropped.

    Reachability is narrow: the link is written under the identity stamp, so a
    Project with a connector-minted link but NO dbo identity must have LOST its
    identity afterwards. It is also already reachable post-ph3a for any row
    staged with NULL FKs. It was left as-is rather than guarded on
    `external_customer is not None`, which would make the clear silently
    conditional on its CALLER instead of on the data.

    Both halves are pinned: it clears, and the next payload-bearing pull puts it
    back. An unrecoverable clear would be a different decision.
    """
    connector = _build(parent_address=None)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)
    # A connector-minted link from an earlier pull: `_FakeAddressService`
    # reports a qbo_id for every id except HAND_SET_ADDRESS_ID.
    connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=4321, address_type_id=ADDRESS_TYPE_BILLING,
    )

    _heal(connector, job)

    assert connector.project_address_service.deleted == [1], (
        "the clear did not fire -- then this edge does not exist and the "
        "docstring on _own_billing_address_id is wrong. (Asserted on the DELETE "
        "and not just on an empty table: a link that was never created would "
        "leave the table empty too.)"
    )
    assert connector.project_address_service.links() == []

    with patch(FASTPATH_LOCK_TARGET, mock_qbo_app_lock_granted):
        connector.sync_from_qbo_customer(
            job, _own_addresses_payload(job, bill=REAL_OWN_BILL),
        )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [
        connector.address_connector.address_id_for(JOB_BILL_QBO_ID)
    ], "the cleared link was not restored -- the clear is data loss, not churn"


def test_invariant_4_the_heal_path_never_clears_a_HAND_SET_link():
    """⚠️ INVARIANT 4, on the path that now resolves least. A hand-entered
    address has no `dbo.Address.qbo_id`, and the connector must never clobber
    human data — least of all on a path that is resolving nothing because it was
    handed nothing."""
    connector = _build(parent_address=None)
    connector.project_address_service.create(
        project_id=PROJECT_ID, address_id=HAND_SET_ADDRESS_ID,
        address_type_id=ADDRESS_TYPE_BILLING,
    )

    _heal(connector, _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP))

    assert connector.project_address_service.links() == [
        (ADDRESS_TYPE_BILLING, HAND_SET_ADDRESS_ID)
    ]
    assert connector.project_address_service.deleted == []


# ===========================================================================
# Section 3 — the eight invariants, re-asserted against the payload-only shape
# ===========================================================================

def test_invariant_1_shipping_never_inherits_from_the_parent():
    """⚠️ THE 16-PROJECT ERROR PATH, and the single most damaging change that
    could be made to this module. A parent's address is a SIBLING project's
    street — wrong for 16 of the 61 projects U-506 P1 covered.

    ph3b makes the temptation sharper, not weaker: the shipping slot now
    resolves NOTHING on the payload-less path, and "give it the parent as a
    fallback" is the obvious-looking way to close that. It is wrong. The trap is
    live in the fixture — `SIBLING_STREET_ADDRESS` sits in `dbo.Address` under
    `<parent>_ship`, non-blank and ready to resolve — so a fallback of either
    shape fills the slot here immediately.
    """
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer(bill_addr_id=REAL_OWN_BILL, ship_addr_id=REAL_OWN_SHIP)

    # (a) the payload-less path: nothing, not the parent's anything.
    _heal(connector, job)
    assert _links_of_type(connector, ADDRESS_TYPE_SHIPPING) == []

    # (b) the payload path, with the job's OWN ShipAddr blank.
    payload_conn = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    payload_conn._sync_addresses(
        job, PROJECT_ID,
        external_customer=_own_addresses_payload(
            job, bill=REAL_OWN_BILL, ship=BLANK_OWN_SHIP
        ),
    )
    assert _links_of_type(payload_conn, ADDRESS_TYPE_SHIPPING) == []

    for conn in (connector, payload_conn):
        assert PARENT_SHIP_QBO_ID not in conn.address_service.identity_qbo_ids(), (
            "something asked for the parent's _ship identity -- that is a "
            "sibling project's street"
        )


def test_invariant_2_the_billing_chain_is_own_bill_then_parent_bill_only():
    """⚠️ INVARIANT 2. Two links, in that order, and NEITHER ship link may
    re-enter the chain (U-506 P2 removed both: own-ship is the construction site
    and outranked the owner's real remit-to; parent-ship is a sibling's street).

    Own-bill must WIN when present, and the parent must not even be READ —
    laziness is part of the contract, not an optimisation: the chain is a
    generator so a job carrying its own address costs zero extra round trips.
    """
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer()

    connector._sync_addresses(
        job, PROJECT_ID,
        external_customer=_own_addresses_payload(
            job, bill=REAL_OWN_BILL, ship=REAL_OWN_SHIP
        ),
    )

    assert _links_of_type(connector, ADDRESS_TYPE_BILLING) == [
        connector.address_connector.address_id_for(JOB_BILL_QBO_ID)
    ]
    assert connector.address_service.identity_reads == [], (
        "the parent was read although the job had its own BillAddr -- the chain "
        "is no longer lazy, or the order was inverted"
    )

    # ...and with own-bill blank, link 2 fills it — from `<parent>_bill` ONLY.
    fallback_conn = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    fallback_conn._sync_addresses(
        job, PROJECT_ID,
        external_customer=_own_addresses_payload(
            job, bill=BLANK_OWN_BILL, ship=REAL_OWN_SHIP
        ),
    )
    assert _links_of_type(fallback_conn, ADDRESS_TYPE_BILLING) == [PARENT_BILL_ADDRESS_ID]
    assert fallback_conn.address_service.identity_qbo_ids() == [
        billing_address_qbo_id(PARENT_REF)
    ]


@pytest.mark.parametrize(
    "line1, city, postal, is_address",
    [
        (None, None, None, False),
        ("", "", "", False),
        ("   ", "\t", " \n", False),
        ("4527 Beacon Dr.", None, None, True),
        (None, "Nashville", None, True),
        (None, None, "37220", True),
    ],
)
def test_invariant_3_blank_means_absent_and_is_never_minted(line1, city, postal, is_address):
    """⚠️ INVARIANT 3. Blank is `line1` + `city` + `postal_code` all empty after
    `.strip()`, and a blank address is ABSENT: never minted, never linked.

    Keying on PRESENCE instead of CONTENT is what produced 191 blank
    `dbo.Address` rows and 1,012 name-only "To:" blocks. With the payload now
    the ONLY source, this branch is the only thing standing between QBO's
    placeholder objects — an `Id` and nothing else, 29 of the 32 live addresses
    — and that defect recurring.

    Asserted against the shared rule as well as the outcome, because the MINT
    (here) and the READ (`_is_blank_dbo_address`, the same function on the dbo
    column names) disagreeing is invisible: the row is written, then never
    chosen.
    """
    connector = _build(parent_address=None)
    job = _qbo_customer()
    payload = SimpleNamespace(
        id=job.qbo_id,
        bill_addr=SimpleNamespace(
            line1=line1, line2="Suite 200", city=city,
            country_sub_division_code="TN", postal_code=postal,
        ),
        ship_addr=None,
    )

    connector._sync_addresses(job, PROJECT_ID, external_customer=payload)

    assert bool(connector.address_connector.minted) is is_address
    assert bool(_links_of_type(connector, ADDRESS_TYPE_BILLING)) is is_address
    assert address_fields_are_blank(line1, city, postal) is (not is_address)


def test_invariant_6_realm_scoping_fails_closed():
    """⚠️ INVARIANT 6. `ReadAddressByQboIdAndRealmId` is realm-scoped and fails
    CLOSED. A job whose realm does not match the parent address row's realm must
    inherit NOTHING — serving one company file's owner address to another's job
    is silent cross-tenant leakage of a remit-to address.

    Covers the NULL direction too: a job with no realm must not match a row that
    has one.
    """
    for job_realm in ("some-other-realm", None):
        connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
        job = _qbo_customer(realm_id=job_realm)

        connector._sync_addresses(
            job, PROJECT_ID,
            external_customer=_own_addresses_payload(job, bill=BLANK_OWN_BILL),
        )

        assert connector.project_address_service.links() == [], (
            f"a job in realm {job_realm!r} inherited realm {REALM!r}'s owner "
            "address -- realm scoping must fail closed"
        )


def test_invariant_7_the_pull_projects_parents_before_jobs():
    """⚠️ INVARIANT 7, and ph3b makes it the ONLY thing feeding billing Link 2.

    A job's billing fallback reads the `dbo.Address` the PARENT's projection
    mints. Before ph3b a mis-ordered pull merely missed a refresh, because the
    child's own staging read could still mint the row as a side effect. That
    side effect is gone: project jobs first and the parent's row does not exist
    yet, so every job under a brand-new owner silently degrades to a name-only
    "To:" block — the exact 1,012-invoice defect U-506 P1 was built to end.

    Asserted on the ORDER of the two projection calls in a driven pull, not on
    the source text.
    """
    order = []
    repo = Mock()
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.side_effect = [
        SimpleNamespace(qbo_id="J-1", is_job=True, is_parent_customer=False),
        SimpleNamespace(qbo_id="P-1", is_job=False, is_parent_customer=True),
    ]
    service = QboCustomerService(repo=repo)

    # The JOB is listed FIRST in QBO's response, so an implementation that
    # simply projected in arrival order would fail here.
    externals = [
        customer_service_module.QboCustomerExternalSchema(
            Id="J-1", SyncToken="0", DisplayName="BD - 4527 Beacon Dr.",
            Job=True, Active=True,
        ),
        customer_service_module.QboCustomerExternalSchema(
            Id="P-1", SyncToken="0", DisplayName="Beacon Dr.", Job=False, Active=True,
        ),
    ]

    parent_connector = Mock()
    parent_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: order.append(("parent", row.qbo_id))
        or SimpleNamespace(id=1)
    )
    job_connector = Mock()
    job_connector.sync_from_qbo_customer.side_effect = (
        lambda row, external=None: order.append(("job", row.qbo_id))
        or SimpleNamespace(id=2)
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
        return_value=parent_connector,
    ), patch(
        f"{customer_service_module.__name__}.CustomerProjectConnector",
        return_value=job_connector,
    ):
        service.sync_from_qbo(realm_id=REALM, sync_to_modules=True)

    assert [tier for tier, _ in order] == ["parent", "job"], (
        "jobs projected before parents -- billing Link 2 reads a dbo.Address "
        "the parent's projection has not minted yet, and ph3b removed the "
        "side effect that used to cover for it"
    )


def test_invariant_8_the_owner_mailing_semantic_is_a_confirmed_decision(monkeypatch):
    """⚠️ INVARIANT 8. `PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING = True` shipped
    as an EM assumption and was CONFIRMED by Chris on 2026-09-23. It is not an
    open question to re-litigate on a later pass; the False branch is retained
    only because it documents what the decision ruled out.

    ph3b does not touch the semantic, and this asserts that: the flag is still
    the single flip point, and flipping it still removes inheritance (nothing
    ph3b deleted has quietly become load-bearing for the chain's shape).
    """
    assert PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING is True

    monkeypatch.setattr(svc, "PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING", False)
    connector = _build(parent_address=REAL_PARENT_BILL_ADDRESS)
    job = _qbo_customer()

    connector._sync_addresses(
        job, PROJECT_ID,
        external_customer=_own_addresses_payload(job, bill=BLANK_OWN_BILL),
    )

    assert connector.address_service.identity_reads == [], (
        "the parent was read with inheritance switched OFF"
    )
    assert connector.project_address_service.links() == []


# ===========================================================================
# Section 4 — the PARENT half: its fallback is gone too
# ===========================================================================

def test_the_parent_half_projects_nothing_without_a_payload():
    """`CustomerCustomerConnector._own_billing_address` lost its staging arm in
    the same unit. Driven with the FK populated and a real, non-blank staging
    row behind it, so a reinstated read is distinguishable from an empty table.

    Nothing is lost: the pull records `external_by_id[qbo_customer.id]` and
    appends to `parent_customers` in the SAME try block after the SAME upsert,
    and looks the payload up by the exact value staged as `qbo_id`, so a parent
    row cannot reach this connector without its payload.
    `test_the_parent_staging_fallback_cannot_fire_from_the_pull`
    (test_u513_ph3_customer_no_staging_write.py) drives that through a real pull.
    """
    address_connector = Mock()
    connector = CustomerCustomerConnector(
        customer_service=Mock(),
        reconciliation_repo=Mock(),
        address_connector=address_connector,
    )
    staging_row = SimpleNamespace(qbo_id="P-1", bill_addr_id=902, realm_id=REALM)

    connector._project_own_billing_address(staging_row, None)

    address_connector.qbo_physical_address_service.read_by_id.assert_not_called()
    address_connector.sync_address_from_external.assert_not_called()


def test_the_parent_half_reads_nothing_off_the_connector_any_more():
    """`_own_billing_address` is a `@staticmethod` taking only the payload —
    ph3b's deletion left it reading NOTHING off the connector: no repo, no
    service, no staging handle, not even the `qbo_customer` staging row it took
    for its FK.

    Pinned structurally because re-introducing `self` (or the staging row) is
    what a second source for this customer's address would look like on the way
    back in.
    """
    params = list(
        inspect.signature(CustomerCustomerConnector._own_billing_address).parameters
    )
    assert params == ["external_customer"], (
        f"_own_billing_address took on new inputs ({params}) -- it has a second "
        "source again"
    )
    assert isinstance(
        inspect.getattr_static(CustomerCustomerConnector, "_own_billing_address"),
        staticmethod,
    )
