from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.client import PPPoEClient, SessionState
from app.models.router import Router
from app.models.traffic import TrafficSample
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "dash-tester").delete()
        db.commit()
        db.add(User(username="dash-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('dash-tester')}"}


def _cleanup(router_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        # ON DELETE CASCADE removes clients, session_state and samples.
        db.query(Router).filter(Router.id.in_(router_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _add_client(db: Session, router_id: int, username: str, active: bool, bps: tuple[int, int] | None) -> int:
    c = PPPoEClient(router_id=router_id, username=username, is_active=active)
    db.add(c)
    db.flush()
    if bps is not None:
        db.add(
            SessionState(
                router_id=router_id, client_id=c.id, interface_id=f"*{username}", last_rx_bps=bps[0], last_tx_bps=bps[1]
            )
        )
    return c.id


def test_dashboard_summary_uses_session_state_bps_for_active_clients():
    db: Session = SessionLocal()
    polled_at = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    try:
        router = Router(
            name="Dash Router", host="10.0.0.8", api_username="a", api_password_encrypted="", last_polled_at=polled_at
        )
        db.add(router)
        db.flush()
        c1 = _add_client(db, router.id, "u1", True, (1000, 500))
        _add_client(db, router.id, "u2", True, (2000, 1500))
        # Offline client: no session_state row -> contributes nothing.
        _add_client(db, router.id, "u3", False, None)
        # A huge historical sample must be irrelevant now (no samples scan).
        db.add(TrafficSample(client_id=c1, sampled_at=datetime.now(timezone.utc), rx_bps=9_999_999, tx_bps=9_999_999))
        db.commit()
        router_id = router.id
    finally:
        db.close()

    try:
        response = client.get("/dashboard/summary", headers=_auth_headers())
        assert response.status_code == 200
        body = response.json()
        entry = next(r for r in body["by_router"] if r["router_id"] == router_id)
        assert entry["clients_connected"] == 2
        assert entry["current_rx_bps"] == 3000
        assert entry["current_tx_bps"] == 2000
        assert datetime.fromisoformat(entry["last_polled_at"]) == polled_at
        assert body["current_rx_bps"] >= 3000
        assert body["total_clients_connected"] >= 2
    finally:
        _cleanup([router_id])


def test_dashboard_summary_excludes_disabled_routers():
    db: Session = SessionLocal()
    try:
        enabled = Router(name="Dash On", host="10.0.0.30", api_username="a", api_password_encrypted="")
        disabled = Router(
            name="Dash Off", host="10.0.0.31", api_username="a", api_password_encrypted="", enabled=False
        )
        db.add_all([enabled, disabled])
        db.flush()
        _add_client(db, enabled.id, "on_user", True, (10, 20))
        _add_client(db, disabled.id, "off_user", True, (777, 888))
        db.commit()
        ids = (enabled.id, disabled.id)
    finally:
        db.close()

    try:
        body = client.get("/dashboard/summary", headers=_auth_headers()).json()
        router_ids = {r["router_id"] for r in body["by_router"]}
        assert ids[0] in router_ids
        assert ids[1] not in router_ids
    finally:
        _cleanup(list(ids))


def test_dashboard_summary_router_without_clients_reports_zero_and_null_last_poll():
    db: Session = SessionLocal()
    try:
        router = Router(name="Dash Empty", host="10.0.0.32", api_username="a", api_password_encrypted="")
        db.add(router)
        db.commit()
        router_id = router.id
    finally:
        db.close()

    try:
        body = client.get("/dashboard/summary", headers=_auth_headers()).json()
        entry = next(r for r in body["by_router"] if r["router_id"] == router_id)
        assert (entry["clients_connected"], entry["current_rx_bps"], entry["current_tx_bps"]) == (0, 0, 0)
        assert entry["last_polled_at"] is None
    finally:
        _cleanup([router_id])


def test_dashboard_counts_a_client_with_two_sessions_once_and_sums_its_speed():
    db: Session = SessionLocal()
    try:
        router = Router(name="Dash Two Sessions", host="10.0.0.11", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        client_id = _add_client(db, router.id, "doble", True, (1000, 100))
        db.add(
            SessionState(
                router_id=router.id, client_id=client_id, interface_id="*doble-1", last_rx_bps=500, last_tx_bps=50
            )
        )
        db.commit()
        router_id = router.id
    finally:
        db.close()

    try:
        response = client.get("/dashboard/summary", headers=_auth_headers())
        assert response.status_code == 200
        entry = next(r for r in response.json()["by_router"] if r["router_id"] == router_id)
        assert entry["clients_connected"] == 1
        assert (entry["current_rx_bps"], entry["current_tx_bps"]) == (1500, 150)
    finally:
        _cleanup([router_id])


def test_dashboard_summary_reports_polling_interval_and_clients_seen_this_period():
    from app.models.traffic import AccumulationPeriod
    from app.services.app_settings import get_polling_interval_seconds

    db: Session = SessionLocal()
    try:
        before = client.get("/dashboard/summary", headers=_auth_headers()).json().get("clients_seen_this_period", 0)
        router = Router(name="Dash Seen", host="10.0.0.40", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        online = _add_client(db, router.id, "seen_on", True, (1, 1))
        offline = _add_client(db, router.id, "seen_off", False, None)
        db.add_all([AccumulationPeriod(client_id=online), AccumulationPeriod(client_id=offline)])
        db.commit()
        router_id = router.id
        interval = get_polling_interval_seconds(db)
    finally:
        db.close()

    try:
        body = client.get("/dashboard/summary", headers=_auth_headers()).json()
        assert body["polling_interval_seconds"] == interval
        assert body["clients_seen_this_period"] == before + 2
    finally:
        _cleanup([router_id])
