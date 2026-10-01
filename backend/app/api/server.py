from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.schemas.server import ServerHistory
from app.services.server_stats import BUCKET_SECONDS, get_server_history

router = APIRouter(prefix="/server", tags=["server"], dependencies=[Depends(get_current_user)])


@router.get("/history", response_model=ServerHistory)
def history(hours: int = Query(24), db: Session = Depends(get_db)):
    if hours not in BUCKET_SECONDS:
        raise HTTPException(status_code=422, detail=f"hours must be one of {sorted(BUCKET_SECONDS)}")
    return get_server_history(db, hours)
