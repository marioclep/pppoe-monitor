from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

import app.services.polling as polling
from app.database import SessionLocal
from app.models.alert import AlertEvent, AlertThreshold
from app.models.client import PPPoEClient, SessionState
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.services.mikrotik_client import MikrotikError, MikrotikSession, RouterResources
from app.services.polling import poll_all_routers, poll_router


def _session(
    username: str,
    uptime_seconds: int,
    rx_bytes: int | None,
    tx_bytes: int | None,
    interface_id: str | None = None,
    interface_name: str | None = None,
    address: str | None = None,
    mac: str | None = None,
) -> MikrotikSession:
    """A PPPoE session as fetch_active_sessions returns it. By default the
    user has one session on "<pppoe-USERNAME>" with an id derived from that
    name; with rx/tx None the interface wasn't found, so there is no id."""
    if interface_name is None:
        interface_name = f"<pppoe-{username}>"
    if interface_id is None and rx_bytes is not None:
        interface_id = f"*{interface_name}"
    return MikrotikSession(
        username=username,
        uptime_seconds=uptime_seconds,
        interface_id=interface_id,
        interface_name=interface_name,
        rx_bytes=rx_bytes,
        tx_bytes=tx_bytes,
        address=address,
        mac=mac,
    )


@pytest.fixture(autouse=True)
def no_resources(monkeypatch):
    """Tests talk to no router: resources are unavailable unless a test
    serves them (see _serve_resources)."""
    monkeypatch.setattr("app.services.polling.fetch_resources", lambda r: None)


def _serve_resources(monkeypatch, resources: RouterResources | None) -> None:
    monkeypatch.setattr("app.services.polling.fetch_resources", lambda r: resources)


def _states(db: Session, client_id: int) -> list[SessionState]:
    return db.query(SessionState).filter_by(client_id=client_id).order_by(SessionState.interface_id).all()


def _serve(monkeypatch, *sessions: MikrotikSession) -> None:
    monkeypatch.setattr("app.services.polling.fetch_active_sessions", lambda r: list(sessions))


def _client(db: Session, router: Router, username: str) -> PPPoEClient:
    return db.query(PPPoEClient).filter_by(router_id=router.id, username=username).one()


def _period_totals(db: Session, client_id: int) -> tuple[int, int]:
    db.expire_all()
    period = db.query(AccumulationPeriod).filter_by(client_id=client_id, period_end=None).one()
    return period.rx_bytes_total, period.tx_bytes_total


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _delete_router_data(db: Session, router_id: int) -> None:
    db.query(RouterPollStat).filter(RouterPollStat.router_id == router_id).delete(synchronize_session=False)
    db.query(TrafficSample).filter(
        TrafficSample.client_id.in_(
            db.query(PPPoEClient.id).filter(PPPoEClient.router_id == router_id)
        )
    ).delete(synchronize_session=False)
    db.query(AccumulationPeriod).filter(
        AccumulationPeriod.client_id.in_(
            db.query(PPPoEClient.id).filter(PPPoEClient.router_id == router_id)
        )
    ).delete(synchronize_session=False)
    db.query(SessionState).filter(
        SessionState.client_id.in_(
            db.query(PPPoEClient.id).filter(PPPoEClient.router_id == router_id)
        )
    ).delete(synchronize_session=False)
    db.query(PPPoEClient).filter(PPPoEClient.router_id == router_id).delete()


@pytest.fixture
def router(db: Session):
    # Polled successfully an hour ago: sessions first seen now with uptime
    # <= 1h (+ one polling interval of slack) started since then and have
    # all their bytes counted (see the first-sighting tests below).
    obj = Router(
        name="Poll Test Router",
        host="10.0.0.9",
        api_username="admin",
        api_password_encrypted="",
        last_polled_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(obj)
    db.commit()
    yield obj
    db.rollback()
    _delete_router_data(db, obj.id)
    db.delete(obj)
    db.commit()


def test_poll_router_creates_client_and_first_sample(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("nuevo_cliente", 300, 1000, 500)],
    )

    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="nuevo_cliente").first()
    assert client is not None
    assert client.is_active is True

    sample = db.query(TrafficSample).filter_by(client_id=client.id).first()
    assert sample.rx_bytes_delta == 1000
    assert sample.tx_bytes_delta == 500

    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert period.rx_bytes_total == 1000
    assert period.tx_bytes_total == 500


