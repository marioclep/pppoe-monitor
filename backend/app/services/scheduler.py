from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.database import SessionLocal
from app.services.app_settings import get_polling_interval_seconds
from app.services.polling import poll_all_routers
from app.services.purge import run_purge_job
from app.services.reset import local_timezone, run_reset_job
from app.services.rollup import run_rollup_job
from app.services.server_stats import cpu_meter, run_server_stats_job

__all__ = ["get_polling_interval_seconds", "start_scheduler", "stop_scheduler", "reschedule_polling"]


def start_scheduler() -> BackgroundScheduler:
    db = SessionLocal()
    try:
        interval = get_polling_interval_seconds(db)
    finally:
        db.close()

    tz = local_timezone()
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        poll_all_routers,
        trigger=IntervalTrigger(seconds=interval),
        id="poll_all_routers",
        replace_existing=True,
        max_instances=1,
    )
    # Due-based reset (see app/services/reset.py): check hourly, and once
    # right away at startup so a reset missed while the backend was down is
    # caught up immediately.
    scheduler.add_job(
        run_reset_job,
        trigger=CronTrigger(minute=5, timezone=tz),
        id="run_reset_job",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(tz),
    )
    # Hourly rollup of the 5-minute samples (see app/services/rollup.py), a
    # couple of minutes after the hour so most of the hour's polls have
    # committed by then; and once at startup to catch up. Every run also
    # recomputes the last rolled-up hour to pick up samples committed late.
    scheduler.add_job(
        run_rollup_job,
        trigger=CronTrigger(minute=2, timezone=tz),
        id="run_rollup_job",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(tz),
    )
    scheduler.add_job(
        run_purge_job,
        trigger=CronTrigger(hour=0, minute=15, timezone=tz),
        id="run_purge_job",
        replace_existing=True,
        max_instances=1,
    )
    # Host CPU/memory/disk for the "Servidor" page. The CPU is measured since
    # the previous reading: set the baseline so the first one covers a minute.
    cpu_meter.percent()
    scheduler.add_job(
        run_server_stats_job,
        trigger=IntervalTrigger(seconds=60),
        id="run_server_stats_job",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler) -> None:
    scheduler.shutdown(wait=False)


def reschedule_polling(scheduler: BackgroundScheduler, seconds: int) -> None:
    scheduler.reschedule_job("poll_all_routers", trigger="interval", seconds=seconds)
