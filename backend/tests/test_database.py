from sqlalchemy import text

from app.database import engine


def test_can_connect_to_database():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