def test_poll_router_accumulates_across_polls(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("c2", 300, 1000, 500)],
    )
    poll_router(db, router)

    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("c2", 600, 1800, 900)],
    )
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="c2").first()
    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert period.rx_bytes_total == 1800  # 1000 + (1800-1000)
    assert period.tx_bytes_total == 900   # 500 + (900-500)


def test_poll_router_marks_missing_clients_inactive(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("c3", 300, 1000, 500)],
    )
    poll_router(db, router)

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", lambda r: [])
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="c3").first()
    assert client.is_active is False


def test_poll_router_swallows_mikrotik_errors(db, router, monkeypatch):
    def raise_error(r):
        raise MikrotikError("router unreachable")

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", raise_error)

    poll_router(db, router)  # must not raise

    assert db.query(PPPoEClient).filter_by(router_id=router.id).count() == 0
    assert (
        db.query(TrafficSample)
        .filter(
            TrafficSample.client_id.in_(
                db.query(PPPoEClient.id).filter(PPPoEClient.router_id == router.id)
            )
        )
        .count()
        == 0
    )


def test_poll_router_resets_session_state_after_offline_gap(db, router, monkeypatch):
    """A client that drops out of /ppp/active and later reappears must be
    treated as a fresh session, not a continuation of the stale one --
    otherwise compute_delta would misread the new session's uptime/byte
    counters against leftover state from before the gap."""
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("c4", 300, 1000, 500)],
    )
    poll_router(db, router)

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", lambda r: [])
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="c4").first()
    assert _states(db, client.id) == []

    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("c4", 60, 800, 400)],
    )
    poll_router(db, router)

    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert period.rx_bytes_total == 1800  # 1000 + 800 (treated as a first observation again)
    assert period.tx_bytes_total == 900   # 500 + 400


def test_poll_router_computes_bps(db, router, monkeypatch):
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)
    times = iter([t0, t1])

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times)

    monkeypatch.setattr("app.services.polling.datetime", FixedDateTime)
    router.last_polled_at = t0 - timedelta(seconds=600)
    db.commit()

    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("bps1", 300, 1000, 500)],
    )
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="bps1").first()
    first_sample = (
        db.query(TrafficSample).filter_by(client_id=client.id).order_by(TrafficSample.id).first()
    )
    # First observation: no last_poll_at to measure against, so elapsed
    # falls back to the session's own uptime (300s).
    assert first_sample.rx_bps == int(1000 * 8 / 300)
    assert first_sample.tx_bps == int(500 * 8 / 300)

    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("bps1", 310, 1800, 900)],
    )
    poll_router(db, router)

    second_sample = (
        db.query(TrafficSample).filter_by(client_id=client.id).order_by(TrafficSample.id.desc()).first()
    )
    # Continuing session: elapsed = t1 - t0 = 10s; rx_delta=800, tx_delta=400.
    assert second_sample.rx_bps == int(800 * 8 / 10)
    assert second_sample.tx_bps == int(400 * 8 / 10)


