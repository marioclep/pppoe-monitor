from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.MASTER_ENCRYPTION_KEY.encode())


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
