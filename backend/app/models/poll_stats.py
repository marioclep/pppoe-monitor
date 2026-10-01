from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RouterPollStat(Base):
    """Totals of one successful poll of one router: what the dashboard's
    history charts are drawn from (a few hundred thousand rows for 90 days,
    instead of aggregating millions of per-client samples). clients_connected
    is NULL on rows backfilled from traffic_hourly, which never stored it."""

    __tablename__ = "router_poll_stats"
    __table_args__ = (
        Index("ix_router_poll_stats_polled_at", "polled_at"),
        Index("ix_router_poll_stats_router_time", "router_id", "polled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clients_connected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    # The router's own resources, read in the same poll; NULL when that read
    # failed and on rows from before it was collected.
    cpu_load: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mem_free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mem_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hdd_free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hdd_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
