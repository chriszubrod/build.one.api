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