def test_poll_router_notify_failure_after_commit_does_not_raise_and_event_persists(db, router, monkeypatch):
    """A notify() failure happens strictly after db.commit() succeeds, so it
    must not raise out of poll_router and must not affect the already
    committed AlertEvent."""
    threshold = AlertThreshold(client_id=None, direction="download", bytes_threshold=100, notify_channel="email")
    db.add(threshold)
    db.commit()
    try:
        monkeypatch.setattr(
            "app.services.polling.fetch_active_sessions",
            lambda r: [_session("alert_client2", 300, 1000, 500)],
        )
        mock_notify = MagicMock(side_effect=RuntimeError("SMTP timeout"))
        monkeypatch.setattr("app.services.polling.notify", mock_notify)

        poll_router(db, router)  # must not raise despite notify failing

        client = db.query(PPPoEClient).filter_by(router_id=router.id, username="alert_client2").first()
        assert client is not None
        events = db.query(AlertEvent).filter_by(client_id=client.id).all()
        assert len(events) == 1
        mock_notify.assert_called_once()
        assert mock_notify.call_args.args[3] == "email"  # the threshold's notify_channel
    finally:
        db.rollback()
        db.query(AlertEvent).filter_by(threshold_id=threshold.id).delete()
        db.query(AlertThreshold).filter_by(id=threshold.id).delete()
        db.commit()


def test_poll_router_rolls_back_alert_event_and_skips_notify_when_later_client_fails(db, router, monkeypatch):
    """If a later client's processing raises within the same router's poll,
    poll_all_routers rolls back the whole router (existing behavior). An
    AlertEvent flushed earlier in the loop for a different client must be
    rolled back along with everything else, and notify() must never be
    called for it -- otherwise a notification would go out for data that
    was never actually persisted, causing a duplicate alert on the next
    poll."""
    threshold = AlertThreshold(client_id=None, direction="download", bytes_threshold=100, notify_channel="email")
    db.add(threshold)
    db.commit()
    try:
        monkeypatch.setattr(
            "app.services.polling.fetch_active_sessions",
            lambda r: [
                _session("alert_client3", 300, 1000, 500),
                _session("boom_client", 300, 1000, 500),
            ],
        )

        original_get_or_create_client = polling._get_or_create_client

        def fail_for_boom(db, router_id, username, now):
            if username == "boom_client":
                raise RuntimeError("simulated failure processing session")
            return original_get_or_create_client(db, router_id, username, now)

        monkeypatch.setattr("app.services.polling._get_or_create_client", fail_for_boom)

        mock_notify = MagicMock()
        monkeypatch.setattr("app.services.polling.notify", mock_notify)

        poll_all_routers()  # must not raise -- poll_all_routers catches and rolls back

        mock_notify.assert_not_called()
        assert db.query(AlertEvent).filter_by(threshold_id=threshold.id).count() == 0
        assert db.query(PPPoEClient).filter_by(router_id=router.id, username="alert_client3").first() is None
    finally:
        db.rollback()
        db.query(AlertEvent).filter_by(threshold_id=threshold.id).delete()
        db.query(AlertThreshold).filter_by(id=threshold.id).delete()
        db.commit()


