"""Pure-logic tests for W-9 field extraction from Document Intelligence layout results."""
import pytest

from entities.taxpayer.business.model import TaxpayerClassification
from entities.taxpayer.business.w9_parser import parse_w9_fields

_VALID_CLASSIFICATIONS = {c.value for c in TaxpayerClassification}


def test_well_formed_w9_from_di_dict():
    di = {
        "content": (
            "Name John A Smith Business name Smith Consulting LLC "
            "Federal tax classification Individual/sole proprietor "
            "Social security number 123-45-6789 Signature John Smith Date 03/15/2026"
        ),
        "key_value_pairs": [
            {
                "key": "Name (as shown on your income tax return)",
                "value": "John A Smith",
            },
            {
                "key": "Business name/disregarded entity name",
                "value": "Smith Consulting LLC",
            },
        ],
    }
    result = parse_w9_fields(di)

    assert result["entity_name"] == "John A Smith"
    assert result["business_name"] == "Smith Consulting LLC"
    assert result["taxpayer_id_number"] == "123456789"
    cls = result["classification"]
    assert cls is None or cls in _VALID_CLASSIFICATIONS
    assert isinstance(cls, (str, type(None)))
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["unresolved"], list)


def test_ein_from_content_without_ssn():
    di = {
        "content": "Employer identification number 12-3456789",
        "key_value_pairs": [],
    }
    result = parse_w9_fields(di)
    assert result["taxpayer_id_number"] == "123456789"


def test_sparse_empty_di():
    di = {"content": "", "key_value_pairs": []}
    result = parse_w9_fields(di)

    assert result["entity_name"] is None
    assert result["business_name"] is None
    assert result["classification"] is None
    assert result["taxpayer_id_number"] is None
    assert result["confidence"] == 0.0
    assert set(result["unresolved"]) == {
        "entity_name",
        "taxpayer_id_number",
        "classification",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": None, "key_value_pairs": None},
    ],
)
def test_robustness_missing_keys_do_not_raise(payload):
    result = parse_w9_fields(payload)
    assert isinstance(result, dict)
    assert "confidence" in result
    assert isinstance(result["unresolved"], list)
