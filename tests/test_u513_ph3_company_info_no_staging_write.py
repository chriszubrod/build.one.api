"""U-513 phase 3a — the CompanyInfo pull no longer WRITES `qbo.PhysicalAddress`.

Phases 1 and 2 stopped the code READING the staging table: the three addresses
project into `dbo.Address` from the inline QBO payload via
`CompanyInfoAddressConnector`. The write was deliberately left in place so the
customer and vendor packages could be converted in parallel against a table that
still existed. With those converted, the write had no reader at all -- a row per
slot per tick that nothing consumed -- and it is what kept the table from being
droppable. Phase 3a removes it. Phase 3b (the EM, separately) drops the table and
its 7 sprocs.

⛔ SCOPE. This unit does NOT remove `qbo.PhysicalAddress`, its sprocs, or
   `QboPhysicalAddressRepository`. Other packages still reference them until
   phase 3b, and the old container may still be live when this deploys.

WHY DELETING THE WRITE IS SAFE -- verified, not assumed
------------------------------------------------------
The one thing that would make it unsafe is a surviving read path that resolves
the company address via a staging row. There is none:

  * `_project_addresses_from_payload` reads the slots off the external
    `QboCompanyInfo` schema object and passes their fields straight to
    `CompanyInfoAddressConnector.project_address`.
  * `project_address` calls `PhysicalAddressAddressConnector`.
    `sync_address_from_external`, which resolves and writes through
    `AddressService` only -- the staging service on that connector is reached
    exclusively by `sync_from_qbo_to_address`, the staging-id entry point this
    package no longer calls (pinned below, and in
    test_u513_company_info_address_from_payload.py).
  * The staging row ids the write produced were threaded onto the transient
    record's `company_addr_id` / `legal_addr_id` /
    `customer_communication_addr_id`. Nothing reads them:
    `CompanyInfoCompanyConnector` uses qbo_id / realm_id / legal_name /
    web_addr, and `scripts/sync_qbo_company_info.py` only echoes the record
    into its response body. Pinned in test_u505_company_info_staging_repoint.py.

WHAT MUST NOT MOVE
------------------
Removing the write must not disturb what the projection keys on, or a pull
MINTS A DUPLICATE `dbo.Address` instead of updating the existing row. Sections 2
and 3 assert the identity and the realm explicitly rather than by round-trip,
because both are values a refactor can change while every behavioural test stays
green.
"""
import ast
import inspect
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.company_info.business import service as service_module
from integrations.intuit.qbo.company_info.business.service import (
    ADDRESS_SLOTS,
    QboCompanyInfoService,
)
from integrations.intuit.qbo.company_info.connector.address.business import (
    service as connector_module,
)
from integrations.intuit.qbo.company_info.connector.address.business.service import (
    CompanyInfoAddressConnector,
)
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo as QboCompanyInfoExternal,
)

REALM = "9130353016965726"

SERVICE_MODULE = "integrations.intuit.qbo.company_info.business.service"
CLIENT_TARGET = f"{SERVICE_MODULE}.QboCompanyInfoClient"
ADDRESS_CONNECTOR_TARGET = f"{SERVICE_MODULE}.CompanyInfoAddressConnector"
# U-513 ph3b: there is no staging repository left to patch. `QboPhysicalAddressRepository`,
# its module, the service above it and the `qbo.PhysicalAddress` table itself are
# all deleted, so the ph3a-era patch target does not resolve and the guards that
# used it are subsumed by a stronger fact: the module cannot be imported at all
# (pinned by tests/test_u513_ph3b_package_removed.py). What still belongs HERE is
# the source-level and call-shape evidence that this pull's address path never
# reaches for one -- those tests are below, unchanged in substance.

LINE1 = "PO Box 594"
CITY = "Brentwood"
POSTAL = "37024"

# The identity each slot projects under when QBO omits the address `Id`.
# Written out literally rather than derived from ADDRESS_SLOTS: a test that
# rebuilds the value the same way the code does cannot catch the code changing.
EXPECTED_SYNTHETIC_IDS = {
    "CompanyAddr": f"{REALM}-company",
    "LegalAddr": f"{REALM}-legal",
    "CustomerCommunicationAddr": f"{REALM}-customer-communication",
}

WRITE_METHODS = ("create", "update_by_id", "update_by_qbo_id", "delete_by_id")


