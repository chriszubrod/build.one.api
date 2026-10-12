"""U-664: decrypt_if_encrypted must skip decrypt_sensitive_data for non-Fernet plaintext."""

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from shared.encryption import (
    EncryptionError,
    decrypt_if_encrypted,
    decrypt_sensitive_data,
    encrypt_sensitive_data,
)

TEST_KEY = Fernet.generate_key()
OTHER_KEY = Fernet.generate_key()

PLAINTEXT_SECRET = "a" * 40  # legacy QBO-style secret; not Fernet-shaped


@pytest.fixture(autouse=True)
def stable_encryption_key(monkeypatch):
    monkeypatch.setattr(
        "shared.encryption._get_encryption_key",
        lambda: TEST_KEY,
    )


def test_plaintext_returned_unchanged_without_decrypt_or_error_logs(caplog):
    with patch(
        "shared.encryption.decrypt_sensitive_data",
        wraps=decrypt_sensitive_data,
    ) as decrypt_mock:
        with caplog.at_level("DEBUG", logger="shared.encryption"):
            result = decrypt_if_encrypted(PLAINTEXT_SECRET, field_name="qbo.client_secret")

    assert result == PLAINTEXT_SECRET
    decrypt_mock.assert_not_called()
    error_records = [
        r for r in caplog.records if r.levelno >= 40 and r.name == "shared.encryption"
    ]
    assert error_records == []


def test_fernet_ciphertext_decrypts_under_test_key():
    ciphertext = encrypt_sensitive_data("my-secret-value")
    assert decrypt_if_encrypted(ciphertext, field_name="test.field") == "my-secret-value"


def test_fernet_ciphertext_wrong_key_raises_and_logs_key_mismatch(caplog):
    other_fernet = Fernet(OTHER_KEY)
    ciphertext = other_fernet.encrypt(b"token-from-other-key").decode()

    with caplog.at_level("ERROR", logger="shared.encryption"):
        with pytest.raises(EncryptionError):
            decrypt_if_encrypted(ciphertext, field_name="ms.Auth.RefreshToken")

    assert any(
        "probable key mismatch" in record.message
        and "ms.Auth.RefreshToken" in record.message
        for record in caplog.records
        if record.levelno >= 40
    )


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_values_returned_unchanged(empty):
    assert decrypt_if_encrypted(empty, field_name="ignored") is empty


def _fernet_ciphertext(plaintext: str = "my-secret-value") -> str:
    return encrypt_sensitive_data(plaintext)


@pytest.mark.parametrize(
    "decorate",
    [
        lambda tok: tok + "\u00a0",
        lambda tok: "\ufeff" + tok,
        lambda tok: tok[:20] + "\u00a0" + tok[20:],
    ],
    ids=["trailing-nbsp", "leading-bom", "embedded-nbsp"],
)
def test_fernet_with_tolerated_decoration_decrypts(decorate):
    """Fernet ignores non-alphabet bytes, so these decrypted before U-664 and must still."""
    assert decrypt_if_encrypted(decorate(_fernet_ciphertext())) == "my-secret-value"


def test_decorated_wrong_key_token_fails_closed(caplog):
    """U-664 decision (Codex recheck): a token under another key with Fernet-tolerated non-ASCII
    decoration used to be RETURNED as if it were plaintext (an accident of the str-decoding shape
    check). It is ciphertext, so it now fails closed like any other key mismatch — returning it
    could send ciphertext to QBO/MS as a bearer token."""
    decorated = Fernet(OTHER_KEY).encrypt(b"other-secret").decode() + " "
    with caplog.at_level("ERROR", logger="shared.encryption"):
        with pytest.raises(EncryptionError):
            decrypt_if_encrypted(decorated, field_name="test.field")
    assert any("probable key mismatch" in r.getMessage() for r in caplog.records)
