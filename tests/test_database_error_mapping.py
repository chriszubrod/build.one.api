"""Pure-logic tests for SQL Server constraint-violation classification and HTTP mapping."""

import pytest
from fastapi import HTTPException

from shared.api.responses import raise_database_error, raise_workflow_error
from shared.database import (
    CONCURRENCY_KEYWORDS,
    DatabaseConcurrencyError,
    DatabaseConnectionError,
    DatabaseConstraintError,
    map_database_error,
)
from shared.db_constraints import (
    FK_MISSING,
    FK_MISSING_MESSAGE,
    FK_REFERENCE,
    FK_REFERENCE_MESSAGE,
    UNIQUE,
    UNIQUE_MESSAGE,
    classify_constraint_violation,
    status_for_clean_message,
)

FK_DELETE_547 = (
    'The DELETE statement conflicted with the REFERENCE constraint "FK_RoleModule_Role". '
    'The conflict occurred in database "buildone", table "dbo.RoleModule", column \'RoleId\'. (547)'
)
FK_INSERT_547 = (
    'The INSERT statement conflicted with the FOREIGN KEY constraint "FK_TimeLog_Project". '
    'The conflict occurred in database "buildone", table "dbo.TimeLog", column \'ProjectId\'. (547)'
)
UQ_2627 = (
    "Violation of UNIQUE KEY constraint 'UQ_RoleModule_RoleId_ModuleId'. "
    "Cannot insert duplicate key in object 'dbo.RoleModule'. "
    "The duplicate key value is (1, 2). (2627)"
)
UX_2601 = (
    "Cannot insert duplicate key row in object 'dbo.TimeLog' with unique index "
    "'UX_TimeLog_TimeEntryId_ClockIn'. "
    "The duplicate key value is (12, 2026-07-27 08:00:00). (2601)"
)
CHECK_547 = (
    'The INSERT statement conflicted with the CHECK constraint "CK_Thing_Amount". '
    'The conflict occurred in database "buildone", table "dbo.Thing", column \'Amount\'. (547)'
)
DEADLOCK_1205 = (
    'Transaction (Process ID 61) was deadlocked on lock resources with another process '
    'and has been chosen as the deadlock victim. Rerun the transaction. (1205)'
)
LOCK_TIMEOUT_1222 = 'Lock request time out period exceeded. (1222)'
ROWVERSION_RAISERROR = (
    'Concurrency conflict: the user record has been modified by another user. '
    'Please refresh and try again. (50000)'
)
SNAPSHOT_CONFLICT_3960 = (
    'Snapshot isolation transaction aborted due to update conflict. You cannot use '
    'snapshot isolation to access table \'dbo.Bill\' directly or indirectly in database '
    '\'buildone\' to update, delete, or insert the row that has been modified or deleted '
    'by another transaction. Retry the transaction or change the isolation level for the '
    'update/delete statement. (3960)'
)
CONNECTION_WITH_2601_IN_HOST = (
    'Login timeout expired while connecting to db-unique.internal:2601'
)
UQ_2627_KEY_VALUE_CONTAINS_547 = (
    "Violation of UNIQUE KEY constraint 'UQ_Thing_Code'. Cannot insert duplicate key in "
    "object 'dbo.Thing'. The duplicate key value is (547). (2627)"
)


def _odbc(sql_text: str) -> Exception:
    return Exception(
        "('23000', '[23000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        + sql_text
        + " (SQLExecDirectW)')"
    )


def test_map_database_error_fk_delete_547_is_constraint_not_concurrency():
    mapped = map_database_error(_odbc(FK_DELETE_547))
    assert isinstance(mapped, DatabaseConstraintError)
    assert not isinstance(mapped, DatabaseConcurrencyError)
    assert mapped.violation.kind == FK_REFERENCE
    assert str(mapped) == FK_REFERENCE_MESSAGE


def test_map_database_error_fk_insert_547_is_fk_missing():
    mapped = map_database_error(_odbc(FK_INSERT_547))
    assert isinstance(mapped, DatabaseConstraintError)
    assert mapped.violation.kind == FK_MISSING
    assert str(mapped) == FK_MISSING_MESSAGE


@pytest.mark.parametrize(
    "sql_text, expected_detail",
    [
        (FK_DELETE_547, FK_REFERENCE_MESSAGE),
        (FK_INSERT_547, FK_MISSING_MESSAGE),
    ],
)
def test_raise_workflow_error_fk_547_flavors_map_to_422(sql_text, expected_detail):
    mapped = map_database_error(_odbc(sql_text))
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error(str(mapped), 'Failed to delete role module')
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == expected_detail


@pytest.mark.parametrize("sql_text", [FK_DELETE_547, FK_INSERT_547])
def test_raise_workflow_error_fk_detail_leaks_no_schema(sql_text):
    mapped = map_database_error(_odbc(sql_text))
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error(str(mapped), 'Failed to delete role module')
    detail = exc_info.value.detail.lower()
    for leaked in ('fk_', '547', 'dbo.', 'buildone', 'constraint', 'duplicate', 'unique'):
        assert leaked not in detail


@pytest.mark.parametrize("sql_text", [UQ_2627, UX_2601])
def test_map_database_error_unique_violations(sql_text):
    mapped = map_database_error(_odbc(sql_text))
    assert isinstance(mapped, DatabaseConstraintError)
    assert mapped.violation.kind == UNIQUE


