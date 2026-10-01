from datetime import datetime

from pydantic import BaseModel


class ServerStatsPoint(BaseModel):
    # Start of the bucket (UTC); for `current`, when it was read.
    t: datetime
    cpu_percent: float
    mem_used_bytes: int
    mem_total_bytes: int
    disk_used_bytes: int
    disk_total_bytes: int


class ServerHistory(BaseModel):
    bucket_seconds: int
    # Latest reading, even if older than the range; None before the first one.
    current: ServerStatsPoint | None
    points: list[ServerStatsPoint]
