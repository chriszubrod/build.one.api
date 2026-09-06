"""U-370 B2/B3/B4/B7 — Address design-debt closeout.

No live DB. B2/B4 mock the repo connection; B3/B7 are annotations +
route-dependency inspection.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_args
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.params import Depends as DependsParam

from entities.address.api.router import router
from entities.address.business.model import Address, Country
from entities.address.business.service import AddressService
from entities.address.persistence.repo import AddressRepository
from shared.rbac_constants import Modules

REPO_ROOT = Path(__file__).resolve().parents[1]
_REPO_PY = REPO_ROOT / "entities" / "address" / "persistence" / "repo.py"
_INVOICE_ROUTER = REPO_ROOT / "entities" / "invoice" / "api" / "router.py"

_EXPECTED_BY_ROUTE: list[tuple[str, str, str]] = [
    ("POST", "/api/v1/create/address", "can_create"),
    ("GET", "/api/v1/get/addresses", "can_read"),
    ("GET", "/api/v1/get/address/{public_id}", "can_read"),
    ("PUT", "/api/v1/update/address/{public_id}", "can_update"),
    ("DELETE", "/api/v1/delete/address/{public_id}", "can_delete"),
]


def _address(**overrides) -> Address:
    fields = dict(
        id=41,
        public_id="addr-pub",
        row_version="AAAAAAAAAAA=",
        created_datetime=None,
        modified_datetime=None,
        street_one="1 Main St",
        street_two=None,
        city="Brattleboro",
        state="VT",
        zip="05301",
        country=Country.UNITED_STATES,
    )
    fields.update(overrides)
    return Address(**fields)


def _mock_row():
    from types import SimpleNamespace

    return SimpleNamespace(
        Id=41,
        PublicId="00000000-0000-0000-0000-000000000041",
        RowVersion=b"\x00\x00\x00\x00\x00\x00\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-02 00:00:00",
        StreetOne="1 Main St",
        StreetTwo=None,
        City="Brattleboro",
        State="VT",
        Zip="05301",
        QboId=None,
        RealmId=None,
    )


def _setup_mock_connection(mock_get_connection, *, fetchone=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


# --- B2 country writes -------------------------------------------------------


def test_repo_country_writes_are_country_name_only():
    source = _REPO_PY.read_text(encoding="utf-8")
    assert "hasattr" not in source
    assert "isinstance(country" not in source
    assert "finding/creating the country record" not in source
    assert "finding/updating the country record" not in source


@patch("entities.address.persistence.repo.call_procedure")
@patch("entities.address.persistence.repo.get_connection")
def test_create_writes_country_name(mock_get_connection, mock_call):
    _setup_mock_connection(mock_get_connection, fetchone=_mock_row())

    AddressRepository().create(
        street_one="1 Main St",
        city="Brattleboro",
        state="VT",
        zip="05301",
        country=Country.UNITED_STATES,
    )

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["params"]["Country"] == "United States"


@patch("entities.address.persistence.repo.call_procedure")
@patch("entities.address.persistence.repo.get_connection")
def test_update_writes_country_name_even_when_unset(mock_get_connection, mock_call):
    _setup_mock_connection(mock_get_connection, fetchone=_mock_row())

    AddressRepository().update_by_id(_address(country=None))

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["params"]["Country"] == "United States"


# --- B3 id typing ------------------------------------------------------------


def test_address_id_and_read_by_id_are_int():
    id_type = Address.__dataclass_fields__["id"].type
    args = get_args(id_type)
    assert int in args
    assert type(None) in args

    sig = inspect.signature(AddressService.read_by_id)
    assert sig.parameters["id"].annotation is int


def test_invoice_draw_request_passes_address_id_as_int():
    source = _INVOICE_ROUTER.read_text(encoding="utf-8")
    assert "AddressService().read_by_id(pas[0].address_id)" in source
    assert "read_by_id(str(pas[0].address_id))" not in source


# --- B4 stamp passthrough ----------------------------------------------------


def test_set_qbo_identity_forwards_to_repo():
    repo = Mock()
    service = AddressService(repo=repo)

    service.set_qbo_identity(id=41, qbo_id="PA-99", realm_id="realm-1")

    repo.set_qbo_identity.assert_called_once_with(
        id=41, qbo_id="PA-99", realm_id="realm-1"
    )


# --- B7 VENDORS-only RBAC ----------------------------------------------------


def _full_path(route_path: str) -> str:
    if route_path.startswith(router.prefix):
        return route_path
    prefix = router.prefix.rstrip("/")
    path = route_path if route_path.startswith("/") else f"/{route_path}"
    return f"{prefix}{path}"


def _endpoint_for(method: str, path: str):
    for route in router.routes:
        if getattr(route, "path", None) is None:
            continue
        if _full_path(route.path) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if method in methods:
            return route.endpoint
    return None


def _assert_current_user_rbac(endpoint, expected_permission: str) -> None:
    sig = inspect.signature(endpoint)
    assert "current_user" in sig.parameters, "route must declare current_user"
    param = sig.parameters["current_user"]
    assert param.default is not inspect.Parameter.empty, "current_user must use Depends(...)"
    dep = param.default
    assert isinstance(dep, DependsParam), "current_user must be a FastAPI Depends"
    inner = dep.dependency
    assert inner.__qualname__ == "require_module_api.<locals>._dependency", (
        f"expected require_module_api RBAC, got {inner!r} ({inner.__qualname__})"
    )
    nonlocals = inspect.getclosurevars(inner).nonlocals
    assert nonlocals.get("module_name") == Modules.VENDORS, nonlocals
    assert nonlocals.get("permission") == expected_permission, nonlocals


@pytest.mark.parametrize("method,path,permission", _EXPECTED_BY_ROUTE)
def test_address_route_uses_vendors_module_rbac(method, path, permission):
    endpoint = _endpoint_for(method, path)
    assert endpoint is not None, f"no route registered for {method} {path}"
    _assert_current_user_rbac(endpoint, permission)
