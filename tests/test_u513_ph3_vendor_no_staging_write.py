"""U-513 ph3a (vendor half) — the vendor pull no longer WRITES
`qbo.PhysicalAddress`, and the address data path survived the removal.

Where this sits
---------------
ph1 + ph2 stopped the code READING `qbo.PhysicalAddress`: the projection now
builds `dbo.Address` from the vendor's INLINE `BillAddr` payload
(`VendorVendorConnector._bill_address_from_payload`), and ph2 converted
`scripts/sync_qbo_vendor.py` — the scheduler/admin/CLI path — to
`sync_from_qbo(sync_to_modules=True)` so the payload actually reaches it.

The WRITE was deliberately left standing then, which is why the table could not
be dropped. ph3a removes it: `QboVendorService._upsert_physical_address` is
gone, and both `qbo.Vendor` staging writes now pass `bill_addr_id=None`.

What is deliberately NOT removed here
-------------------------------------
The `bill_addr_id` repo keyword, model field, column and sproc parameter all
stay — ph3b drops those. Leaving the parameter accepting NULL is what makes
this deploy compatible in BOTH directions: the container being replaced can
still resolve an existing vendor's address through its staging row, because
`UpdateQboVendorByQboId` guards the column with
`CASE WHEN @BillAddrId IS NULL THEN [BillAddrId] ELSE @BillAddrId END` — so a
NULL write PRESERVES a pre-ph3a id rather than clearing it. Only rows staged
for the first time after ph3a are genuinely NULL. That is asserted below at the
call boundary (what the service hands the repo), which is the only layer these
pure-logic tests can see.

`VendorVendorConnector._bill_address_from_staging` also stayed at ph3a — its
`external is None` dispatch branch already unreachable from every production
entry point (the sole production caller of `sync_from_qbo_vendor` is
`QboVendorService._sync_to_vendors`'s closure, which always threads the payload
it staged from), but still reached by `_bill_address_from_payload`'s
cross-wiring refusal, a defensive branch that must keep refusing.

**ph3b has since deleted that method**, and the refusal now mints nothing rather
than falling back to it. The tests below are unaffected because they were
already written against a NULL `bill_addr_id`, so they never depended on the
staging read resolving anything; `test_u513_ph3b_vendor_no_staging_read.py`
pins the removal and the surviving refusal.

Why the assertions are shaped this way
--------------------------------------
"No `qbo.PhysicalAddress` row is written" is asserted at the REPOSITORY, not by
checking that a helper is absent: the repo is the only place a row can actually
come from, so reinstating the write by any route — the old helper, a new one, a
direct repo call — trips it. The paired projection assertions exist so the
no-write assertions can never pass vacuously: a pull that projected nothing
would also have written nothing.

Pure logic: the QBO client, both repos and the connector's collaborators are
mocked; the harness blocks live pyodbc outright (`tests/conftest.py`).
"""
import inspect
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.vendor.business.service import QboVendorService
from integrations.intuit.qbo.vendor.connector.vendor.business.service import (
    ADDRESS_TYPE_BILLING,
)

# Model builders are reused from the ph1 file rather than re-copied: they build
# the REAL dataclasses/schemas, so a renamed field breaks one definition instead
# of silently diverging across three. Same in-repo precedent as ph2's
# `from test_u513_vendor_address_from_payload import ...`.
from test_u513_vendor_address_from_payload import (
    REALM,
    _addr,
    _build_connector,
    _external,
    _staging,
)

SERVICE_MODULE = "integrations.intuit.qbo.vendor.business.service"
CONNECTOR_MODULE = "integrations.intuit.qbo.vendor.connector.vendor.business.service"

