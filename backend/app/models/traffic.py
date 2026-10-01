from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TrafficSample(Base):
    __tablename__ = "traffic_samples"
    __table_args__ = (
        Index("ix_traffic_samples_client_time", "client_id", "sampled_at"),
        Index("ix_traffic_samples_sampled_at", "sampled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"))
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    rx_bytes_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    rx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    is_online: Mapped[bool] = mapped_column(Boolean, default=True)


class AccumulationPeriod(Base):
    __tablename__ = "accumulation_periods"
    __table_args__ = (
        Index("ix_accum_client_active", "client_id", "period_end"),
        # At most one open (period_end NULL) accumulation period per client.
        Index(
            "uq_accum_one_open_period_per_client",
            "client_id",
            unique=True,
            postgresql_where=text("period_end IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"))
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rx_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)


class TrafficHourly(Base):
    """Per-client traffic summed per UTC hour (see app/services/rollup.py).
    Kept for retention_days, while 5-minute samples are only kept for
    raw_retention_days."""

    __tablename__ = "traffic_hourly"
    __table_args__ = (Index("ix_traffic_hourly_hour_start", "hour_start"),)

    client_id: Mapped[int] = mapped_column(
        ForeignKey("pppoe_clients.id", ondelete="CASCADE"), primary_key=True
    )
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # Highest bps among the hour's 5-minute samples.
    peak_rx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    peak_tx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
