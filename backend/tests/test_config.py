import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings

VALID_KEY = Fernet.generate_key().decode()
VALID_SECRET = "a" * 64


def _settings(**overrides) -> Settings:
    values = {"JWT_SECRET": VALID_SECRET, "MASTER_ENCRYPTION_KEY": VALID_KEY}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_settings_are_accepted():
    s = _settings()
    assert s.JWT_SECRET == VALID_SECRET
    assert s.TZ == "America/Argentina/Cordoba"


@pytest.mark.parametrize(
    "secret",
    ["", "changeme-in-env", "change-me-to-a-long-random-string", "short-but-not-placeholder"],
)
def test_rejects_empty_placeholder_or_short_jwt_secret(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(JWT_SECRET=secret)


@pytest.mark.parametrize("key", ["", "generate-with-python-fernet", "not base64 at all"])
def test_rejects_invalid_master_encryption_key(key):
    with pytest.raises(ValidationError, match="MASTER_ENCRYPTION_KEY"):
        _settings(MASTER_ENCRYPTION_KEY=key)


def test_rejects_unknown_timezone():
    with pytest.raises(ValidationError, match="TZ"):
        _settings(TZ="Mars/Olympus_Mons")


def test_log_level_defaults_to_info_and_is_normalized():
    assert _settings().LOG_LEVEL == "INFO"
    assert _settings(LOG_LEVEL="debug").LOG_LEVEL == "DEBUG"


def test_rejects_unknown_log_level():
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        _settings(LOG_LEVEL="verbose")
