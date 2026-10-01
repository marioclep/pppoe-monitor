# Models are imported here as they're created, so Alembic autogenerate
# and Base.metadata pick them up.
from app.models.alert import AlertEvent, AlertThreshold  # noqa: F401
from app.models.client import PPPoEClient, SessionState  # noqa: F401
from app.models.poll_stats import RouterPollStat  # noqa: F401
from app.models.router import Router  # noqa: F401
from app.models.server_stats import ServerStat  # noqa: F401
from app.models.settings import AppSetting  # noqa: F401
from app.models.traffic import AccumulationPeriod, TrafficHourly, TrafficSample  # noqa: F401
from app.models.user import User  # noqa: F401
