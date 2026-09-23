"""U-519: two LIVE pre-existing bugs in the QBO physical_address package.

BUG 1 - ``POST /intuit/qbo/physical-address/sync`` 500'd on EVERY call. The route read
        ``body.qbo_id``, but its request schema ``QboPhysicalAddressSyncRequest``
        declared ``address_id``. Pydantic v2 raises ``AttributeError`` for an
        undeclared field, so the route could never reach the service.

        ROUND 2 -- the round-1 repair was WRONG and the route is now DELETED.
        Reading ``body.address_id`` made a caller-keyed, unscoped upsert reachable
        for the first time. ``get_physical_address(qbo_id)`` IGNORES the id and
        always returns the realm's own CompanyInfo address, so the field was never
        a remote selector -- only the LOCAL upsert key: ``record_id = qbo_id or
        realm_id`` -> ``read_by_qbo_id``, whose sproc is ``WHERE [QboId] = @QboId``
        with no realm and no ownership predicate. The keyspace is guessable
        (``{customer.id}_bill`` / ``_ship`` / ``{vendor.id}_bill``), so one
        authenticated ``QBO_SYNC``/``can_create`` call could overwrite any party's
        staged address and re-stamp its RealmId -- reaching dbo.Address and the
        mailed "TO OWNER:" block of every draw-request packet. Found by two
        independent Pass-3 reviewers. Deleted rather than narrowed: ZERO callers
        anywhere in the umbrella, and it never once returned a success response.
        The tests below now pin its ABSENCE and the absence of the primitive.

BUG 2 - repeat syncs silently affected ZERO rows. The sync update path passed
        ``existing.row_version`` -- base64 TEXT, per ``business/model.py`` -- into an
        UPDATE whose sproc parameter is ``@RowVersion BINARY(8)``
        (``sql/qbo.physical_address.sql``), so the optimistic-concurrency predicate
        ``WHERE [Id] = @Id AND [RowVersion] = @RowVersion`` never matched: 0 rows
        affected, and the sync silently no-op'd from the second call onward.

        The SAME str-vs-bytes mistake sat on the ``PUT /update/{id}`` route, which
        handed the service ``body.row_version`` (the base64 transport string).

        The decode belongs at the CALLER because ``QboPhysicalAddressRepository``
        does NOT decode internally -- it declares ``row_version: bytes`` and drops the
        value straight into ``call_procedure`` params (pinned below by
        ``test_repo_does_not_decode_row_version_so_callers_must``). That is the house
        pattern: the sibling qbo ``vendor`` / ``bill`` / ``company_info`` services all
        pass ``existing.row_version_bytes``.

None of these tests touch the database or the network.
"""
from __future__ import annotations

import ast
import base64
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import integrations.intuit.qbo.physical_address.api.router as router_module
import integrations.intuit.qbo.physical_address.business.service as service_module
import integrations.intuit.qbo.physical_address.persistence.repo as repo_module
from integrations.intuit.qbo.physical_address.api.schemas import (
    QboPhysicalAddressUpdate,
)
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService

# A real 8-byte SQL Server ROWVERSION and its base64 transport form.
RAW_ROW_VERSION = b"\x00\x00\x00\x00\x00\x00\x13\x88"
B64_ROW_VERSION = base64.b64encode(RAW_ROW_VERSION).decode("ascii")


def _address(**overrides) -> QboPhysicalAddress:
    """Build a QboPhysicalAddress whose row_version is base64 TEXT, as the repo returns."""
    fields = dict(
        id=42,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version=B64_ROW_VERSION,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="realm-1",
        realm_id="realm-1",
        line1="1 Old St",
        line2=None,
        city="Nashville",
        country="US",
        country_sub_division_code="TN",
        postal_code="37201",
    )
    fields.update(overrides)
    return QboPhysicalAddress(**fields)


# ---------------------------------------------------------------------------
# AST helpers -- these generalise BUG 1 instead of pinning one line.
# ---------------------------------------------------------------------------

def _route_functions() -> list[ast.FunctionDef]:
    """Every function in the router module decorated with an @router.<verb>(...)."""
    tree = ast.parse(inspect.getsource(router_module))
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
            ):
                routes.append(node)
                break
    return routes


