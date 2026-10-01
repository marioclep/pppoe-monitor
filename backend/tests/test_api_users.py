import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.user import User

client = TestClient(app)

# Routes any logged-in user may call with POST/PUT/DELETE.
OPEN_WRITE_ROUTES = {("POST", "/auth/login"), ("POST", "/auth/change-password")}


def _make_user(username: str, role: str = "full", password: str = "password-123") -> User:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        user = User(username=username, password_hash=hash_password(password), role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _headers(username: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(username)}"}


def _only_users(*usernames: str) -> None:
    """Leave only these users in the table (for the last-full-user rules)."""
    db = SessionLocal()
    try:
        db.query(User).filter(User.username.notin_(usernames)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _write_routes() -> list[tuple[str, str]]:
    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            if (method, route.path) not in OPEN_WRITE_ROUTES:
                routes.append((method, route.path))
    return sorted(routes)


def test_there_are_write_routes_to_check():
    assert ("POST", "/routers") in _write_routes()
    assert ("PUT", "/settings") in _write_routes()
    assert ("POST", "/users") in _write_routes()


@pytest.mark.parametrize("method,path", _write_routes())
def test_readonly_user_gets_403_on_every_write_route(method, path):
    _make_user("ro-writer", role="readonly")
    url = re.sub(r"\{[^}]+\}", "999999", path)
    r = client.request(method, url, json={}, headers=_headers("ro-writer"))
    assert r.status_code == 403
    assert r.json() == {"detail": "Permiso insuficiente"}


@pytest.mark.parametrize("path", ["/routers", "/settings", "/alerts/thresholds", "/dashboard/summary"])
def test_readonly_user_can_read(path):
    _make_user("ro-reader", role="readonly")
    r = client.get(path, headers=_headers("ro-reader"))
    assert r.status_code == 200


def test_me_returns_username_and_role():
    _make_user("me-ro", role="readonly")
    r = client.get("/auth/me", headers=_headers("me-ro"))
    assert r.status_code == 200
    assert r.json() == {"username": "me-ro", "role": "readonly"}


def test_me_requires_auth():
    assert client.get("/auth/me").status_code == 401


# --- change own password ---------------------------------------------------


def test_change_password_with_right_current_password():
    _make_user("changer", role="readonly", password="old-password-1")
    r = client.post(
        "/auth/change-password",
        json={"current_password": "old-password-1", "new_password": "new-password-1"},
        headers=_headers("changer"),
    )
    assert r.status_code == 204
    login = client.post("/auth/login", json={"username": "changer", "password": "new-password-1"})
    assert login.status_code == 200
    old = client.post("/auth/login", json={"username": "changer", "password": "old-password-1"})
    assert old.status_code == 401


def test_change_password_with_wrong_current_password_returns_400():
    _make_user("changer2", password="old-password-1")
    r = client.post(
        "/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-password-1"},
        headers=_headers("changer2"),
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "La contraseña actual no es correcta"}


def test_change_password_rejects_short_new_password():
    _make_user("changer3", password="old-password-1")
    r = client.post(
        "/auth/change-password",
        json={"current_password": "old-password-1", "new_password": "short"},
        headers=_headers("changer3"),
    )
    assert r.status_code == 422


def test_change_password_wrong_attempts_are_throttled():
    _make_user("changer4", password="old-password-1")
    for _ in range(5):
        r = client.post(
            "/auth/change-password",
            json={"current_password": "wrong", "new_password": "new-password-1"},
            headers=_headers("changer4"),
        )
        assert r.status_code == 400
    r = client.post(
        "/auth/change-password",
        json={"current_password": "old-password-1", "new_password": "new-password-1"},
        headers=_headers("changer4"),
    )
    assert r.status_code == 429


# --- user management -------------------------------------------------------


def test_full_user_creates_and_lists_users():
    _make_user("boss")
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "viewer").delete()
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/users",
        json={"username": "viewer", "password": "viewer-pass-1", "role": "readonly"},
        headers=_headers("boss"),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "viewer"
    assert body["role"] == "readonly"
    assert "password" not in body and "password_hash" not in body

    listed = client.get("/users", headers=_headers("boss")).json()
    assert {"viewer", "boss"} <= {u["username"] for u in listed}
    assert client.post("/auth/login", json={"username": "viewer", "password": "viewer-pass-1"}).status_code == 200


def test_readonly_user_cannot_list_users():
    _make_user("ro-lister", role="readonly")
    assert client.get("/users", headers=_headers("ro-lister")).status_code == 403


def test_create_user_with_existing_username_returns_409():
    _make_user("boss")
    _make_user("dup")
    r = client.post(
        "/users", json={"username": "dup", "password": "password-123", "role": "full"}, headers=_headers("boss")
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "x1", "password": "password-123", "role": "admin"},
        {"username": "x1", "password": "short", "role": "full"},
        {"username": "", "password": "password-123", "role": "full"},
    ],
)
def test_create_user_validates_input(payload):
    _make_user("boss")
    assert client.post("/users", json=payload, headers=_headers("boss")).status_code == 422


def test_full_user_changes_role_and_resets_password():
    _make_user("boss")
    target = _make_user("target", role="readonly")
    r = client.put(
        f"/users/{target.id}", json={"role": "full", "password": "reset-pass-1"}, headers=_headers("boss")
    )
    assert r.status_code == 200
    assert r.json()["role"] == "full"
    assert client.post("/auth/login", json={"username": "target", "password": "reset-pass-1"}).status_code == 200


def test_role_change_applies_to_existing_token():
    _make_user("boss")
    target = _make_user("demoted", role="full")
    client.put(f"/users/{target.id}", json={"role": "readonly"}, headers=_headers("boss"))
    r = client.put("/settings", json={}, headers=_headers("demoted"))
    assert r.status_code == 403


def test_full_user_deletes_user_and_its_token_stops_working():
    _make_user("boss")
    target = _make_user("goner", role="readonly")
    assert client.delete(f"/users/{target.id}", headers=_headers("boss")).status_code == 204
    assert client.get("/auth/me", headers=_headers("goner")).status_code == 401


def test_unknown_user_returns_404():
    _make_user("boss")
    assert client.put("/users/999999", json={"role": "full"}, headers=_headers("boss")).status_code == 404
    assert client.delete("/users/999999", headers=_headers("boss")).status_code == 404


def test_user_cannot_delete_itself():
    boss = _make_user("boss")
    _make_user("boss2")
    r = client.delete(f"/users/{boss.id}", headers=_headers("boss"))
    assert r.status_code == 400
    assert r.json() == {"detail": "No podés borrar tu propio usuario"}


def test_last_full_user_cannot_be_demoted():
    boss = _make_user("boss")
    _make_user("ro-only", role="readonly")
    _only_users("boss", "ro-only")
    r = client.put(f"/users/{boss.id}", json={"role": "readonly"}, headers=_headers("boss"))
    assert r.status_code == 409
    assert r.json() == {"detail": "Tiene que quedar al menos un usuario con acceso completo"}


def test_demoting_a_full_user_is_allowed_when_another_remains():
    _make_user("boss")
    other = _make_user("boss2")
    _only_users("boss", "boss2")
    r = client.put(f"/users/{other.id}", json={"role": "readonly"}, headers=_headers("boss"))
    assert r.status_code == 200


def test_new_users_default_to_full_role_in_model():
    """Users created without a role (e.g. by the CLI) get full access."""
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "no-role").delete()
        db.commit()
        user = User(username="no-role", password_hash=hash_password("x"))
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.role == "full"
    finally:
        db.close()
