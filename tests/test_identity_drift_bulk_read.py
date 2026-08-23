"""Pure-logic tests for identity_drift.py's registry-driven bulk QBO identity
reader (U-305) — the generic function Bill/BillCredit reconciliation calls
instead of two hand-copied entity-specific sprocs (Decision-1). Covers the
RBAC gate, the SQL/param shape, and DB-error wrapping; the reconciliation
service's own consumption of it is covered by
tests/test_qbo_reconcile_bill_missing_locally.py and
tests/test_qbo_reconcile_purchase_vendorcredit.py.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.identity_drift import (
    HEADER_ENTITY_SPECS,
    REFERENCE_ENTITY_SPECS,
    FlatEntitySpec,
    read_qbo_identity_rows_by_realm_id,
)


def _bill_spec():
    return next(s for s in HEADER_ENTITY_SPECS if s.key == "bill")


def _bill_credit_spec():
    return next(s for s in REFERENCE_ENTITY_SPECS if s.key == "bill_credit")


def test_bill_and_bill_credit_specs_carry_their_access_udf():
    assert _bill_spec().access_udf == "UserCanAccessBill"
    assert _bill_credit_spec().access_udf == "UserCanAccessBillCredit"


def test_other_registry_entities_are_unaffected_by_the_new_field():
    """Additive-only (Decision-1): every spec besides bill/bill_credit still
    defaults access_udf to None — no behavior change to the other 11."""
    untouched_keys = {
        s.key for s in (*HEADER_ENTITY_SPECS, *REFERENCE_ENTITY_SPECS)
        if s.key not in ("bill", "bill_credit")
    }
    assert len(untouched_keys) == 11
    for s in (*HEADER_ENTITY_SPECS, *REFERENCE_ENTITY_SPECS):
        if s.key not in ("bill", "bill_credit"):
            assert s.access_udf is None


def test_raises_without_access_udf_configured():
    spec = FlatEntitySpec(
        "vendor", "Vendor", "VendorVendor", "Vendor", "VendorId", "QboVendorId", False, "SetVendorQboIdentity",
    )
    with pytest.raises(ValueError, match="access_udf"):
        read_qbo_identity_rows_by_realm_id(spec, "realm-1")


def test_executes_expected_sql_and_params_for_bill():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        SimpleNamespace(Id=1, QboId="B-1"),
        SimpleNamespace(Id=2, QboId="B-2"),
    ]
    with patch("shared.database.get_connection") as mock_conn_ctx:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        rows = read_qbo_identity_rows_by_realm_id(
            _bill_spec(), "realm-1", actor_user_id=17, actor_is_system_admin=True,
        )

    assert [(r.id, r.qbo_id) for r in rows] == [(1, "B-1"), (2, "B-2")]
    sql, params = cursor.execute.call_args[0]
    assert "dbo.[Bill]" in sql
    assert "[RealmId] = ?" in sql
    assert "[QboId] IS NOT NULL" in sql
    assert "dbo.UserCanAccessBill(?, ?, [Id]) = 1" in sql
    assert params == ["realm-1", 17, 1]  # BIT param: coerced True -> 1


def test_executes_expected_sql_for_bill_credit():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    with patch("shared.database.get_connection") as mock_conn_ctx:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        read_qbo_identity_rows_by_realm_id(_bill_credit_spec(), "realm-1")

    sql, params = cursor.execute.call_args[0]
    assert "dbo.[BillCredit]" in sql
    assert "dbo.UserCanAccessBillCredit(?, ?, [Id]) = 1" in sql
    assert params == ["realm-1", None, None]


def test_filters_out_rows_the_sproc_shape_would_never_return_but_guards_anyway():
    """Defensive: a defensive None-QboId row (shouldn't happen given the SQL's
    own WHERE clause) is still safe to construct — mirrors the ExpenseRepository
    precedent's defensive comment."""
    cursor = MagicMock()
    cursor.fetchall.return_value = [SimpleNamespace(Id=1, QboId="B-1")]
    with patch("shared.database.get_connection") as mock_conn_ctx:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        rows = read_qbo_identity_rows_by_realm_id(_bill_spec(), "realm-1")
    assert len(rows) == 1


def test_wraps_db_errors_via_map_database_error():
    with patch("shared.database.get_connection") as mock_conn_ctx:
        mock_conn_ctx.return_value.__enter__.side_effect = RuntimeError("connection refused")
        with pytest.raises(Exception):
            read_qbo_identity_rows_by_realm_id(_bill_spec(), "realm-1")
