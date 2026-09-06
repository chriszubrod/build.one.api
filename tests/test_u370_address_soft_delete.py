"""U-370 C1/C2 — unused-only Address soft-delete + street/city adopt docs.

No live DB. SQL guards are static; service/repo tests mock the connection.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.address.business.model import Address, Country
from entities.address.business.service import AddressService
from entities.address.persistence.repo import AddressRepository
from shared.database import DatabaseConstraintError
from shared.db_constraints import FK_REFERENCE_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[1]
_ADDRESS_SQL = REPO_ROOT / "entities" / "address" / "sql" / "dbo.address.sql"


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


def _sql() -> str:
    return _ADDRESS_SQL.read_text(encoding="utf-8")


# --- C1 SQL -----------------------------------------------------------------


def test_live_address_reads_filter_is_deleted():
    text = _sql()
    for proc in (
        "ReadAddresses",
        "ReadAddressById",
        "ReadAddressByQboIdAndRealmId",
        "ReadAddressByPublicId",
        "ReadAddressByStreetOneAndCity",
        "UpdateAddressById",
    ):
        assert proc in text
    assert "ReadDeletedAddressByQboIdAndRealmId" in text
    assert text.count("[IsDeleted] = 0") >= 5
    assert "[IsDeleted] = 1" in text


def test_delete_address_is_unused_only_soft_delete():
    text = _sql()
    delete_start = text.index("CREATE OR ALTER PROCEDURE DeleteAddressById")
    delete_body = text[delete_start:text.index("CREATE OR ALTER PROCEDURE SetAddressQboIdentity")]
    assert "DELETE FROM dbo.[Address]" not in delete_body
    assert "[IsDeleted] = 1" in delete_body
    assert "VendorAddress" in delete_body
    assert "ProjectAddress" in delete_body
    assert "ROLLBACK" not in delete_body


# --- C2 SQL -----------------------------------------------------------------


def test_street_city_read_is_deterministic_without_unique_index():
    text = _sql()
    street_start = text.index("CREATE OR ALTER PROCEDURE ReadAddressByStreetOneAndCity")
    street_body = text[street_start:text.index("CREATE OR ALTER PROCEDURE UpdateAddressById")]
    assert "SELECT TOP 1" in street_body
    assert "ORDER BY [Id] ASC" in street_body
    assert "[IsDeleted] = 0" in street_body
    assert "UNIQUE INDEX" not in street_body
    assert "UQ_Address_Street" not in text


# --- C1 service / repo ------------------------------------------------------


def test_delete_unused_returns_tombstone():
    existing = _address()
    tombstone = _address(is_deleted=True)
    repo = Mock()
    repo.read_by_public_id.return_value = existing
    repo.delete_by_id.return_value = tombstone
    service = AddressService(repo=repo)

    result = service.delete_by_public_id("addr-pub")

    assert result is tombstone
    repo.delete_by_id.assert_called_once_with(41)


def test_delete_in_use_raises_fk_reference_without_409():
    repo = Mock()
    repo.read_by_public_id.return_value = _address()
    repo.delete_by_id.return_value = None
    service = AddressService(repo=repo)

    with pytest.raises(DatabaseConstraintError) as exc_info:
        service.delete_by_public_id("addr-pub")

    assert str(exc_info.value) == FK_REFERENCE_MESSAGE
    assert exc_info.value.violation.http_status == 422


def test_delete_missing_returns_none_without_repo_delete():
    repo = Mock()
    repo.read_by_public_id.return_value = None
    service = AddressService(repo=repo)

    assert service.delete_by_public_id("missing") is None
    repo.delete_by_id.assert_not_called()


@patch("entities.address.persistence.repo.call_procedure")
@patch("entities.address.persistence.repo.get_connection")
def test_read_deleted_by_qbo_identity_calls_sproc(mock_get_connection, mock_call):
    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(
        Id=77,
        PublicId="00000000-0000-0000-0000-000000000077",
        RowVersion=b"\x00\x00\x00\x00\x00\x00\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-02 00:00:00",
        StreetOne="1 Main St",
        StreetTwo=None,
        City="Brattleboro",
        State="VT",
        Zip="05301",
        IsDeleted=True,
        QboId="PA-99",
        RealmId="realm-1",
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn

    result = AddressRepository().read_deleted_by_qbo_identity("PA-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadDeletedAddressByQboIdAndRealmId"
    assert result is not None
    assert result.id == 77
    assert result.is_deleted is True
    assert result.qbo_id == "PA-99"
