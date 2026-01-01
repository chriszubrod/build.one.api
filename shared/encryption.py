# Python Standard Library Imports
from typing import Optional
from cryptography.fernet import Fernet
import base64
import os

# Third-party Imports

# Local Imports
import config


def _get_encryption_key() -> bytes:
    """
    Get or generate the encryption key for sensitive data.
    In production, this should come from secure config/environment variables.
    """
    settings = config.Settings()
    key_str = getattr(settings, 'encryption_key', None) or os.getenv('ENCRYPTION_KEY', '')
    
    if key_str:
        # Use existing key from config/environment
        return base64.urlsafe_b64encode(key_str.encode().ljust(32)[:32])
    else:
        # For development: generate a key (store this securely in production!)
        # In production, generate once and store in config/environment
        key = Fernet.generate_key()
        return key


def encrypt_sensitive_data(data: str) -> Optional[str]:
    """
    Encrypt sensitive data for secure storage.
    
    Args:
        data: The plain text data to encrypt
        
    Returns:
        Encrypted data as a string, or None if encryption fails
    """
    if not data:
        return None
    try:
        f = Fernet(_get_encryption_key())
        return f.encrypt(data.encode()).decode()
    except Exception:
        return None


def decrypt_sensitive_data(encrypted_data: str) -> Optional[str]:
    """
    Decrypt sensitive data for display.
    
    Args:
        encrypted_data: The encrypted data to decrypt
        
    Returns:
        Decrypted plain text data, or None if decryption fails
    """
    if not encrypted_data:
        return None
    try:
        f = Fernet(_get_encryption_key())
        return f.decrypt(encrypted_data.encode()).decode()
    except Exception:
        return None
