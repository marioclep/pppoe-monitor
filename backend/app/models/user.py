from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

ROLE_FULL = "full"
ROLE_READONLY = "readonly"
ROLES = (ROLE_FULL, ROLE_READONLY)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # "full" (everything) or "readonly" (sees everything, changes nothing).
    role: Mapped[str] = mapped_column(String(16), default=ROLE_FULL, server_default=ROLE_FULL)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