def _external(**overrides):
    """A real external-schema CompanyInfo, not a SimpleNamespace -- a renamed
    field must break these tests rather than silently diverge from what
    `sync_from_qbo` actually reads."""
    payload = {
        "Id": "CI-1",
        "CompanyName": "ROGERS BUILD INC",
        "LegalName": "Rogers Build, Inc.",
        "Country": "US",
        "FiscalYearStartMonth": 1,
    }
    payload.update(overrides)
    return QboCompanyInfoExternal(**payload)


def _addr(**overrides):
    payload = {
        "Line1": LINE1,
        "City": CITY,
        "PostalCode": POSTAL,
        "CountrySubDivisionCode": "TN",
    }
    payload.update(overrides)
    return payload


def _all_three(**overrides):
    """A response with all three slots populated -- the case that used to mint
    three staging rows, so "no rows were written" is not satisfied by an input
    that would never have written any."""
    payload = {
        "CompanyAddr": _addr(),
        "LegalAddr": _addr(Line1="1 Legal Way", City="Nashville", PostalCode="37201"),
        "CustomerCommunicationAddr": _addr(Line1="2 CC Rd", City="Franklin", PostalCode="37064"),
    }
    payload.update(overrides)
    return _external(**payload)


def _patched_client(response):
    client = MagicMock()
    client.get_company_info.return_value = response
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


def _run_pull(response, *, address_connector=None):
    """Drive the REAL `sync_from_qbo` and hand back its outcome plus the address
    connector it drove, so a caller can assert on exactly which of the
    connector's methods the pull reached."""
    svc = QboCompanyInfoService()
    connector = address_connector or MagicMock()
    with patch(CLIENT_TARGET, return_value=_patched_client(response)), patch(
        ADDRESS_CONNECTOR_TARGET, MagicMock(return_value=connector)
    ):
        outcome = svc.sync_from_qbo(realm_id=REALM)
    return outcome, connector


