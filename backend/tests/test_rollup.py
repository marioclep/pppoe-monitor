from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import TrafficHourly, TrafficSample
from app.services.rollup import (
    HOURLY_ROLLUP_KEY,
    client_hourly_history,
    get_rollup_watermark,
    run_hourly_rollup,
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def keep_watermark(db):
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    original = row.value if row else None
    yield
    db.rollback()
    _set_watermark(db, original)


@pytest.fixture
def client_id(db):
    router = Router(name="Rollup Test Router", host="10.0.0.50", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="rollup_user")
    db.add(client)
    db.commit()
    yield client.id
    db.rollback()
    # ON DELETE CASCADE removes the client, its samples and hourly rows.
    db.query(Router).filter(Router.id == router.id).delete()
    db.commit()


def _set_watermark(db: Session, value: datetime | str | None) -> None:
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    if value is None:
        if row is not None:
            db.delete(row)
    else:
        text_value = value.isoformat() if isinstance(value, datetime) else value
        if row is None:
            db.add(AppSetting(key=HOURLY_ROLLUP_KEY, value=text_value))
        else:
            row.value = text_value
    db.commit()


def _sample(db: Session, client_id: int, at: datetime, rx: int, tx: int, rx_bps: int = 0, tx_bps: int = 0) -> None:
    db.add(
        TrafficSample(
            client_id=client_id, sampled_at=at, rx_bytes_delta=rx, tx_bytes_delta=tx, rx_bps=rx_bps, tx_bps=tx_bps
        )
    )
    db.commit()


def _hourly(db: Session, client_id: int) -> list[tuple]:
    db.expire_all()
    rows = db.query(TrafficHourly).filter_by(client_id=client_id).order_by(TrafficHourly.hour_start).all()
    return [(r.hour_start, r.rx_bytes, r.tx_bytes, r.peak_rx_bps, r.peak_tx_bps) for r in rows]


def test_rollup_sums_bytes_and_keeps_peak_per_hour(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10, rx_bps=50, tx_bps=5)
    _sample(db, client_id, _at(1, 10, 35), 200, 20, rx_bps=80, tx_bps=3)
    _sample(db, client_id, _at(1, 11, 10), 400, 40, rx_bps=20, tx_bps=9)
    _set_watermark(db, _at(1, 10))

    new_watermark = run_hourly_rollup(db, now=_at(1, 12, 30))

    assert _hourly(db, client_id) == [
        (_at(1, 10), 300, 30, 80, 5),
        (_at(1, 11), 400, 40, 20, 9),
    ]
    assert new_watermark == _at(1, 12)
    assert get_rollup_watermark(db) == _at(1, 12)


def test_rollup_never_summarizes_the_hour_in_progress(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10)
    _sample(db, client_id, _at(1, 11, 10), 400, 40)
    _set_watermark(db, _at(1, 10))

    run_hourly_rollup(db, now=_at(1, 11, 20))

    assert [row[0] for row in _hourly(db, client_id)] == [_at(1, 10)]
    assert get_rollup_watermark(db) == _at(1, 11)


def test_rollup_rerun_replaces_instead_of_adding(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10, rx_bps=50, tx_bps=5)
    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))

    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))

    assert _hourly(db, client_id) == [(_at(1, 10), 100, 10, 50, 5)]


def test_rollup_without_watermark_starts_at_oldest_sample_and_crosses_chunks(db, client_id):
    """The first run on an existing database covers days of samples in
    several committed chunks: no hour may be lost or doubled at a border."""
    _sample(db, client_id, _at(1, 10, 5), 100, 1)
    _sample(db, client_id, _at(2, 9, 55), 200, 2)
    _sample(db, client_id, _at(2, 10, 5), 300, 3)
    _sample(db, client_id, _at(2, 23, 55), 400, 4)
    _sample(db, client_id, _at(3, 0, 5), 500, 5)
    _set_watermark(db, None)

    run_hourly_rollup(db, now=_at(3, 1, 30))

    assert [(row[0], row[1]) for row in _hourly(db, client_id)] == [
        (_at(1, 10), 100),
        (_at(2, 9), 200),
        (_at(2, 10), 300),
        (_at(2, 23), 400),
        (_at(3, 0), 500),
    ]
    assert get_rollup_watermark(db) == _at(3, 1)


def test_rollup_recomputes_last_rolled_up_hour_for_late_samples(db, client_id):
    """A sample can be committed after its hour was already rolled up (the
    poller takes `now` before a long transaction and commits later). Every
    run re-rolls the last rolled-up hour so such late samples aren't lost."""
    _sample(db, client_id, _at(1, 10, 5), 100, 10)
    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))
    assert _hourly(db, client_id) == [(_at(1, 10), 100, 10, 0, 0)]

    # Late-committed sample lands in the already-rolled-up hour 10.
    _sample(db, client_id, _at(1, 10, 59), 50, 5)

    run_hourly_rollup(db, now=_at(1, 11, 40))

    assert _hourly(db, client_id) == [(_at(1, 10), 150, 15, 0, 0)]


def test_rollup_clamps_a_future_watermark(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10)
    _set_watermark(db, _at(1, 15))  # clock skew: watermark ahead of now

    new_watermark = run_hourly_rollup(db, now=_at(1, 11, 30))

    assert new_watermark == _at(1, 11)
    assert _hourly(db, client_id) == [(_at(1, 10), 100, 10, 0, 0)]


def test_garbage_watermark_reads_as_missing(db):
    _set_watermark(db, "garbage")
    assert get_rollup_watermark(db) is None
    _set_watermark(db, "2026-01-01T10:00:00")  # no timezone
    assert get_rollup_watermark(db) is None


def test_client_hourly_history_mixes_rollup_and_live_samples(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 1_800_000, 900_000, rx_bps=9_000, tx_bps=4_000)
    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))
    # Hour 11 is not rolled up yet: it comes from the samples on the fly.
    _sample(db, client_id, _at(1, 11, 5), 450_000, 0, rx_bps=7_000, tx_bps=0)
    _sample(db, client_id, _at(1, 11, 10), 450_000, 0, rx_bps=5_000, tx_bps=0)

    points = client_hourly_history(db, client_id, since=_at(1, 9, 30), now=_at(1, 12, 30))

    assert [(p.hour_start, p.rx_bytes, p.rx_bps, p.peak_rx_bps) for p in points] == [
        (_at(1, 10), 1_800_000, 1_800_000 * 8 // 3600, 9_000),
        (_at(1, 11), 900_000, 900_000 * 8 // 3600, 7_000),
    ]


def test_client_hourly_history_hour_in_progress_uses_elapsed_seconds(db, client_id):
    _set_watermark(db, None)  # nothing rolled up: the whole range is live
    _sample(db, client_id, _at(1, 10, 10), 1800, 900)

    (point,) = client_hourly_history(db, client_id, since=_at(1, 8), now=_at(1, 10, 30))

    assert point.hour_start == _at(1, 10)
    # 30 minutes (1800 s) of the hour have elapsed.
    assert (point.rx_bps, point.tx_bps) == (1800 * 8 // 1800, 900 * 8 // 1800)