def test_poll_all_routers_continues_after_one_router_raises(db, router, monkeypatch):
    router2 = Router(
        name="Poll Test Router 2",
        host="10.0.0.10",
        api_username="admin",
        api_password_encrypted="",
        last_polled_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db.add(router2)
    db.commit()
    try:
        def fetch(r):
            if r.id == router.id:
                raise KeyError("malformed response from router")
            return [_session("c5", 300, 1000, 500)]

        monkeypatch.setattr("app.services.polling.fetch_active_sessions", fetch)

        poll_all_routers()  # must not raise, and must still poll router2

        client = db.query(PPPoEClient).filter_by(router_id=router2.id, username="c5").first()
        assert client is not None
        sample = db.query(TrafficSample).filter_by(client_id=client.id).first()
        assert sample is not None
        assert sample.rx_bytes_delta == 1000
        assert sample.tx_bytes_delta == 500
    finally:
        db.rollback()
        _delete_router_data(db, router2.id)
        db.delete(router2)
        db.commit()



def test_poll_router_leaves_state_of_session_without_counters_untouched(db, router, monkeypatch):
    """A session whose interface couldn't be found comes back with
    interface_id/rx_bytes/tx_bytes None. The client stays online, and that
    session's state (matched by interface name, the only key we have),
    samples and period totals must not be touched -- then the next poll with
    counters continues the deltas instead of taking a new baseline."""
    _serve(monkeypatch, _session("flaky", 300, 1000, 500))
    poll_router(db, router)
    client = _client(db, router, "flaky")
    (state_before,) = _states(db, client.id)
    snapshot = (state_before.last_uptime_seconds, state_before.last_rx_bytes, state_before.last_tx_bytes)
    samples_before = db.query(TrafficSample).filter_by(client_id=client.id).count()

    _serve(monkeypatch, _session("flaky", 600, None, None))
    poll_router(db, router)

    db.expire_all()
    assert db.get(PPPoEClient, client.id).is_active is True
    (state_after,) = _states(db, client.id)
    assert (state_after.last_uptime_seconds, state_after.last_rx_bytes, state_after.last_tx_bytes) == snapshot
    assert db.query(TrafficSample).filter_by(client_id=client.id).count() == samples_before
    assert _period_totals(db, client.id) == (1000, 500)

    _serve(monkeypatch, _session("flaky", 900, 1600, 800))
    poll_router(db, router)
    assert _period_totals(db, client.id) == (1600, 800)


def test_two_sessions_one_without_counters_only_counts_the_other(db, router, monkeypatch):
    """A client with two sessions where only one reports counters this poll:
    only that session's delta is counted, the other session's state is left
    untouched (matched by interface name), and a later poll with its counters
    back continues its delta from the poll-1 values rather than rebaselining."""
    _serve(
        monkeypatch,
        _session("dual.user", 100, 1000, 100, interface_id="*A"),
        _session("dual.user", 100, 2000, 200, interface_id="*B", interface_name="<pppoe-dual.user-1>"),
    )
    poll_router(db, router)
    client = _client(db, router, "dual.user")
    samples_after_poll1 = db.query(TrafficSample).filter_by(client_id=client.id).count()
    state_b_before = [s for s in _states(db, client.id) if s.interface_id == "*B"][0]
    b_snapshot = (state_b_before.last_uptime_seconds, state_b_before.last_rx_bytes, state_b_before.last_tx_bytes)

    _serve(
        monkeypatch,
        _session("dual.user", 400, 1500, 150, interface_id="*A"),
        _session("dual.user", 250, None, None, interface_id=None, interface_name="<pppoe-dual.user-1>"),
    )
    poll_router(db, router)

    db.expire_all()
    assert db.query(TrafficSample).filter_by(client_id=client.id).count() == samples_after_poll1 + 1
    latest_sample = (
        db.query(TrafficSample).filter_by(client_id=client.id).order_by(TrafficSample.id.desc()).first()
    )
    assert (latest_sample.rx_bytes_delta, latest_sample.tx_bytes_delta) == (500, 50)
    assert _period_totals(db, client.id) == (1000 + 2000 + 500, 100 + 200 + 50)
    state_b_after = [s for s in _states(db, client.id) if s.interface_id == "*B"][0]
    assert (
        state_b_after.last_uptime_seconds,
        state_b_after.last_rx_bytes,
        state_b_after.last_tx_bytes,
    ) == b_snapshot
    assert db.get(PPPoEClient, client.id).is_active is True

    _serve(
        monkeypatch,
        _session("dual.user", 700, 1700, 170, interface_id="*A"),
        _session("dual.user", 300, 2400, 240, interface_id="*B", interface_name="<pppoe-dual.user-1>"),
    )
    poll_router(db, router)

    # *B's delta is measured against its poll-1 values (2000/200), not
    # rebaselined from poll 2 (where it had no counters).
    assert _period_totals(db, client.id) == (
        1000 + 2000 + 500 + 200 + (2400 - 2000),
        100 + 200 + 50 + 20 + (240 - 200),
    )


def _seed_setting_interval(db: Session) -> int:
    from app.services.app_settings import get_polling_interval_seconds

    return get_polling_interval_seconds(db)


def test_first_poll_of_router_takes_existing_sessions_as_baseline(db, router, monkeypatch):
    """On a router's very first successful poll we can't know when sessions
    started, so their current counters are a baseline, not period traffic."""
    router.last_polled_at = None
    db.commit()
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("old_timer", 60, 5_000_000, 1_000_000)],
    )

    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="old_timer").first()
    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert (period.rx_bytes_total, period.tx_bytes_total) == (0, 0)
    sample = db.query(TrafficSample).filter_by(client_id=client.id).one()
    assert (sample.rx_bytes_delta, sample.rx_bps, sample.tx_bps) == (0, 0, 0)
    (state,) = _states(db, client.id)
    assert (state.last_rx_bytes, state.last_tx_bytes) == (5_000_000, 1_000_000)
    assert router.last_polled_at is not None


