from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.client import PPPoEClient, SessionState
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.traffic import AccumulationPeriod
from app.models.user import User
from app.services.dashboard_history import get_router_history
from app.services.router_overview import get_router_overview

# Far in the past, so other rows in the dev database never fall in range.
NOW = datetime(2001, 1, 2, 0, 0, tzinfo=timezone.utc)
GB = 1024**3
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
        Router(name=f"Page {n}", host=f"10.8.0.{n}", api_username="a", api_password_encrypted="")
        for n in (1, 2)
    ]
    db.add_all(made)
    db.commit()
    yield made
    db.rollback()
    # ON DELETE CASCADE removes clients, periods, session_state and poll stats.
    db.query(Router).filter(Router.id.in_([r.id for r in made])).delete(synchronize_session=False)
    db.commit()


def _stat(db, router, minutes_ago, rx=0, tx=0, clients=None, cpu=None, mem=None, hdd=None):
    """mem / hdd: (free, total) bytes."""
    db.add(
        RouterPollStat(
            router_id=router.id,
            polled_at=NOW - timedelta(minutes=minutes_ago),
            clients_connected=clients,
            rx_bps=rx,
            tx_bps=tx,
            cpu_load=cpu,
            mem_free_bytes=mem[0] if mem else None,
            mem_total_bytes=mem[1] if mem else None,
            hdd_free_bytes=hdd[0] if hdd else None,
            hdd_total_bytes=hdd[1] if hdd else None,
        )
    )


def test_router_history_averages_one_router_with_resource_percents(db, routers):
    r1, r2 = routers
    _stat(db, r1, 12, rx=100, tx=1000, clients=10, cpu=10, mem=(3 * GB, 4 * GB), hdd=(96, 128))  # bucket NOW-15m
    _stat(db, r1, 11, rx=300, tx=3000, clients=20, cpu=30, mem=(1 * GB, 4 * GB), hdd=(96, 128))  # same bucket
    _stat(db, r1, 2, rx=50, tx=500, clients=5)  # NOW-5m, resources unreadable
    _stat(db, r2, 12, rx=9999, tx=9999, clients=99, cpu=99)  # another router
    db.commit()

    history = get_router_history(db, r1.id, 24, now=NOW)

    assert history.bucket_seconds == 300
    assert [
        (p.t, p.rx_bps, p.tx_bps, p.clients_connected, p.cpu_load, p.mem_percent, p.hdd_percent)
        for p in history.points
    ] == [
        (NOW - timedelta(minutes=15), 200, 2000, 15, 20.0, 50.0, 25.0),
        (NOW - timedelta(minutes=5), 50, 500, 5, None, None, None),
    ]


def test_router_overview_sums_its_clients_and_reports_latest_resources(db, routers):
    r1, r2 = routers
    r1.board_name = "CCR2004-1G-12S+2XS"
    r1.routeros_version = "7.24.2 (stable)"
    r1.uptime_seconds = 3600
    r1.resources_at = NOW - timedelta(minutes=2)
    r1.last_polled_at = NOW - timedelta(minutes=2)
    online = PPPoEClient(router_id=r1.id, username="on", is_active=True)
    offline = PPPoEClient(router_id=r1.id, username="off", is_active=False)
    elsewhere = PPPoEClient(router_id=r2.id, username="other", is_active=True)
    db.add_all([online, offline, elsewhere])
    db.flush()
    db.add(SessionState(router_id=r1.id, client_id=online.id, interface_id="*on", last_rx_bps=10, last_tx_bps=100))
    db.add_all(
        [
            AccumulationPeriod(client_id=online.id, period_start=NOW, rx_bytes_total=1, tx_bytes_total=10),
            AccumulationPeriod(client_id=offline.id, period_start=NOW, rx_bytes_total=2, tx_bytes_total=20),
            # Closed period (previous month): not this month's traffic.
            AccumulationPeriod(
                client_id=online.id, period_start=NOW, period_end=NOW, rx_bytes_total=500, tx_bytes_total=500
            ),
            AccumulationPeriod(client_id=elsewhere.id, period_start=NOW, rx_bytes_total=7, tx_bytes_total=70),
        ]
    )
    _stat(db, r1, 7, cpu=40, mem=(1 * GB, 4 * GB), hdd=(64, 128))
    _stat(db, r1, 2, clients=1)  # latest poll could not read resources
    db.commit()

    overview = get_router_overview(db, r1.id)

    assert overview is not None
    assert (overview.name, overview.board_name, overview.routeros_version, overview.uptime_seconds) == (
        "Page 1",
        "CCR2004-1G-12S+2XS",
        "7.24.2 (stable)",
        3600,
    )
    assert (overview.clients_connected, overview.clients_total) == (1, 2)
    assert (overview.current_rx_bps, overview.current_tx_bps) == (10, 100)
    assert (overview.month_rx_bytes, overview.month_tx_bytes) == (3, 30)
    assert (overview.cpu_load, overview.mem_free_bytes, overview.mem_total_bytes) == (40, 1 * GB, 4 * GB)
    assert (overview.hdd_free_bytes, overview.hdd_total_bytes) == (64, 128)
    assert overview.resources_polled_at == NOW - timedelta(minutes=7)


def test_router_overview_of_a_router_never_polled(db, routers):
    overview = get_router_overview(db, routers[0].id)

    assert overview is not None
    assert (overview.clients_connected, overview.clients_total, overview.month_tx_bytes) == (0, 0, 0)
    assert overview.cpu_load is None and overview.resources_polled_at is None


def test_router_overview_of_unknown_router_is_none(db):
    assert get_router_overview(db, 999_999_999) is None


def _auth_headers() -> dict:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "router-page-tester").delete()
        db.add(User(username="router-page-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('router-page-tester')}"}


def test_router_page_endpoints(routers):
    r1, _ = routers
    headers = _auth_headers()

    overview = client.get(f"/routers/{r1.id}/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["name"] == "Page 1"
    assert "polling_interval_seconds" in overview.json()

    history = client.get(f"/dashboard/routers/{r1.id}/history?hours=168", headers=headers)
    assert history.status_code == 200
    assert history.json()["bucket_seconds"] == 1800

    assert client.get(f"/dashboard/routers/{r1.id}/history?hours=5", headers=headers).status_code == 422
    assert client.get("/dashboard/routers/999999999/history", headers=headers).status_code == 404
    assert client.get("/routers/999999999/overview", headers=headers).status_code == 404
    assert client.get(f"/routers/{r1.id}/overview").status_code == 401
    assert client.get(f"/dashboard/routers/{r1.id}/history").status_code == 401
