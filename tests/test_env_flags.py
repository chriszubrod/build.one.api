"""Pure-logic tests for shared.env_flags (U-254)."""
import logging

import pytest

from shared.env_flags import _env_positive_int, env_flag_enabled, is_truthy


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("True", True),
        (" TRUE ", True),
        ("false", False),
        ("", False),
        (None, False),
        ("yes", False),
        ("1", False),
    ],
)
def test_is_truthy(value, expected):
    assert is_truthy(value) is expected


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("true", True),
        ("True", True),
        (" false", False),
        (None, False),
        ("", False),
    ],
)
def test_env_flag_enabled(monkeypatch, env_value, expected):
    name = "TEST_ENV_FLAG_U254"
    monkeypatch.delenv(name, raising=False)
    if env_value is not None:
        monkeypatch.setenv(name, env_value)
    assert env_flag_enabled(name) is expected


def test_env_positive_int_missing_or_empty_uses_default(monkeypatch):
    name = "TEST_POS_INT_U254"
    monkeypatch.delenv(name, raising=False)
    assert _env_positive_int(name, 60, minimum=0, warn=False) == 60
    monkeypatch.setenv(name, "")
    assert _env_positive_int(name, 60, minimum=0, warn=False) == 60


def test_env_positive_int_valid_value(monkeypatch):
    name = "TEST_POS_INT_U254"
    monkeypatch.setenv(name, "  120  ")
    assert _env_positive_int(name, 60, minimum=0, warn=False) == 120


def test_env_positive_int_invalid_logs_and_returns_default(monkeypatch, caplog):
    name = "TEST_POS_INT_U254"
    monkeypatch.setenv(name, "not-a-number")
    with caplog.at_level(logging.WARNING):
        assert _env_positive_int(name, 60, minimum=0, warn=True) == 60
    assert "Invalid TEST_POS_INT_U254='not-a-number'; using default 60" in caplog.text


def test_env_positive_int_below_minimum_logs_and_returns_default(monkeypatch, caplog):
    name = "TEST_POS_INT_U254"
    monkeypatch.setenv(name, "-1")
    with caplog.at_level(logging.WARNING):
        assert _env_positive_int(name, 60, minimum=0, warn=True) == 60
    assert "Negative TEST_POS_INT_U254=-1; using default 60" in caplog.text


def test_env_positive_int_minimum_boundary(monkeypatch):
    name = "TEST_POS_INT_U254"
    monkeypatch.setenv(name, "0")
    assert _env_positive_int(name, 60, minimum=0, warn=False) == 0
    monkeypatch.setenv(name, "1")
    assert _env_positive_int(name, 50, minimum=1, warn=False) == 1


def test_env_positive_int_warn_false_is_silent(monkeypatch, caplog):
    name = "TEST_POS_INT_U254"
    monkeypatch.setenv(name, "bad")
    with caplog.at_level(logging.WARNING):
        assert _env_positive_int(name, 50, minimum=1, warn=False) == 50
    assert caplog.text == ""


def test_env_positive_int_custom_logger_name(monkeypatch, caplog):
    name = "TEST_POS_INT_U254"
    custom_logger = logging.getLogger("custom.test.logger")
    monkeypatch.setenv(name, "not-a-number")
    with caplog.at_level(logging.WARNING):
        assert _env_positive_int(name, 60, minimum=0, warn=True, logger=custom_logger) == 60
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "custom.test.logger"


def test_env_positive_int_below_nonzero_minimum_uses_generic_wording(monkeypatch, caplog):
    name = "TEST_POS_INT_U254"
    monkeypatch.setenv(name, "0")
    with caplog.at_level(logging.WARNING):
        assert _env_positive_int(name, 50, minimum=1, warn=True) == 50
    assert "Value TEST_POS_INT_U254=0 below minimum 1; using default 50" in caplog.text
    assert "Negative" not in caplog.text