def test_first_sighting_of_long_running_session_is_baseline_then_deltas_count(db, router, monkeypatch):
    router.last_polled_at = datetime.now(timezone.utc) - timedelta(seconds=300)
    db.commit()
    slack = _seed_setting_interval(db)
    long_uptime = 300 + slack + 3600  # clearly started before the previous poll
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("veteran", long_uptime, 9_000_000, 3_000_000)],
    )
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="veteran").first()
    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert (period.rx_bytes_total, period.tx_bytes_total) == (0, 0)

    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [
            _session("veteran", long_uptime + 300, 9_000_700, 3_000_100)
        ],
    )
    poll_router(db, router)
    db.refresh(period)
    assert (period.rx_bytes_total, period.tx_bytes_total) == (700, 100)


def test_first_sighting_of_session_started_since_previous_poll_counts_all_bytes(db, router, monkeypatch):
    router.last_polled_at = datetime.now(timezone.utc) - timedelta(seconds=300)
    db.commit()
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("newcomer", 120, 4000, 2000)],
    )
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="newcomer").first()
    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert (period.rx_bytes_total, period.tx_bytes_total) == (4000, 2000)
    sample = db.query(TrafficSample).filter_by(client_id=client.id).one()
    assert sample.rx_bps == int(4000 * 8 / 120)


def test_failed_poll_does_not_update_last_polled_at(db, router, monkeypatch):
    before = router.last_polled_at

    def raise_error(r):
        raise MikrotikError("router unreachable")

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", raise_error)
    poll_router(db, router)
    db.refresh(router)
    assert router.last_polled_at == before


def test_successful_poll_updates_last_polled_at(db, router, monkeypatch):
    before = router.last_polled_at
    monkeypatch.setattr("app.services.polling.fetch_active_sessions", lambda r: [])
    poll_router(db, router)
    db.refresh(router)
    assert router.last_polled_at > before


def test_poll_router_persists_bps_on_session_state(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("bps_state", 100, 10_000, 5_000)],
    )
    poll_router(db, router)
    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="bps_state").first()
    (state,) = _states(db, client.id)
    assert state.last_rx_bps == int(10_000 * 8 / 100)
    assert state.last_tx_bps == int(5_000 * 8 / 100)


def test_poll_router_is_noop_if_router_was_disabled_meanwhile(db, router, monkeypatch):
    """A poll that was already in flight when the router got disabled must
    not re-activate its clients."""
    db.query(Router).filter(Router.id == router.id).update({Router.enabled: False})
    db.commit()
    # Simulate the in-flight poll's stale in-memory view (enabled=True).
    router.enabled = True
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [_session("late", 10, 1, 1)],
    )

    poll_router(db, router)

    assert db.query(PPPoEClient).filter_by(router_id=router.id, username="late").first() is None


