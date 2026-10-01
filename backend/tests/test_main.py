from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app

client = TestClient(app)


def test_health_returns_ok_when_database_answers():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class _BrokenSession:
    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_health_returns_503_when_database_is_unavailable():
    def broken_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {"status": "error", "detail": "database unavailable"}
