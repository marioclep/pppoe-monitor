from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.server_stats import ServerStat
from app.models.settings import AppSetting
from app.models.traffic import TrafficHourly, TrafficSample
from app.services.purge import purge_old_samples
from app.services.rollup import HOURLY_ROLLUP_KEY, floor_hour

_KEYS = ["retention_days", "raw_retention_days", HOURLY_ROLLUP_KEY]


def _write_settings(db: Session, values: dict[str, str | None]) -> None:
    for key, value in values.items():
        row = db.get(AppSetting, key)
        if value is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()


@pytest.fixture
def db():
    session = SessionLocal()
    snapshot = {key: (row.value if (row := session.get(AppSetting, key)) else None) for key in _KEYS}
    _write_settings(session, {"retention_days": "90", "raw_retention_days": "7"})
    yield session
    session.rollback()
    _write_settings(session, snapshot)
    session.close()


@pytest.fixture
def client_id(db):
    router = Router(name="Purge Test", host="10.0.0.6", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="purge_user")
    db.add(client)
    db.commit()
    yield client.id
    db.rollback()
    db.query(Router).filter(Router.id == router.id).delete()  # cascades to client data
    db.commit()


NOW = datetime.now(timezone.utc)


def _samples(db: Session, client_id: int, *ages: timedelta) -> list[int]:
    rows = [TrafficSample(client_id=client_id, sampled_at=NOW - age) for age in ages]
    db.add_all(rows)
    db.commit()
    return [row.id for row in rows]


def _hours(db: Session, client_id: int, *ages: timedelta) -> None:
    db.add_all(
        TrafficHourly(
            client_id=client_id, hour_start=floor_hour(NOW - age), rx_bytes=1, tx_bytes=1, peak_rx_bps=1, peak_tx_bps=1
        )
        for age in ages
    )
    db.commit()


def _remaining_samples(db: Session, client_id: int) -> list[int]:
    return [row.id for row in db.query(TrafficSample).filter_by(client_id=client_id).order_by(TrafficSample.id)]


def _remaining_hours(db: Session, client_id: int) -> list[datetime]:
    rows = db.query(TrafficHourly).filter_by(client_id=client_id).order_by(TrafficHourly.hour_start)
    return [row.hour_start for row in rows]


def test_purge_keeps_samples_for_raw_retention_and_hours_for_retention(db, client_id):
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(NOW).isoformat()})
    _old, recent = _samples(db, client_id, timedelta(days=8), timedelta(days=6))
    _hours(db, client_id, timedelta(days=91), timedelta(days=89))

    result = purge_old_samples(db, today=NOW)

    assert _remaining_samples(db, client_id) == [recent]
    assert _remaining_hours(db, client_id) == [floor_hour(NOW - timedelta(days=89))]
    assert result.samples >= 1 and result.hourly >= 1

    again = purge_old_samples(db, today=NOW)
    assert _remaining_samples(db, client_id) == [recent]
    assert again.samples == 0 and again.hourly == 0


def test_purge_never_deletes_samples_not_rolled_up_yet(db, client_id):
    # The rollup lags 10 days behind (e.g. the job was failing).
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(NOW - timedelta(days=10)).isoformat()})
    _rolled_up, pending = _samples(db, client_id, timedelta(days=12), timedelta(days=9))

    purge_old_samples(db, today=NOW)

    assert _remaining_samples(db, client_id) == [pending]


@pytest.mark.parametrize("watermark", [None, "garbage"])
def test_purge_without_usable_watermark_deletes_no_samples(db, client_id, watermark):
    _write_settings(db, {HOURLY_ROLLUP_KEY: watermark})
    ids = _samples(db, client_id, timedelta(days=30))
    _hours(db, client_id, timedelta(days=91))

    purge_old_samples(db, today=NOW)

    assert _remaining_samples(db, client_id) == ids
    assert _remaining_hours(db, client_id) == []  # the hourly retention still applies


