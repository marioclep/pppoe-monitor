from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PPPoEClient(Base):
    __tablename__ = "pppoe_clients"
    __table_args__ = (UniqueConstraint("router_id", "username", name="uq_client_router_username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(String(128), index=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Client IP and MAC of the most recent session seen with them. Kept after
    # the client disconnects, so the list still shows its last IP.
    last_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_mac: Mapped[str | None] = mapped_column(String(17), nullable=True)


class SessionState(Base):
    """Counters of one live PPPoE session as of the last poll. A user can
    hold several sessions at once; each has its own row, keyed by the
    RouterOS id of its dynamic interface (names are reused on reconnect,
    ids are not). The row is deleted when the session disappears."""

    __tablename__ = "session_state"
    __table_args__ = (UniqueConstraint("router_id", "interface_id", name="uq_session_state_router_interface"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[str] = mapped_column(String(32))
    # Only for logs/diagnostics, and to recognize a live session whose
    # interface (and so its id) was missing from one poll.
    interface_name: Mapped[str] = mapped_column(String(128), default="")
    last_uptime_seconds: Mapped[int] = mapped_column(Integer, default=0)
    last_rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Throughput of this session measured by the most recent poll. A
    # client's current bps is the sum over its sessions; with no sessions
    # (offline) it reads as 0.
    last_rx_bps: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    last_tx_bps: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    # Client IP of this session (from /ppp/active), when known.
    address: Mapped[str | None] = mapped_column(String(45), nullable=True)


def session_speed_subquery():
    """Current speed per client: the sum of its live sessions' last bps.
    A client without sessions (offline) has no row here -- coalesce to 0."""
    return (
        select(
            SessionState.client_id.label("client_id"),
            func.sum(SessionState.last_rx_bps).label("rx_bps"),
            func.sum(SessionState.last_tx_bps).label("tx_bps"),
            # Client IPs of its live sessions (NULL when none is known).
            func.array_agg(SessionState.address).filter(SessionState.address.isnot(None)).label("addresses"),
        )
        .group_by(SessionState.client_id)
        .subquery("session_speed")
    )
