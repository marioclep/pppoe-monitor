from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ServerStat(Base):
    """One reading of the host the monitor runs on (CPU, memory, disk),
    taken every minute for the "Servidor" page."""

    __tablename__ = "server_stats"
    __table_args__ = (Index("ix_server_stats_sampled_at", "sampled_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cpu_percent: Mapped[float] = mapped_column(Float)
    mem_used_bytes: Mapped[int] = mapped_column(BigInteger)
    mem_total_bytes: Mapped[int] = mapped_column(BigInteger)
    disk_used_bytes: Mapped[int] = mapped_column(BigInteger)
    disk_total_bytes: Mapped[int] = mapped_column(BigInteger)