def test_purge_deletes_in_batches(db, client_id, monkeypatch):
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(NOW).isoformat()})
    _samples(db, client_id, *[timedelta(days=20, minutes=i) for i in range(5)])
    batches: list[int] = []
    real_execute = db.execute

    def counting_execute(statement, params=None, *args, **kwargs):
        if params and "batch_size" in params:
            batches.append(params["batch_size"])
        return real_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", counting_execute)

    purge_old_samples(db, today=NOW, batch_size=2)

    assert _remaining_samples(db, client_id) == []
    # 5 old samples in batches of 2: at least 3 sample batches + 1 hourly batch.
    assert len(batches) >= 4 and set(batches) == {2}


def test_purge_keeps_rollup_overlap_hour_when_watermark_lags(db, client_id):
    # The rollup re-rolls [watermark - ROLLUP_OVERLAP, watermark) on every
    # run, no matter how far the watermark lags behind raw_retention_days: a
    # sample in that hour must survive purge even though it is already older
    # than raw_retention_days, or the re-roll would overwrite it with a
    # partial sum.
    watermark = floor_hour(NOW - timedelta(days=10))
    _write_settings(db, {HOURLY_ROLLUP_KEY: watermark.isoformat()})
    just_below_watermark = TrafficSample(client_id=client_id, sampled_at=watermark - timedelta(minutes=30))
    two_hours_below_watermark = TrafficSample(client_id=client_id, sampled_at=watermark - timedelta(hours=2, minutes=30))
    db.add_all([just_below_watermark, two_hours_below_watermark])
    db.commit()

    purge_old_samples(db, today=NOW)

    remaining = _remaining_samples(db, client_id)
    assert remaining == [just_below_watermark.id]


def test_purge_keeps_whole_hour_not_yet_fully_past_cutoff(db, client_id):
    # The rollup re-rolls the hour just below its watermark on every run
    # (ROLLUP_OVERLAP), so a partially purged hour would be overwritten with
    # a partial sum. Purge must therefore delete whole hours only: the
    # cutoff is min(floor_hour(today - raw_retention_days), watermark).
    today = datetime(2026, 3, 10, 12, 30, tzinfo=timezone.utc)
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(today).isoformat()})
    # today - 7d = 2026-03-03 12:30, whose hour is [12:00, 13:00).
    same_hour = TrafficSample(client_id=client_id, sampled_at=datetime(2026, 3, 3, 12, 10, tzinfo=timezone.utc))
    previous_hour = TrafficSample(client_id=client_id, sampled_at=datetime(2026, 3, 3, 11, 50, tzinfo=timezone.utc))
    db.add_all([same_hour, previous_hour])
    db.commit()

    purge_old_samples(db, today=today)

    remaining = _remaining_samples(db, client_id)
    assert remaining == [same_hour.id]


def test_purge_trims_router_poll_stats_to_retention_days(db, client_id):
    router_id = db.get(PPPoEClient, client_id).router_id
    now = datetime.now(timezone.utc)
    for age in (timedelta(days=91), timedelta(days=89), timedelta(hours=1)):
        db.add(RouterPollStat(router_id=router_id, polled_at=now - age, clients_connected=1, rx_bps=1, tx_bps=1))
    db.commit()

    result = purge_old_samples(db, today=now)

    remaining = sorted((now - s.polled_at).days for s in db.query(RouterPollStat).filter_by(router_id=router_id))
    assert remaining == [0, 89]
    assert result.poll_stats >= 1


def test_purge_trims_server_stats_to_retention_days(db):
    now = datetime.now(timezone.utc)
    ages = (timedelta(days=91), timedelta(days=89), timedelta(hours=1))
    rows = [
        ServerStat(
            sampled_at=now - age,
            cpu_percent=1,
            mem_used_bytes=1,
            mem_total_bytes=2,
            disk_used_bytes=1,
            disk_total_bytes=2,
        )
        for age in ages
    ]
    db.add_all(rows)
    db.commit()
    ids = [r.id for r in rows]

    try:
        result = purge_old_samples(db, today=now)

        remaining = sorted((now - s.sampled_at).days for s in db.query(ServerStat).filter(ServerStat.id.in_(ids)))
        assert remaining == [0, 89]
        assert result.server_stats >= 1
    finally:
        db.query(ServerStat).filter(ServerStat.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
