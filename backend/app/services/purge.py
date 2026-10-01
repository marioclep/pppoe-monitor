from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.app_settings import get_raw_retention_days, get_retention_days
from app.services.rollup import ROLLUP_OVERLAP, floor_hour, get_rollup_watermark

BATCH_SIZE = 10_000

_DELETE_SAMPLES_BATCH = text(
    "DELETE FROM traffic_samples WHERE id IN "
    "(SELECT id FROM traffic_samples WHERE sampled_at < :cutoff LIMIT :batch_size)"
)
_DELETE_HOURS_BATCH = text(
    "DELETE FROM traffic_hourly WHERE (client_id, hour_start) IN "
    "(SELECT client_id, hour_start FROM traffic_hourly WHERE hour_start < :cutoff LIMIT :batch_size)"
)

_DELETE_POLL_STATS_BATCH = text(
    "DELETE FROM router_poll_stats WHERE id IN "
    "(SELECT id FROM router_poll_stats WHERE polled_at < :cutoff LIMIT :batch_size)"
)
_DELETE_SERVER_STATS_BATCH = text(
    "DELETE FROM server_stats WHERE id IN "
    "(SELECT id FROM server_stats WHERE sampled_at < :cutoff LIMIT :batch_size)"
)


@dataclass(frozen=True)
class PurgeResult:
    samples: int
    hourly: int
    poll_stats: int
    server_stats: int


def _delete_in_batches(db: Session, statement, cutoff: datetime, batch_size: int) -> int:
    """Delete in batches with a commit after each, so a large backlog never
    holds one huge transaction (and its locks) open against the poller."""
    total = 0
    while True:
        deleted = db.execute(statement, {"cutoff": cutoff, "batch_size": batch_size}).rowcount
        db.commit()
        total += deleted
        if deleted < batch_size:
            return total


def purge_old_samples(db: Session, today: datetime | None = None, batch_size: int = BATCH_SIZE) -> PurgeResult:
    today = today or datetime.now(timezone.utc)

    # 5-minute samples live raw_retention_days -- but a sample not yet summed
    # into traffic_hourly is never deleted: stop at the rollup watermark, and
    # without a (readable) watermark delete none. The rollup also re-rolls
    # [watermark - ROLLUP_OVERLAP, watermark) on every run, so those samples
    # must stay too, even when they are already older than raw_retention_days.
    # The cutoff is floored to the start of an hour, deleting whole hours only.
    samples = 0
    watermark = get_rollup_watermark(db)
    if watermark is not None:
        cutoff = min(
            floor_hour(today - timedelta(days=get_raw_retention_days(db))),
            watermark - ROLLUP_OVERLAP,
        )
        samples = _delete_in_batches(db, _DELETE_SAMPLES_BATCH, cutoff, batch_size)

    hourly_cutoff = today - timedelta(days=get_retention_days(db))
    hourly = _delete_in_batches(db, _DELETE_HOURS_BATCH, hourly_cutoff, batch_size)
    # The dashboard history covers the same span as the hourly rollup.
    poll_stats = _delete_in_batches(db, _DELETE_POLL_STATS_BATCH, hourly_cutoff, batch_size)
    # So does the server's resource history.
    server_stats = _delete_in_batches(db, _DELETE_SERVER_STATS_BATCH, hourly_cutoff, batch_size)
    return PurgeResult(samples=samples, hourly=hourly, poll_stats=poll_stats, server_stats=server_stats)


def run_purge_job() -> None:
    db = SessionLocal()
    try:
        purge_old_samples(db)
    finally:
        db.close()