@pytest.mark.parametrize("sql_text", [UQ_2627, UX_2601])
def test_raise_workflow_error_unique_violations_map_to_422_with_duplicate_token(sql_text):
    mapped = map_database_error(_odbc(sql_text))
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error(str(mapped), 'Failed to save')
    assert exc_info.value.status_code == 422
    assert 'duplicate' in exc_info.value.detail.lower()


@pytest.mark.parametrize(
    "sql_text",
    [DEADLOCK_1205, LOCK_TIMEOUT_1222, ROWVERSION_RAISERROR],
)
def test_regression_real_concurrency_still_maps_to_409(sql_text):
    """These are the cases the 'conflict'/'deadlock'/'lock'/'concurrency' keywords
    legitimately cover; the number-first block must not steal them."""
    mapped = map_database_error(_odbc(sql_text))
    assert isinstance(mapped, DatabaseConcurrencyError)
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error(str(mapped), 'x')
    assert exc_info.value.status_code == 409


def test_snapshot_update_conflict_3960_still_classifies_as_concurrency():
    """Behavioral pin for the 'conflict' keyword. SQL 3960 is the ONE real
    concurrency error whose message matches NO other keyword in the list —
    not 'deadlock', not 'lock', not 'concurrency', not 'version'. Deleting
    'conflict' from map_database_error's concurrency list must turn this RED.
    Asserted here rather than by scraping the source, because the source now
    carries the word 'conflict' in a comment and a source-scrape passes even
    after the runtime keyword is removed."""
    mapped = map_database_error(_odbc(SNAPSHOT_CONFLICT_3960))
    assert isinstance(mapped, DatabaseConcurrencyError)
    assert not isinstance(mapped, DatabaseConstraintError)
    with pytest.raises(HTTPException) as exc_info:
        raise_workflow_error(str(mapped), 'x')
    assert exc_info.value.status_code == 409


def test_snapshot_3960_matches_no_concurrency_keyword_other_than_conflict():
    """Guards the canary above: the 3960 fixture is only a proof that 'conflict' is
    load-bearing for as long as 'conflict' is the ONLY keyword it matches. Derived
    from the production tuple, never a hand-copied list — a copy would keep passing
    while the real list drifted, and the canary would silently stop being one."""
    low = SNAPSHOT_CONFLICT_3960.lower()
    assert [k for k in CONCURRENCY_KEYWORDS if k in low] == ['conflict']


def test_connection_error_with_number_like_token_is_not_a_constraint_violation():
    """Regression pin for the bare-substring false positive: a genuine connection
    failure whose HOST:PORT happens to contain 2601 and the word 'unique' must not
    be stolen by the constraint classifier."""
    assert classify_constraint_violation(CONNECTION_WITH_2601_IN_HOST) is None
    mapped = map_database_error(Exception(CONNECTION_WITH_2601_IN_HOST))
    assert not isinstance(mapped, DatabaseConstraintError)
    assert isinstance(mapped, DatabaseConnectionError)


def test_unique_violation_whose_key_value_contains_547_is_not_classified_as_fk():
    v = classify_constraint_violation(_odbc(UQ_2627_KEY_VALUE_CONTAINS_547).args[0])
    assert v is not None and v.kind == UNIQUE


def test_check_constraint_547_not_classified_as_fk():
    assert classify_constraint_violation(CHECK_547) is None
    mapped = map_database_error(_odbc(CHECK_547))
    assert not isinstance(mapped, DatabaseConstraintError)


def test_raise_database_error_fk_delete_547_via_map_database_error():
    raw = _odbc(FK_DELETE_547)
    wrapped = map_database_error(raw)
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(wrapped)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == FK_REFERENCE_MESSAGE


def test_raise_database_error_fk_insert_547_via_map_database_error():
    raw = _odbc(FK_INSERT_547)
    wrapped = map_database_error(raw)
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(wrapped)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == FK_MISSING_MESSAGE


def test_raise_database_error_unique_2627_preserves_u154_original_message():
    raw = _odbc(UQ_2627)
    wrapped = map_database_error(raw)
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(wrapped)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == str(raw)


def test_raise_database_error_mapped_unique_detail_keeps_ios_claim_tokens():
    """iOS's isDuplicateKeyRejection matches on 'duplicate' / 'unique' / the
    constraint name. Going through map_database_error the detail is now the RAW
    driver text (it no longer carries the 'Database operation failed: ' prefix
    that the OLD misclassification added) — pin the tokens that actually matter
    rather than that stale prefix."""
    raw = _odbc(UQ_2627)
    with pytest.raises(HTTPException) as exc_info:
        raise_database_error(map_database_error(raw))
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail.lower()
    assert 'duplicate' in detail
    assert 'unique' in detail
    assert 'uq_rolemodule_roleid_moduleid' in detail


@pytest.mark.parametrize(
    "message",
    [FK_REFERENCE_MESSAGE, FK_MISSING_MESSAGE, UNIQUE_MESSAGE],
)
def test_status_for_clean_message_known_messages(message):
    assert status_for_clean_message(message) == 422


def test_status_for_clean_message_unknown_returns_none():
    assert status_for_clean_message('Something went wrong') is None
