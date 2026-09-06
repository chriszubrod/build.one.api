"""U-370 — Address HTTP CRUD guards (404 / 422 / schema vs SQL lengths).

No live DB. Router tests call the endpoints directly with a patched
AddressService; schema tests construct Pydantic models.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from entities.address.api.router import (
    delete_address_by_public_id_router,
    get_address_by_public_id_router,
    update_address_by_public_id_router,
)
from entities.address.api.schemas import AddressCreate, AddressUpdate
from entities.address.business.model import Address, Country
from entities.address.business.service import AddressService
from shared.api.errors import ApiError, ErrorCode
from shared.database import DatabaseConstraintError
from shared.db_constraints import FK_REFERENCE_MESSAGE, FK_REFERENCE_VIOLATION

_VALID_CREATE = dict(
    street_one="1 Main St",
    city="Brattleboro",
    state="VT",
    zip="05301",
)
_VALID_UPDATE = dict(
    row_version="AAAAAAAAAAA=",
    **_VALID_CREATE,
)


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


# --- A1 / A4 router ----------------------------------------------------------


def test_get_missing_address_is_404_not_attribute_error():
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.read_by_public_id.return_value = None
        with pytest.raises(ApiError) as exc_info:
            get_address_by_public_id_router(public_id="missing", current_user={})

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Address not found"
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


def test_get_address_returns_item_envelope():
    address = _address()
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.read_by_public_id.return_value = address
        result = get_address_by_public_id_router(public_id="addr-pub", current_user={})

    assert result == {"data": address.to_dict()}


def test_delete_missing_address_is_404_not_attribute_error():
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.delete_by_public_id.return_value = None
        with pytest.raises(ApiError) as exc_info:
            delete_address_by_public_id_router(public_id="missing", current_user={})

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Address not found"
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


def test_delete_in_use_address_is_422_fk_reference_not_409():
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.delete_by_public_id.side_effect = DatabaseConstraintError(
            FK_REFERENCE_VIOLATION,
            "The DELETE statement conflicted with the REFERENCE constraint "
            '"FK_VendorAddress_Address". (547) (SQLExecDirectW)',
        )
        with pytest.raises(ApiError) as exc_info:
            delete_address_by_public_id_router(public_id="in-use", current_user={})

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == FK_REFERENCE_MESSAGE
    assert exc_info.value.error_code == ErrorCode.FK_VIOLATION
    assert exc_info.value.status_code != 409


def test_delete_unused_address_returns_item_envelope():
    address = _address()
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.delete_by_public_id.return_value = address
        result = delete_address_by_public_id_router(public_id="addr-pub", current_user={})

    assert result == {"data": address.to_dict()}


def test_delete_non_constraint_error_is_not_rewritten_to_422():
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.delete_by_public_id.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            delete_address_by_public_id_router(public_id="addr-pub", current_user={})


def test_update_missing_address_is_404():
    with patch("entities.address.api.router.AddressService") as service_cls:
        service_cls.return_value.update_by_public_id.return_value = None
        with pytest.raises(ApiError) as exc_info:
            update_address_by_public_id_router(
                public_id="missing",
                body=AddressUpdate(**_VALID_UPDATE),
                current_user={},
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Address not found"
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


# --- A2 service --------------------------------------------------------------


def test_update_by_public_id_returns_none_without_calling_repo_update():
    repo = Mock()
    repo.read_by_public_id.return_value = None
    service = AddressService(repo=repo)

    result = service.update_by_public_id("missing", SimpleNamespace(**_VALID_UPDATE))

    assert result is None
    repo.update_by_id.assert_not_called()


def test_update_by_public_id_writes_fields_then_calls_repo():
    existing = _address()
    repo = Mock()
    repo.read_by_public_id.return_value = existing
    repo.update_by_id.return_value = existing
    service = AddressService(repo=repo)
    body = SimpleNamespace(
        row_version="new-rv",
        street_one="2 Oak",
        street_two="Ste 4",
        city="Nashville",
        state="TN",
        zip="37201",
    )

    result = service.update_by_public_id("addr-pub", body)

    assert result is existing
    repo.update_by_id.assert_called_once_with(existing)
    assert existing.row_version == "new-rv"
    assert existing.street_one == "2 Oak"
    assert existing.street_two == "Ste 4"
    assert existing.city == "Nashville"
    assert existing.state == "TN"
    assert existing.zip == "37201"
    assert existing.country is Country.UNITED_STATES


# --- A3 schema vs SQL (state/zip only; city unchanged) -----------------------


@pytest.mark.parametrize("model, kwargs", [
    (AddressCreate, _VALID_CREATE),
    (AddressUpdate, _VALID_UPDATE),
])
def test_valid_state_and_zip_are_accepted(model, kwargs):
    model(**kwargs)


@pytest.mark.parametrize("model, kwargs", [
    (AddressCreate, _VALID_CREATE),
    (AddressUpdate, _VALID_UPDATE),
])
@pytest.mark.parametrize("field, value", [
    ("state", "CAL"),
    ("zip", "05301-1234"),
    ("zip", "123456"),
])
def test_state_and_zip_over_sql_length_are_rejected(model, kwargs, field, value):
    payload = dict(kwargs)
    payload[field] = value
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize("model, kwargs", [
    (AddressCreate, _VALID_CREATE),
    (AddressUpdate, _VALID_UPDATE),
])
def test_city_length_is_unchanged(model, kwargs):
    model(**{**kwargs, "city": "C" * 100})
    with pytest.raises(ValidationError):
        model(**{**kwargs, "city": "C" * 101})
