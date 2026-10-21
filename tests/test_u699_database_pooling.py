"""U-699: ODBC pooling configuration and dead-pooled-connection mitigation in shared.database."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyodbc
import pytest

import shared.database as db


def test_pyodbc_pooling_enabled_before_connect():
    assert db.pyodbc.pooling is True


def test_is_transient_includes_communication_link_for_pooled_dead_handle():
    err = pyodbc.Error("08S01", "[08S01] [Microsoft][ODBC Driver 18 for SQL Server]Communication link failure")
    assert db.is_transient_error(err) is True


def test_get_connection_retries_when_validate_sees_transient_execute_error():
    good = MagicMock(name="conn_ok")
    bad = MagicMock(name="conn_bad")
    calls = {"n": 0}

    def fake_connect():
        calls["n"] += 1
        return bad if calls["n"] == 1 else good

    def validate(conn):
        if conn is bad:
            raise pyodbc.Error("08S01", "Communication link failure")

    with patch.object(db, "_connect", side_effect=fake_connect), patch.object(
        db, "_validate_connection", side_effect=validate
    ):
        with db.get_connection() as conn:
            assert conn is good
    bad.close.assert_called_once()


def test_get_connection_does_not_retry_non_transient_validation_error():
    connect_calls = {"n": 0}

    def fake_connect():
        connect_calls["n"] += 1
        return MagicMock(name="conn")

    def validate(_conn):
        raise pyodbc.Error("42000", "[42000] permission denied on object")

    with patch.object(db, "_connect", side_effect=fake_connect), patch.object(
        db, "_validate_connection", side_effect=validate
    ):
        with pytest.raises(pyodbc.Error):
            with db.get_connection():
                pass

    assert connect_calls["n"] == 1
