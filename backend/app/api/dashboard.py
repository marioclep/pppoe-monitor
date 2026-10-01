from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.client import PPPoEClient, session_speed_subquery
from app.models.router import Router
from app.models.traffic import AccumulationPeriod
from app.schemas.client import DashboardHistory, DashboardSummary, RouterHistory, RouterPolls, RouterSummary
from app.services.app_settings import get_polling_interval_seconds
from app.services.dashboard_history import (
    BUCKET_SECONDS,
    get_dashboard_history,
    get_router_history,
    get_router_polls,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db)):
    # Current throughput comes from session_state (one row per live PPPoE
    # session, summed per client), so this never scans traffic_samples.
    # Disabled routers are excluded.
    speed = session_speed_subquery()
    rows = (
        db.query(
            Router.id,
            Router.name,
            Router.last_polled_at,
            func.count(PPPoEClient.id),
            func.coalesce(func.sum(speed.c.rx_bps), 0),
            func.coalesce(func.sum(speed.c.tx_bps), 0),
        )
        .filter(Router.enabled.is_(True))
        .outerjoin(PPPoEClient, (PPPoEClient.router_id == Router.id) & (PPPoEClient.is_active.is_(True)))
        .outerjoin(speed, speed.c.client_id == PPPoEClient.id)
        .group_by(Router.id, Router.name, Router.last_polled_at)
        .order_by(Router.name)
        .all()
    )
    by_router = [
        RouterSummary(
            router_id=router_id,
            router_name=name,
            clients_connected=clients_connected,
            current_rx_bps=int(rx_bps),
            current_tx_bps=int(tx_bps),
            last_polled_at=last_polled_at,
        )
        for router_id, name, last_polled_at, clients_connected, rx_bps, tx_bps in rows
    ]
    clients_seen = (
        db.query(func.count(AccumulationPeriod.id))
        .join(PPPoEClient, PPPoEClient.id == AccumulationPeriod.client_id)
        .join(Router, Router.id == PPPoEClient.router_id)
        .filter(AccumulationPeriod.period_end.is_(None), Router.enabled.is_(True))
        .scalar()
    )
    return DashboardSummary(
        total_clients_connected=sum(r.clients_connected for r in by_router),
        current_rx_bps=sum(r.current_rx_bps for r in by_router),
        current_tx_bps=sum(r.current_tx_bps for r in by_router),
        by_router=by_router,
        polling_interval_seconds=get_polling_interval_seconds(db),
        clients_seen_this_period=clients_seen,
    )


@router.get("/history", response_model=DashboardHistory)
def history(hours: int = Query(24), db: Session = Depends(get_db)):
    if hours not in BUCKET_SECONDS:
        raise HTTPException(status_code=422, detail=f"hours must be one of {sorted(BUCKET_SECONDS)}")
    return get_dashboard_history(db, hours)


@router.get("/routers/{router_id}/polls", response_model=RouterPolls)
def router_polls(router_id: int, hours: int = Query(24, ge=1, le=168), db: Session = Depends(get_db)):
    if db.get(Router, router_id) is None:
        raise HTTPException(status_code=404, detail="Router not found")
    return get_router_polls(db, router_id, hours)


@router.get("/routers/{router_id}/history", response_model=RouterHistory)
def router_history(router_id: int, hours: int = Query(24), db: Session = Depends(get_db)):
    if hours not in BUCKET_SECONDS:
        raise HTTPException(status_code=422, detail=f"hours must be one of {sorted(BUCKET_SECONDS)}")
    if db.get(Router, router_id) is None:
        raise HTTPException(status_code=404, detail="Router not found")
    return get_router_history(db, router_id, hours)
