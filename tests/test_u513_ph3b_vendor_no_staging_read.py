"""U-513 ph3b (vendor half) + U-529 — the last `qbo.PhysicalAddress` reader in
the vendor package is gone, and seven never-working vendor routes with it.

PART 1 — the staging reader
---------------------------
`VendorVendorConnector._bill_address_from_staging` read `dbo.Address` back out
of `qbo.PhysicalAddress` via the local id the staging upsert stashed on
`qbo.Vendor.BillAddrId`. ph3a stopped WRITING that cache, so from then on the
branch could only ever resolve a row frozen at the last pre-ph3a pull; ph3b
drops the table, after which it could not resolve at all. It is deleted.

It had TWO callers, and they are not equivalent:

  1. `_sync_addresses`'s `external is None` dispatch — dead since ph2 converted
     `scripts/sync_qbo_vendor.py`; the sole production caller of
     `sync_from_qbo_vendor` is `QboVendorService._sync_to_vendors`'s closure,
     which always threads the payload it staged from. A payload-less projection
     now mints no address at all. The VENDOR still projects.

  2. `_bill_address_from_payload`'s **cross-wiring refusal** — live defensive
     code that must survive. When a payload's `Id` does not match the staging
     row's `qbo_id`, taking that payload's `BillAddr` would write ONE vendor's
     address under ANOTHER vendor's `{id}_bill` identity: silent, permanent
     corruption on both rows, and a page of vendors is the ordinary case, not
     the edge one.

ph3b changed only what refusing FALLS BACK TO — from "this row's own staging
address" to nothing. The refusal is strictly stronger for it: it never produced
the mispaired vendor's address before, and now it produces none. Section 1C is
the part of this file most worth keeping honest, because a "simplification" that
drops the `external_id != staging_qbo_id` check entirely would still pass every
blankness and happy-path test in the package.

PART 2 — U-529, seven routes that never once worked
---------------------------------------------------
`integrations/intuit/qbo/vendor/api/router.py` exposed seven routes calling
`QboVendorService` methods that DO NOT EXIST — `create`, `update_by_id`,
`delete_by_id`, `read_by_sync_token`, `read_by_display_name`,
`read_by_company_name`, `read_by_tax_identifier`. Every one raised
`AttributeError` -> 500 on every call since it was written. They are DELETED,
not repaired, per the U-519 precedent: supplying the missing service methods
would convert a dead endpoint into a live caller-keyed write primitive over a
staging table that only the QBO pull may write. Two of them also bound
`bill_addr_id` straight off the request body — the column U-513 is dropping.

Zero callers across all five umbrella repos (API including
`entities/*/intelligence/`, web, iOS, MCP, scheduler); the only references were
the routes themselves and `qbo.vendor.spec.md`'s description of the pre-React
Jinja templates, which no longer exist.

Pure logic: no database, no network. Model builders are imported from the ph1
file rather than re-copied, so a renamed dataclass field breaks these tests
instead of silently diverging.
"""
from __future__ import annotations

import ast
import inspect

import pytest

import integrations.intuit.qbo.vendor.api.router as router_module
import integrations.intuit.qbo.vendor.api.schemas as schemas_module
import integrations.intuit.qbo.vendor.connector.vendor.business.service as connector_module
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from integrations.intuit.qbo.vendor.connector.vendor.business.service import (
    ADDRESS_TYPE_BILLING,
    VendorVendorConnector,
)

from test_u513_vendor_address_from_payload import (  # noqa: E402  (test-local helper module)
    REALM,
    _addr,
    _build_connector,
    _external,
    _staging,
)

# The seven service methods the deleted routes called. None has ever existed.
DEAD_SERVICE_METHODS = (
    "create",
    "read_by_sync_token",
    "read_by_display_name",
    "read_by_company_name",
    "read_by_tax_identifier",
    "update_by_id",
    "delete_by_id",
)

