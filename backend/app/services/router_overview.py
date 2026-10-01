"""The header and cards of a router's page: identity, live totals, this
month's traffic and the latest resources it reported."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.client import PPPoEClient, session_speed_subquery
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.traffic import AccumulationPeriod
from app.schemas.router import RouterOverview
from app.services.app_settings import get_polling_interval_seconds


def get_router_overview(db: Session, router_id: int) -> RouterOverview | None:
    router = db.get(Router, router_id)
    if router is None:
        return None

    speed = session_speed_subquery()
    connected, rx_bps, tx_bps = (
        db.query(
            func.count(PPPoEClient.id),
            func.coalesce(func.sum(speed.c.rx_bps), 0),
            func.coalesce(func.sum(speed.c.tx_bps), 0),
        )
        .outerjoin(speed, speed.c.client_id == PPPoEClient.id)
        .filter(PPPoEClient.router_id == router_id, PPPoEClient.is_active.is_(True))
        .one()
    )
    clients_total = db.query(func.count(PPPoEClient.id)).filter(PPPoEClient.router_id == router_id).scalar()
    month_rx, month_tx = (
        db.query(
            func.coalesce(func.sum(AccumulationPeriod.rx_bytes_total), 0),
            func.coalesce(func.sum(AccumulationPeriod.tx_bytes_total), 0),
        )
        .join(PPPoEClient, PPPoEClient.id == AccumulationPeriod.client_id)
        .filter(PPPoEClient.router_id == router_id, AccumulationPeriod.period_end.is_(None))
        .one()
    )
    latest = (
        db.query(RouterPollStat)
        .filter(
            RouterPollStat.router_id == router_id,
            (RouterPollStat.cpu_load.isnot(None))
            | (RouterPollStat.mem_total_bytes.isnot(None))
            | (RouterPollStat.hdd_total_bytes.isnot(None)),
        )
        .order_by(RouterPollStat.polled_at.desc())
        .first()
    )

    return RouterOverview(
        id=router.id,
        name=router.name,
        host=router.host,
        port=router.port,
        enabled=router.enabled,
        last_polled_at=router.last_polled_at,
        polling_interval_seconds=get_polling_interval_seconds(db),
        board_name=router.board_name,
        routeros_version=router.routeros_version,
        uptime_seconds=router.uptime_seconds,
        resources_at=router.resources_at,
        clients_connected=connected,
        clients_total=clients_total,
        current_rx_bps=int(rx_bps),
        current_tx_bps=int(tx_bps),
        month_rx_bytes=int(month_rx),
        month_tx_bytes=int(month_tx),
        resources_polled_at=latest.polled_at if latest else None,
        cpu_load=latest.cpu_load if latest else None,
        mem_free_bytes=latest.mem_free_bytes if latest else None,
        mem_total_bytes=latest.mem_total_bytes if latest else None,
        hdd_free_bytes=latest.hdd_free_bytes if latest else None,
        hdd_total_bytes=latest.hdd_total_bytes if latest else None,
    )