# U-513 ph3b deleted both bindings a `qbo.PhysicalAddress` write could be reached
# through -- `QboPhysicalAddressService`, `QboPhysicalAddressRepository`, their
# modules, and the table and sprocs under them are all gone, so neither module
# path resolves any more. The autouse fixture that patched them is replaced by
# the source-level guard in section 1, which asserts the vendor pull's own text
# cannot name a staging repository; the deletion itself is pinned by
# tests/test_u513_ph3b_package_removed.py.


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _RecordingVendorRepo:
    """In-memory `QboVendorRepository` that RECORDS the kwargs of every staging
    write, so `bill_addr_id` can be read at the exact call boundary the service
    controls. `existing` drives which branch `_upsert_vendor` takes."""

    def __init__(self, existing=None):
        self.existing = existing
        self.create_calls = []
        self.update_calls = []
        self._next_id = 100

    def read_by_qbo_id_and_realm_id(self, *, qbo_id, realm_id):
        return self.existing

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._echo(kwargs)

    def update_by_qbo_id(self, **kwargs):
        self.update_calls.append(kwargs)
        return self._echo(kwargs)

    def _echo(self, kwargs):
        """Echo back the row those kwargs would have produced, so the projection
        sees the rows THIS pull staged -- including the NULL bill_addr_id."""
        self._next_id += 1
        row = _staging(
            qbo_id=kwargs.get("qbo_id"),
            realm_id=kwargs.get("realm_id"),
            bill_addr_id=kwargs.get("bill_addr_id"),
            display_name=kwargs.get("display_name"),
        )
        row.id = self._next_id
        return row


def _client_returning(*externals):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.query_all_vendors.return_value = list(externals)
    return client


def _run_pull(service, *externals, connector, realm_id=REALM):
    """Drive a FULL pull through the production shape: fetch -> stage -> project,
    exactly as `scripts/sync_qbo_vendor.py` invokes it."""
    with patch(f"{SERVICE_MODULE}.QboVendorClient",
               return_value=_client_returning(*externals)), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector):
        return service.sync_from_qbo(realm_id=realm_id, sync_to_modules=True)


# --------------------------------------------------------------------------
# 1. The write is gone
# --------------------------------------------------------------------------


def test_full_pull_writes_no_qbo_physical_address_row():
    """The headline behavior. A vendor carrying a REAL, non-blank BillAddr is
    the case that used to stage a row -- so this is the pull that would trip the
    guard if the write came back."""
    repo = _RecordingVendorRepo()
    service = QboVendorService(repo=repo)
    connector, _, _, address_connector = _build_connector()

    outcome = _run_pull(service, _external(bill_addr=_addr()), connector=connector)

    # Non-vacuous: the pull really did stage and project this vendor, and really
    # did have an address in hand to (not) stage.
    assert outcome.synced_count == 1
    assert outcome.projected_count == 1
    address_connector.sync_address_from_external.assert_called_once()


def test_update_path_writes_no_qbo_physical_address_row():
    """The UPDATE branch staged an address too (it re-upserted the same
    `{id}_bill` row on every 4-hour pull), so it needs its own guard -- a fix
    applied only to the CREATE branch would pass the test above."""
    repo = _RecordingVendorRepo(existing=_staging(bill_addr_id=555))
    service = QboVendorService(repo=repo)
    connector, _, _, address_connector = _build_connector()

    outcome = _run_pull(service, _external(bill_addr=_addr()), connector=connector)

    assert repo.update_calls and not repo.create_calls
    assert outcome.projected_count == 1
    address_connector.sync_address_from_external.assert_called_once()


def test_upsert_physical_address_helper_is_gone():
    """Structural companion to the behavioural guards: ph3b's drop of the table +
    sprocs assumes nothing in this service can still reach them."""
    assert not hasattr(QboVendorService, "_upsert_physical_address")
    assert not hasattr(QboVendorService(repo=_RecordingVendorRepo()),
                       "physical_address_service")


