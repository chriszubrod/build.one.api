"""Pure-logic tests for taxpayer TIN blind index and API masking (no DB)."""
from entities.taxpayer.business.model import Taxpayer
from shared.encryption import blind_index


def test_blind_index_deterministic_and_distinct():
    a = blind_index("123456789")
    b = blind_index("123456789")
    c = blind_index("987654321")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_blind_index_empty_inputs_return_none():
    assert blind_index("") is None
    assert blind_index(None) is None


def test_taxpayer_to_dict_masks_tin_and_omits_hash():
    taxpayer = Taxpayer(
        id=1,
        public_id="tp-pub",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        entity_name="x",
        business_name=None,
        classification=None,
        taxpayer_id_number="123456789",
        taxpayer_id_number_hash="should-not-leak",
    )
    d = taxpayer.to_dict()

    assert d["taxpayer_id_number"] == "*****6789"
    assert d["taxpayer_id_last4"] == "6789"
    assert "taxpayer_id_number_hash" not in d
