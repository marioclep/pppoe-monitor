"""Tolerant readers for values stored in the `settings` table.

Values are stored as strings. A missing, empty, malformed or out-of-range
value must never take the app down (a 500 on GET /settings, or a scheduler
crash-loop at startup): it falls back to the default and logs a warning.
"""
import logging

from sqlalchemy.orm import Session

from app.models.settings import AppSetting

logger = logging.getLogger(__name__)

DEFAULT_POLLING_INTERVAL_SECONDS = 300
MIN_POLLING_INTERVAL_SECONDS = 60
DEFAULT_RESET_DAY_OF_MONTH = 1
DEFAULT_RETENTION_DAYS = 90
DEFAULT_RAW_RETENTION_DAYS = 7
LANGUAGES = ("es", "en")
DEFAULT_LANGUAGE = "es"


def parse_int_setting(
    key: str,
    raw: str | None,
    default: int | None,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Setting %s has invalid value %r; using default %r", key, raw, default)
        return default
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        logger.warning("Setting %s value %r is out of range; using default %r", key, value, default)
        return default
    return value


def parse_language(raw: str | None) -> str:
    if raw is None or raw == "":
        return DEFAULT_LANGUAGE
    if raw not in LANGUAGES:
        logger.warning("Setting language has invalid value %r; using default %r", raw, DEFAULT_LANGUAGE)
        return DEFAULT_LANGUAGE
    return raw


def get_language(db: Session) -> str:
    """The installation's interface language, also used for alert messages."""
    row = db.get(AppSetting, "language")
    return parse_language(row.value if row else None)


def get_int_setting(
    db: Session, key: str, default: int, min_value: int | None = None, max_value: int | None = None
) -> int:
    row = db.get(AppSetting, key)
    return parse_int_setting(key, row.value if row else None, default, min_value, max_value)


def get_polling_interval_seconds(db: Session) -> int:
    return get_int_setting(
        db, "polling_interval_seconds", DEFAULT_POLLING_INTERVAL_SECONDS, min_value=MIN_POLLING_INTERVAL_SECONDS
    )


def get_reset_day_of_month(db: Session) -> int:
    return get_int_setting(db, "reset_day_of_month", DEFAULT_RESET_DAY_OF_MONTH, min_value=1, max_value=28)


def get_retention_days(db: Session) -> int:
    return get_int_setting(db, "retention_days", DEFAULT_RETENTION_DAYS, min_value=1)


def get_raw_retention_days(db: Session) -> int:
    """Days of 5-minute samples kept; older traffic survives only as the
    hourly rollup (kept for get_retention_days)."""
    return get_int_setting(db, "raw_retention_days", DEFAULT_RAW_RETENTION_DAYS, min_value=1)