def _source_without_prose(module):
    """The module's executable text: comment lines and every docstring removed.

    Both are stripped because they deliberately NAME the deleted write -- that
    naming is the record of why it must stay deleted, and scanning it would make
    every assertion below fail on its own documentation."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


# --------------------------------------------------------------------------
# 1. A full pull writes NO qbo.PhysicalAddress row.
# --------------------------------------------------------------------------


# `test_a_full_pull_never_constructs_the_staging_repository` and
# `test_a_full_pull_calls_no_staging_write_method[create|update_by_id|
# update_by_qbo_id|delete_by_id]` were DELETED by U-513 ph3b, not lost. Both
# patched `QboPhysicalAddressRepository` at its home module and asserted the pull
# never touched it. That class, its module and the table beneath it no longer
# exist, so the patch target does not resolve and the property they proved is now
# structural: there is nothing left to construct or call. The two source-level
# guards below still carry the "never reintroduce it" half, and
# tests/test_u513_ph3b_package_removed.py pins the deletion itself.


def test_the_service_module_no_longer_imports_the_staging_repository():
    """The classic reinstatement is a module-level
    `from ...physical_address.persistence.repo import QboPhysicalAddressRepository`,
    which binds at import time and therefore cannot be caught by patching. This
    catches it directly: the name must not be an attribute of the module."""
    assert not hasattr(service_module, "QboPhysicalAddressRepository"), (
        "the service module imports the staging repository again"
    )
    assert not hasattr(connector_module, "QboPhysicalAddressRepository")


@pytest.mark.parametrize("module", [service_module, connector_module],
                         ids=["service", "address_connector"])
def test_no_staging_write_call_survives_in_the_source(module):
    """A behavioural pin can be satisfied by a write that is merely gated off,
    and the patch-based tests above have a documented blind spot (a module-level
    re-import). This closes both: the executable text of the address path must
    not name the staging repository or a write through one."""
    body = _source_without_prose(module)

    assert "QboPhysicalAddressRepository" not in body
    assert "physical_address_repo" not in body
    for method in WRITE_METHODS:
        assert f".{method}(" not in body, (
            f"a .{method}( call survives in {module.__name__}"
        )


def test_the_pull_reaches_exactly_one_connector_method():
    """`project_address` and NOTHING else. The connector is a MagicMock, which
    answers to any attribute, so asserting a specific method was not called
    proves little -- enumerating what WAS called is what actually closes the set.

    This was `test_the_staging_id_projection_entry_point_is_never_called` until
    U-513 ph3b, pinned against `sync_from_qbo_to_address`; that method is now
    deleted from the real connector, so the closed-set assertion (which it
    already carried) is the whole test."""
    _outcome, connector = _run_pull(_all_three())

    assert {c[0] for c in connector.method_calls} == {"project_address"}


# `test_the_staging_table_itself_is_untouched_by_this_unit` was ph3a's explicit
# SCOPE GATE: it asserted `QboPhysicalAddressRepository` and its create/update/
# read methods were all still present, because deleting them was phase 3b's job
# and the previously-deployed container still needed them. Phase 3b has now
# happened -- that is this deletion -- so the gate has been crossed rather than
# broken. Its inverse lives in tests/test_u513_ph3b_package_removed.py.


# --------------------------------------------------------------------------
# 2. Each slot still projects under its OWN identity.
#    A wrong identity MINTS A DUPLICATE rather than updating.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field,expected", sorted(EXPECTED_SYNTHETIC_IDS.items()))
def test_each_slot_projects_under_its_own_synthetic_identity(field, expected):
    """When QBO omits the address `Id`, the identity is `{realm}-{suffix}` with
    the slot's OWN suffix. Asserted against a literal, not against a value
    recomputed from `ADDRESS_SLOTS`.

    ⚠️ This is NOT the realm id. Project a bare realm and all three slots
    collide on one `dbo.Address`; project anything else and the existing row is
    missed and a duplicate minted."""
    _outcome, connector = _run_pull(_external(**{field: _addr()}))

    connector.project_address.assert_called_once()
    qbo_id = connector.project_address.call_args.kwargs["qbo_id"]
    assert qbo_id == expected
    assert qbo_id != REALM, "the realm id is not the address identity"


def test_qbos_own_address_id_wins_over_the_synthetic():
    """`dbo.Address.QboId` carries the real Id whenever QBO supplies one."""
    _outcome, connector = _run_pull(
        _external(CompanyAddr=_addr(Id="1612"))
    )

    assert connector.project_address.call_args.kwargs["qbo_id"] == "1612"


def test_all_three_slots_keep_distinct_identities_in_one_pull():
    """Anti-collision across a whole response, and the ORDER is the slot order:
    a transposition would write the legal address under the company identity."""
    _outcome, connector = _run_pull(
        _all_three(CustomerCommunicationAddr=_addr(Id="1612"))
    )

    projected = [c.kwargs["qbo_id"] for c in connector.project_address.call_args_list]
    assert projected == [
        EXPECTED_SYNTHETIC_IDS["CompanyAddr"],
        EXPECTED_SYNTHETIC_IDS["LegalAddr"],
        "1612",
    ]
    assert len(set(projected)) == 3, "two slots resolved to the same dbo.Address"


def test_the_identity_derivation_is_unchanged_at_its_own_level():
    """`_address_qbo_id` directly, so a caller-level refactor cannot satisfy the
    assertions above with a different derivation."""
    assert QboCompanyInfoService._address_qbo_id(
        MagicMock(id=None), realm_id=REALM, suffix="company"
    ) == f"{REALM}-company"
    assert QboCompanyInfoService._address_qbo_id(
        MagicMock(id="1612"), realm_id=REALM, suffix="company"
    ) == "1612"


def test_the_slot_suffixes_are_still_the_three_the_rows_were_written_under():
    """`ADDRESS_SLOTS` is the sole source of the suffixes. Editing one re-keys
    that slot's `dbo.Address` on the next tick."""
    assert list(ADDRESS_SLOTS) == [
        ("company_addr", "company"),
        ("legal_addr", "legal"),
        ("customer_communication_addr", "customer-communication"),
    ]


def test_the_payload_is_still_the_source_of_the_projected_fields():
    """Anti-vacuity for the identity tests: the right identity carrying the
    wrong content would still corrupt the row."""
    _outcome, connector = _run_pull(
        _external(
            CompanyAddr=_addr(
                Line1="123 Main St", Line2="Suite 4", City="Franklin",
                CountrySubDivisionCode="TN", PostalCode="37064",
            )
        )
    )

    address_ref = connector.project_address.call_args.args[0]
    assert address_ref.line1 == "123 Main St"
    assert address_ref.line2 == "Suite 4"
    assert address_ref.city == "Franklin"
    assert address_ref.country_sub_division_code == "TN"
    assert address_ref.postal_code == "37064"


# --------------------------------------------------------------------------
# 3. The realm is still None. Flipping it to the live realm is a silent outage.
# --------------------------------------------------------------------------


