import pytest
from fastapi import HTTPException

from shared.api.responses import (
    accepted_response,
    item_response,
    list_response,
    raise_database_error,
    raise_not_found,
    raise_workflow_error,
)


def test_list_response_count_defaults_to_len():
    data = [{"id": 1}, {"id": 2}]
    assert list_response(data) == {"data": data, "count": 2}


def test_list_response_count_override():
    data = [{"id": 1}]
    assert list_response(data, count=99) == {"data": data, "count": 99}


def test_list_response_empty():
    assert list_response([]) == {"data": [], "count": 0}


def test_list_response_passes_data_unchanged():
    data = [{"nested": {"x": [1, 2]}}]
    result = list_response(data)
    assert result["data"] is data


def test_item_response():
    payload = {"public_id": "abc", "name": "Bill"}
    assert item_response(payload) == {"data": payload}


def test_accepted_response_default_id_field():
    assert accepted_response("abc") == {"status": "accepted", "id": "abc"}


def test_accepted_response_custom_id_field():
    assert accepted_response("abc", id_field="public_id") == {
        "status": "accepted",
        "public_id": "abc",
    }


def test_raise_workflow_error_empty_uses_default_message():
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error("", "Something went wrong")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Something went wrong"


def test_raise_workflow_error_already_exists_is_409():
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error("Vendor already exists", "default")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Vendor already exists"


def test_raise_workflow_error_concurrency_is_409():
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error("concurrency conflict", "default")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "concurrency conflict"


def test_raise_workflow_error_row_version_is_409():
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error("row-version mismatch", "default")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "row-version mismatch"


def test_raise_workflow_error_other_string_is_400():
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error("something else", "default")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "something else"


def test_raise_workflow_error_case_insensitive_already_exists():
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error("ALREADY EXISTS", "default")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "ALREADY EXISTS"


def test_raise_not_found():
    with pytest.raises(HTTPException) as exc_info:
        raise_not_found("Bill")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Bill not found"


def test_raise_database_error_unique_key_is_422_with_original_message():
    original = Exception("Violation of UNIQUE KEY constraint")
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(original)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Violation of UNIQUE KEY constraint"


def test_raise_database_error_duplicate_key_is_422():
    original = Exception("duplicate key row")
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(original)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "duplicate key row"


def test_raise_database_error_non_db_error_reraises_unchanged():
    original = ValueError("boom")
    with pytest.raises(ValueError) as exc_info:
        raise_database_error(original)
    assert exc_info.value is original
    assert str(exc_info.value) == "boom"


def _odbc_error(sql_text: str) -> Exception:
    """A pyodbc failure in the shape routers actually receive it — the raw
    driver text after shared.database.map_database_error re-wrapped it."""
    return Exception(
        "Database operation failed: ('23000', '[23000] [Microsoft]"
        f"[ODBC Driver 18 for SQL Server][SQL Server]{sql_text} (SQLExecDirectW)')"
    )


# SQL Server's two 547 phrasings: a blocked parent delete vs. a child row
# naming a parent that doesn't exist.
FK_DELETE_SQL = (
    'The DELETE statement conflicted with the REFERENCE constraint "FK_RoleModule_Role". '
    "The conflict occurred in database \"builddb\", table \"dbo.RoleModule\", column 'RoleId'. (547)"
)
FK_INSERT_SQL = (
    'The INSERT statement conflicted with the FOREIGN KEY constraint "FK_TimeLog_Project". '
    "The conflict occurred in database \"builddb\", table \"dbo.TimeLog\", column 'ProjectId'. (547)"
)


@pytest.mark.parametrize(
    "sql_text, expected_detail",
    [
        (FK_DELETE_SQL, "This record is still referenced by other records and cannot be deleted."),
        (FK_INSERT_SQL, "This record references another record that no longer exists."),
    ],
)
def test_raise_database_error_fk_violation_is_422_with_clean_message(sql_text, expected_detail):
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(_odbc_error(sql_text))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == expected_detail


@pytest.mark.parametrize("sql_text", [FK_DELETE_SQL, FK_INSERT_SQL])
def test_raise_database_error_fk_detail_leaks_no_schema_and_no_duplicate_tokens(sql_text):
    """Asserted on whatever the branch returns, so a future wording change
    can't quietly reintroduce a schema leak — or the words 'duplicate' /
    'unique', which would false-fire the iOS duplicate-claim recovery."""
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(_odbc_error(sql_text))
    detail = exc_info.value.detail.lower()
    for leaked in ("fk_", "547", "dbo.", "builddb", "constraint", "duplicate", "unique"):
        assert leaked not in detail


def test_raise_database_error_fk_survives_real_map_database_error_wrapping():
    """End-to-end shape check: map_database_error's 'conflict' keyword matches
    'conflicted', so a real 547 arrives prefixed 'Concurrency violation: '.
    The FK branch must still fire through that prefix."""
    from shared.database import map_database_error

    wrapped = map_database_error(Exception(FK_DELETE_SQL))
    assert "Concurrency violation" in str(wrapped)
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(wrapped)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "This record is still referenced by other records and cannot be deleted."


def test_raise_database_error_unique_key_2627_regression_ios_contract():
    original = Exception(
        """Database operation failed: ('23000', "[23000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Violation of UNIQUE KEY constraint 'UQ_RoleModule_RoleId_ModuleId'. Cannot insert duplicate key in object 'dbo.RoleModule'. The duplicate key value is (1, 2). (2627) (SQLExecDirectW)")"""
    )
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(original)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == str(original)


def test_raise_database_error_check_constraint_547_not_caught():
    original = Exception(
        """Database operation failed: ('23000', '[23000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]The INSERT statement conflicted with the CHECK constraint "CK_X". The conflict occurred in database "builddb", table "dbo.Thing", column 'Amount'. (547) (SQLExecDirectW)')"""
    )
    with pytest.raises(Exception) as exc_info:
        raise_database_error(original)
    assert exc_info.value is original


def test_raise_database_error_constraint_without_fk_phrase_reraises():
    original = Exception("Some generic constraint failure without error number")
    with pytest.raises(Exception) as exc_info:
        raise_database_error(original)
    assert exc_info.value is original


def test_raise_database_error_foreign_key_phrase_without_547_reraises():
    original = Exception(
        'The INSERT statement conflicted with the FOREIGN KEY constraint "FK_TimeLog_Project".'
    )
    with pytest.raises(Exception) as exc_info:
        raise_database_error(original)
    assert exc_info.value is original
