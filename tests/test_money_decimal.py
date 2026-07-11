from decimal import Decimal

from entities.contract_labor.business.bill_service import VENDOR_CONFIG


def test_vendor_config_rate_and_markup_are_decimal_not_float():
    for vendor_name, config in VENDOR_CONFIG.items():
        rate = config["rate"]
        markup = config["markup"]
        assert isinstance(rate, Decimal), f"{vendor_name} rate is not Decimal"
        assert isinstance(markup, Decimal), f"{vendor_name} markup is not Decimal"
        assert not isinstance(rate, float), f"{vendor_name} rate is float"
        assert not isinstance(markup, float), f"{vendor_name} markup is float"


def test_vendor_config_known_selvin_values():
    selvin = VENDOR_CONFIG["Selvin Humberto Cordova Tercero"]
    assert selvin["rate"] == Decimal("500.00")
    assert selvin["markup"] == Decimal("0.35")


def test_vendor_config_known_wilmer_rate():
    wilmer = VENDOR_CONFIG["Wilmer Diaz"]
    assert wilmer["rate"] == Decimal("260.00")


def test_vendor_config_markup_not_built_from_float_literal():
    """0.35 is not exactly float-representable. The config markup must equal
    Decimal('0.35') and Decimal(str(0.35)) (the required idiom) but NOT the
    raw-float Decimal(0.35) — proving VENDOR_CONFIG was NOT built from float
    literals. Fails if someone rewrites the markup as a float."""
    markup = VENDOR_CONFIG["Selvin Humberto Cordova Tercero"]["markup"]
    assert markup == Decimal("0.35")
    assert markup == Decimal(str(0.35))
    assert markup != Decimal(0.35)