def _body_schema(node: ast.FunctionDef):
    """Resolve the request-schema class annotating this route's ``body`` parameter."""
    for arg in list(node.args.args) + list(node.args.kwonlyargs):
        if arg.arg == "body" and arg.annotation is not None:
            return getattr(router_module, ast.unparse(arg.annotation))
    return None


def _body_attrs(node: ast.FunctionDef) -> set[str]:
    """Every attribute name this route reads off ``body``."""
    return {
        sub.attr
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute)
        and isinstance(sub.value, ast.Name)
        and sub.value.id == "body"
    }


def _route_named(name: str) -> ast.FunctionDef:
    for node in _route_functions():
        if node.name == name:
            return node
    raise AssertionError(f"route {name!r} not found in router module")


# ---------------------------------------------------------------------------
# BUG 1 -- generalised: every body.<attr> must exist on THAT route's schema.
# ---------------------------------------------------------------------------

def test_every_body_attribute_read_exists_on_that_routes_request_schema():
    """Generalises BUG 1: a route may only read fields its own schema declares.

    Mutation guard: rename any field on any of the three request schemas and this
    goes RED, naming the route, the attribute and the schema.
    """
    routes = _route_functions()
    assert routes, "no @router routes discovered -- this test would be vacuous"

    problems: list[str] = []
    per_route: dict[str, int] = {}

    for node in routes:
        schema = _body_schema(node)
        if schema is None:
            continue  # GET/DELETE routes take no body
        declared = set(schema.model_fields)
        attrs = _body_attrs(node)
        per_route[node.name] = len(attrs)
        for attr in sorted(attrs):
            if attr not in declared:
                problems.append(
                    f"{node.name} reads body.{attr}, which {schema.__name__} does not "
                    f"declare (declares: {sorted(declared)})"
                )

    # Anti-vacuity: the three body-bearing routes are create / update / sync, and each
    # must really have been inspected -- an empty read-set would make this pass blindly.
    assert set(per_route) == {
        "create_qbo_physical_address_router",
        "update_qbo_physical_address_by_id_router",
    }, f"unexpected set of body-bearing routes: {sorted(per_route)}"
    assert all(count >= 3 for count in per_route.values()), (
        f"a route contributed suspiciously few body reads: {per_route}"
    )
    assert not problems, "route reads a field its schema does not declare:\n" + "\n".join(problems)


def test_sync_route_is_gone_and_its_schema_with_it():
    """BUG 1 round 2: the route and its request model must STAY deleted.

    Not merely 'not broken' -- absent. Re-adding it reintroduces an unscoped,
    caller-keyed write over qbo.PhysicalAddress (see module docstring).
    """
    route_names = {
        node.name
        for node in ast.walk(ast.parse(inspect.getsource(router_module)))
        if isinstance(node, ast.FunctionDef)
    }
    assert "sync_from_qbo_physical_address_router" not in route_names, (
        "the /sync route is back -- it keys an unscoped upsert off the request body"
    )

    import integrations.intuit.qbo.physical_address.api.schemas as schemas_module

    assert not hasattr(schemas_module, "QboPhysicalAddressSyncRequest"), (
        "QboPhysicalAddressSyncRequest is back; its address_id is a caller-controlled "
        "row key and its access_token is a body-borne OAuth token the service ignores"
    )


def test_sync_from_qbo_cannot_be_keyed_off_caller_input():
    """The primitive itself is gone, not just the route that reached it.

    `sync_from_qbo` must accept realm_id and NOTHING else, so no future caller can
    choose which row gets overwritten.
    """
    params = inspect.signature(QboPhysicalAddressService.sync_from_qbo).parameters
    assert set(params) == {"self", "realm_id"}, (
        f"sync_from_qbo grew a parameter: {sorted(params)}. A record-selector "
        f"argument here is the U-519 IDOR; key it on realm_id only."
    )


def test_no_route_in_this_package_reaches_sync_from_qbo_with_body_data():
    """Standing invariant, AST-level: a NEW route must not pipe request-body values
    into sync_from_qbo. Catches a re-add that dodges the by-name checks above."""
    offenders = []
    for node in ast.walk(ast.parse(inspect.getsource(router_module))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "sync_from_qbo"):
            continue
        for kw in node.keywords:
            rendered = ast.unparse(kw.value)
            if rendered.startswith("body."):
                offenders.append(f"sync_from_qbo({kw.arg}={rendered})")
    assert not offenders, (
        "a router passes request-body data into sync_from_qbo:\n  " + "\n  ".join(offenders)
    )


