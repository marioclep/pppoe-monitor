from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.server_stats import ServerStat
from app.models.user import User
from app.services import server_stats
from app.services.server_stats import (
    CpuMeter,
    ServerReading,
    get_server_history,
    read_server,
    record_server_stats,
)

# Far in the past, so other rows in the dev database never fall in range.
NOW = datetime(2001, 1, 2, 0, 0, tzinfo=timezone.utc)
GB = 1024**3
client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.query(ServerStat).filter(ServerStat.sampled_at < datetime(2002, 1, 1, tzinfo=timezone.utc)).delete()
    session.commit()
    session.close()


def _stat(db, minutes_ago, cpu, mem_used, disk_used, mem_total=4 * GB, disk_total=40 * GB):
    db.add(
        ServerStat(
            sampled_at=NOW - timedelta(minutes=minutes_ago),
            cpu_percent=cpu,
            mem_used_bytes=mem_used,
            mem_total_bytes=mem_total,
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
        )
    )


def _times(busy, idle, iowait=0.0, guest=0.0):
    # user includes guest time on Linux, as in /proc/stat.
    return SimpleNamespace(
        user=busy + guest, nice=0.0, system=0.0, idle=idle, iowait=iowait, irq=0.0, softirq=0.0, steal=0.0,
        guest=guest, guest_nice=0.0,
    )


def test_cpu_meter_measures_since_previous_call_from_any_thread():
    samples = iter([_times(busy=100, idle=900), _times(busy=130, idle=960, iowait=10)])
    meter = CpuMeter(cpu_times=lambda: next(samples))

    assert meter.percent() is None  # first call only sets the baseline
    result = []
    worker = threading.Thread(target=lambda: result.append(meter.percent()))
    worker.start()
    worker.join()

    # 30 busy out of 30 + 60 idle + 10 iowait.
    assert result == [30.0]


def test_cpu_meter_does_not_count_guest_time_twice():
    samples = iter([_times(busy=0, idle=0), _times(busy=50, idle=50, guest=100)])
    meter = CpuMeter(cpu_times=lambda: next(samples))
    meter.percent()

    # guest is busy time already inside user: 150 busy of 200, not of 300.
    assert meter.percent() == 75.0


def test_read_server_uses_available_memory_and_root_disk(monkeypatch):
    paths = []
    fake = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(total=8 * GB, available=6 * GB),
        disk_usage=lambda path: paths.append(path) or SimpleNamespace(total=40 * GB, used=10 * GB),
    )
    monkeypatch.setattr(server_stats, "psutil", fake)
    monkeypatch.setattr(server_stats, "cpu_meter", SimpleNamespace(percent=lambda: 12.5))

    reading = read_server()

    assert reading == ServerReading(
        cpu_percent=12.5, mem_used_bytes=2 * GB, mem_total_bytes=8 * GB, disk_used_bytes=10 * GB, disk_total_bytes=40 * GB
    )
    assert paths == ["/"]


def test_read_server_is_none_until_the_cpu_has_a_baseline(monkeypatch):
    monkeypatch.setattr(server_stats, "cpu_meter", SimpleNamespace(percent=lambda: None))

    assert read_server() is None


def test_record_server_stats_stores_one_row(db):
    reading = ServerReading(
        cpu_percent=7.0, mem_used_bytes=1 * GB, mem_total_bytes=4 * GB, disk_used_bytes=5 * GB, disk_total_bytes=40 * GB
    )

    record_server_stats(db, reading, now=NOW)

    rows = db.query(ServerStat).filter(ServerStat.sampled_at == NOW).all()
    assert [(r.cpu_percent, r.mem_used_bytes, r.disk_total_bytes) for r in rows] == [(7.0, 1 * GB, 40 * GB)]


def test_history_averages_each_bucket_and_reports_latest_as_current(db):
    _stat(db, 12, cpu=10, mem_used=1 * GB, disk_used=4 * GB)  # bucket NOW-15m
    _stat(db, 11, cpu=30, mem_used=3 * GB, disk_used=6 * GB)  # same bucket
    _stat(db, 2, cpu=50, mem_used=2 * GB, disk_used=7 * GB)  # bucket NOW-5m
    db.commit()

    history = get_server_history(db, 24, now=NOW)

    assert history.bucket_seconds == 300
    assert [(p.t, p.cpu_percent, p.mem_used_bytes, p.disk_used_bytes) for p in history.points] == [
        (NOW - timedelta(minutes=15), 20.0, 2 * GB, 5 * GB),
        (NOW - timedelta(minutes=5), 50.0, 2 * GB, 7 * GB),
    ]
    assert history.points[0].mem_total_bytes == 4 * GB
    assert history.current is not None
    assert (history.current.t, history.current.cpu_percent) == (NOW - timedelta(minutes=2), 50.0)


def test_history_ignores_rows_outside_the_range(db):
    _stat(db, 25 * 60, cpu=99, mem_used=1, disk_used=1)
    db.commit()

    history = get_server_history(db, 24, now=NOW)

    assert history.points == []
    # The latest reading is shown even when it is older than the range.
    assert history.current is not None and history.current.cpu_percent == 99


def test_history_without_rows_has_no_current(db):
    history = get_server_history(db, 24, now=datetime(1990, 1, 1, tzinfo=timezone.utc))

    assert history.points == []
    assert history.current is None


def _auth_headers() -> dict:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "server-tester").delete()
        db.add(User(username="server-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('server-tester')}"}


def test_history_endpoint_requires_auth():
    assert client.get("/server/history").status_code == 401


def test_history_endpoint_rejects_unknown_range():
    assert client.get("/server/history?hours=5", headers=_auth_headers()).status_code == 422


def test_history_endpoint_returns_points():
    response = client.get("/server/history?hours=168", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["bucket_seconds"] == 1800
    assert isinstance(body["points"], list)
    assert "current" in body
