"""U-508 — the CompanyInfo pull's match-by-address-fields fallback is deleted,
and must not come back on the path that replaced it.

`QboCompanyInfoService._sync_physical_address` used to carry a "# Migration:"
block: on a qbo_id MISS it called `QboPhysicalAddressRepository.read_all()`
(957 rows), linear-scanned in Python for a row with the same
(line1, city, postal_code), and REWROTE that row's `qbo_id` to CompanyInfo's.
It was scoped by neither realm nor owner.

Why it originally shipped WITH U-514, and why it then shipped ALONE
(U-514 was split out on 2026-09-23 and is NOT deployed):

    Realm-scoping the read makes the three CompanyInfo-owned
    `qbo.PhysicalAddress` rows MISS -- they carry `RealmId = NULL`. That miss
    fed the fallback. `read_all()` is `ORDER BY [QboId] ASC`, and '1246_bill'
    sorts before '1612', so the first row matching
    ('PO Box 594', 'Brentwood', '37024') is Vendor 1246 (Rogers Build Inc.)'s
    BILLING address. The pull would have silently re-keyed it to
    CompanyInfo's id. Verified against live data.

Why the block was already spent, and therefore safe to delete rather than fix:
commit e3f2f068 (07:34:07 UTC) introduced it 14 minutes AFTER the three rows it
was written to heal were created (07:20:32 UTC). It re-keyed them once in
January 2026 and has matched nothing since -- zero rows carry the synthetic
shape it healed. It has never fired destructively.

WHAT CHANGED IN U-513 PHASE 3a, AND WHY THIS FILE STILL EXISTS
--------------------------------------------------------------
Phase 3a deleted `_sync_physical_address` outright -- host method, staging
write and all -- so the specific call site this file used to guard no longer
exists to be guarded. The DEFECT, though, is not a property of that method: it
is "resolve an address by its street instead of by its identity, and adopt
whatever you find". The pull still resolves addresses; it just does so against
`dbo.Address` now, via `CompanyInfoAddressConnector`. So these tests moved down
onto the surviving path rather than being deleted with the method:

  * a qbo_id MISS must still CREATE under the identity the pull derived
    (`_address_qbo_id`), never adopt a field-matched stranger's row;
  * the company_info package must hand the projection nothing it COULD use to
    go looking for a same-street row -- an identity and the fields to write,
    and that is all;
  * no call site in the package may enumerate an address table.

Its TRIGGER is still live and deliberately kept: `sync_from_qbo` still
synthesises `f"{realm_id}-company"` (and -legal / -customer-communication) when
QBO omits an address `Id`, so the miss branch remains permanently reachable.
`test_synthetic_id_trigger_is_still_live` pins that, which is what stops the
tests above it from passing vacuously against a pull that can no longer miss.

`PhysicalAddressAddressConnector`'s OWN street/city adopt is a different,
bounded thing and is not in scope here: it runs only under the create lock on a
confirmed dbo miss, and it refuses any row already carrying a different
(QboId, RealmId). What this file guards is that company_info never builds a
second, unbounded one.
"""
import inspect
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo as QboCompanyInfoExternal,
)

REALM = "9130353016965726"

SERVICE_MODULE = "integrations.intuit.qbo.company_info.business.service"
CLIENT_TARGET = f"{SERVICE_MODULE}.QboCompanyInfoClient"
ADDRESS_CONNECTOR_TARGET = f"{SERVICE_MODULE}.CompanyInfoAddressConnector"

# The live collision, verbatim: Vendor 1246 (Rogers Build Inc.)'s billing
# address shares all three matched fields with the company address. Every
# payload below carries it, so a reinstated field-match would have a real
# stranger's row to reach for.
SHARED_LINE1 = "PO Box 594"
SHARED_CITY = "Brentwood"
SHARED_POSTAL = "37024"

# Every call an address-field scan would have to make, at any layer.
FIELD_MATCH_CALLS = (
    "read_all",
    "read_by_street_one_and_city",
    "read_by_qbo_id",
    "read_by_id",
)


def _external(**overrides):
    payload = {
        "Id": "CI-1",
        "CompanyName": "ROGERS BUILD INC",
        "LegalName": "Rogers Build, Inc.",
        "Country": "US",
        "FiscalYearStartMonth": 1,
    }
    payload.update(overrides)
    return QboCompanyInfoExternal(**payload)


def _company_addr(**overrides):
    payload = {
        "Line1": SHARED_LINE1,
        "City": SHARED_CITY,
        "PostalCode": SHARED_POSTAL,
        "CountrySubDivisionCode": "TN",
    }
    payload.update(overrides)
    return payload


def _patched_client(response):
    client = MagicMock()
    client.get_company_info.return_value = response
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