def test_create_and_update_routes_legitimately_read_body_qbo_id():
    """Guards the two body.qbo_id reads that are CORRECT -- do not 'fix' these.

    Both QboPhysicalAddressCreate and QboPhysicalAddressUpdate really do declare
    qbo_id, so the create/update routes reading it is right.
    """
    for route_name in (
        "create_qbo_physical_address_router",
        "update_qbo_physical_address_by_id_router",
    ):
        node = _route_named(route_name)
        schema = _body_schema(node)
        assert "qbo_id" in _body_attrs(node), f"{route_name} should read body.qbo_id"
        assert "qbo_id" in schema.model_fields, (
            f"{schema.__name__} must declare qbo_id for {route_name} to read it"
        )


def test_sync_from_qbo_keys_the_record_on_the_realm():
    """BUG 1 round 2, behaviourally: the upsert key is the realm, full stop.

    Mutation guard: restore `record_id = qbo_id or realm_id` plus a qbo_id
    parameter and this test can no longer express the call, because the signature
    no longer accepts one.
    """
    repo = _FakeRepo(existing=None)
    service = QboPhysicalAddressService()
    service.repo = repo

    with patch.object(service_module, "QboPhysicalAddressClient", _fake_client(QBO_PAYLOAD)):
        service.sync_from_qbo(realm_id="realm-1")

    assert len(repo.create_calls) == 1
    assert repo.create_calls[0]["qbo_id"] == "realm-1", (
        "record must be keyed on the realm, never on caller input"
    )


# ---------------------------------------------------------------------------
# BUG 2 -- the repo does not decode, so every caller must pass bytes.
# ---------------------------------------------------------------------------

def test_repo_does_not_decode_row_version_so_callers_must():
    """Pins WHY the fix belongs at the caller: the repo is a straight passthrough.

    ``UpdateQboPhysicalAddressById`` declares ``@RowVersion BINARY(8)``. If this ever
    starts decoding internally, this test goes RED and the caller-side decodes below
    become wrong -- that is the intended signal.
    """
    captured: dict = {}

    db_row = SimpleNamespace(
        Id=42,
        PublicId="11111111-1111-1111-1111-111111111111",
        RowVersion=RAW_ROW_VERSION,
        CreatedDatetime=None,
        ModifiedDatetime=None,
        QboId="realm-1",
        RealmId="realm-1",
        Line1="1 Old St",
        Line2=None,
        City="Nashville",
        Country="US",
        CountrySubDivisionCode="TN",
        PostalCode="37201",
    )
    conn = MagicMock()
    conn.__enter__.return_value.cursor.return_value.fetchone.return_value = db_row

    def _spy(*, cursor, name, params):
        captured["name"] = name
        captured["params"] = params
        return cursor

    with patch.object(repo_module, "get_connection", return_value=conn), patch.object(
        repo_module, "call_procedure", side_effect=_spy
    ):
        repo_module.QboPhysicalAddressRepository().update_by_id(
            id=42,
            row_version=RAW_ROW_VERSION,
            qbo_id="realm-1",
            line1="1 Old St",
            line2=None,
            city="Nashville",
            country="US",
            country_sub_division_code="TN",
            postal_code="37201",
        )

    assert captured["name"] == "UpdateQboPhysicalAddressById"
    assert captured["params"]["RowVersion"] == RAW_ROW_VERSION, (
        "repo must pass row_version through untouched -- callers own the decode"
    )
    assert isinstance(captured["params"]["RowVersion"], bytes)


def test_update_route_passes_decoded_bytes_to_the_service():
    """BUG 2 on the PUT route: the base64 transport string must be decoded to BINARY(8).

    Mutation guard: revert to ``row_version=body.row_version`` and this goes RED on
    the isinstance assertion.
    """
    captured: dict = {}
    fake_service = Mock()
    fake_service.update_by_id.side_effect = lambda **kw: captured.update(kw) or _address()

    body = QboPhysicalAddressUpdate(row_version=B64_ROW_VERSION, qbo_id="realm-1", line1="2 New St")
    with patch.object(router_module, "service", fake_service):
        router_module.update_qbo_physical_address_by_id_router(
            id=42, body=body, current_user={}
        )

    assert isinstance(captured["row_version"], bytes), (
        f"update route handed the service {type(captured['row_version']).__name__}; "
        "the sproc parameter is BINARY(8) and the repo does not decode"
    )
    assert captured["row_version"] == RAW_ROW_VERSION
    assert len(captured["row_version"]) == 8, "a SQL Server ROWVERSION is exactly 8 bytes"


