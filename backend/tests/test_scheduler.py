import threading

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.services.scheduler import (
    get_polling_interval_seconds,
    reschedule_polling,
    start_scheduler,
    stop_scheduler,
)


@pytest.fixture(autouse=True)
def reset_job_calls(monkeypatch):
    """start_scheduler runs the reset job immediately; never let it touch
    the dev database from these tests. Records calls instead."""
    called = threading.Event()
    monkeypatch.setattr("app.services.scheduler.run_reset_job", called.set)
    return called


@pytest.fixture(autouse=True)
def rollup_job_calls(monkeypatch):
    """start_scheduler runs the rollup job immediately; never let it touch
    the dev database from these tests. Records calls instead."""
    called = threading.Event()
    monkeypatch.setattr("app.services.scheduler.run_rollup_job", called.set)
    return called


def test_get_polling_interval_seconds_reads_setting():
    db: Session = SessionLocal()
    try:
        row = db.get(AppSetting, "polling_interval_seconds")
        original = row.value
        row.value = "120"
        db.commit()

        assert get_polling_interval_seconds(db) == 120
    finally:
        row.value = original
        db.commit()
        db.close()


def test_start_and_stop_scheduler_registers_polling_job():
    scheduler = start_scheduler()
    try:
        job_ids = [job.id for job in scheduler.get_jobs()]
        assert "poll_all_routers" in job_ids
    finally:
        stop_scheduler(scheduler)


def test_reschedule_polling_updates_job_interval():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: None,
        trigger=IntervalTrigger(seconds=300),
        id="poll_all_routers",
    )
    scheduler.start(paused=True)
    try:
        reschedule_polling(scheduler, 120)
        job = scheduler.get_job("poll_all_routers")
        assert job.trigger.interval.total_seconds() == 120
    finally:
        scheduler.shutdown(wait=False)


def test_get_polling_interval_seconds_falls_back_on_garbage_and_scheduler_still_starts():
    db: Session = SessionLocal()
    try:
        row = db.get(AppSetting, "polling_interval_seconds")
        original = row.value
        for garbage in ("None", "", "abc", "5"):
            row.value = garbage
            db.commit()
            assert get_polling_interval_seconds(db) == 300, garbage

        row.value = "None"
        db.commit()
        scheduler = start_scheduler()  # previously crashed with int("None")
        try:
            job = scheduler.get_job("poll_all_routers")
            assert job.trigger.interval.total_seconds() == 300
        finally:
            stop_scheduler(scheduler)
    finally:
        row.value = original
        db.commit()
        db.close()


def test_scheduler_runs_reset_check_at_startup_and_hourly(reset_job_calls):
    scheduler = start_scheduler()
    try:
        assert reset_job_calls.wait(timeout=5), "reset job did not run at startup"
        job = scheduler.get_job("run_reset_job")
        assert str(job.trigger.timezone) == "America/Argentina/Cordoba"
        # Hourly: next run is at minute 5 of some hour, < 1h away.
        assert job.next_run_time.minute == 5
    finally:
        stop_scheduler(scheduler)


def test_scheduler_runs_rollup_at_startup_and_hourly(rollup_job_calls):
    scheduler = start_scheduler()
    try:
        assert rollup_job_calls.wait(timeout=5), "rollup job did not run at startup"
        job = scheduler.get_job("run_rollup_job")
        assert job.next_run_time.minute == 2
    finally:
        stop_scheduler(scheduler)


def test_scheduler_records_server_stats_every_minute():
    scheduler = start_scheduler()
    try:
        job = scheduler.get_job("run_server_stats_job")
        assert job.trigger.interval.total_seconds() == 60
    finally:
        stop_scheduler(scheduler)
