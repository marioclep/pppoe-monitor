"""Resources of the host the monitor runs on, read every minute. Inside the
backend container /proc and the root filesystem already describe the Docker
host (containers share its kernel, and overlayfs reports the filesystem
Docker lives on), so no host mounts are needed. Buckets are averaged like the
dashboard history; empty buckets are absent and the frontend draws a gap."""
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psutil
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.server_stats import ServerStat
from app.schemas.server import ServerHistory, ServerStatsPoint
from app.services.dashboard_history import BUCKET_SECONDS

__all__ = [
    "BUCKET_SECONDS",
    "CpuMeter",
    "ServerReading",
    "cpu_meter",
    "read_server",
    "record_server_stats",
    "get_server_history",
]


@dataclass(frozen=True)
class ServerReading:
    cpu_percent: float
    mem_used_bytes: int
    mem_total_bytes: int
    disk_used_bytes: int
    disk_total_bytes: int


class CpuMeter:
    """Host CPU busy % since the previous call, with one baseline for the
    whole process. psutil.cpu_percent(None) keeps its baseline per thread,
    and the scheduler runs each job on whichever pool thread is free: there a
    reading was often a thread's first (always 0.0) or spanned many minutes."""

    def __init__(self, cpu_times: Callable[[], Any] = psutil.cpu_times) -> None:
        self._cpu_times = cpu_times
        self._last: tuple[float, float] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _busy_and_total(times: Any) -> tuple[float, float]:
        # On Linux user/nice already include guest/guest_nice: drop them from
        # the total so they aren't counted twice (as psutil does).
        total = sum(times) if isinstance(times, tuple) else sum(vars(times).values())
        total -= getattr(times, "guest", 0.0) + getattr(times, "guest_nice", 0.0)
        idle = times.idle + getattr(times, "iowait", 0.0)
        return total - idle, total

    def percent(self) -> float | None:
        """None on the first call, which only sets the baseline."""
        with self._lock:
            busy, total = self._busy_and_total(self._cpu_times())
            last, self._last = self._last, (busy, total)
        if last is None:
            return None
        elapsed = total - last[1]
        if elapsed <= 0:
            return 0.0
        return round(min(100.0, max(0.0, (busy - last[0]) / elapsed * 100)), 1)


cpu_meter = CpuMeter()


def read_server() -> ServerReading | None:
    """None until the CPU meter has a baseline (start_scheduler sets it), so
    every stored CPU value is the average of about the last minute."""
    cpu = cpu_meter.percent()
    if cpu is None:
        return None
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return ServerReading(
        cpu_percent=cpu,
        # "Used" as the memory apps can't get back: page cache is available.
        mem_used_bytes=memory.total - memory.available,
        mem_total_bytes=memory.total,
        disk_used_bytes=disk.used,
        disk_total_bytes=disk.total,
    )


def record_server_stats(db: Session, reading: ServerReading, now: datetime | None = None) -> None:
    db.add(
        ServerStat(
            sampled_at=now or datetime.now(timezone.utc),
            cpu_percent=reading.cpu_percent,
            mem_used_bytes=reading.mem_used_bytes,
            mem_total_bytes=reading.mem_total_bytes,
            disk_used_bytes=reading.disk_used_bytes,
            disk_total_bytes=reading.disk_total_bytes,
        )
    )
    db.commit()


def run_server_stats_job() -> None:
    db = SessionLocal()
    try:
        reading = read_server()
        if reading is not None:
            record_server_stats(db, reading)
    finally:
        db.close()


_HISTORY = text(
    """
    SELECT date_bin(make_interval(secs => :bucket), sampled_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket_start,
           AVG(cpu_percent) AS cpu_percent,
           AVG(mem_used_bytes) AS mem_used_bytes,
           MAX(mem_total_bytes) AS mem_total_bytes,
           AVG(disk_used_bytes) AS disk_used_bytes,
           MAX(disk_total_bytes) AS disk_total_bytes
    FROM server_stats
    WHERE sampled_at >= :since AND sampled_at < :until
    GROUP BY 1
    ORDER BY 1
    """
)


def _point(t: datetime, row) -> ServerStatsPoint:
    return ServerStatsPoint(
        t=t,
        cpu_percent=round(float(row.cpu_percent), 1),
        mem_used_bytes=round(row.mem_used_bytes),
        mem_total_bytes=row.mem_total_bytes,
        disk_used_bytes=round(row.disk_used_bytes),
        disk_total_bytes=row.disk_total_bytes,
    )


def get_server_history(db: Session, hours: int, now: datetime | None = None) -> ServerHistory:
    bucket = BUCKET_SECONDS[hours]
    until = now or datetime.now(timezone.utc)
    rows = db.execute(_HISTORY, {"bucket": bucket, "since": until - timedelta(hours=hours), "until": until}).all()
    latest = (
        db.query(ServerStat).filter(ServerStat.sampled_at <= until).order_by(ServerStat.sampled_at.desc()).first()
    )
    return ServerHistory(
        bucket_seconds=bucket,
        current=None if latest is None else _point(latest.sampled_at, latest),
        points=[_point(row.bucket_start, row) for row in rows],
    )
