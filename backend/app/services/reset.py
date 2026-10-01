"""Monthly accumulation reset.

Due-based rather than "fire exactly on day N": the job runs hourly (and once
at startup) and resets whenever the most recent reset date -- day
`reset_day_of_month` of this month, or of last month if that day hasn't come
yet -- is later than the `last_reset_date` recorded in the settings table.
So a reset missed because the backend was down (or a misfire) is caught up
at the next check, and running it again for the same date is a no-op.

Dates are local dates in the configured timezone (env `TZ`, default
America/Argentina/Cordoba), so the reset happens around local midnight.
"""
import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.settings import AppSetting
from app.models.traffic import AccumulationPeriod
from app.services.app_settings import get_reset_day_of_month

logger = logging.getLogger(__name__)

LAST_RESET_DATE_KEY = "last_reset_date"


def local_timezone() -> ZoneInfo:
    return ZoneInfo(settings.TZ)


def local_today(now_utc: datetime | None = None) -> date:
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(local_timezone()).date()


def most_recent_reset_date(today: date, reset_day: int) -> date:
    """Day `reset_day` of this month if it has been reached, otherwise of the
    previous month. (reset_day is limited to 1-28, so it exists in every
    month.)"""
    if today.day >= reset_day:
        return today.replace(day=reset_day)
    if today.month == 1:
        return date(today.year - 1, 12, reset_day)
    return date(today.year, today.month - 1, reset_day)


def _read_last_reset_date(db: Session) -> date | None:
    row = db.get(AppSetting, LAST_RESET_DATE_KEY)
    if row is None or not row.value:
        return None
    try:
        return date.fromisoformat(row.value)
    except ValueError:
        logger.warning("Setting %s has invalid value %r; ignoring it", LAST_RESET_DATE_KEY, row.value)
        return None


def _write_last_reset_date(db: Session, value: date) -> None:
    row = db.get(AppSetting, LAST_RESET_DATE_KEY)
    if row is None:
        db.add(AppSetting(key=LAST_RESET_DATE_KEY, value=value.isoformat()))
    else:
        row.value = value.isoformat()


def run_reset_if_due(db: Session, today: date | None = None) -> bool:
    """Close every open accumulation period and open a fresh one if a reset
    is due. Returns True if a reset was performed."""
    today = today or local_today()
    due_date = most_recent_reset_date(today, get_reset_day_of_month(db))
    last_reset = _read_last_reset_date(db)
    if last_reset is not None and last_reset >= due_date:
        return False

    open_periods = db.query(AccumulationPeriod).filter(AccumulationPeriod.period_end.is_(None)).all()

    if last_reset is None:
        # First run of this logic (fresh install, or upgrade from the old
        # fixed-day cron job, which did not record resets). Only reset if
        # some open period actually predates the due date's local midnight;
        # otherwise those periods already belong to the current cycle.
        boundary = datetime.combine(due_date, time.min, tzinfo=local_timezone())
        if not any(period.period_start < boundary for period in open_periods):
            _write_last_reset_date(db, due_date)
            db.commit()
            logger.info("Recorded %s as the last monthly reset (no stale periods)", due_date)
            return False

    now = datetime.now(timezone.utc)
    for period in open_periods:
        period.period_end = now
    # Close the old periods before opening new ones: at most one open period
    # per client is enforced by a unique index.
    db.flush()
    for period in open_periods:
        db.add(
            AccumulationPeriod(
                client_id=period.client_id,
                period_start=now,
                period_end=None,
                rx_bytes_total=0,
                tx_bytes_total=0,
            )
        )
    _write_last_reset_date(db, due_date)
    db.commit()
    logger.info(
        "Monthly reset for %s done (%d periods closed; previous reset: %s)", due_date, len(open_periods), last_reset
    )
    return True


def run_reset_job() -> None:
    db = SessionLocal()
    try:
        run_reset_if_due(db)
    finally:
        db.close()
