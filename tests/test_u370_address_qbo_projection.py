"""U-370 B1 — Address sprocs project QboId/RealmId on every Address-shaped result.

`ReadAddressById` / `ReadAddressByQboIdAndRealmId` already returned identity;
Create / list / by-public-id / street+city / Update / Delete did not. Repo
`_from_db` already `getattr`s those columns. No live DB.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.address.persistence.repo import AddressRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
_ADDRESS_SQL = REPO_ROOT / "entities" / "address" / "sql" / "dbo.address.sql"

_COL_TOKEN = re.compile(r"\[(\w+)\]")

# Every sproc that returns a full Address row — SetAddressQboIdentity is a
# stamp-only OUTPUT (Id/QboId/RealmId/Stolen) and is out of this shape.
_ADDRESS_RESULT_PROCS = (
    "CreateAddress",
    "ReadAddresses",
    "ReadAddressById",
    "ReadAddressByQboIdAndRealmId",
    "ReadAddressByPublicId",
    "ReadAddressByStreetOneAndCity",
    "UpdateAddressById",
    "DeleteAddressById",
)


def _proc_body(proc_name: str) -> str:
    text = _ADDRESS_SQL.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?dbo\]?\s*\.\s*)?\[?{proc_name}\b\]?"
        rf"(.*?)^END;",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    assert match, f"could not isolate {proc_name}'s body in {_ADDRESS_SQL}"
    return match.group(1)


def _result_column_blocks(proc_name: str) -> list[frozenset[str]]:
    """Column set for every Address-shaped result list (SELECT…FROM or OUTPUT)."""
    body = _proc_body(proc_name)
    blocks = [
        frozenset(_COL_TOKEN.findall(seg))
        for seg in re.findall(r"\bSELECT\b(.*?)\bFROM\b", body, re.IGNORECASE | re.DOTALL)
    ]
    blocks.extend(
        frozenset(_COL_TOKEN.findall(seg))
        for seg in re.findall(
            r"\bOUTPUT\b(.*?)(?:\bVALUES\b|\bWHERE\b)", body, re.IGNORECASE | re.DOTALL
        )
    )
    assert blocks, f"no SELECT/OUTPUT result lists found in {proc_name}"
    return blocks


def _mock_row(**kwargs):
    defaults = {
        "Id": 41,
        "PublicId": "00000000-0000-0000-0000-000000000041",
        "RowVersion": b"\x00\x00\x00\x00\x00\x00\x00\x01",
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-02 00:00:00",
        "StreetOne": "1 Main St",
        "StreetTwo": None,
        "City": "Brattleboro",
        "State": "VT",
        "Zip": "05301",
        "QboId": "PA-99",
        "RealmId": "realm-1",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _setup_mock_connection(mock_get_connection, *, fetchone=None, fetchall=None):
    cursor = MagicMock()
    if fetchone is not None:
        cursor.fetchone.return_value = fetchone
    if fetchall is not None:
        cursor.fetchall.return_value = fetchall
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


BY_ID_COLUMNS = _result_column_blocks("ReadAddressById")[0]


@pytest.mark.parametrize("proc_name", _ADDRESS_RESULT_PROCS)
def test_address_result_sprocs_project_qbo_identity(proc_name):
    """Fails red on the pre-B1 file (the five/six gapped sprocs lacked
    QboId/RealmId) and green after. Shape must match ReadAddressById."""
    for columns in _result_column_blocks(proc_name):
        assert {"QboId", "RealmId"} <= columns
        assert columns == BY_ID_COLUMNS


@patch("entities.address.persistence.repo.get_connection")
def test_read_all_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchall=[_mock_row()])

    addresses = AddressRepository().read_all()

    assert len(addresses) == 1
    assert addresses[0].qbo_id == "PA-99"
    assert addresses[0].realm_id == "realm-1"
    assert "ReadAddresses" in cursor.execute.call_args[0][0]


@patch("entities.address.persistence.repo.get_connection")
def test_read_by_public_id_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchone=_mock_row())

    address = AddressRepository().read_by_public_id("00000000-0000-0000-0000-000000000041")

    assert address.qbo_id == "PA-99"
    assert address.realm_id == "realm-1"
    assert "ReadAddressByPublicId" in cursor.execute.call_args[0][0]


@patch("entities.address.persistence.repo.get_connection")
def test_read_by_street_one_and_city_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchone=_mock_row())

    address = AddressRepository().read_by_street_one_and_city("1 Main St", "Brattleboro")

    assert address.qbo_id == "PA-99"
    assert address.realm_id == "realm-1"
    assert "ReadAddressByStreetOneAndCity" in cursor.execute.call_args[0][0]


@patch("entities.address.persistence.repo.get_connection")
def test_read_all_defaults_identity_to_none_when_row_lacks_it(mock_get_connection):
    row = _mock_row()
    del row.QboId
    del row.RealmId
    _setup_mock_connection(mock_get_connection, fetchall=[row])

    addresses = AddressRepository().read_all()

    assert addresses[0].qbo_id is None
    assert addresses[0].realm_id is None