DEAD_ROUTE_FUNCTIONS = (
    "create_qbo_vendor_router",
    "get_qbo_vendor_by_sync_token_router",
    "get_qbo_vendor_by_display_name_router",
    "get_qbo_vendor_by_company_name_router",
    "get_qbo_vendor_by_tax_identifier_router",
    "update_qbo_vendor_by_id_router",
    "delete_qbo_vendor_by_id_router",
)

DEAD_ROUTE_PATHS = (
    "/create/qbo-vendor",
    "/update/qbo-vendor/{id}",
    "/delete/qbo-vendor/{id}",
    "/get/qbo-vendor/sync-token/{sync_token}",
    "/get/qbo-vendor/display-name/{display_name}",
    "/get/qbo-vendor/company-name/{company_name}",
    "/get/qbo-vendor/tax-identifier/{tax_identifier}",
)


# ---------------------------------------------------------------------------
# AST helpers — these generalise the assertions instead of pinning one line.
# ---------------------------------------------------------------------------


def _module_tree(module) -> ast.Module:
    return ast.parse(inspect.getsource(module))


def _attribute_reads(module, attr: str) -> list[str]:
    """Every `<expr>.<attr>` read in `module`, rendered as source.

    Deliberately NOT restricted to one receiver name: the point is that NOTHING
    in this module reads the attribute, whatever it calls the object holding it.
    Keyword arguments (`f(bill_addr_id=None)`) are not attribute reads and do
    not appear here — that write-side keyword is out of ph3b's scope and stays.
    """
    return [
        ast.unparse(node)
        for node in ast.walk(_module_tree(module))
        if isinstance(node, ast.Attribute) and node.attr == attr
    ]