def test_two_sessions_of_the_same_user_are_summed_in_sample_and_period(db, router, monkeypatch):
    """RouterOS lets a user hold two PPPoE sessions at once (e.g. castro.melina
    from two consecutive MACs). Each has its own interface and counters; the
    client's traffic is the sum of both."""
    _serve(
        monkeypatch,
        _session("castro.melina", 100, 1000, 100, interface_id="*A"),
        _session("castro.melina", 50, 3000, 300, interface_id="*B", interface_name="<pppoe-castro.melina-1>"),
    )
    poll_router(db, router)

    client = _client(db, router, "castro.melina")
    sample = db.query(TrafficSample).filter_by(client_id=client.id).one()
    assert (sample.rx_bytes_delta, sample.tx_bytes_delta) == (4000, 400)
    assert sample.rx_bps == int(1000 * 8 / 100) + int(3000 * 8 / 50)
    assert [s.interface_id for s in _states(db, client.id)] == ["*A", "*B"]

    _serve(
        monkeypatch,
        _session("castro.melina", 400, 1500, 150, interface_id="*A"),
        _session("castro.melina", 350, 3700, 370, interface_id="*B", interface_name="<pppoe-castro.melina-1>"),
    )
    poll_router(db, router)

    assert db.query(TrafficSample).filter_by(client_id=client.id).count() == 2
    assert _period_totals(db, client.id) == (4000 + 500 + 700, 400 + 50 + 70)


def test_when_one_of_two_sessions_drops_only_its_state_goes(db, router, monkeypatch):
    _serve(
        monkeypatch,
        _session("moyano.julio", 100, 1000, 100, interface_id="*A"),
        _session("moyano.julio", 100, 2000, 200, interface_id="*B", interface_name="<pppoe-moyano.julio-1>"),
    )
    poll_router(db, router)

    _serve(monkeypatch, _session("moyano.julio", 400, 1500, 150, interface_id="*A"))
    poll_router(db, router)

    client = _client(db, router, "moyano.julio")
    assert _period_totals(db, client.id) == (3000 + 500, 300 + 50)
    assert client.is_active is True
    assert [s.interface_id for s in _states(db, client.id)] == ["*A"]


def test_reconnect_with_a_new_interface_is_a_new_session(db, router, monkeypatch):
    _serve(monkeypatch, _session("reconecta", 100, 1000, 100, interface_id="*A"))
    poll_router(db, router)

    # Reconnected between polls: a new dynamic interface, counters from 0.
    _serve(monkeypatch, _session("reconecta", 30, 200, 20, interface_id="*B"))
    poll_router(db, router)

    client = _client(db, router, "reconecta")
    assert _period_totals(db, client.id) == (1200, 120)
    assert [s.interface_id for s in _states(db, client.id)] == ["*B"]


def test_same_interface_id_with_lower_uptime_counts_as_new_session(db, router, monkeypatch):
    """E.g. RouterOS handing out the same id again after a reboot."""
    _serve(monkeypatch, _session("reinicio", 3600 * 24, 50_000, 5_000, interface_id="*A"))
    poll_router(db, router)  # long-running session first seen: baseline only

    _serve(monkeypatch, _session("reinicio", 20, 700, 70, interface_id="*A"))
    poll_router(db, router)

    client = _client(db, router, "reinicio")
    assert _period_totals(db, client.id) == (700, 70)


def test_interface_id_reused_by_another_user_is_not_a_continuation(db, router, monkeypatch):
    _serve(monkeypatch, _session("ana", 20, 1000, 100, interface_id="*A"))
    poll_router(db, router)

    # After a reboot the id can come back on someone else's session, even
    # with a higher uptime: those counters are beto's, not a delta of ana's.
    # (Uptime 50 is within one polling interval: a first sighting counts it all.)
    _serve(monkeypatch, _session("beto", 50, 1500, 150, interface_id="*A"))
    poll_router(db, router)

    ana, beto = _client(db, router, "ana"), _client(db, router, "beto")
    assert _period_totals(db, ana.id) == (1000, 100)
    assert _period_totals(db, beto.id) == (1500, 150)
    assert ana.is_active is False
    assert _states(db, ana.id) == []
    assert [s.interface_id for s in _states(db, beto.id)] == ["*A"]


