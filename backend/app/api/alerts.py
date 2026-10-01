from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_full
from app.database import get_db
from app.models.alert import AlertEvent, AlertThreshold
from app.models.client import PPPoEClient
from app.models.router import Router
from app.schemas.alert import AlertEventOut, ThresholdCreate, ThresholdOut

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(get_current_user)])

EVENTS_LIMIT = 200


def _threshold_out(db: Session, obj: AlertThreshold) -> ThresholdOut:
    username = router_name = None
    if obj.client_id is not None:
        username, router_name = (
            db.query(PPPoEClient.username, Router.name)
            .join(Router, Router.id == PPPoEClient.router_id)
            .filter(PPPoEClient.id == obj.client_id)
            .one()
        )
    return ThresholdOut(
        id=obj.id,
        client_id=obj.client_id,
        client_username=username,
        router_name=router_name,
        direction=obj.direction,
        bytes_threshold=obj.bytes_threshold,
        notify_channel=obj.notify_channel,
    )


@router.get("/thresholds", response_model=list[ThresholdOut])
def list_thresholds(client_id: int | None = None, db: Session = Depends(get_db)):
    """All thresholds (globals first), or only this client's."""
    query = db.query(AlertThreshold)
    if client_id is not None:
        query = query.filter(AlertThreshold.client_id == client_id)
    rows = query.order_by(AlertThreshold.client_id.is_not(None), AlertThreshold.id).all()
    return [_threshold_out(db, t) for t in rows]


def _find_threshold(db: Session, client_id: int | None, direction: str) -> AlertThreshold | None:
    return db.query(AlertThreshold).filter(
        AlertThreshold.client_id == client_id, AlertThreshold.direction == direction
    ).first()


def _update_threshold(db: Session, obj: AlertThreshold, payload: ThresholdCreate, response: Response) -> ThresholdOut:
    obj.bytes_threshold = payload.bytes_threshold
    obj.notify_channel = payload.notify_channel
    db.commit()
    db.refresh(obj)
    response.status_code = status.HTTP_200_OK
    return _threshold_out(db, obj)


@router.post(
    "/thresholds",
    response_model=ThresholdOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_full)],
)
def create_threshold(payload: ThresholdCreate, response: Response, db: Session = Depends(get_db)):
    # Upsert: at most one threshold per client and direction, and one global
    # (client_id NULL) per direction -- enforced by partial unique indexes.
    # Posting again for the same scope updates the existing row.
    if payload.client_id is not None and db.get(PPPoEClient, payload.client_id) is None:
        raise HTTPException(status_code=404, detail="Client not found")

    existing = _find_threshold(db, payload.client_id, payload.direction)
    if existing is not None:
        return _update_threshold(db, existing, payload, response)

    obj = AlertThreshold(**payload.model_dump())
    db.add(obj)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request inserted the row for this scope between our
        # lookup and our insert: fall back to updating that row.
        db.rollback()
        existing = _find_threshold(db, payload.client_id, payload.direction)
        if existing is None:
            raise
        return _update_threshold(db, existing, payload, response)
    db.refresh(obj)
    return _threshold_out(db, obj)


@router.delete(
    "/thresholds/{threshold_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_full)]
)
def delete_threshold(threshold_id: int, db: Session = Depends(get_db)):
    obj = db.get(AlertThreshold, threshold_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Threshold not found")
    db.delete(obj)
    db.commit()


@router.get("/events", response_model=list[AlertEventOut])
def list_events(db: Session = Depends(get_db)):
    """The latest alerts, newest first."""
    rows = (
        db.query(AlertEvent, PPPoEClient.username, Router.id, Router.name)
        .join(PPPoEClient, PPPoEClient.id == AlertEvent.client_id)
        .join(Router, Router.id == PPPoEClient.router_id)
        .order_by(AlertEvent.triggered_at.desc(), AlertEvent.id.desc())
        .limit(EVENTS_LIMIT)
        .all()
    )
    return [
        AlertEventOut(
            id=e.id,
            client_id=e.client_id,
            client_username=username,
            router_id=router_id,
            router_name=router_name,
            direction=e.direction,
            threshold_bytes=e.threshold_bytes,
            triggered_at=e.triggered_at,
            accumulated_bytes_at_trigger=e.accumulated_bytes_at_trigger,
        )
        for e, username, router_id, router_name in rows
    ]
