from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.alert import AlertEvent, AlertThreshold
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.user import User

client = TestClient(app)

ROUTER_NAME = "Alerts Test Router"


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "alerts-tester").delete()
        db.commit()
        db.add(User(username="alerts-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('alerts-tester')}"}


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        db = SessionLocal()
        try:
            db.query(AlertEvent).delete()
            db.query(AlertThreshold).delete()
            db.query(Router).filter(Router.name == ROUTER_NAME).delete()
            db.commit()
        finally:
            db.close()

    wipe()
    yield
    wipe()


def _seed_client(username: str = "alerts-client") -> int:
    db: Session = SessionLocal()
    try:
        router = db.query(Router).filter(Router.name == ROUTER_NAME).first()
        if router is None:
            router = Router(name=ROUTER_NAME, host="10.0.0.1", api_username="admin", api_password_encrypted="enc")
            db.add(router)
            db.flush()
        pppoe_client = PPPoEClient(router_id=router.id, username=username)
        db.add(pppoe_client)
        db.commit()
        return pppoe_client.id
    finally:
        db.close()


def _post(payload: dict):
    return client.post("/alerts/thresholds", json=payload, headers=_auth_headers())


def test_create_list_delete_global_threshold():
    resp = _post({"direction": "download", "bytes_threshold": 500_000_000_000, "notify_channel": "email"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["direction"] == "download"
    assert body["client_id"] is None
    assert body["client_username"] is None
    assert body["router_name"] is None

    listed = client.get("/alerts/thresholds", headers=_auth_headers()).json()
    assert [t["id"] for t in listed] == [body["id"]]

    assert client.delete(f"/alerts/thresholds/{body['id']}", headers=_auth_headers()).status_code == 204


def test_one_global_threshold_per_direction():
    down = _post({"direction": "download", "bytes_threshold": 100})
    up = _post({"direction": "upload", "bytes_threshold": 200})
    assert down.status_code == 201 and up.status_code == 201
    assert down.json()["id"] != up.json()["id"]

    again = _post({"direction": "upload", "bytes_threshold": 300, "notify_channel": "telegram"})
    assert again.status_code == 200
    assert again.json()["id"] == up.json()["id"]
    assert again.json()["bytes_threshold"] == 300
    assert again.json()["notify_channel"] == "telegram"
    assert len(client.get("/alerts/thresholds", headers=_auth_headers()).json()) == 2


def test_client_threshold_upserts_per_direction_and_names_the_client():
    client_id = _seed_client()
    first = _post({"client_id": client_id, "direction": "upload", "bytes_threshold": 100})
    assert first.status_code == 201
    assert first.json()["client_username"] == "alerts-client"
    assert first.json()["router_name"] == ROUTER_NAME

    second = _post({"client_id": client_id, "direction": "upload", "bytes_threshold": 999})
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    other = _post({"client_id": client_id, "direction": "download", "bytes_threshold": 50})
    assert other.status_code == 201
    assert other.json()["id"] != first.json()["id"]


def test_list_thresholds_filtered_by_client():
    a = _seed_client("client-a")
    b = _seed_client("client-b")
    _post({"client_id": a, "direction": "download", "bytes_threshold": 1})
    _post({"client_id": b, "direction": "download", "bytes_threshold": 1})
    _post({"direction": "download", "bytes_threshold": 1})

    listed = client.get(f"/alerts/thresholds?client_id={a}", headers=_auth_headers()).json()
    assert [t["client_username"] for t in listed] == ["client-a"]


def test_post_threshold_falls_back_to_update_when_concurrent_insert_wins(monkeypatch):
    """Simulates the race the unique index guards against: the upsert's
    lookup misses, but by insert time another request has created the
    global row. The IntegrityError must turn into an update (200), not a
    500."""
    db: Session = SessionLocal()
    try:
        winner = AlertThreshold(client_id=None, direction="download", bytes_threshold=1, notify_channel="email")
        db.add(winner)
        db.commit()
        winner_id = winner.id
    finally:
        db.close()

    import app.api.alerts as alerts_api

    real_find = alerts_api._find_threshold
    calls = {"n": 0}

    def stale_first_lookup(db, client_id, direction):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # lookup ran before the concurrent insert committed
        return real_find(db, client_id, direction)

    monkeypatch.setattr(alerts_api, "_find_threshold", stale_first_lookup)
    resp = _post({"direction": "download", "bytes_threshold": 777, "notify_channel": "telegram"})
    assert resp.status_code == 200
    assert resp.json()["id"] == winner_id
    assert resp.json()["bytes_threshold"] == 777


def test_post_threshold_for_unknown_client_returns_404():
    assert _post({"client_id": 999999999, "direction": "download", "bytes_threshold": 1}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"direction": "download", "bytes_threshold": 1, "notify_channel": "sms"},
        {"direction": "total", "bytes_threshold": 1},
        {"bytes_threshold": 1},
        {"direction": "upload", "bytes_threshold": 0},
    ],
)
def test_post_threshold_validates_input(payload):
    assert _post(payload).status_code == 422


def test_events_name_the_client_router_and_direction():
    client_id = _seed_client("heavy-one")
    db: Session = SessionLocal()
    try:
        db.add(
            AlertEvent(
                client_id=client_id,
                threshold_id=None,
                direction="upload",
                threshold_bytes=300,
                accumulated_bytes_at_trigger=523,
                triggered_at=datetime(2026, 9, 30, 12, tzinfo=timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    events = client.get("/alerts/events", headers=_auth_headers()).json()
    assert len(events) == 1
    event = events[0]
    assert event["client_id"] == client_id
    assert event["client_username"] == "heavy-one"
    assert event["router_name"] == ROUTER_NAME
    assert event["direction"] == "upload"
    assert event["threshold_bytes"] == 300
    assert event["accumulated_bytes_at_trigger"] == 523


def test_list_events_returns_empty_list_when_none():
    response = client.get("/alerts/events", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json() == []
