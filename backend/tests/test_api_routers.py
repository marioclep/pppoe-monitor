from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.router import Router
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "router-tester").delete()
        db.commit()
        db.add(User(username="router-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    token = create_access_token("router-tester")
    return {"Authorization": f"Bearer {token}"}


def test_create_and_list_router():
    headers = _auth_headers()
    payload = {
        "name": "Router Centro",
        "host": "10.0.0.1",
        "api_username": "admin",
        "api_password": "secret123",
    }
    create_resp = client.post("/routers", json=payload, headers=headers)
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["name"] == "Router Centro"
    assert "api_password" not in body
    assert "api_password_encrypted" not in body

    list_resp = client.get("/routers", headers=headers)
    assert list_resp.status_code == 200
    assert any(r["name"] == "Router Centro" for r in list_resp.json())


def test_router_endpoints_require_auth():
    response = client.get("/routers")
    assert response.status_code in (401, 403)


def test_disabling_router_marks_clients_inactive_and_clears_session_state():
    from app.models.client import PPPoEClient, SessionState

    headers = _auth_headers()
    db: Session = SessionLocal()
    try:
        router = Router(name="Disable Me", host="10.0.0.40", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        online = PPPoEClient(router_id=router.id, username="online", is_active=True)
        db.add(online)
        db.flush()
        db.add(
            SessionState(
                router_id=router.id, client_id=online.id, interface_id="*1", last_rx_bps=100, last_tx_bps=50
            )
        )
        db.commit()
        router_id, client_id = router.id, online.id
    finally:
        db.close()

    try:
        resp = client.put(f"/routers/{router_id}", json={"enabled": False}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert "last_polled_at" in resp.json()

        db = SessionLocal()
        try:
            assert db.get(PPPoEClient, client_id).is_active is False
            assert db.query(SessionState).filter_by(router_id=router_id).count() == 0
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(Router).filter(Router.id == router_id).delete()
            db.commit()
        finally:
            db.close()


def test_update_router_fields_keeps_password_when_omitted():
    headers = _auth_headers()
    created = client.post(
        "/routers",
        json={"name": "Edit Me", "host": "10.0.0.41", "api_username": "admin", "api_password": "orig-pass"},
        headers=headers,
    ).json()
    try:
        resp = client.put(
            f"/routers/{created['id']}",
            json={"name": "Edited", "host": "10.0.0.42", "port": 8443, "use_tls": False, "verify_tls": False},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert (body["name"], body["host"], body["port"], body["use_tls"]) == ("Edited", "10.0.0.42", 8443, False)

        from app.core.crypto import decrypt

        db = SessionLocal()
        try:
            assert decrypt(db.get(Router, created["id"]).api_password_encrypted) == "orig-pass"
        finally:
            db.close()
    finally:
        client.delete(f"/routers/{created['id']}", headers=headers)


def _capture_connection_checks(monkeypatch) -> list:
    """Replace the real router probe with a recorder; returns the list of
    (router, password) pairs the endpoint asked to check."""
    from app.api import routers as routers_api
    from app.services.mikrotik_client import ConnectionCheckResult

    calls = []

    def fake_check(router, password, lang="es"):
        calls.append((router, password))
        return ConnectionCheckResult(
            ok=True,
            message="Conectado — RouterOS 7.16, RB4011, 3 sesiones PPPoE activas.",
            routeros_version="7.16",
            board_name="RB4011",
            active_sessions=3,
        )

    monkeypatch.setattr(routers_api, "check_connection", fake_check)
    return calls


def _stored_router(name: str, password: str) -> int:
    from app.core.crypto import encrypt

    db: Session = SessionLocal()
    try:
        db.query(Router).filter(Router.name == name).delete()
        router = Router(
            name=name,
            host="10.0.0.77",
            port=8443,
            api_username="stored-user",
            api_password_encrypted=encrypt(password),
            use_tls=True,
            verify_tls=False,
        )
        db.add(router)
        db.commit()
        return router.id
    finally:
        db.close()


def test_connection_test_uses_unsaved_form_data_without_creating_a_router(monkeypatch):
    headers = _auth_headers()
    calls = _capture_connection_checks(monkeypatch)
    db: Session = SessionLocal()
    before = db.query(Router).count()
    db.close()

    resp = client.post(
        "/routers/test",
        json={
            "host": "10.9.9.9",
            "port": 80,
            "api_username": "monitor",
            "api_password": "typed-pass",
            "use_tls": False,
            "verify_tls": False,
        },
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["active_sessions"] == 3
    assert body["routeros_version"] == "7.16"
    (router, password), = calls
    assert (router.host, router.port, router.api_username, router.use_tls) == (
        "10.9.9.9",
        80,
        "monitor",
        False,
    )
    assert password == "typed-pass"
    db = SessionLocal()
    assert db.query(Router).count() == before
    db.close()


def test_connection_test_falls_back_to_stored_password_when_editing(monkeypatch):
    headers = _auth_headers()
    calls = _capture_connection_checks(monkeypatch)
    router_id = _stored_router("Edit Probe", "stored-secret")

    resp = client.post(
        "/routers/test",
        json={"host": "10.0.0.78", "api_username": "edited-user", "router_id": router_id},
        headers=headers,
    )

    assert resp.status_code == 200
    (router, password), = calls
    assert password == "stored-secret"
    assert (router.host, router.api_username) == ("10.0.0.78", "edited-user")


def test_connection_test_typed_password_wins_over_stored(monkeypatch):
    headers = _auth_headers()
    calls = _capture_connection_checks(monkeypatch)
    router_id = _stored_router("Edit Probe 2", "stored-secret")

    client.post(
        "/routers/test",
        json={"host": "h", "api_username": "u", "api_password": "new-one", "router_id": router_id},
        headers=headers,
    )

    assert calls[0][1] == "new-one"


def test_connection_test_unknown_router_id_is_404(monkeypatch):
    headers = _auth_headers()
    _capture_connection_checks(monkeypatch)

    resp = client.post(
        "/routers/test",
        json={"host": "h", "api_username": "u", "router_id": 999999},
        headers=headers,
    )

    assert resp.status_code == 404


def test_saved_router_connection_test_uses_stored_settings(monkeypatch):
    headers = _auth_headers()
    calls = _capture_connection_checks(monkeypatch)
    router_id = _stored_router("Row Probe", "row-secret")

    resp = client.post(f"/routers/{router_id}/test", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    (router, password), = calls
    assert (router.host, router.port, router.api_username) == ("10.0.0.77", 8443, "stored-user")
    assert password == "row-secret"


def test_saved_router_connection_test_unknown_id_is_404(monkeypatch):
    headers = _auth_headers()
    _capture_connection_checks(monkeypatch)

    resp = client.post("/routers/999999/test", headers=headers)

    assert resp.status_code == 404


def test_connection_test_requires_auth(monkeypatch):
    _capture_connection_checks(monkeypatch)

    assert client.post("/routers/test", json={"host": "h", "api_username": "u"}).status_code == 401
    assert client.post("/routers/1/test").status_code == 401


def test_connection_test_uses_the_installation_language(monkeypatch):
    from app.models.settings import AppSetting
    from app.services.mikrotik_client import ConnectionCheckResult

    seen = []

    def fake_check(router, password, lang="es"):
        seen.append(lang)
        return ConnectionCheckResult(ok=True, message="x")

    monkeypatch.setattr("app.api.routers.check_connection", fake_check)
    headers = _auth_headers()
    payload = {"host": "10.0.0.9", "port": 443, "api_username": "a", "api_password": "b", "use_tls": True}
    db: Session = SessionLocal()
    try:
        db.merge(AppSetting(key="language", value="en"))
        db.commit()
        assert client.post("/routers/test", json=payload, headers=headers).status_code == 200
        db.query(AppSetting).filter_by(key="language").delete()
        db.commit()
        assert client.post("/routers/test", json=payload, headers=headers).status_code == 200
    finally:
        db.query(AppSetting).filter_by(key="language").delete()
        db.commit()
        db.close()
    assert seen == ["en", "es"]
