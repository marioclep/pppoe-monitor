import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient, SessionState
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.services.alerts import PendingNotification, evaluate_alerts
from app.services.app_settings import get_polling_interval_seconds
from app.services.delta import compute_delta
from app.services.mikrotik_client import MikrotikError, MikrotikSession, fetch_active_sessions, fetch_resources
from app.services.notifications import notify

logger = logging.getLogger(__name__)


def _get_or_create_client(db: Session, router_id: int, username: str, now: datetime) -> PPPoEClient:
    client = (
        db.query(PPPoEClient)
        .filter_by(router_id=router_id, username=username)
        .first()
    )
    if client is None:
        client = PPPoEClient(router_id=router_id, username=username, first_seen=now, last_seen=now, is_active=True)
        db.add(client)
        db.flush()
    else:
        client.last_seen = now
        client.is_active = True
    return client


def _get_or_create_active_period(db: Session, client_id: int, now: datetime) -> AccumulationPeriod:
    period = (
        db.query(AccumulationPeriod)
        .filter_by(client_id=client_id, period_end=None)
        .first()
    )
    if period is None:
        period = AccumulationPeriod(client_id=client_id, period_start=now)
        db.add(period)
        db.flush()
    return period


def _compute_bps(delta_bytes: int, elapsed_seconds: float) -> int:
    if elapsed_seconds <= 0:
        return 0
    return int(delta_bytes * 8 / elapsed_seconds)


def _started_since_previous_poll(
    uptime_seconds: int, now: datetime, previous_poll_at: datetime | None, slack_seconds: int
) -> bool:
    """Whether a session seen for the first time began after the router's
    previous successful poll (plus one polling interval of slack for clock
    and scheduling jitter) -- i.e. all of its bytes are new traffic we have
    not accounted for yet. Always False on a router's very first poll."""
    if previous_poll_at is None:
        return False
    return uptime_seconds <= (now - previous_poll_at).total_seconds() + slack_seconds


def _session_delta(
    session: MikrotikSession,
    state: SessionState | None,
    now: datetime,
    previous_poll_at: datetime | None,
    slack_seconds: int,
) -> tuple[int, int, float]:
    """Bytes (rx, tx) the session transferred since it was last polled, and
    the seconds they span (0 when nothing is counted)."""
    if state is None:
        # First sighting of this session (new client, reconnect, or its
        # router was disabled). Only count its bytes if it started after our
        # previous successful poll of this router; otherwise its counters
        # include traffic from before we were watching (e.g. a months-long
        # session when monitoring starts), so they are just the baseline for
        # future deltas.
        if _started_since_previous_poll(session.uptime_seconds, now, previous_poll_at, slack_seconds):
            return session.rx_bytes, session.tx_bytes, session.uptime_seconds
        return 0, 0, 0
    rx_delta = compute_delta(session.uptime_seconds, session.rx_bytes, state.last_uptime_seconds, state.last_rx_bytes)
    tx_delta = compute_delta(session.uptime_seconds, session.tx_bytes, state.last_uptime_seconds, state.last_tx_bytes)
    # Same "session reset" condition compute_delta uses internally: with
    # uptime going backwards (an id reused after a router reboot)
    # last_poll_at is meaningless for this session, so use its own uptime.
    if session.uptime_seconds < state.last_uptime_seconds:
        return rx_delta, tx_delta, session.uptime_seconds
    return rx_delta, tx_delta, (now - state.last_poll_at).total_seconds()


