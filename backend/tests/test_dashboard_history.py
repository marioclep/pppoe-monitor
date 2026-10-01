from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.user import User
from app.services.dashboard_history import get_dashboard_history, get_router_polls

# Far in the past, so other rows in the dev database never fall in range.
NOW = datetime(2001, 1, 2, 0, 0, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def routers(db):
    made = [
        Router(name=f"Hist {n}", host=f"10.9.0.{n}", api_username="a", api_password_encrypted="", enabled=enabled)
        for n, enabled in ((1, True), (2, True), (3, False))
    ]
    db.add_all(made)
    db.commit()
    yield made
    db.rollback()
    db.query(Router).filter(Router.id.in_([r.id for r in made])).delete(synchronize_session=False)
    db.commit()


def _stat(db, router, minutes_ago, rx, tx, clients):
    db.add(
        RouterPollStat(
            router_id=router.id,
            polled_at=NOW - timedelta(minutes=minutes_ago),
            clients_connected=clients,
            rx_bps=rx,
            tx_bps=tx,
        )
    )


def test_24h_uses_5_minute_buckets_and_sums_routers(db, routers):
    r1, r2, _ = routers
    _stat(db, r1, 12, rx=100, tx=1000, clients=10)  # bucket NOW-15m
    _stat(db, r2, 11, rx=50, tx=500, clients=5)  # same bucket
    _stat(db, r1, 2, rx=300, tx=3000, clients=12)  # bucket NOW-5m
    db.commit()

    history = get_dashboard_history(db, 24, now=NOW)

    assert history.bucket_seconds == 300
    assert [(p.t, p.rx_bps, p.tx_bps, p.clients_connected) for p in history.points] == [
        (NOW - timedelta(minutes=15), 150, 1500, 15),
        (NOW - timedelta(minutes=5), 300, 3000, 12),
    ]


def test_7d_averages_each_router_within_the_bucket_then_sums(db, routers):
    r1, r2, _ = routers
    # One 30-minute bucket [NOW-30m, NOW): r1 polled twice, r2 once.
    _stat(db, r1, 25, rx=100, tx=100, clients=10)
    _stat(db, r1, 20, rx=300, tx=300, clients=20)
    _stat(db, r2, 22, rx=40, tx=40, clients=4)
    db.commit()

    history = get_dashboard_history(db, 168, now=NOW)

    assert history.bucket_seconds == 1800
    assert [(p.rx_bps, p.clients_connected) for p in history.points] == [(240, 19)]


def test_null_clients_of_one_router_keep_the_others(db, routers):
    r1, r2, _ = routers
    _stat(db, r1, 30, rx=10, tx=10, clients=None)  # backfilled from hourly
    _stat(db, r2, 30, rx=20, tx=20, clients=7)
    db.commit()

    points = get_dashboard_history(db, 720, now=NOW).points

    assert [(p.rx_bps, p.clients_connected) for p in points] == [(30, 7)]


def test_all_null_clients_stay_null(db, routers):
    r1, _, _ = routers
    _stat(db, r1, 30, rx=10, tx=10, clients=None)
    db.commit()

    assert get_dashboard_history(db, 720, now=NOW).points[0].clients_connected is None


def test_disabled_routers_and_rows_outside_the_range_are_excluded(db, routers):
    r1, _, disabled = routers
    _stat(db, disabled, 5, rx=999, tx=999, clients=99)
    _stat(db, r1, 60 * 25, rx=5, tx=5, clients=1)  # 25h ago: outside 24h
    db.commit()

    assert get_dashboard_history(db, 24, now=NOW).points == []


@pytest.mark.parametrize("hours,bucket", [(24, 300), (168, 1800), (720, 7200), (2160, 21600)])
def test_bucket_per_range(db, hours, bucket):
    assert get_dashboard_history(db, hours, now=NOW).bucket_seconds == bucket


def _auth_headers() -> dict:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "hist-tester").delete()
        db.add(User(username="hist-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('hist-tester')}"}


def test_endpoint_returns_history_and_validates_hours():
    headers = _auth_headers()
    ok = client.get("/dashboard/history?hours=24", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["bucket_seconds"] == 300
    assert isinstance(ok.json()["points"], list)
    assert client.get("/dashboard/history?hours=25", headers=headers).status_code == 422


def test_endpoint_requires_auth():
    assert client.get("/dashboard/history?hours=24").status_code == 401


def test_the_last_90_seconds_are_left_out_while_a_cycle_may_still_be_writing(db, routers):
    r1, r2, _ = routers
    _stat(db, r1, 1, rx=10, tx=10, clients=1)  # 60 s before NOW: cycle possibly in progress
    _stat(db, r2, 3, rx=20, tx=20, clients=2)
    db.commit()

    assert [p.rx_bps for p in get_dashboard_history(db, 24, now=NOW).points] == [20]


def test_router_polls_are_one_router_raw_and_in_order(db, routers):
    r1, r2, _ = routers
    _stat(db, r1, 7, rx=300, tx=3000, clients=12)
    _stat(db, r1, 2, rx=100, tx=1000, clients=10)
    _stat(db, r2, 3, rx=50, tx=500, clients=5)
    _stat(db, r1, 25 * 60, rx=9, tx=9, clients=9)  # older than 24 h
    db.commit()

    polls = get_router_polls(db, r1.id, 24, now=NOW)

    assert polls.router_id == r1.id
    assert [(p.t, p.clients_connected, p.rx_bps, p.tx_bps) for p in polls.points] == [
        (NOW - timedelta(minutes=7), 12, 300, 3000),
        (NOW - timedelta(minutes=2), 10, 100, 1000),
    ]


def test_router_polls_endpoint(db, routers):
    r1, _, _ = routers
    headers = _auth_headers()

    ok = client.get(f"/dashboard/routers/{r1.id}/polls?hours=24", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["router_id"] == r1.id
    assert isinstance(ok.json()["points"], list)
    assert client.get(f"/dashboard/routers/{r1.id}/polls?hours=0", headers=headers).status_code == 422
    assert client.get(f"/dashboard/routers/{r1.id}/polls?hours=200", headers=headers).status_code == 422
    assert client.get("/dashboard/routers/999999999/polls", headers=headers).status_code == 404
    assert client.get(f"/dashboard/routers/{r1.id}/polls").status_code == 401