def _run_pull(response, *, address_connector=None):
    """Drive the real `sync_from_qbo`, not the projection directly -- the
    synthetic-id derivation (`... or f"{realm_id}-company"`) lives in the
    caller, and it is what makes the miss branch reachable at all."""
    svc = QboCompanyInfoService()
    connector = address_connector or MagicMock()
    with patch(CLIENT_TARGET, return_value=_patched_client(response)), patch(
        ADDRESS_CONNECTOR_TARGET, MagicMock(return_value=connector)
    ):
        svc.sync_from_qbo(realm_id=REALM)
    return connector


# --------------------------------------------------------------------------
# 1. A qbo_id miss RESOLVES BY IDENTITY. It never adopts a row by address fields.
# --------------------------------------------------------------------------


def test_qbo_id_miss_projects_under_the_derived_identity():
    """The address whose (line1, city, postal_code) is shared with Vendor 1246's
    billing address must still project under ITS OWN identity. That is what
    makes a miss a CREATE at the `dbo.Address` layer rather than an adoption:
    `sync_address_from_external` looks the identity up and, finding nothing,
    creates under it."""
    connector = _run_pull(_external(CompanyAddr=_company_addr(Id="1612")))

    connector.project_address.assert_called_once()
    _args, kwargs = connector.project_address.call_args
    assert kwargs["qbo_id"] == "1612", (
        "a qbo_id miss must resolve under the pull's own derived identity; "
        "adopting an address-field twin is the U-508 defect"
    )


def test_the_projection_is_handed_no_way_to_find_a_same_street_row():
    """THE structural guard, now that the deleted block's host method is gone.

    `project_address` receives the payload's address ref and the identity --
    nothing else. A reinstated fallback needs somewhere to say "and if that
    misses, go looking by street"; this asserts there is no such channel, at the
    call site AND in the signature, so one cannot be added without failing here.
    """
    connector = _run_pull(_external(CompanyAddr=_company_addr()))

    args, kwargs = connector.project_address.call_args
    assert len(args) == 1, f"unexpected positional args: {args!r}"
    assert set(kwargs) == {"qbo_id"}, (
        f"the projection gained a channel beyond the identity: {sorted(kwargs)}"
    )

    from integrations.intuit.qbo.company_info.connector.address.business.service import (
        CompanyInfoAddressConnector,
    )

    params = list(inspect.signature(CompanyInfoAddressConnector.project_address).parameters)
    assert params == ["self", "address_ref", "qbo_id"], (
        f"project_address grew a parameter; a match-by-fields hint would ride "
        f"in on exactly this: {params}"
    )


# --------------------------------------------------------------------------
# 2. No address table is enumerated anywhere in the package.
# --------------------------------------------------------------------------


def test_no_enumerating_read_is_called_during_a_pull():
    """The 957-row scan is gone, not merely narrowed. Pinned separately from the
    behavioural tests because a future "optimised" variant of the same fallback
    (scan fewer rows, still by address fields) would keep those green while
    reintroducing exactly this cross-owner reach.

    Asserted against the connector the service actually drives: every attribute
    a scan could use is recorded by the Mock, so any of them being touched
    fails."""
    connector = _run_pull(
        _external(
            CompanyAddr=_company_addr(),
            LegalAddr=_company_addr(Line1="1 Legal Way", City="Nashville", PostalCode="37201"),
            CustomerCommunicationAddr=_company_addr(),
        )
    )

    called = {c[0] for c in connector.method_calls}
    assert called == {"project_address"}, (
        f"the pull called something other than the projection: {sorted(called)}"
    )
    for banned in FIELD_MATCH_CALLS:
        assert getattr(connector, banned).call_count == 0, (
            f"{banned} was called during address sync -- see _address_qbo_id's "
            "docstring for the row it would adopt"
        )


@pytest.mark.parametrize("module_path", [
    "integrations.intuit.qbo.company_info.business.service",
    "integrations.intuit.qbo.company_info.connector.address.business.service",
])
def test_package_source_carries_no_field_match_call(module_path):
    """A behavioural pin can be satisfied by a fallback that is merely gated off.
    This asserts the call sites are absent from the shipped source of BOTH
    modules on the address path -- the service that derives the identity and the
    connector that spends it.

    Docstrings and comments are stripped first: they NAME the deleted calls, and
    that naming is the record of why they must stay deleted."""
    import importlib

    module = importlib.import_module(module_path)
    source = inspect.getsource(module)
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for obj in [module] + [
        getattr(cls, name)
        for cls in vars(module).values()
        if inspect.isclass(cls) and cls.__module__ == module_path
        for name in vars(cls)
        if callable(getattr(cls, name, None))
    ]:
        doc = getattr(obj, "__doc__", None)
        if doc:
            body = body.replace(doc, "")
    for banned in FIELD_MATCH_CALLS:
        assert banned not in body, (
            f"{banned} must not appear in {module_path} -- see _address_qbo_id's "
            "docstring for the row it would adopt"
        )


# --------------------------------------------------------------------------
# 3. THE P0, as a test.
# --------------------------------------------------------------------------


