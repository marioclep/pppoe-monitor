from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_full
from app.core.crypto import decrypt, encrypt
from app.database import get_db
from app.models.client import PPPoEClient, SessionState
from app.models.router import Router
from app.schemas.router import (
    RouterConnectionTest,
    RouterConnectionTestResult,
    RouterCreate,
    RouterOut,
    RouterOverview,
    RouterUpdate,
)
from app.services.app_settings import get_language
from app.services.mikrotik_client import check_connection
from app.services.router_overview import get_router_overview

router = APIRouter(prefix="/routers", tags=["routers"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[RouterOut])
def list_routers(db: Session = Depends(get_db)):
    return db.query(Router).order_by(Router.name).all()


@router.post("", response_model=RouterOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_full)])
def create_router(payload: RouterCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"api_password"})
    obj = Router(**data, api_password_encrypted=encrypt(payload.api_password))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def _stored_password(router: Router) -> str:
    return decrypt(router.api_password_encrypted) if router.api_password_encrypted else ""


def _get_router_or_404(db: Session, router_id: int) -> Router:
    obj = db.get(Router, router_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Router not found")
    return obj


@router.post("/test", response_model=RouterConnectionTestResult, dependencies=[Depends(require_full)])
def probe_router_connection(payload: RouterConnectionTest, db: Session = Depends(get_db)):
    """Probe a router with the (possibly unsaved) form settings. Nothing is
    stored. A failed probe is a normal result (ok=false), not an HTTP error."""
    password = payload.api_password
    if password is None and payload.router_id is not None:
        password = _stored_password(_get_router_or_404(db, payload.router_id))
    candidate = Router(**payload.model_dump(exclude={"api_password", "router_id"}))
    return asdict(check_connection(candidate, password or "", get_language(db)))


@router.post("/{router_id}/test", response_model=RouterConnectionTestResult, dependencies=[Depends(require_full)])
def probe_saved_router_connection(router_id: int, db: Session = Depends(get_db)):
    obj = _get_router_or_404(db, router_id)
    return asdict(check_connection(obj, _stored_password(obj), get_language(db)))


@router.put("/{router_id}", response_model=RouterOut, dependencies=[Depends(require_full)])
def update_router(router_id: int, payload: RouterUpdate, db: Session = Depends(get_db)):
    obj = _get_router_or_404(db, router_id)
    data = payload.model_dump(exclude_unset=True, exclude={"api_password"})
    for key, value in data.items():
        setattr(obj, key, value)
    if payload.api_password is not None:
        obj.api_password_encrypted = encrypt(payload.api_password)
    # Write the router row first: poll_router locks this row (FOR UPDATE)
    # before touching clients, so taking locks in the same order here avoids
    # deadlocks and serializes a disable against an in-flight poll.
    db.flush()
    if data.get("enabled") is False:
        # A disabled router is no longer polled, so nothing would ever mark
        # its clients offline: do it now, and drop its sessions' state so
        # they read as 0 bps and are treated as a first sighting if the
        # router is re-enabled later.
        db.query(SessionState).filter(SessionState.router_id == router_id).delete(synchronize_session=False)
        db.query(PPPoEClient).filter(PPPoEClient.router_id == router_id).update(
            {PPPoEClient.is_active: False}, synchronize_session=False
        )
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{router_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_full)])
def delete_router(router_id: int, db: Session = Depends(get_db)):
    obj = _get_router_or_404(db, router_id)
    db.delete(obj)
    db.commit()


@router.get("/{router_id}/overview", response_model=RouterOverview)
def router_overview(router_id: int, db: Session = Depends(get_db)):
    overview = get_router_overview(db, router_id)
    if overview is None:
        raise HTTPException(status_code=404, detail="Router not found")
    return overview
