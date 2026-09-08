"""U-411 — `/get/bill-line-item-attachment/by-bill-line-item/{...}` contract.

Three defects were reported live against prod on 2026-09-08. Two were real:

1. The path parameter was named `bill_line_item_id` but is resolved as a
   BillLineItem PUBLIC id, straight into `ReadBillLineItemByPublicId
   (@PublicId UNIQUEIDENTIFIER)`. An integer therefore failed in the DRIVER
   (SQL 8114) rather than at the edge.
2. That driver failure surfaced as a 500 whose `detail` was the raw ODBC text
   ("[Microsoft][ODBC Driver 18 for SQL Server]... (8114) (SQLExecDirectW)").

The third — "returns a single object, should be a list" — is REFUTED, and these
tests pin the refutation so a future reader does not re-open it: the live
`UQ_BillLineItemAttachment_BillLineItemId` constraint makes >1 link row per
BillLineItem impossible (prod 2026-09-08: 3931 rows / 3931 distinct line items /
0 with more than one). The many-side runs the other way — 215 Attachments are
each linked to several BillLineItems, which is how a multi-line bill shares one
invoice PDF, and that needs no shape change here.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import entities.bill_line_item_attachment.api.router as blia_router
from shared.access import EntityNotAccessibleError
from shared.api.errors import ErrorCode
from shared.api.responses import parse_public_id, raise_server_error
from shared.database import DatabaseConstraintError, DatabaseOperationError
from shared.db_constraints import UNIQUE_VIOLATION

USER = {"id": 1, "tenant_id": 1}
BLI_PUBLIC_ID = "2db45c78-c915-4315-9e51-e633bd844e47"

# The exact body prod returned for `GET .../by-bill-line-item/24838`.
ODBC_8114 = (
    "Database operation failed: ('42000', '[42000] [Microsoft][ODBC Driver 18 "
    "for SQL Server][SQL Server]Error converting data type nvarchar to "
    "uniqueidentifier. (8114) (SQLExecDirectW)')"
)

BASE_SQL = Path(__file__).resolve().parents[1] / (
    "entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql"
)


LINK_ROW = {
    "id": 7,
    "public_id": "11111111-1111-1111-1111-111111111111",
    "bill_line_item_id": 24838,
    "attachment_id": 99,
}


def _call(public_id):
    return blia_router.get_bill_line_item_attachment_by_bill_line_item_public_id_router(
        bill_line_item_public_id=public_id, current_user=USER
    )


# --- Defect 1: the parameter is named for what it actually is ----------------


def test_route_path_names_the_parameter_public_id():
    """`by-<x>/{<x>_public_id}` is the prevailing convention (bill_credit's sibling,
    budget, budget_revision, employee_project_rate, vendor_project_rate, ...).
    The old `{bill_line_item_id}` promised the BIGINT id the endpoint cannot take."""
    paths = [r.path for r in blia_router.router.routes]
    assert (
        "/api/v1/get/bill-line-item-attachment/by-bill-line-item/{bill_line_item_public_id}"
        in paths
    )
    assert not any("{bill_line_item_id}" in p for p in paths)


# --- Defect 2: a malformed id is rejected at the edge, cleanly ----------------


def test_non_uuid_is_a_clean_422_and_never_reaches_the_database():
    """`24838` is the exact input that produced the reported 500 in prod."""
    with patch.object(blia_router, "service", MagicMock()) as service:
        with pytest.raises(HTTPException) as exc:
            _call("24838")
    assert exc.value.status_code == 422
    assert exc.value.detail == "bill_line_item_public_id must be a UUID"
    assert exc.value.error_code == ErrorCode.VALIDATION_ERROR
    # No DB connection is opened for input the edge can reject on its own.
    service.read_by_bill_line_item_id.assert_not_called()


def test_driver_text_never_reaches_the_caller():
    """Whatever escapes the service, the client gets a generic 500 — not the
    server-side type names, driver build and SQLSTATE the old handler echoed."""
    service = MagicMock()
    service.read_by_bill_line_item_id.side_effect = DatabaseOperationError(ODBC_8114)
    with patch.object(blia_router, "service", service):
        with pytest.raises(HTTPException) as exc:
            _call(BLI_PUBLIC_ID)
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to read bill line item attachment"


def test_real_constraint_violations_keep_their_4xx():
    """raise_server_error must not flatten a classified DB failure into a 500 —
    the iOS duplicate-claim recovery keys on the 422 + the original message."""
    error = DatabaseConstraintError(UNIQUE_VIOLATION, original="Cannot insert duplicate key ...")
    with pytest.raises(HTTPException) as exc:
        raise_server_error(error, "generic")
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.DUPLICATE_KEY


def test_access_denial_stays_a_404_and_is_not_flattened_into_500():
    """`BillLineItemService.read_by_public_id` calls `assert_can_access_bill`, so a
    non-admin without UserProject access reaches this router as
    EntityNotAccessibleError. It must reach its own handler (404, never 403 or 500)
    -- the old `detail=str(e)` answered 500 with "BillLineItem <id> is not
    accessible to the current actor", disclosing both existence and access state."""
    service = MagicMock()
    service.read_by_bill_line_item_id.side_effect = EntityNotAccessibleError("BillLineItem", 24838)
    with patch.object(blia_router, "service", service):
        with pytest.raises(EntityNotAccessibleError):
            _call(BLI_PUBLIC_ID)


