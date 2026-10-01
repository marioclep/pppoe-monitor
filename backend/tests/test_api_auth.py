from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models.user import User

client = TestClient(app)


def _make_user(db: Session, username: str, password: str) -> User:
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return user


def test_login_with_valid_credentials_returns_token():
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "testadmin").delete()
        db.commit()
        _make_user(db, "testadmin", "s3cret")
    finally:
        db.close()

    response = client.post("/auth/login", json={"username": "testadmin", "password": "s3cret"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_with_invalid_credentials_returns_401():
    response = client.post("/auth/login", json={"username": "nope", "password": "wrong"})
    assert response.status_code == 401


def test_get_current_user_without_auth_header_returns_401():
    """Test that missing Authorization header returns 401, not 403."""
    # Create a test app with a protected route
    test_app = FastAPI()
    test_router = APIRouter()

    @test_router.get("/protected")
    def protected_route(current_user: User = Depends(get_current_user)):
        return {"username": current_user.username}

    test_app.include_router(test_router)
    test_client = TestClient(test_app)

    # Request without Authorization header should return 401
    response = test_client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def test_get_current_user_with_invalid_token_returns_401():
    """Test that invalid token returns 401."""
    test_app = FastAPI()
    test_router = APIRouter()

    @test_router.get("/protected")
    def protected_route(current_user: User = Depends(get_current_user)):
        return {"username": current_user.username}

    test_app.include_router(test_router)
    test_client = TestClient(test_app)

    # Request with invalid token should return 401
    response = test_client.get("/protected", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def _reset_user(username: str, password: str) -> None:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        _make_user(db, username, password)
    finally:
        db.close()


def test_login_is_blocked_after_five_failures_even_with_the_right_password():
    _reset_user("throttled", "right-password")
    for _ in range(5):
        r = client.post("/auth/login", json={"username": "throttled", "password": "wrong"})
        assert r.status_code == 401

    r = client.post("/auth/login", json={"username": "throttled", "password": "wrong"})
    assert r.status_code == 429
    assert r.json() == {"detail": "Too many failed login attempts, try again later"}

    r = client.post("/auth/login", json={"username": "throttled", "password": "right-password"})
    assert r.status_code == 429


def test_successful_login_resets_the_failure_count():
    _reset_user("resetme", "right-password")
    for _ in range(4):
        client.post("/auth/login", json={"username": "resetme", "password": "wrong"})
    assert client.post(
        "/auth/login", json={"username": "resetme", "password": "right-password"}
    ).status_code == 200

    for _ in range(4):
        r = client.post("/auth/login", json={"username": "resetme", "password": "wrong"})
        assert r.status_code == 401


def test_failed_login_is_logged_with_user_and_ip(caplog):
    with caplog.at_level("WARNING", logger="app.api.auth"):
        client.post("/auth/login", json={"username": "ghost", "password": "wrong"})
    assert "login failed user=ghost ip=testclient" in caplog.text