def test_the_projection_realm_is_still_none():
    """⚠️ The sharpest edge in the unit, and unaffected by deleting the write.

    This family's three `qbo.PhysicalAddress` rows carry `RealmId = NULL`
    (verified in prod) because the now-deleted write never passed a realm; the
    `dbo.Address` rows stamped from them carry NULL too, and
    `ReadAddressByQboIdAndRealmId` matches NULL only against NULL. Those dbo rows
    still exist -- deleting the code that caused them changes nothing about them.

    Pass the LIVE realm and the identity read MISSES the very row it must
    update, falls to the street/city adopt path, finds that same row, and trips
    `_check_no_conflicting_address_identity` (same QboId, different realm) -- a
    plain ValueError, which `record_projection_error` classifies as a PERMANENT
    SKIP. The watermark advances and the company address silently stops reaching
    dbo. Flipping it is U-514's, in the same deploy as its realm backfill."""
    assert CompanyInfoAddressConnector.ADDRESS_IDENTITY_REALM_ID is None

    address_connector = MagicMock()
    connector = CompanyInfoAddressConnector(address_connector=address_connector)

    connector.project_address(
        MagicMock(line1=LINE1, line2=None, city=CITY,
                  country_sub_division_code="TN", postal_code=POSTAL),
        qbo_id=f"{REALM}-company",
    )

    kwargs = address_connector.sync_address_from_external.call_args.kwargs
    assert kwargs["realm_id"] is None, (
        "passing the live realm MISSES this family's RealmId=NULL dbo.Address "
        "rows and turns the projection into a permanent skip"
    )
    assert kwargs["qbo_id"] == f"{REALM}-company"


def test_the_live_realm_never_reaches_the_projection_through_a_full_pull():
    """End to end, not just at the connector: `sync_from_qbo` knows the live
    realm (it mints the synthetic ids from it), so the only thing stopping it
    reaching `sync_address_from_external` is that `project_address` takes no
    realm argument at all."""
    address_connector = MagicMock()
    real_connector = CompanyInfoAddressConnector(address_connector=address_connector)

    _run_pull(_all_three(), address_connector=real_connector)

    assert address_connector.sync_address_from_external.call_count == 3
    for call in address_connector.sync_address_from_external.call_args_list:
        assert call.kwargs["realm_id"] is None, (
            f"the live realm reached the projection: {call.kwargs['realm_id']!r}"
        )


# --------------------------------------------------------------------------
# 4. U-505's missing-id guard still records a STAGING FAILURE.
# --------------------------------------------------------------------------


def test_a_missing_qbo_id_is_still_a_staging_failure_so_the_watermark_holds():
    """Skips are excluded from `should_hold`. Downgrading this to a skip would
    advance the delta cursor past a broken row and lose the Company permanently.
    Deleting the write moved code that ran BEFORE this guard, so it is exactly
    the kind of change that could have reordered it."""
    outcome, _connector = _run_pull(_external(Id=None, CompanyAddr=_addr()))

    assert outcome.staging_failed_ids == ["<no-id>"]
    assert outcome.skipped_ids == [], "a missing Id must never be a skip"
    assert outcome.should_hold is True
    assert not outcome.synced


def test_a_missing_qbo_id_projects_nothing_and_stages_nothing():
    """Ordering, both ways. The address projection must still sit inside the
    success path only -- and now that the write is gone, a malformed response
    leaves NO trace anywhere, where before it wrote three staging rows it could
    not project."""
    outcome, connector = _run_pull(_all_three(Id=None))

    assert outcome.staging_failed_ids == ["<no-id>"]
    assert connector.method_calls == []


def test_the_missing_id_guard_still_raises_value_error():
    """The guard at its own level, so a caller-level refactor cannot quietly
    delete it while the outcome-level assertions above are satisfied by
    something else."""
    svc = QboCompanyInfoService()
    with pytest.raises(ValueError, match="QBO CompanyInfo must have an ID"):
        svc._build_company_info(_external(Id=None), realm_id=REALM)


def test_a_healthy_pull_does_not_hold():
    """Anti-vacuity for the guard tests: if `should_hold` were True for every
    response, the assertion above would pass while the watermark never advanced."""
    outcome, connector = _run_pull(_all_three())

    assert outcome.should_hold is False
    assert outcome.staging_failed_ids == []
    assert len(outcome.synced) == 1
    assert connector.project_address.call_count == 3
