from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["download", "upload"]


class ThresholdCreate(BaseModel):
    client_id: int | None = None
    direction: Direction
    bytes_threshold: int = Field(gt=0)
    # Alerts for this threshold are sent through this channel only.
    notify_channel: Literal["email", "telegram"] = "email"


class ThresholdOut(BaseModel):
    id: int
    client_id: int | None
    # The PPPoE user and its router, for client thresholds (None if global).
    client_username: str | None
    router_name: str | None
    direction: Direction
    bytes_threshold: int
    notify_channel: str


class AlertEventOut(BaseModel):
    id: int
    client_id: int
    client_username: str
    router_id: int
    router_name: str
    direction: Direction
    threshold_bytes: int
    triggered_at: datetime
    accumulated_bytes_at_trigger: int