def _function_names(module) -> set[str]:
    return {
        node.name
        for node in ast.walk(_module_tree(module))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _route_decorator_paths(module) -> set[str]:
    """Every path string passed to an `@router.<verb>("...")` decorator."""
    paths = set()
    for node in ast.walk(_module_tree(module)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                paths.add(dec.args[0].value)
    return paths


def _service_calls(module) -> set[str]:
    """Every `service.<name>(...)` / `self.service.<name>(...)` method called."""
    called = set()
    for node in ast.walk(_module_tree(module)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func).split(".")[-2:-1] == ["service"]
        ):
            called.add(node.func.attr)
    return called


# ===========================================================================
# PART 1A — no reader of the staging table remains in this package
# ===========================================================================


def test_the_staging_reader_method_is_gone():
    """`_bill_address_from_staging` must STAY deleted, by name."""
    assert not hasattr(VendorVendorConnector, "_bill_address_from_staging"), (
        "_bill_address_from_staging is back; it reads qbo.PhysicalAddress, a "
        "table nothing writes and ph3b drops"
    )
    assert "_bill_address_from_staging" not in _function_names(connector_module)


def test_no_reader_of_bill_addr_id_remains_in_the_connector():
    """The generalised form: nothing in the connector module READS
    `bill_addr_id` off anything, so a re-add under a different method name is
    caught too. `qbo.Vendor.BillAddrId` is the only handle the connector ever
    had on `qbo.PhysicalAddress`; with no read of it, no reader can exist."""
    offenders = _attribute_reads(connector_module, "bill_addr_id")
    assert offenders == [], (
        "the connector reads bill_addr_id again: " + ", ".join(offenders)
    )


def test_the_connector_never_calls_sync_from_qbo_to_address():
    """The other half of the same guarantee, from the callee side:
    `PhysicalAddressAddressConnector.sync_from_qbo_to_address` IS the
    `qbo.PhysicalAddress` read. No call site may remain."""
    offenders = [
        ast.unparse(node)
        for node in ast.walk(_module_tree(connector_module))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "sync_from_qbo_to_address"
    ]
    assert offenders == [], (
        "the connector reads qbo.PhysicalAddress again: " + ", ".join(offenders)
    )


def test_the_address_source_is_the_inline_payload_alone():
    """Exactly one address-minting call site survives, and it is the payload
    one. Stated positively so the three absence assertions above cannot all be
    satisfied by a connector that mints no addresses at all."""
    minting = [
        node.func.attr
        for node in ast.walk(_module_tree(connector_module))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("sync_")
        and "address" in node.func.attr
    ]
    assert minting == ["sync_address_from_external"]


# ===========================================================================
# PART 1B — the payload path still projects, under the same identity
# ===========================================================================


def test_payload_path_still_projects_under_the_unchanged_identity():
    """Removing the reader must not remove the DATA. `dbo.Address` is still
    minted from the inline `BillAddr` under the same synthetic
    `{qbo_vendor_id}_bill` string — the identity that keeps the already-minted
    rows MATCHED rather than duplicated under a new key."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(), _external(bill_addr=_addr()))

    address_connector.sync_address_from_external.assert_called_once_with(
        qbo_id="1246_bill",
        realm_id=REALM,
        line1="PO Box 594",
        line2="Suite 200",
        city="Brentwood",
        country_sub_division_code="TN",
        postal_code="37024",
    )
    vendor_address_service.create.assert_called_once_with(
        vendor_id="55", address_id="900", address_type_id=str(ADDRESS_TYPE_BILLING),
    )


def test_identity_is_still_the_staging_rows_own_qbo_id():
    """A second id, so the `{id}_bill` shape is pinned rather than one literal."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(qbo_id="329"), _external("329", bill_addr=_addr()))

    assert address_connector.sync_address_from_external.call_args.kwargs["qbo_id"] == "329_bill"


def test_realm_still_passes_through_unlaundered_so_scoping_fails_closed():
    """Realm scoping must fail CLOSED downstream, which it cannot do if this
    connector launders a NULL realm into `""` — an empty string is a value that
    can MATCH; None is the absence the callee must refuse."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(realm_id=None), _external(bill_addr=_addr()))

    assert address_connector.sync_address_from_external.call_args.kwargs["realm_id"] is None


@pytest.mark.parametrize(
    "blank_addr",
    [
        pytest.param(_addr(line1=None, city=None, postal_code=None), id="all-null"),
        pytest.param(_addr(line1="", city="", postal_code=""), id="all-empty"),
        pytest.param(_addr(line1="  ", city="\t", postal_code=" "), id="whitespace"),
        pytest.param(
            _addr(line1=None, line2="Suite 200", city=None,
                  country_sub_division_code="TN", postal_code=None),
            id="line2-and-state-only",
        ),
    ],
)
def test_blank_still_means_absent_after_the_reader_is_gone(blank_addr):
    """Blank = `line1` + `city` + `postal_code` all empty after `.strip()`, and
    blank means ABSENT: mint nothing, link nothing. `bill_addr_id=555` is set so
    a surviving staging fallback would betray itself here."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(bill_addr_id=555), _external(bill_addr=blank_addr),
    )

    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_not_called()
    vendor_address_service.repo.update_by_id.assert_not_called()


def test_a_single_populated_field_is_still_a_real_address():
    """The blankness rule must not tighten into a silent address-dropper."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(), _external(bill_addr=_addr(line1=None, city="Brentwood", postal_code=None)),
    )

    address_connector.sync_address_from_external.assert_called_once()


# ===========================================================================
# PART 1C — THE INVARIANT MOST AT RISK: the cross-wiring refusal survives
# ===========================================================================


def test_mispaired_payload_is_still_refused():
    """The headline ph3b invariant. A payload whose `Id` is not this staging
    row's must be REFUSED: its `BillAddr` is never projected."""
    connector, _, _, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(_staging(qbo_id="1246"), _external("329", bill_addr=_addr()))

    address_connector.sync_address_from_external.assert_not_called()


