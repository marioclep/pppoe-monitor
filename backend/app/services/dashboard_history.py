"""Network-wide traffic and connected clients over time, from
router_poll_stats. Each bucket first averages every router's polls inside
it (so a router polled twice in a bucket isn't counted twice), then sums
the routers. Empty buckets are simply absent: the frontend draws a gap."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.poll_stats import RouterPollStat
from app.schemas.client import (
    DashboardHistory,
    DashboardHistoryPoint,
    RouterHistory,
    RouterHistoryPoint,
    RouterPoll,
    RouterPolls,
)

BUCKET_SECONDS: dict[int, int] = {24: 300, 168: 1800, 720: 7200, 2160: 21600}
# A polling cycle stamps all its routers with the cycle's start time but
# commits them one by one over several seconds: rows this recent may belong
# to a cycle still in progress, and a half-written bucket would read as a dip.
SETTLE_SECONDS = 90

_HISTORY = text(
    """
    WITH per_router AS (
        SELECT date_bin(make_interval(secs => :bucket), s.polled_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket_start,
               s.router_id,
               AVG(s.rx_bps) AS rx_bps,
               AVG(s.tx_bps) AS tx_bps,
               AVG(s.clients_connected) AS clients
        FROM router_poll_stats s
        JOIN routers r ON r.id = s.router_id
        WHERE r.enabled AND s.polled_at >= :since AND s.polled_at < :until
        GROUP BY 1, 2
    )
    SELECT bucket_start, SUM(rx_bps) AS rx_bps, SUM(tx_bps) AS tx_bps, SUM(clients) AS clients
    FROM per_router
    GROUP BY bucket_start
    ORDER BY bucket_start
    """
)


def get_dashboard_history(db: Session, hours: int, now: datetime | None = None) -> DashboardHistory:
    bucket = BUCKET_SECONDS[hours]
    until = (now or datetime.now(timezone.utc)) - timedelta(seconds=SETTLE_SECONDS)
    rows = db.execute(
        _HISTORY, {"bucket": bucket, "since": until - timedelta(hours=hours), "until": until}
    ).all()
    return DashboardHistory(
        bucket_seconds=bucket,
        points=[
            DashboardHistoryPoint(
                t=row.bucket_start,
                rx_bps=round(row.rx_bps),
                tx_bps=round(row.tx_bps),
                clients_connected=None if row.clients is None else round(row.clients),
            )
            for row in rows
        ],
    )


def get_router_polls(db: Session, router_id: int, hours: int, now: datetime | None = None) -> RouterPolls:
    """Every poll of one router in the last `hours`, as stored (no buckets):
    for the hover chart of a router row on the dashboard."""
    until = now or datetime.now(timezone.utc)
    rows = (
        db.query(RouterPollStat)
        .filter(
            RouterPollStat.router_id == router_id,
            RouterPollStat.polled_at >= until - timedelta(hours=hours),
            RouterPollStat.polled_at <= until,
        )
        .order_by(RouterPollStat.polled_at)
        .all()
    )
    return RouterPolls(
        router_id=router_id,
        points=[
            RouterPoll(t=r.polled_at, clients_connected=r.clients_connected, rx_bps=r.rx_bps, tx_bps=r.tx_bps)
            for r in rows
        ],
    )


_ROUTER_HISTORY = text(
    """
    SELECT date_bin(make_interval(secs => :bucket), polled_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket_start,
           AVG(rx_bps) AS rx_bps,
           AVG(tx_bps) AS tx_bps,
           AVG(clients_connected) AS clients,
           AVG(cpu_load) AS cpu_load,
           AVG(100.0 * (mem_total_bytes - mem_free_bytes) / NULLIF(mem_total_bytes, 0)) AS mem_percent,
           AVG(100.0 * (hdd_total_bytes - hdd_free_bytes) / NULLIF(hdd_total_bytes, 0)) AS hdd_percent
    FROM router_poll_stats
    WHERE router_id = :router_id AND polled_at >= :since AND polled_at < :until
    GROUP BY 1
    ORDER BY 1
    """
)


def _round1(value) -> float | None:
    return None if value is None else round(float(value), 1)


def get_router_history(db: Session, router_id: int, hours: int, now: datetime | None = None) -> RouterHistory:
    """One router's traffic, connected clients and resources over time, in the
    dashboard's buckets. Only this router's polls are in a bucket, so there is
    no half-written cycle to wait for."""
    bucket = BUCKET_SECONDS[hours]
    until = now or datetime.now(timezone.utc)
    rows = db.execute(
        _ROUTER_HISTORY,
        {"bucket": bucket, "router_id": router_id, "since": until - timedelta(hours=hours), "until": until},
    ).all()
    return RouterHistory(
        bucket_seconds=bucket,
        points=[
            RouterHistoryPoint(
                t=row.bucket_start,
                rx_bps=round(row.rx_bps),
                tx_bps=round(row.tx_bps),
                clients_connected=None if row.clients is None else round(row.clients),
                cpu_load=_round1(row.cpu_load),
                mem_percent=_round1(row.mem_percent),
                hdd_percent=_round1(row.hdd_percent),
            )
            for row in rows
        ],
    )