def test_a_status_a_service_already_chose_is_not_downgraded_to_500():
    chosen = HTTPException(status_code=409, detail="row-version conflict")
    with pytest.raises(HTTPException) as exc:
        raise_server_error(chosen, "generic")
    assert exc.value is chosen


# --- Defect 3 (refuted): the single-item shape, and what enforces it ----------


def test_valid_public_id_returns_one_item_not_a_list():
    service = MagicMock()
    service.read_by_bill_line_item_id.return_value = SimpleNamespace(to_dict=lambda: LINK_ROW)
    with patch.object(blia_router, "service", service):
        result = _call(BLI_PUBLIC_ID.upper())
    service.read_by_bill_line_item_id.assert_called_once_with(
        bill_line_item_public_id=BLI_PUBLIC_ID  # normalized, case and all
    )
    assert set(result) == {"data"}
    assert isinstance(result["data"], dict)


def test_missing_link_is_a_404():
    service = MagicMock()
    service.read_by_bill_line_item_id.return_value = None
    with patch.object(blia_router, "service", service):
        with pytest.raises(HTTPException) as exc:
            _call(BLI_PUBLIC_ID)
    assert exc.value.status_code == 404


def test_unique_constraint_still_guarantees_at_most_one_link_per_line_item():
    """The single-item response shape is only correct while this constraint
    stands. Drop it and this test fails, forcing the response-shape decision
    (and its web/iOS/MCP client changes) to be made deliberately."""
    sql = BASE_SQL.read_text()
    assert "UQ_BillLineItemAttachment_BillLineItemId" in sql
    assert "UNIQUE ([BillLineItemId])" in sql


# --- The shared helper -------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        BLI_PUBLIC_ID,
        BLI_PUBLIC_ID.upper(),
        "{2db45c78-c915-4315-9e51-e633bd844e47}",
        "2db45c78c91543159e51e633bd844e47",
    ],
)
def test_parse_public_id_normalizes_accepted_forms(raw):
    assert parse_public_id(raw, "x") == BLI_PUBLIC_ID


@pytest.mark.parametrize("raw", [None, "", "24838", "abc", 24838])
def test_parse_public_id_rejects_non_uuids(raw):
    with pytest.raises(HTTPException) as exc:
        parse_public_id(raw, "bill_line_item_public_id")
    assert exc.value.status_code == 422
