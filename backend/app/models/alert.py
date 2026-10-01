from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# On a PPPoE-server interface TX is the client's download and RX its upload.
DIRECTION_DOWNLOAD = "download"
DIRECTION_UPLOAD = "upload"
DIRECTIONS = (DIRECTION_DOWNLOAD, DIRECTION_UPLOAD)


class AlertThreshold(Base):
    __tablename__ = "alert_thresholds"
    __table_args__ = (
        # At most one threshold per client and direction...
        Index(
            "uq_alert_thresholds_client_direction",
            "client_id",
            "direction",
            unique=True,
            postgresql_where=text("client_id IS NOT NULL"),
        ),
        # ...and at most one global (client_id NULL) threshold per direction.
        Index(
            "uq_alert_thresholds_global_direction",
            "direction",
            unique=True,
            postgresql_where=text("client_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("pppoe_clients.id", ondelete="CASCADE"), nullable=True
    )
    # "download" or "upload": compared against that side of the month's usage.
    direction: Mapped[str] = mapped_column(String(16))
    bytes_threshold: Mapped[int] = mapped_column(BigInteger)
    notify_channel: Mapped[str] = mapped_column(String(32), default="email")


class AlertEvent(Base):
    """One alert: a client crossed a threshold in one direction. At most one
    per client and direction per accumulation period. Kept (with the
    threshold it crossed copied in) if the threshold is later deleted."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"))
    threshold_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_thresholds.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(16))
    threshold_bytes: Mapped[int] = mapped_column(BigInteger)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    accumulated_bytes_at_trigger: Mapped[int] = mapped_column(BigInteger)