@pytest.mark.parametrize(
    "module_name", [SERVICE_MODULE, CONNECTOR_MODULE], ids=["service", "connector"],
)
def test_no_staging_repository_is_named_anywhere_in_the_pull(module_name):
    """Replaces ph3a's autouse patch fixture, which armed a stub at
    `QboPhysicalAddressRepository`'s home module and asserted the pull never
    constructed it. ph3b deleted that module, so there is no longer a target to
    patch -- and a name that cannot be imported cannot be called. What a patch
    could never catch, and this does, is the reinstatement shape: a module-level
    import binds before any fixture runs. So assert on the module's own text and
    bindings instead.

    Docstrings and comments are NOT stripped here (unlike the company_info
    sibling's `_source_without_prose`) because these two modules must not name
    the staging repository even in prose -- there is nothing left for such a
    mention to refer to."""
    import importlib

    module = importlib.import_module(module_name)
    assert not hasattr(module, "QboPhysicalAddressRepository")
    assert not hasattr(module, "QboPhysicalAddressService")

    source = inspect.getsource(module)
    for dead in ("QboPhysicalAddressRepository", "QboPhysicalAddressService",
                 "physical_address.persistence", "physical_address.business"):
        assert dead not in source, f"{module_name} still names {dead}"


# --------------------------------------------------------------------------
# 2. The qbo.Vendor staging row is still written, with a NULL bill_addr_id
# --------------------------------------------------------------------------


def test_staging_vendor_row_is_still_written_with_a_null_bill_addr_id():
    """Only the ADDRESS write goes away. The vendor staging row itself is
    untouched -- ph3b drops the column, this phase only stops populating it."""
    repo = _RecordingVendorRepo()
    connector, _, _, _ = _build_connector()

    _run_pull(QboVendorService(repo=repo), _external(bill_addr=_addr()),
              connector=connector)

    assert len(repo.create_calls) == 1
    written = repo.create_calls[0]
    assert written["bill_addr_id"] is None
    # ...and the rest of the row still arrives, so this is a narrowed write and
    # not a broken one.
    assert written["qbo_id"] == "1246"
    assert written["realm_id"] == REALM
    assert written["display_name"] == "Acme Supply"
    assert written["active"] is True


def test_update_path_also_passes_a_null_bill_addr_id():
    """`UpdateQboVendorByQboId` NULL-PRESERVES this column, so passing None is
    how a pre-ph3a id survives the rollout for the old container to read. What
    this phase owns is the call: the service must never source an id again."""
    repo = _RecordingVendorRepo(existing=_staging(bill_addr_id=555))
    connector, _, _, _ = _build_connector()

    _run_pull(QboVendorService(repo=repo), _external(bill_addr=_addr()),
              connector=connector)

    assert len(repo.update_calls) == 1
    assert repo.update_calls[0]["bill_addr_id"] is None


def test_a_vendor_with_no_bill_addr_still_stages_normally():
    """The removed block was guarded by `if qbo_vendor.bill_addr:`. Nothing about
    the staging write may now depend on that field at all."""
    repo = _RecordingVendorRepo()
    connector, _, _, address_connector = _build_connector()

    outcome = _run_pull(QboVendorService(repo=repo), _external(bill_addr=None),
                        connector=connector)

    assert outcome.synced_count == 1
    assert repo.create_calls[0]["bill_addr_id"] is None
    address_connector.sync_address_from_external.assert_not_called()


# --------------------------------------------------------------------------
# 3. dbo.Address is still projected from the payload — the data path survived
# --------------------------------------------------------------------------


def test_dbo_address_still_projected_from_payload_under_the_same_identity():
    """Removing the write must not remove the DATA. `dbo.Address` is still minted
    from the inline `BillAddr`, under the same synthetic `{vendor_id}_bill`
    identity the staging row used to carry -- which is what keeps the already-
    minted rows MATCHED rather than duplicated."""
    repo = _RecordingVendorRepo()
    connector, _, vendor_address_service, address_connector = _build_connector()

    _run_pull(QboVendorService(repo=repo), _external(bill_addr=_addr()),
              connector=connector)

    address_connector.sync_address_from_external.assert_called_once_with(
        qbo_id="1246_bill",
        realm_id=REALM,
        line1="PO Box 594",
        line2="Suite 200",
        city="Brentwood",
        country_sub_division_code="TN",
        postal_code="37024",
    )
    # And the staging READ was not what supplied it.
    address_connector.sync_from_qbo_to_address.assert_not_called()
    vendor_address_service.create.assert_called_once_with(
        vendor_id="55", address_id="900", address_type_id=str(ADDRESS_TYPE_BILLING),
    )


