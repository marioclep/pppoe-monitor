from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.client import PPPoEClient, SessionState, session_speed_subquery
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.schemas.client import ClientHistoryPoint, ClientOut, ClientPage
from app.services.app_settings import get_raw_retention_days
from app.services.rollup import client_hourly_history

router = APIRouter(prefix="/clients", tags=["clients"], dependencies=[Depends(get_current_user)])

MAX_HISTORY_HOURS = 90 * 24


# Current bps comes from session_state (one row per live PPPoE session,
# written by every poll, deleted when the session ends), summed per client
# -- no scan of traffic_samples.
_SESSION_SPEED = session_speed_subquery()
_RX_TOTAL = func.coalesce(AccumulationPeriod.rx_bytes_total, 0)
_TX_TOTAL = func.coalesce(AccumulationPeriod.tx_bytes_total, 0)
_RX_BPS = func.coalesce(_SESSION_SPEED.c.rx_bps, 0)
_TX_BPS = func.coalesce(_SESSION_SPEED.c.tx_bps, 0)

# On a PPPoE-server interface TX is the client's download and RX its upload.
_SORT_COLUMNS = {
    "username": PPPoEClient.username,
    "router": Router.name,
    "status": PPPoEClient.is_active,
    "current": _TX_BPS + _RX_BPS,
    "download": _TX_TOTAL,
    "upload": _RX_TOTAL,
}


def _clients_query(db: Session):
    return (
        db.query(
            PPPoEClient,
            Router.name.label("router_name"),
            _RX_TOTAL.label("rx_total"),
            _TX_TOTAL.label("tx_total"),
            _RX_BPS.label("current_rx_bps"),
            _TX_BPS.label("current_tx_bps"),
            _SESSION_SPEED.c.addresses,
        )
        .join(Router, Router.id == PPPoEClient.router_id)
        .outerjoin(
            AccumulationPeriod,
            (AccumulationPeriod.client_id == PPPoEClient.id) & (AccumulationPeriod.period_end.is_(None)),
        )
        .outerjoin(_SESSION_SPEED, _SESSION_SPEED.c.client_id == PPPoEClient.id)
    )


def _to_out(row) -> ClientOut:
    c, router_name, rx_total, tx_total, current_rx_bps, current_tx_bps, addresses = row
    return ClientOut(
        id=c.id,
        router_id=c.router_id,
        router_name=router_name,
        username=c.username,
        is_active=c.is_active,
        last_seen=c.last_seen,
        accumulated_rx_bytes=rx_total,
        accumulated_tx_bytes=tx_total,
        current_rx_bps=int(current_rx_bps) if c.is_active else 0,
        current_tx_bps=int(current_tx_bps) if c.is_active else 0,
        addresses=sorted(addresses or []) if c.is_active else [],
        last_address=c.last_address,
        last_mac=c.last_mac,
    )


def _escape_like(text: str) -> str:
    # PPPoE usernames often contain "_", a LIKE wildcard: search literally.
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=ClientPage)
def list_clients(
    router_id: int | None = None,
    active_only: bool = False,
    q: str | None = None,
    sort_by: str = Query("download", pattern="^(" + "|".join(_SORT_COLUMNS) + ")$"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = _clients_query(db)
    if router_id is not None:
        query = query.filter(PPPoEClient.router_id == router_id)
    if active_only:
        query = query.filter(PPPoEClient.is_active.is_(True))
    if q:
        # By username, or by IP: a live session's or the last known one.
        pattern = f"%{_escape_like(q.strip())}%"
        query = query.filter(
            or_(
                PPPoEClient.username.ilike(pattern, escape="\\"),
                PPPoEClient.last_address.ilike(pattern, escape="\\"),
                exists().where(
                    SessionState.client_id == PPPoEClient.id,
                    SessionState.address.ilike(pattern, escape="\\"),
                ),
            )
        )

    total = query.order_by(None).count()
    sort_column = _SORT_COLUMNS[sort_by]
    order = sort_column.asc() if dir == "asc" else sort_column.desc()
    # The id tie-break keeps pages stable when many rows share a value (e.g. 0).
    rows = (
        query.order_by(order, PPPoEClient.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ClientPage(items=[_to_out(row) for row in rows], total=total, page=page, page_size=page_size)


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: int, db: Session = Depends(get_db)):
    row = _clients_query(db).filter(PPPoEClient.id == client_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return _to_out(row)


@router.get("/{client_id}/history", response_model=list[ClientHistoryPoint])
def client_history(
    client_id: int,
    hours: int = Query(24, ge=1, le=MAX_HISTORY_HOURS),
    db: Session = Depends(get_db),
):
    client = db.get(PPPoEClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    if hours > get_raw_retention_days(db) * 24:
        # Past the 5-minute retention: one point per hour.
        return [
            ClientHistoryPoint(
                sampled_at=p.hour_start,
                rx_bytes_delta=p.rx_bytes,
                tx_bytes_delta=p.tx_bytes,
                rx_bps=p.rx_bps,
                tx_bps=p.tx_bps,
                peak_rx_bps=p.peak_rx_bps,
                peak_tx_bps=p.peak_tx_bps,
            )
            for p in client_hourly_history(db, client_id, since, now)
        ]

    samples = (
        db.query(TrafficSample)
        .filter(TrafficSample.client_id == client_id, TrafficSample.sampled_at >= since)
        .order_by(TrafficSample.sampled_at)
        .all()
    )
    return [
        ClientHistoryPoint(
            sampled_at=s.sampled_at,
            rx_bytes_delta=s.rx_bytes_delta,
            tx_bytes_delta=s.tx_bytes_delta,
            rx_bps=s.rx_bps,
            tx_bps=s.tx_bps,
        )
        for s in samples
    ]
