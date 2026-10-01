from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder values shipped in docs/examples (and the old in-code default).
# Accepting any of them would make every JWT forgeable by anyone who has read
# this repository.
KNOWN_PLACEHOLDER_JWT_SECRETS = {
    "changeme-in-env",
    "change-me-to-a-long-random-string",
    "change-me",
    "changeme",
    "secret",
}
MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://pppoe:pppoe@localhost:5432/pppoe"
    # No usable default on purpose: must come from the environment / .env.
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    TOKEN_EXPIRE_MINUTES: int = 480
    MASTER_ENCRYPTION_KEY: str = ""
    # Local timezone used for the monthly accumulation reset (and the
    # scheduler's cron jobs).
    TZ: str = "America/Argentina/Cordoba"
    # Root log level for the app's own loggers (stdout).
    LOG_LEVEL: str = "INFO"

    @field_validator("JWT_SECRET")
    @classmethod
    def _validate_jwt_secret(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "JWT_SECRET is not set. Generate one with `openssl rand -hex 32` and put it in .env."
            )
        if value.strip().lower() in KNOWN_PLACEHOLDER_JWT_SECRETS:
            raise ValueError(
                "JWT_SECRET is still a placeholder value from the docs/examples. "
                "Generate a real one with `openssl rand -hex 32`."
            )
        if len(value) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters long "
                "(generate one with `openssl rand -hex 32`)."
            )
        return value

    @field_validator("MASTER_ENCRYPTION_KEY")
    @classmethod
    def _validate_master_encryption_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except Exception:
            raise ValueError(
                "MASTER_ENCRYPTION_KEY is missing or is not a valid Fernet key. Generate one with "
                "python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            ) from None
        return value

    @field_validator("TZ")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                f"TZ={value!r} is not a valid IANA timezone name (e.g. America/Argentina/Cordoba)."
            ) from None
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                f"LOG_LEVEL={value!r} is not valid (use DEBUG, INFO, WARNING, ERROR or CRITICAL)."
            )
        return normalized


settings = Settings()