def test_each_vendor_on_a_page_still_gets_its_own_address():
    """The closure pairing is what keeps vendor A's address off vendor B's
    identity, and it is the thing a NULL `bill_addr_id` can no longer backstop:
    before ph3a a mis-pairing still landed on this row's own staging address, now
    there is nothing to fall back to. A page of vendors is the ordinary case."""
    repo = _RecordingVendorRepo()
    connector, _, _, address_connector = _build_connector()

    outcome = _run_pull(
        QboVendorService(repo=repo),
        _external("1246", display_name="Alpha", bill_addr=_addr(city="Brentwood")),
        _external("329", display_name="Beta", bill_addr=_addr(city="Nashville")),
        connector=connector,
    )

    assert outcome.projected_count == 2
    by_identity = {
        c.kwargs["qbo_id"]: c.kwargs["city"]
        for c in address_connector.sync_address_from_external.call_args_list
    }
    assert by_identity == {"1246_bill": "Brentwood", "329_bill": "Nashville"}


def test_realm_still_reaches_the_address_projection_unlaundered():
    """Realm scoping fails CLOSED downstream, which it cannot do if a NULL realm
    arrives as `""` (an empty string is a value that can MATCH)."""
    repo = _RecordingVendorRepo()
    connector, _, _, address_connector = _build_connector()

    _run_pull(QboVendorService(repo=repo), _external(bill_addr=_addr()),
              connector=connector, realm_id=None)

    assert address_connector.sync_address_from_external.call_args.kwargs["realm_id"] is None


# --------------------------------------------------------------------------
# 4. The ph1/ph2 invariants still hold with no staging row behind them
# --------------------------------------------------------------------------


def test_blank_inline_address_still_mints_nothing_through_a_full_pull():
    """Blank (`line1` + `city` + `postal_code` all empty after `.strip()`) means
    ABSENT. The 191 blank `dbo.Address` rows came from staging the shell and
    reading it back; with the write gone there is no second route to them, and
    this pins that the payload route does not reopen one."""
    repo = _RecordingVendorRepo()
    connector, _, vendor_address_service, address_connector = _build_connector()
    blank = _addr(line1="  ", line2="Suite 200", city="", postal_code=None)

    outcome = _run_pull(QboVendorService(repo=repo), _external(bill_addr=blank),
                        connector=connector)

    assert outcome.projected_count == 1  # the VENDOR still projects
    address_connector.sync_address_from_external.assert_not_called()
    address_connector.sync_from_qbo_to_address.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_mispaired_payload_is_still_refused_and_now_writes_nothing_at_all():
    """The ph1 cross-wiring guard stays: a payload whose `Id` is not this staging
    row's must be REFUSED, never written under the wrong identity. Post-ph3a the
    refusal's staging fallback finds a NULL `bill_addr_id`, so the outcome is
    'nothing written' rather than 'this row's own stale address' -- still a
    refusal, never a cross-wire."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(qbo_id="1246", bill_addr_id=None),
        _external("329", bill_addr=_addr()),
    )

    address_connector.sync_address_from_external.assert_not_called()
    address_connector.sync_from_qbo_to_address.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_address_failure_still_does_not_fail_the_vendor_projection():
    """Projection failures stay FAILURES, but an ADDRESS failure stays isolated:
    it is logged and swallowed so a bad address never holds the pull watermark
    over a vendor that synced fine."""
    repo = _RecordingVendorRepo()
    connector, _, _, address_connector = _build_connector()
    address_connector.sync_address_from_external.side_effect = RuntimeError("boom")

    outcome = _run_pull(QboVendorService(repo=repo), _external(bill_addr=_addr()),
                        connector=connector)

    assert outcome.projected_count == 1
    assert outcome.projection_failed_ids == []
    assert outcome.should_hold is False