class _FakeRepo:
    """In-memory stand-in for QboPhysicalAddressRepository -- records every call."""

    def __init__(self, existing: QboPhysicalAddress | None):
        self.existing = existing
        self.update_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def read_by_qbo_id(self, *, qbo_id):
        return self.existing

    def read_by_id(self, *, id):
        return self.existing

    def update_by_id(self, **kwargs):
        self.update_calls.append(kwargs)
        return self.existing

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _address(**{k: v for k, v in kwargs.items() if k != "realm_id"})


def _fake_client(qbo_address):
    """Patch target for QboPhysicalAddressClient -- a context manager yielding a stub."""
    cm = MagicMock()
    cm.__enter__.return_value = SimpleNamespace(
        get_physical_address=lambda *, qbo_id=None: qbo_address
    )
    return Mock(return_value=cm)


QBO_PAYLOAD = SimpleNamespace(
    line1="500 Broadway",
    line2="Suite 300",
    city="Nashville",
    country="US",
    country_sub_division_code="TN",
    postal_code="37203",
)


def test_second_sync_of_an_existing_row_actually_updates_it():
    """BUG 2 at the reported site: a repeat sync must hit UPDATE with decoded bytes.

    Before the fix this passed the base64 string, so ``WHERE RowVersion = @RowVersion``
    never matched and the UPDATE affected 0 rows -- a silent no-op.

    Mutation guard: revert to ``row_version=existing.row_version`` and this goes RED.
    """
    existing = _address()
    repo = _FakeRepo(existing=existing)
    service = QboPhysicalAddressService(repo=repo)

    with patch.object(service_module, "QboPhysicalAddressClient", _fake_client(QBO_PAYLOAD)):
        service.sync_from_qbo(realm_id="realm-1")

    assert repo.create_calls == [], "an existing row must be updated, never re-created"
    assert len(repo.update_calls) == 1, "the second sync must issue exactly one UPDATE"

    call = repo.update_calls[0]
    assert isinstance(call["row_version"], bytes), (
        f"sync passed {type(call['row_version']).__name__}; the sproc parameter is "
        "BINARY(8), so a base64 string can never match and the UPDATE affects 0 rows"
    )
    assert call["row_version"] == RAW_ROW_VERSION
    assert call["id"] == 42
    # The new QBO values really do reach the UPDATE -- not just the row_version.
    assert call["line1"] == "500 Broadway"
    assert call["postal_code"] == "37203"


def test_first_sync_with_no_existing_row_creates_and_never_updates():
    """Anti-vacuity partner: proves the fake repo drives BOTH branches, so the
    update-branch assertions above are really exercising the update path."""
    repo = _FakeRepo(existing=None)
    service = QboPhysicalAddressService(repo=repo)

    with patch.object(service_module, "QboPhysicalAddressClient", _fake_client(QBO_PAYLOAD)):
        service.sync_from_qbo(realm_id="realm-1")

    assert repo.update_calls == []
    assert len(repo.create_calls) == 1
    assert repo.create_calls[0]["qbo_id"] == "realm-1", "record_id defaults to realm_id"
    assert "row_version" not in repo.create_calls[0], "CREATE has no optimistic-concurrency token"


def test_no_row_version_call_site_in_the_package_passes_the_base64_string():
    """Sweeps the whole package for the str-vs-bytes mistake, so a NEW call site
    reintroducing it fails here even if it dodges every test above."""
    offenders: list[str] = []
    for module in (router_module, service_module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "row_version":
                continue
            rendered = ast.unparse(node.value)
            # Allowed: <obj>.row_version_bytes, base64.b64decode(...), or a bytes-typed
            # parameter forwarded verbatim (QboPhysicalAddressService.update_by_id).
            ok = (
                rendered.endswith(".row_version_bytes")
                or "b64decode" in rendered
                or rendered == "row_version"
            )
            if not ok:
                offenders.append(f"{module.__name__}: row_version={rendered}")

    assert not offenders, (
        "row_version call site passes a base64 string into a BINARY(8) parameter:\n"
        + "\n".join(offenders)
    )
