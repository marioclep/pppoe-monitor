from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Placeholder returned by GET in place of a stored secret value, and
# recognized by PUT as "leave this secret unchanged".
SECRET_MASK = "********"

SITE_NAME_MAX_LENGTH = 40


class SettingsOut(BaseModel):
    polling_interval_seconds: int
    reset_day_of_month: int
    retention_days: int
    raw_retention_days: int
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_password_set: bool = False
    telegram_bot_token_set: bool = False
    site_name: str | None = None
    language: str = "es"


class SettingsUpdate(BaseModel):
    """Partial update. For the optional SMTP/Telegram fields (including
    smtp_port and the two secrets), null or an empty string clears the
    stored value; sending the SECRET_MASK placeholder for a secret leaves it
    unchanged."""

    polling_interval_seconds: int | None = Field(default=None, ge=60)
    reset_day_of_month: int | None = Field(default=None, ge=1, le=28)
    retention_days: int | None = Field(default=None, ge=1)
    raw_retention_days: int | None = Field(default=None, ge=1)
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # Shown after "Dashboard" and in the browser tab ("Dashboard ACME").
    site_name: str | None = Field(default=None, max_length=SITE_NAME_MAX_LENGTH)
    # Interface language of the whole installation, and of alert messages.
    language: Literal["es", "en"] | None = None

    @field_validator("site_name", mode="before")
    @classmethod
    def _strip_site_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    # Omitting one of these leaves it unchanged, but an explicit null is an
    # error: they have no "unset" state. (Validators only run for fields
    # actually present in the payload.)
    @field_validator(
        "polling_interval_seconds",
        "reset_day_of_month",
        "retention_days",
        "raw_retention_days",
        "language",
        mode="before",
    )
    @classmethod
    def _reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("must not be null (omit the field to leave it unchanged)")
        return value
