from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import AccumulationPeriod
from app.services.reset import (
    LAST_RESET_DATE_KEY,
    local_today,
    most_recent_reset_date,
    run_reset_if_due,
)

_KEYS = ["reset_day_of_month", LAST_RESET_DATE_KEY]


def _set_setting(db: Session, key: str, value: str | None) -> None:
    row = db.get(AppSetting, key)
    if value is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


@pytest.fixture
def db():
    session = SessionLocal()
    snapshot = {k: (r.value if (r := session.get(AppSetting, k)) else None) for k in _KEYS}
    created_router_ids: list[int] = []
    session.info["created_router_ids"] = created_router_ids
    yield session
    session.rollback()
    for key, value in snapshot.items():
        _set_setting(session, key, value)
    session.query(Router).filter(Router.id.in_(created_router_ids)).delete(synchronize_session=False)
    session.commit()
    session.close()


def _client_with_open_period(db: Session, period_start: datetime, rx: int = 5000) -> PPPoEClient:
    router = Router(name="Reset Test", host="10.0.0.5", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    db.info["created_router_ids"].append(router.id)
    client = PPPoEClient(router_id=router.id, username="reset_user")
    db.add(client)
    db.flush()
    db.add(AccumulationPeriod(client_id=client.id, period_start=period_start, rx_bytes_total=rx, tx_bytes_total=3000))
    db.commit()
    return client


def _configure(db: Session, reset_day: int, last_reset: str | None) -> None:
    _set_setting(db, "reset_day_of_month", str(reset_day))
    _set_setting(db, LAST_RESET_DATE_KEY, last_reset)
    db.commit()


def _periods(db: Session, client_id: int) -> tuple[list[AccumulationPeriod], list[AccumulationPeriod]]:
    rows = db.query(AccumulationPeriod).filter_by(client_id=client_id).all()
    return [p for p in rows if p.period_end is None], [p for p in rows if p.period_end is not None]


def _last_reset(db: Session) -> str | None:
    db.expire_all()
    row = db.get(AppSetting, LAST_RESET_DATE_KEY)
    return row.value if row else None


def test_most_recent_reset_date():
    assert most_recent_reset_date(date(2026, 10, 1), 1) == date(2026, 10, 1)
    assert most_recent_reset_date(date(2026, 10, 15), 1) == date(2026, 10, 1)
    assert most_recent_reset_date(date(2026, 10, 3), 28) == date(2026, 9, 28)
    assert most_recent_reset_date(date(2026, 1, 3), 5) == date(2025, 12, 5)


def test_local_today_uses_configured_timezone():
    # 01:00 UTC on Oct 1 is still Sep 30 (22:00) in America/Argentina/Cordoba.
    assert local_today(datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)) == date(2026, 9, 30)
    assert local_today(datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc)) == date(2026, 10, 1)


def test_resets_on_reset_day(db):
    _configure(db, 1, "2026-09-01")
    client = _client_with_open_period(db, datetime(2026, 9, 1, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 1)) is True

    open_, closed = _periods(db, client.id)
    assert len(open_) == 1 and (open_[0].rx_bytes_total, open_[0].tx_bytes_total) == (0, 0)
    assert len(closed) == 1 and closed[0].rx_bytes_total == 5000
    assert _last_reset(db) == "2026-10-01"


def test_missed_reset_day_is_caught_up_at_next_check(db):
    # Backend was down on Oct 1 (reset day): the first check after that,
    # on Oct 3, must still perform the October reset.
    _configure(db, 1, "2026-09-01")
    client = _client_with_open_period(db, datetime(2026, 9, 1, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 3)) is True

    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 1)
    assert _last_reset(db) == "2026-10-01"


def test_missed_reset_across_month_boundary_is_caught_up(db):
    # Reset day 28, down from Sep 27 to Oct 2: on Oct 2 the Sep 28 reset
    # is still owed.
    _configure(db, 28, "2026-08-28")
    client = _client_with_open_period(db, datetime(2026, 8, 28, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 2)) is True
    assert _last_reset(db) == "2026-09-28"
    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 1)


def test_already_reset_this_month_is_noop(db):
    _configure(db, 1, "2026-10-01")
    client = _client_with_open_period(db, datetime(2026, 10, 1, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 15)) is False

    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 0)


def test_before_this_months_reset_day_is_noop(db):
    _configure(db, 20, "2026-09-20")
    client = _client_with_open_period(db, datetime(2026, 9, 20, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 15)) is False
    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 0)


def test_is_idempotent_when_run_repeatedly(db):
    _configure(db, 1, "2026-09-01")
    client = _client_with_open_period(db, datetime(2026, 9, 1, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 1)) is True
    assert run_reset_if_due(db, today=date(2026, 10, 1)) is False
    assert run_reset_if_due(db, today=date(2026, 10, 2)) is False

    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 1)


def test_first_run_without_last_reset_date_does_not_reset_current_cycle(db):
    # Upgrade from the old cron job: periods already started after this
    # month's reset boundary -> just record the date, don't cut them.
    # (Dates in the past so that every other open period in the shared dev
    # database also started after this boundary.)
    _configure(db, 1, None)
    client = _client_with_open_period(db, datetime(2020, 1, 2, 12, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2020, 1, 5)) is False
    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 0)
    assert _last_reset(db) == "2020-01-01"


def test_first_run_without_last_reset_date_resets_stale_periods(db):
    # Upgrade where the old job had missed the reset: an open period from
    # before this month's boundary must be reset.
    _configure(db, 1, None)
    client = _client_with_open_period(db, datetime(2026, 9, 1, 3, tzinfo=timezone.utc))

    assert run_reset_if_due(db, today=date(2026, 10, 5)) is True
    open_, closed = _periods(db, client.id)
    assert (len(open_), len(closed)) == (1, 1)
    assert _last_reset(db) == "2026-10-01"