def test_first_sighting_rule_applies_to_each_session(db, router, monkeypatch):
    _serve(monkeypatch, _session("mixto", 100, 1000, 100, interface_id="*A"))
    poll_router(db, router)

    # *B is seen for the first time with a long uptime (its bytes predate our
    # watching it): baseline only, while *A keeps counting deltas.
    _serve(
        monkeypatch,
        _session("mixto", 400, 1500, 150, interface_id="*A"),
        _session("mixto", 3600 * 24, 9_000_000, 900_000, interface_id="*B", interface_name="<pppoe-mixto-1>"),
    )
    poll_router(db, router)

    client = _client(db, router, "mixto")
    assert _period_totals(db, client.id) == (1500, 150)
    (state_b,) = [s for s in _states(db, client.id) if s.interface_id == "*B"]
    assert (state_b.last_rx_bytes, state_b.last_rx_bps) == (9_000_000, 0)


def _poll_stats(db: Session, router_id: int) -> list[RouterPollStat]:
    db.expire_all()
    return db.query(RouterPollStat).filter_by(router_id=router_id).order_by(RouterPollStat.polled_at).all()


def test_successful_poll_writes_router_poll_stats(db, router, monkeypatch):
    _serve(monkeypatch, _session("ana", 100, 1_000, 2_000), _session("beto", 100, 5_000, 6_000))
    poll_router(db, router)
    _serve(monkeypatch, _session("ana", 400, 31_000, 62_000), _session("beto", 400, 5_000, 6_000))
    db.expire_all()
    router_row = db.get(Router, router.id)
    router_row.last_polled_at = router_row.last_polled_at - timedelta(seconds=300)
    db.commit()
    poll_router(db, router_row)

    stats = _poll_stats(db, router.id)
    assert len(stats) == 2
    last = stats[-1]
    assert last.polled_at == db.get(Router, router.id).last_polled_at
    assert last.clients_connected == 2
    ana_state = db.query(SessionState).filter_by(client_id=_client(db, router, "ana").id).one()
    beto_state = db.query(SessionState).filter_by(client_id=_client(db, router, "beto").id).one()
    assert last.rx_bps == ana_state.last_rx_bps + beto_state.last_rx_bps
    assert last.tx_bps == ana_state.last_tx_bps + beto_state.last_tx_bps
    assert last.rx_bps > 0


def test_poll_counts_a_client_with_two_sessions_once(db, router, monkeypatch):
    _serve(
        monkeypatch,
        _session("doble", 100, 1_000, 1_000, interface_id="*a", interface_name="<pppoe-doble>"),
        _session("doble", 100, 1_000, 1_000, interface_id="*b", interface_name="<pppoe-doble-1>"),
    )
    poll_router(db, router)
    assert _poll_stats(db, router.id)[-1].clients_connected == 1


def test_failed_poll_writes_no_router_poll_stats(db, router, monkeypatch):
    def boom(r):
        raise MikrotikError("unreachable")

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", boom)
    poll_router(db, router)
    assert _poll_stats(db, router.id) == []


def test_poll_stats_use_the_cycle_timestamp_when_given(db, router, monkeypatch):
    cycle_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    _serve(monkeypatch, _session("ciclo", 100, 1_000, 1_000))
    poll_router(db, router, cycle_at=cycle_at)
    assert _poll_stats(db, router.id)[-1].polled_at == cycle_at


