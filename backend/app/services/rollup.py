"""Hourly rollup of traffic_samples into traffic_hourly.

5-minute samples are only kept for raw_retention_days (see purge.py); long
history ranges are served from these per-hour sums. A watermark in the
settings table (HOURLY_ROLLUP_KEY) records how far the rollup is complete:
every closed hour before it is in traffic_hourly, and purge never deletes a
sample at or after it.

A poll can take its `now` and commit its sample well after that -- long
enough to land in an hour already rolled up. So every run re-rolls the last
rolled-up hour too (ROLLUP_OVERLAP), picking up samples committed late;
ON CONFLICT replace keeps that idempotent.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.models.traffic import TrafficHourly, TrafficSample

logger = logging.getLogger(__name__)

HOURLY_ROLLUP_KEY = "hourly_rollup_until"
# A first rollup over days of existing samples is split into chunks, each
# committed together with its watermark, so an interruption keeps progress.
ROLLUP_CHUNK = timedelta(days=1)
# Every run also re-rolls this much before a stored watermark, to pick up
# samples committed late into an hour already marked complete.
ROLLUP_OVERLAP = timedelta(hours=1)

# One row per (client, UTC hour) with samples in [:since, :until).
_HOURLY_BUCKETS = """
    SELECT client_id,
           date_trunc('hour', sampled_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS hour_start,
           SUM(rx_bytes_delta) AS rx_bytes,
           SUM(tx_bytes_delta) AS tx_bytes,
           MAX(rx_bps) AS peak_rx_bps,
           MAX(tx_bps) AS peak_tx_bps
    FROM traffic_samples
    WHERE sampled_at >= :since AND sampled_at < :until {client_filter}
    GROUP BY 1, 2
"""

_UPSERT_HOURS = text(
    "INSERT INTO traffic_hourly (client_id, hour_start, rx_bytes, tx_bytes, peak_rx_bps, peak_tx_bps)"
    + _HOURLY_BUCKETS.format(client_filter="")
    + """
    ON CONFLICT (client_id, hour_start) DO UPDATE SET
        rx_bytes = EXCLUDED.rx_bytes,
        tx_bytes = EXCLUDED.tx_bytes,
        peak_rx_bps = EXCLUDED.peak_rx_bps,
        peak_tx_bps = EXCLUDED.peak_tx_bps
    """
)

_CLIENT_HOURS = text(_HOURLY_BUCKETS.format(client_filter="AND client_id = :client_id") + " ORDER BY 2")


def floor_hour(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def get_rollup_watermark(db: Session) -> datetime | None:
    """The instant (UTC, on the hour) up to which traffic_hourly is complete,
    or None if there is none -- or it is unreadable, which is treated the
    same: an unreadable watermark restarts the rollup from the oldest
    sample, which can overwrite the oldest hour with a partial sum if older
    samples have since been purged; purge keeps every sample meanwhile."""
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    if row is None or not row.value.strip():
        return None
    try:
        value = datetime.fromisoformat(row.value)
    except ValueError:
        value = None
    if value is None or value.tzinfo is None:
        logger.warning("Setting %s has invalid value %r; treating it as missing", HOURLY_ROLLUP_KEY, row.value)
        return None
    return floor_hour(value)


def _set_rollup_watermark(db: Session, value: datetime) -> None:
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    if row is None:
        db.add(AppSetting(key=HOURLY_ROLLUP_KEY, value=value.isoformat()))
    else:
        row.value = value.isoformat()


def run_hourly_rollup(db: Session, now: datetime | None = None) -> datetime | None:
    """Roll up every closed hour from the watermark (or the oldest sample)
    to the start of the current hour -- the hour in progress never is --
    and return the new watermark. A stored watermark is clamped to `until`
    (clock skew) and re-rolled from ROLLUP_OVERLAP before it, so a sample
    committed late into an already-rolled-up hour is not lost."""
    until = floor_hour(now or datetime.now(timezone.utc))
    since = get_rollup_watermark(db)
    if since is None:
        oldest = db.query(func.min(TrafficSample.sampled_at)).scalar()
        since = floor_hour(oldest) if oldest is not None else until
        if since >= until:
            # Nothing closed to roll up yet: everything before `until` is
            # (trivially) complete.
            _set_rollup_watermark(db, until)
            db.commit()
            return until
    else:
        since = min(since, until) - ROLLUP_OVERLAP
    while since < until:
        chunk_end = min(since + ROLLUP_CHUNK, until)
        db.execute(_UPSERT_HOURS, {"since": since, "until": chunk_end})
        _set_rollup_watermark(db, chunk_end)
        db.commit()
        since = chunk_end
    return get_rollup_watermark(db)


def run_rollup_job() -> None:
    db = SessionLocal()
    try:
        run_hourly_rollup(db)
    finally:
        db.close()


@dataclass(frozen=True)
class HourlyPoint:
    hour_start: datetime
    rx_bytes: int
    tx_bytes: int
    # Average over the hour -- or over its elapsed part, for the hour in
    # progress.
    rx_bps: int
    tx_bps: int
    peak_rx_bps: int
    peak_tx_bps: int


def client_hourly_history(db: Session, client_id: int, since: datetime, now: datetime) -> list[HourlyPoint]:
    """One point per hour with traffic, from the hour containing `since` to
    now: rolled-up hours from traffic_hourly, the rest (not rolled up yet,
    including the hour in progress) aggregated on the fly from the samples."""
    start = floor_hour(since)
    watermark = get_rollup_watermark(db)
    if watermark is not None:
        watermark = min(watermark, floor_hour(now))  # clamp clock skew
    boundary = max(start, watermark) if watermark is not None else start

    stored = (
        db.query(TrafficHourly)
        .filter(
            TrafficHourly.client_id == client_id,
            TrafficHourly.hour_start >= start,
            TrafficHourly.hour_start < boundary,
        )
        .order_by(TrafficHourly.hour_start)
        .all()
    )
    rows = [(h.hour_start, h.rx_bytes, h.tx_bytes, h.peak_rx_bps, h.peak_tx_bps) for h in stored]
    current_hour = floor_hour(now)
    live = db.execute(
        _CLIENT_HOURS,
        {"since": boundary, "until": current_hour + timedelta(hours=1), "client_id": client_id},
    )
    rows += [(r.hour_start, r.rx_bytes, r.tx_bytes, r.peak_rx_bps, r.peak_tx_bps) for r in live]

    points = []
    for hour_start, rx_bytes, tx_bytes, peak_rx_bps, peak_tx_bps in rows:
        seconds = 3600 if hour_start < current_hour else max((now - hour_start).total_seconds(), 1)
        points.append(
            HourlyPoint(
                hour_start=hour_start,
                rx_bytes=int(rx_bytes),
                tx_bytes=int(tx_bytes),
                rx_bps=int(int(rx_bytes) * 8 / seconds),
                tx_bps=int(int(tx_bytes) * 8 / seconds),
                peak_rx_bps=int(peak_rx_bps),
                peak_tx_bps=int(peak_tx_bps),
            )
        )
    return points
