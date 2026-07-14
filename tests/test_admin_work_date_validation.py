import pytest
from fastapi import HTTPException

from shared.api.admin import _validate_work_date


def test_validate_work_date_valid_returns_unchanged():
    assert _validate_work_date("2026-07-14") == "2026-07-14"


@pytest.mark.parametrize(
    "bad_date",
    ["notadate", "2026-13-45", "07/14/2026"],
)
def test_validate_work_date_invalid_raises_400(bad_date):
    with pytest.raises(HTTPException) as exc_info:
        _validate_work_date(bad_date)
    assert exc_info.value.status_code == 400