def test_mispaired_payload_mints_nothing_under_the_wrong_identity():
    """Restated as the corruption it prevents, so the test survives a rewrite
    of the refusal's internals: NO address may be minted under `1246_bill` (this
    row's identity) carrying vendor 329's payload content, and NO address may be
    minted under `329_bill` either — this projection is writing vendor 1246."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(qbo_id="1246"),
        _external("329", bill_addr=_addr(line1="999 Wrong Way", city="Memphis", postal_code="38103")),
    )

    minted = [
        c.kwargs.get("qbo_id") for c in address_connector.sync_address_from_external.call_args_list
    ]
    assert minted == [], f"the refusal minted an address under {minted}"
    vendor_address_service.create.assert_not_called()
    vendor_address_service.repo.update_by_id.assert_not_called()


def test_the_refusal_no_longer_reaches_staging_even_with_an_id_available():
    """What ph3b CHANGED: the refusal used to fall back to
    `_bill_address_from_staging`. `bill_addr_id=555` is populated, so a
    surviving fallback would resolve it. Nothing must be read, minted or
    linked."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(qbo_id="1246", bill_addr_id=555), _external("329", bill_addr=_addr()),
    )

    address_connector.sync_from_qbo_to_address.assert_not_called()
    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_not_called()


@pytest.mark.parametrize(
    "staging_qbo_id,payload_id",
    [
        pytest.param("1246", "329", id="different-ids"),
        pytest.param("1246", "12460", id="prefix-not-equality"),
        pytest.param("1246", None, id="payload-has-no-id"),
    ],
)
def test_every_mismatch_shape_reachable_end_to_end_is_refused(staging_qbo_id, payload_id):
    """Through the full projection, for the shapes that actually reach the
    refusal. The equality is on the PAIR — not a truthiness or prefix check —
    and a payload with no `Id` is unidentifiable, so it is a mismatch too."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(qbo_id=staging_qbo_id), _external(payload_id, bill_addr=_addr()),
    )

    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_not_called()


@pytest.mark.parametrize(
    "staging_qbo_id,payload_id",
    [
        pytest.param("1246", "329", id="different-ids"),
        pytest.param("1246", "12460", id="prefix-not-equality"),
        pytest.param("1246", None, id="payload-has-no-id"),
        pytest.param(None, "1246", id="staging-row-has-no-qbo-id"),
        pytest.param(None, None, id="neither-side-has-an-id"),
    ],
)
def test_the_guard_itself_refuses_every_mismatch_shape(staging_qbo_id, payload_id):
    """The guard's own truth table, called directly.

    Two shapes here cannot be driven end-to-end: a staging row with no `qbo_id`
    is rejected UPSTREAM by `run_identity_fastpath_dbo_only` (the projection
    raises before any address work — see `sync_from_qbo_vendor`'s backstop), so
    the refusal would never be exercised for them through the public method.
    They still matter: `None == None` must NOT read as 'paired' and wave an
    unidentifiable payload through if that upstream guard ever moves."""
    connector, *_ = _build_connector()

    result = connector._bill_address_from_payload(
        _staging(qbo_id=staging_qbo_id), _external(payload_id, bill_addr=_addr()),
    )

    assert result is None


def test_the_schema_strips_ids_so_the_guard_compares_normalized_values():
    """Why there is no whitespace-mismatch case above: `_QboBaseModel` sets
    `str_strip_whitespace=True`, so QBO's `Id` is already normalized by the time
    the guard sees it and `" 1246"` IS vendor 1246's payload, not a mispairing.
    Pinned because the guard's correctness rests on it — were stripping ever
    turned off, `" 1246"` would start being REFUSED, not silently cross-wired."""
    assert _external(" 1246").id == "1246"

    connector, _, _, address_connector = _build_connector()
    connector.sync_from_qbo_vendor(_staging(qbo_id="1246"), _external(" 1246", bill_addr=_addr()))

    assert address_connector.sync_address_from_external.call_args.kwargs["qbo_id"] == "1246_bill"


def test_the_matched_pair_still_projects_so_the_refusal_is_not_universal():
    """Guards the parametrized test above against passing vacuously: the SAME
    builders on a MATCHED pair do project. A refusal that fired on everything
    would satisfy every assertion in this section."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    connector.sync_from_qbo_vendor(
        _staging(qbo_id="1246"), _external("1246", bill_addr=_addr()),
    )

    address_connector.sync_address_from_external.assert_called_once()
    vendor_address_service.create.assert_called_once()