def poll_router(db: Session, router: Router, cycle_at: datetime | None = None) -> None:
    """Poll one router. cycle_at is the start of the polling cycle this poll
    belongs to (poll_all_routers passes one for all routers): the router's
    RouterPollStat carries it, so a cycle always lands in one dashboard
    bucket even when it straddles a bucket boundary."""
    try:
        sessions = fetch_active_sessions(router)
    except MikrotikError:
        logger.exception("Polling failed for router %s (%s)", router.name, router.host)
        return
    # Best effort, and before taking the row lock below (it's network I/O):
    # None when the router couldn't say, and the poll goes on without it.
    resources = fetch_resources(router)

    # Re-read the router under a row lock: if it was disabled (or deleted)
    # while we were talking to it, drop this poll -- otherwise we would
    # re-activate clients that PUT /routers just marked offline. The lock
    # also serializes us against a concurrent PUT until we commit.
    router = (
        db.query(Router)
        .filter(Router.id == router.id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if router is None or not router.enabled:
        db.rollback()
        return

    now = datetime.now(timezone.utc)
    previous_poll_at = router.last_polled_at
    router.last_polled_at = now
    if resources is not None:
        router.board_name = resources.board_name or router.board_name
        router.routeros_version = resources.version or router.routeros_version
        router.uptime_seconds = resources.uptime_seconds
        router.resources_at = now
    slack_seconds = get_polling_interval_seconds(db)
    pending_notifications: list[PendingNotification] = []

    states = {state.interface_id: state for state in db.query(SessionState).filter_by(router_id=router.id)}
    live_interface_ids: set[str] = set()
    live_names_without_counters: set[str] = set()
    seen_client_ids: set[int] = set()
    router_rx_bps = router_tx_bps = 0

    sessions_by_username: dict[str, list[MikrotikSession]] = defaultdict(list)
    for session in sessions:
        sessions_by_username[session.username].append(session)

    for username, user_sessions in sessions_by_username.items():
        client = _get_or_create_client(db, router.id, username, now)
        seen_client_ids.add(client.id)

        rx_total = tx_total = rx_bps_total = tx_bps_total = 0
        measured = False
        for session in user_sessions:
            if session.address:
                client.last_address = session.address
                client.last_mac = session.mac
            if not session.has_counters:
                # Interface not found (already logged by fetch_active_sessions):
                # the session is alive but its counters are unknown this poll.
                # Keep its state -- matched by name, the only key we have.
                live_names_without_counters.add(session.interface_name)
                continue
            live_interface_ids.add(session.interface_id)

            state = states.get(session.interface_id)
            # An id now carrying another user's session (ids can be reused
            # after a router reboot) is not a continuation of the old one.
            previous = state if state is not None and state.client_id == client.id else None
            rx_delta, tx_delta, elapsed_seconds = _session_delta(
                session, previous, now, previous_poll_at, slack_seconds
            )
            rx_bps = _compute_bps(rx_delta, elapsed_seconds)
            tx_bps = _compute_bps(tx_delta, elapsed_seconds)

            if state is None:
                state = SessionState(router_id=router.id, interface_id=session.interface_id)
                db.add(state)
                states[session.interface_id] = state
            state.client_id = client.id
            state.interface_name = session.interface_name
            state.last_uptime_seconds = session.uptime_seconds
            state.last_rx_bytes = session.rx_bytes
            state.last_tx_bytes = session.tx_bytes
            state.last_poll_at = now
            state.last_rx_bps = rx_bps
            state.last_tx_bps = tx_bps
            state.address = session.address

            rx_total += rx_delta
            tx_total += tx_delta
            rx_bps_total += rx_bps
            tx_bps_total += tx_bps
            measured = True

        if not measured:
            # Online, but no session has known counters this poll: leave the
            # client's samples and totals untouched.
            continue

        db.add(
            TrafficSample(
                client_id=client.id,
                sampled_at=now,
                rx_bytes_delta=rx_total,
                tx_bytes_delta=tx_total,
                rx_bps=rx_bps_total,
                tx_bps=tx_bps_total,
                is_online=True,
            )
        )

        router_rx_bps += rx_bps_total
        router_tx_bps += tx_bps_total

        period = _get_or_create_active_period(db, client.id, now)
        period.rx_bytes_total += rx_total
        period.tx_bytes_total += tx_total

        pending_notifications.extend(evaluate_alerts(db, client.id))

    # Drop the state of sessions that ended, so a later sighting of the same
    # id is treated as a new session rather than a continuation (which would
    # corrupt the delta and bps once the counters restart).
    for interface_id, state in states.items():
        if interface_id not in live_interface_ids and state.interface_name not in live_names_without_counters:
            db.delete(state)

    stale_clients = (
        db.query(PPPoEClient)
        .filter(PPPoEClient.router_id == router.id, PPPoEClient.is_active.is_(True))
        .filter(~PPPoEClient.id.in_(seen_client_ids) if seen_client_ids else True)
        .all()
    )
    for client in stale_clients:
        client.is_active = False

    db.add(
        RouterPollStat(
            router_id=router.id,
            polled_at=cycle_at or now,
            clients_connected=len(seen_client_ids),
            rx_bps=router_rx_bps,
            tx_bps=router_tx_bps,
            cpu_load=resources.cpu_load if resources else None,
            mem_free_bytes=resources.free_memory if resources else None,
            mem_total_bytes=resources.total_memory if resources else None,
            hdd_free_bytes=resources.free_hdd if resources else None,
            hdd_total_bytes=resources.total_hdd if resources else None,
        )
    )
    db.commit()

    # Only send notifications after the poll's data has been committed
    # successfully — if an earlier client's AlertEvent were flushed but a
    # later client in the same router raised, poll_all_routers would roll
    # back the whole router and the flushed AlertEvent would disappear while
    # the notification had already gone out, causing a duplicate on the next
    # poll. Sending here, post-commit, means a notification is only ever
    # sent for an AlertEvent that is durably persisted.
    for channel, subject, message in pending_notifications:
        try:
            notify(db, subject, message, channel)
        except Exception:
            logger.exception("Failed to send alert notification (%s)", subject)


def poll_all_routers() -> None:
    cycle_at = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        routers = db.query(Router).filter_by(enabled=True).all()
        for router in routers:
            try:
                poll_router(db, router, cycle_at=cycle_at)
            except Exception:
                logger.exception("Unexpected error polling router %s (%s)", router.name, router.host)
                db.rollback()
    finally:
        db.close()