def test_foreign_vendor_billing_address_is_never_reachable_from_this_pull():
    """The exact live collision the deleted block would have caused under U-514,
    expressed against the path that survived.

    Vendor 1246 (Rogers Build Inc.)'s billing address `1246_bill` shares
    ('PO Box 594', 'Brentwood', '37024') with the company address, and sorted
    FIRST under `read_all()`'s `ORDER BY [QboId] ASC`. The CompanyInfo response
    here carries no address `Id`, so the caller synthesises `{realm}-company` --
    a guaranteed identity miss, which is precisely the state that fed the
    fallback.

    What the pull does with that miss must be: project under the synthetic
    identity, passing the payload's own fields as CONTENT TO WRITE. The street
    is never an input to a lookup, so there is no path from this pull to
    `1246_bill` at all.
    """
    connector = _run_pull(_external(CompanyAddr=_company_addr()))

    args, kwargs = connector.project_address.call_args
    assert kwargs["qbo_id"] == f"{REALM}-company", (
        "an identity miss must create under the synthetic id, never adopt a "
        "field-matched stranger's row"
    )
    address_ref = args[0]
    assert (address_ref.line1, address_ref.city, address_ref.postal_code) == (
        SHARED_LINE1, SHARED_CITY, SHARED_POSTAL,
    ), "the shared street must reach the projection as content, and only as content"
    assert connector.method_calls == [c for c in connector.method_calls if c[0] == "project_address"]


# --------------------------------------------------------------------------
# 4. The synthetic-id trigger is still live (anti-vacuity for 1-3).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,suffix",
    [
        ("CompanyAddr", "-company"),
        ("LegalAddr", "-legal"),
        ("CustomerCommunicationAddr", "-customer-communication"),
    ],
)
def test_synthetic_id_trigger_is_still_live(field, suffix):
    """When QBO omits an address `Id`, the caller synthesises one and the pull
    projects under it. Delete that `or f"{realm_id}-..."` branch and the tests
    above go vacuous -- there would be no reachable miss left to guard.

    It also pins that the synthesised id is the one PROJECTED, so the identity
    read finds the same `dbo.Address` again next tick."""
    connector = _run_pull(_external(**{field: _company_addr()}))

    connector.project_address.assert_called_once()
    assert connector.project_address.call_args.kwargs["qbo_id"] == f"{REALM}{suffix}"


# --------------------------------------------------------------------------
# 5. (Parked) realm_id threading moved to U-514 on 2026-09-23 along with the
#    realm-scoped read it depends on. The staging write it would have threaded
#    through is gone as of phase 3a; U-514 now owns backfilling the realm onto
#    the `dbo.Address` rows that write left behind. See scratchpad/u514_parked/.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 6. The one transition the deletion leaves unhandled is RECORDED, not silent.
# --------------------------------------------------------------------------


def test_synthetic_to_real_transition_records_its_consequence_and_defers_the_runbook():
    """What the docstring must carry, and what it must NOT.

    MUST: the traced consequence -- conflict, raise, permanent skip, watermark
    advances -- because that is what a reader needs to judge the deletion.

    MUST NOT: the operational runbook. It lived here and drifted out of true
    three times in one day (it cited a unique index that was reverted, claimed
    the rows merely 'drift' when they actually conflict-and-skip, and claimed
    any synthetic row proves a live transition when it does not). A function
    docstring cannot be kept accurate as a procedure; BOARD.md U-518 owns it.

    The record moved with the code: it used to live on `_sync_physical_address`,
    which phase 3a deleted. Its home is now `_address_qbo_id` -- the derivation
    that MINTS the synthetic id, which is where a reader meets the transition.
    """
    doc = QboCompanyInfoService._address_qbo_id.__doc__

    # the consequence, stated correctly
    assert "address_identity_conflict" not in doc or "PERMANENT SKIP" in doc
    assert "_check_no_conflicting_address_identity" in doc
    assert "PERMANENT SKIP" in doc, "the docstring must state that the projection is SKIPPED"
    assert "watermark advances" in doc

    # the false claims that kept coming back
    assert "just drift" not in doc, "the rows do not drift; they conflict and skip"
    assert "Nothing errors" not in doc

    # the runbook is deferred, not inlined
    assert "U-518" in doc, "the docstring must point at the unit that owns the runbook"
    assert "MANUAL REMEDY" not in doc, "the runbook is back in the docstring"


def test_the_deleted_fallbacks_rationale_survived_the_methods_deletion():
    """`_sync_physical_address`'s docstring was the written record of WHY the
    match-by-address-fields block must stay deleted -- the 957-row scan, the
    `ORDER BY [QboId] ASC` ordering, and Vendor 1246's billing address by name.
    Deleting the method deleted that record along with it.

    Without this, a future reader sees only a clean projection and no reason not
    to add a helpful "fall back to matching on the street" branch. The reason
    must still be readable on the code that survived."""
    doc = QboCompanyInfoService._address_qbo_id.__doc__

    assert "U-508a" in doc
    assert "1246" in doc, "the row the fallback would adopt must still be named"
    assert "NEVER ADOPT" in doc