def test_the_equality_check_itself_is_still_in_the_source():
    """Structural backstop for the behavioural tests above: the comparison of
    the payload id against the staging row's `qbo_id` must still be present.
    A refactor that deleted the guard while keeping a permanently-None address
    source would pass every 'nothing was minted' assertion in this section."""
    source = inspect.getsource(connector_module.VendorVendorConnector._bill_address_from_payload)
    compares = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(inspect.cleandoc(source)))
        if isinstance(node, ast.Compare)
    ]
    assert any("external_id" in c and "staging_qbo_id" in c for c in compares), (
        "the payload-id vs staging-qbo_id comparison is gone from "
        "_bill_address_from_payload; the cross-wiring refusal is no longer "
        f"being made. Comparisons found: {compares}"
    )


def test_address_failure_is_still_isolated_from_the_vendor_projection():
    """Unchanged failure isolation: an address error is logged and swallowed, so
    a bad address never holds the pull watermark over a vendor that synced
    fine. (Projection failures themselves stay failures — that is
    `project_records`' job, one level up, and ph3b did not touch it.)"""
    connector, _, _, address_connector = _build_connector()
    address_connector.sync_address_from_external.side_effect = RuntimeError("boom")

    result = connector.sync_from_qbo_vendor(_staging(), _external(bill_addr=_addr()))

    assert result.id == 55


# ===========================================================================
# PART 1D — a payload-less projection mints nothing (the dead dispatch)
# ===========================================================================


def test_payload_less_projection_mints_no_address_but_still_projects_the_vendor():
    """The `external is None` dispatch no longer reads staging. `bill_addr_id`
    is populated, so a surviving reader would resolve it."""
    connector, _, vendor_address_service, address_connector = _build_connector()

    result = connector.sync_from_qbo_vendor(_staging(bill_addr_id=555))

    assert result.id == 55
    address_connector.sync_from_qbo_to_address.assert_not_called()
    address_connector.sync_address_from_external.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_a_payload_less_row_does_not_unlink_an_existing_vendor_address():
    """'No address source' must not become 'unlink what is there'. This
    connector has never unlinked, and a missing payload is not evidence the
    local link is wrong."""
    connector, _, vendor_address_service, _ = _build_connector()
    from unittest.mock import Mock

    existing_link = Mock(id=7, address_id="901", address_type_id=str(ADDRESS_TYPE_BILLING))
    vendor_address_service.read_all_by_vendor_id.return_value = [existing_link]

    connector.sync_from_qbo_vendor(_staging(bill_addr_id=555))

    vendor_address_service.repo.update_by_id.assert_not_called()
    vendor_address_service.create.assert_not_called()


def test_retry_and_pacing_survive_in_the_projection_closure():
    """ph2 moved `with_retry` + `pace_batch` into `_sync_to_vendors`'s closure
    when the script's own loop was deleted. ph3b touched the connector, not the
    service — pin that the resilience is still there, since losing it would be a
    silent regression no address assertion would catch."""
    source = inspect.getsource(QboVendorService._sync_to_vendors)
    assert "with_retry(" in source
    assert "pace_batch(" in source


# ===========================================================================
# PART 2 — U-529: the seven routes are gone, and their schemas with them
# ===========================================================================


def test_the_seven_dead_route_functions_stay_deleted():
    """By name. Re-adding any of them re-adds a 500-on-every-call endpoint — or,
    worse, motivates writing the missing service method and turning it into a
    live unscoped write over a staging table (the U-519 failure mode)."""
    present = _function_names(router_module) & set(DEAD_ROUTE_FUNCTIONS)
    assert present == set(), f"deleted vendor routes are back: {sorted(present)}"


def test_the_seven_dead_route_paths_stay_unregistered():
    """By path, so a re-add under a different function name is caught too."""
    registered = _route_decorator_paths(router_module) & set(DEAD_ROUTE_PATHS)
    assert registered == set(), f"deleted vendor route paths are back: {sorted(registered)}"


def test_the_router_registers_only_the_surviving_surface():
    """Positive statement of the whole surface, so neither assertion above can
    pass because the router lost routes it should still have."""
    assert _route_decorator_paths(router_module) == {
        "/sync/qbo-vendors",
        "/get/qbo-vendors",
        "/get/qbo-vendors/realm/{realm_id}",
        "/get/qbo-vendor/qbo-id/{qbo_id}",
        "/get/qbo-vendor/{id}",
    }


