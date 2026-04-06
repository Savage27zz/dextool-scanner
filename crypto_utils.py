from cryptography.fernet import Fernet

from config import ENCRYPTION_KEY

_fernet = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)


def encrypt_key(private_key_bytes: bytes) -> str:
    return _fernet.encrypt(private_key_bytes).decode()


def decrypt_key(encrypted: str) -> bytes:
    return _fernet.decrypt(encrypted.encode())
