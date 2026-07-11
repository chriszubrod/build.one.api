from decimal import Decimal

import pytest

pytest.importorskip("xlrd")

from entities.contract_labor.business.import_service import ContractLaborImportService

SVC = ContractLaborImportService.__new__(ContractLaborImportService)


def test_parse_time_to_hours_hh_mm():
    result = SVC._parse_time_to_hours("08:30")
    assert result == Decimal("8.5")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


def test_parse_time_to_hours_two_forty_five():
    result = SVC._parse_time_to_hours("02:45")
    assert result == Decimal("2.75")
    assert isinstance(result, Decimal)


def test_parse_time_to_hours_decimal_string():
    result = SVC._parse_time_to_hours("8.5")
    assert result == Decimal("8.5")
    assert isinstance(result, Decimal)


def test_parse_time_to_hours_one_hour():
    result = SVC._parse_time_to_hours("01:00")
    assert result == Decimal("1")
    assert isinstance(result, Decimal)


def test_parse_time_to_hours_none():
    assert SVC._parse_time_to_hours(None) is None


def test_parse_time_to_hours_garbage():
    assert SVC._parse_time_to_hours("garbage") is None


def test_parse_row_valid_tuple_unpack():
    row = (
        "2026-01-20",
        "HP - 123 Main",
        "Wilmer Diaz",
        None,
        None,
        None,
        "08:00",
        None,
        "08:00",
        "framing",
    )
    result = SVC._parse_row(row, row_num=5)
    assert len(result) == 2
    parsed, skip_reason = result
    assert parsed is not None
    assert skip_reason is None
    assert parsed["vendor_name"] == "Wilmer Diaz"
    assert parsed["work_date"] == "2026-01-20"
    assert parsed["total_hours"] == Decimal("8")
    assert isinstance(parsed["total_hours"], Decimal)


def test_parse_row_no_valid_date():
    row = ("", "HP - 123 Main", "Wilmer Diaz", None, None, None, "08:00", None, "08:00", "")
    result = SVC._parse_row(row, row_num=6)
    assert len(result) == 2
    parsed, skip_reason = result
    assert parsed is None
    assert skip_reason is not None
    assert "No valid date" in skip_reason


def test_parse_row_no_vendor_name():
    row = ("2026-01-20", "HP - 123 Main", "", None, None, None, "08:00", None, "08:00", "")
    result = SVC._parse_row(row, row_num=7)
    assert len(result) == 2
    parsed, skip_reason = result
    assert parsed is None
    assert skip_reason is not None
    assert "No vendor/worker name" in skip_reason


def test_parse_row_no_valid_hours():
    row = ("2026-01-20", "HP - 123 Main", "Wilmer Diaz", None, None, None, None, None, None, "")
    result = SVC._parse_row(row, row_num=8)
    assert len(result) == 2
    parsed, skip_reason = result
    assert parsed is None
    assert skip_reason is not None
    assert "No valid hours" in skip_reason
