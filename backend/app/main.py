import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.clients import router as clients_router
from app.api.dashboard import router as dashboard_router
from app.api.routers import router as routers_router
from app.api.server import router as server_router
from app.api.settings import router as settings_router
from app.api.users import router as users_router
from app.config import settings
from app.database import get_db
from app.logging_config import configure_logging
from app.services.scheduler import start_scheduler, stop_scheduler

configure_logging(settings.LOG_LEVEL)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = start_scheduler()
    app.state.scheduler = scheduler
    yield
    stop_scheduler(scheduler)


app = FastAPI(title="PPPoE Monitor", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(routers_router)
app.include_router(dashboard_router)
app.include_router(clients_router)
app.include_router(settings_router)
app.include_router(alerts_router)
app.include_router(server_router)
app.include_router(users_router)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("health check failed: database unavailable")
        return JSONResponse(
            status_code=503, content={"status": "error", "detail": "database unavailable"}
        )
    return {"status": "ok"}