def test_the_router_calls_no_service_method_that_does_not_exist():
    """The generalised defect, not the seven instances: EVERY `service.<name>`
    the router calls must exist on `QboVendorService`. This is what made the
    seven routes 500 — and it catches an eighth written the same way."""
    missing = sorted(
        name for name in _service_calls(router_module)
        if not hasattr(QboVendorService, name)
    )
    assert missing == [], (
        f"the vendor router calls QboVendorService method(s) that do not exist: "
        f"{missing}. Every call to one raises AttributeError -> HTTP 500."
    )


@pytest.mark.parametrize("method", DEAD_SERVICE_METHODS)
def test_the_missing_service_methods_were_not_written_instead(method):
    """The U-519 precedent, pinned. The fix for a dead write route is deletion,
    not supplying the method it called: `create` / `update_by_id` /
    `delete_by_id` on `QboVendorService` would be an unscoped mutation
    primitive over `qbo.Vendor`, which only the QBO pull may write."""
    assert not hasattr(QboVendorService, method), (
        f"QboVendorService.{method} now exists. If a caller genuinely needs it, "
        f"that is its own unit with its own scoping review -- it must not arrive "
        f"as a repair of a deleted route."
    )


def test_the_request_schemas_are_gone_with_their_routes():
    """`QboVendorCreate` / `QboVendorUpdate` existed only for the deleted
    create/update routes, and both declared `bill_addr_id` — a caller-settable
    bind onto the `qbo.Vendor.BillAddrId` column U-513 drops."""
    for gone in ("QboVendorCreate", "QboVendorUpdate"):
        assert not hasattr(schemas_module, gone), (
            f"{gone} is back in the vendor API schemas; its bill_addr_id binds a "
            f"caller value onto the column U-513 is dropping"
        )


def test_the_sync_request_schema_survives():
    """The one schema that IS used. Guards the assertion above from passing
    because the schemas module was emptied wholesale."""
    assert hasattr(schemas_module, "QboVendorSync")
    assert set(schemas_module.QboVendorSync.model_fields) == {
        "realm_id", "last_updated_time", "sync_to_modules",
    }


def test_the_external_push_schemas_are_untouched():
    """A NAME COLLISION worth pinning: `integrations/intuit/qbo/vendor/external/
    schemas.py` has its own `QboVendorCreate` / `QboVendorUpdate` — the outbound
    QBO push payloads, used by `QboVendorClient`. Those are a different pair and
    U-529 must not have taken them with it."""
    import integrations.intuit.qbo.vendor.external.schemas as external_schemas

    assert hasattr(external_schemas, "QboVendorCreate")
    assert hasattr(external_schemas, "QboVendorUpdate")


def test_no_route_in_the_package_binds_bill_addr_id_off_the_request_body():
    """Standing invariant, AST-level: the column U-513 drops must not be
    reachable from a request body by any route, including one written later."""
    offenders = [
        ast.unparse(node)
        for node in ast.walk(_module_tree(router_module))
        if isinstance(node, ast.Attribute)
        and node.attr == "bill_addr_id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "body"
    ]
    assert offenders == [], f"a vendor route binds bill_addr_id off the body: {offenders}"


def test_the_package_exposes_no_write_route_over_the_staging_table():
    """The altitude-correct form of U-529: not 'those three writes are gone' but
    'this package has no staging mutation route at all'. `POST /sync/qbo-vendors`
    is the sole non-GET, and it takes a realm — not a row selector — and runs the
    pull under `system_authz()`."""
    non_get = set()
    for node in ast.walk(_module_tree(router_module)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
                and dec.func.attr != "get"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                non_get.add((dec.func.attr, dec.args[0].value))

    assert non_get == {("post", "/sync/qbo-vendors")}, (
        f"the vendor package grew a write route over qbo.Vendor: {sorted(non_get)}"
    )