def test_poll_all_routers_gives_every_router_the_same_cycle_timestamp(monkeypatch):
    """A cycle polls the routers one after another over several seconds; if
    each stats row carried its own time, a cycle straddling a bucket boundary
    would leave one bucket short of routers (a fake dip in the totals)."""
    calls = []
    monkeypatch.setattr(polling, "poll_router", lambda db, router, cycle_at=None: calls.append(cycle_at))
    extra = [
        Router(name=f"Cycle {n}", host=f"10.8.0.{n}", api_username="a", api_password_encrypted="") for n in (1, 2)
    ]
    session = SessionLocal()
    session.add_all(extra)
    session.commit()
    try:
        poll_all_routers()
    finally:
        session.query(Router).filter(Router.id.in_([r.id for r in extra])).delete(synchronize_session=False)
        session.commit()
        session.close()

    assert len(calls) >= 2
    assert calls[0] is not None
    assert len(set(calls)) == 1


def test_poll_stores_session_ip_and_keeps_the_last_one_after_disconnect(db, router, monkeypatch):
    _serve(monkeypatch, _session("ipuser", 100, 1_000, 1_000, address="10.1.2.3", mac="AA:BB:CC:00:11:22"))
    poll_router(db, router)
    client = _client(db, router, "ipuser")
    assert [s.address for s in _states(db, client.id)] == ["10.1.2.3"]
    assert (client.last_address, client.last_mac) == ("10.1.2.3", "AA:BB:CC:00:11:22")

    _serve(monkeypatch)  # disconnected
    poll_router(db, db.get(Router, router.id))
    db.expire_all()
    client = _client(db, router, "ipuser")
    assert client.is_active is False
    assert (client.last_address, client.last_mac) == ("10.1.2.3", "AA:BB:CC:00:11:22")


def test_poll_without_ip_keeps_the_previous_one(db, router, monkeypatch):
    _serve(monkeypatch, _session("ipkeep", 100, 1_000, 1_000, address="10.9.9.9"))
    poll_router(db, router)
    _serve(monkeypatch, _session("ipkeep", 400, 2_000, 2_000))  # /ppp/active unavailable
    poll_router(db, db.get(Router, router.id))
    db.expire_all()
    assert _client(db, router, "ipkeep").last_address == "10.9.9.9"


RESOURCES = RouterResources(
    cpu_load=12,
    free_memory=3 * 1024**3,
    total_memory=4 * 1024**3,
    free_hdd=100 * 1024**2,
    total_hdd=128 * 1024**2,
    uptime_seconds=86400,
    version="7.24.2 (stable)",
    board_name="CCR2004-1G-12S+2XS",
)


def test_poll_router_stores_router_resources_with_the_poll(db, router, monkeypatch):
    _serve(monkeypatch, _session("con_recursos", 300, 1000, 500))
    _serve_resources(monkeypatch, RESOURCES)

    poll_router(db, router)

    stat = db.query(RouterPollStat).filter_by(router_id=router.id).one()
    assert (stat.cpu_load, stat.mem_free_bytes, stat.mem_total_bytes, stat.hdd_free_bytes, stat.hdd_total_bytes) == (
        12,
        3 * 1024**3,
        4 * 1024**3,
        100 * 1024**2,
        128 * 1024**2,
    )
    db.refresh(router)
    assert (router.board_name, router.routeros_version, router.uptime_seconds) == (
        "CCR2004-1G-12S+2XS",
        "7.24.2 (stable)",
        86400,
    )
    assert router.resources_at == router.last_polled_at


def test_poll_router_without_resources_still_records_traffic(db, router, monkeypatch):
    router.board_name = "RB5009"
    router.routeros_version = "7.20"
    db.commit()
    _serve(monkeypatch, _session("sin_recursos", 300, 1000, 500))
    _serve_resources(monkeypatch, None)

    poll_router(db, router)

    stat = db.query(RouterPollStat).filter_by(router_id=router.id).one()
    assert stat.clients_connected == 1
    assert (stat.cpu_load, stat.mem_total_bytes, stat.hdd_total_bytes) == (None, None, None)
    db.refresh(router)
    # What was last read stays: a failed read doesn't erase the model or version.
    assert (router.board_name, router.routeros_version) == ("RB5009", "7.20")
