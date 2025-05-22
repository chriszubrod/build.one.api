import os
from cryptography.fernet import Fernet

fernet = Fernet(os.getenv('FERNET_KEY').encode())

def encrypt_string(value: str) -> bytes:
    return fernet.encrypt(value.encode())

def decrypt_string(value: bytes) -> str:
    return fernet.decrypt(value).decode()
