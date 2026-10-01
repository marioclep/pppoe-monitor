from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_full
from app.database import get_db
from app.models.settings import AppSetting
from app.schemas.settings import SECRET_MASK, SettingsOut, SettingsUpdate
from app.services.app_settings import (
    DEFAULT_POLLING_INTERVAL_SECONDS,
    DEFAULT_RAW_RETENTION_DAYS,
    DEFAULT_RESET_DAY_OF_MONTH,
    DEFAULT_RETENTION_DAYS,
    MIN_POLLING_INTERVAL_SECONDS,
    parse_int_setting,
    parse_language,
)
from app.services.scheduler import reschedule_polling

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])

_ALL_KEYS = [
    "polling_interval_seconds",
    "reset_day_of_month",
    "retention_days",
    "raw_retention_days",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from",
    "smtp_to",
    "telegram_bot_token",
    "telegram_chat_id",
    "site_name",
    "language",
]

_SECRET_KEYS = {"smtp_password", "telegram_bot_token"}


def _read_all(db: Session) -> dict[str, str]:
    rows = db.query(AppSetting).filter(AppSetting.key.in_(_ALL_KEYS)).all()
    return {row.key: row.value for row in rows}


def _optional_str(values: dict[str, str], key: str) -> str | None:
    return values.get(key) or None


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    values = _read_all(db)
    return SettingsOut(
        polling_interval_seconds=parse_int_setting(
            "polling_interval_seconds",
            values.get("polling_interval_seconds"),
            DEFAULT_POLLING_INTERVAL_SECONDS,
            min_value=MIN_POLLING_INTERVAL_SECONDS,
        ),
        reset_day_of_month=parse_int_setting(
            "reset_day_of_month", values.get("reset_day_of_month"), DEFAULT_RESET_DAY_OF_MONTH, 1, 28
        ),
        retention_days=parse_int_setting(
            "retention_days", values.get("retention_days"), DEFAULT_RETENTION_DAYS, min_value=1
        ),
        raw_retention_days=parse_int_setting(
            "raw_retention_days", values.get("raw_retention_days"), DEFAULT_RAW_RETENTION_DAYS, min_value=1
        ),
        smtp_host=_optional_str(values, "smtp_host"),
        smtp_port=parse_int_setting("smtp_port", values.get("smtp_port"), None, 1, 65535),
        smtp_username=_optional_str(values, "smtp_username"),
        smtp_password=SECRET_MASK if values.get("smtp_password") else None,
        smtp_from=_optional_str(values, "smtp_from"),
        smtp_to=_optional_str(values, "smtp_to"),
        telegram_bot_token=SECRET_MASK if values.get("telegram_bot_token") else None,
        telegram_chat_id=_optional_str(values, "telegram_chat_id"),
        smtp_password_set=bool(values.get("smtp_password")),
        telegram_bot_token_set=bool(values.get("telegram_bot_token")),
        site_name=_optional_str(values, "site_name"),
        language=parse_language(values.get("language")),
    )


@router.put("", response_model=SettingsOut, dependencies=[Depends(require_full)])
def update_settings(payload: SettingsUpdate, request: Request, db: Session = Depends(get_db)):
    updates = payload.model_dump(exclude_unset=True)

    # Never overwrite a stored secret with the mask placeholder the GET
    # endpoint returns in its place: that means the caller re-submitted the
    # form without changing the field.
    for secret_key in _SECRET_KEYS:
        if updates.get(secret_key) == SECRET_MASK:
            del updates[secret_key]

    # 5-minute samples can't outlive the hourly rollup they are summed into.
    current = get_settings(db)
    raw_days = updates.get("raw_retention_days", current.raw_retention_days)
    retention_days = updates.get("retention_days", current.retention_days)
    if raw_days > retention_days:
        raise HTTPException(
            status_code=422,
            detail="La retención de detalle no puede ser mayor que la retención de historial.",
        )

    for key, value in updates.items():
        row = db.get(AppSetting, key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            # null / "" clears an optional setting. (The numeric settings
            # that can't be cleared reject null in SettingsUpdate.)
            if row is not None:
                db.delete(row)
            continue
        str_value = str(value)
        if row is None:
            db.add(AppSetting(key=key, value=str_value))
        else:
            row.value = str_value
    db.commit()

    if "polling_interval_seconds" in updates:
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            reschedule_polling(scheduler, updates["polling_interval_seconds"])

    return get_settings(db)
