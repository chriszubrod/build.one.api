# Python Standard Library Imports
from typing import Optional
from cryptography.fernet import Fernet
import base64
import logging
import os

# Third-party Imports

# Local Imports
import config

logger = logging.getLogger(__name__)
_dev_key_cache: Optional[bytes] = None


class EncryptionKeyError(RuntimeError):
    """Raised when encryption key configuration is invalid or missing."""


class EncryptionError(RuntimeError):
    """Raised when encryption/decryption fails."""


def _normalize_key(key_str: str) -> bytes:
    key_str = key_str.strip()
    if not key_str:
        raise EncryptionKeyError("Encryption key is empty.")
    try:
        decoded = base64.urlsafe_b64decode(key_str)
        if len(decoded) == 32:
            return key_str.encode()
    except Exception:
        pass
    return base64.urlsafe_b64encode(key_str.encode().ljust(32)[:32])


def _get_encryption_key() -> bytes:
    """
    Get or generate the encryption key for sensitive data.
    In production, this should come from secure config/environment variables.
    """
    settings = config.Settings()
    key_str = getattr(settings, 'encryption_key', None) or os.getenv('ENCRYPTION_KEY', '')
    
    if key_str:
        # Use existing key from config/environment
        try:
            return _normalize_key(key_str)
        except EncryptionKeyError:
            raise
        except Exception as exc:
            logger.error("Invalid encryption key configuration: %s", exc)
            raise EncryptionKeyError("Invalid encryption key configuration.")

    env = (settings.env or "").lower()
    if env in {"production", "prod"}:
        message = "ENCRYPTION_KEY is required in production."
        logger.critical(message)
        raise EncryptionKeyError(message)

    global _dev_key_cache
    if _dev_key_cache is None:
        _dev_key_cache = Fernet.generate_key()
        logger.warning("ENCRYPTION_KEY missing; using ephemeral dev key for this process.")
    return _dev_key_cache


def encrypt_sensitive_data(data: str) -> Optional[str]:
    """
    Encrypt sensitive data for secure storage.
    
    Args:
        data: The plain text data to encrypt
        
    Returns:
        Encrypted data as a string
    Raises:
        EncryptionKeyError: If encryption key is missing or invalid
        EncryptionError: If encryption fails
    """
    if not data:
        return None
    try:
        f = Fernet(_get_encryption_key())
        return f.encrypt(data.encode()).decode()
    except Exception as exc:
        logger.exception("Failed to encrypt sensitive data.")
        raise EncryptionError("Failed to encrypt sensitive data.") from exc


def decrypt_sensitive_data(encrypted_data: str) -> Optional[str]:
    """
    Decrypt sensitive data for display.

    Args:
        encrypted_data: The encrypted data to decrypt

    Returns:
        Decrypted plain text data
    Raises:
        EncryptionKeyError: If encryption key is missing or invalid
        EncryptionError: If decryption fails
    """
    if not encrypted_data:
        return None
    try:
        f = Fernet(_get_encryption_key())
        return f.decrypt(encrypted_data.encode()).decode()
    except Exception as exc:
        logger.exception("Failed to decrypt sensitive data.")
        raise EncryptionError("Failed to decrypt sensitive data.") from exc


def decrypt_if_encrypted(value: Optional[str]) -> Optional[str]:
    """
    Decrypt a value if it is a Fernet ciphertext; otherwise return it as-is.

    Used by persistence layers transitioning from plaintext to encrypted
    storage. Existing plaintext rows continue to read correctly; once any
    row is rewritten (e.g., next token refresh), it becomes ciphertext and
    future reads decrypt it. Over the natural rotation window, the entire
    table self-heals to encrypted-at-rest without requiring a migration.
    """
    if not value:
        return value
    try:
        return decrypt_sensitive_data(value)
    except EncryptionError:
        # Not a valid Fernet token — assume legacy plaintext and pass through.
        # Next write for this row will encrypt it.
        return value
