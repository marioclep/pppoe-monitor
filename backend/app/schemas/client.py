from datetime import datetime

from pydantic import BaseModel


class RouterSummary(BaseModel):
    router_id: int
    router_name: str
    clients_connected: int
    current_rx_bps: int
    current_tx_bps: int
    last_polled_at: datetime | None = None


class DashboardSummary(BaseModel):
    total_clients_connected: int
    current_rx_bps: int
    current_tx_bps: int
    by_router: list[RouterSummary]
    # Router status (ok / late / no answer) is judged against this.
    polling_interval_seconds: int
    # Clients with an open accumulation period: seen since the last reset.
    clients_seen_this_period: int


class ClientOut(BaseModel):
    id: int
    router_id: int
    router_name: str
    username: str
    is_active: bool
    last_seen: datetime
    accumulated_rx_bytes: int
    accumulated_tx_bytes: int
    current_rx_bps: int
    current_tx_bps: int
    # IPs of the live sessions (one per session, sorted); empty when offline
    # or when the router doesn't expose /ppp/active.
    addresses: list[str] = []
    # Last known IP and MAC, kept after the client disconnects.
    last_address: str | None = None
    last_mac: str | None = None

    model_config = {"from_attributes": True}


class ClientPage(BaseModel):
    items: list[ClientOut]
    total: int
    page: int
    page_size: int


class ClientHistoryPoint(BaseModel):
    # A 5-minute sample (short ranges) or one UTC hour (long ranges; then
    # sampled_at is the start of the hour).
    sampled_at: datetime
    rx_bytes_delta: int
    tx_bytes_delta: int
    # Average speed over the sample's interval, or over the hour.
    rx_bps: int
    tx_bps: int
    # Hourly points only: the fastest 5-minute sample of the hour.
    peak_rx_bps: int | None = None
    peak_tx_bps: int | None = None


class DashboardHistoryPoint(BaseModel):
    # Start of the bucket (UTC).
    t: datetime
    rx_bps: int
    tx_bps: int
    # None when no router in the bucket has it (rows backfilled from hourly).
    clients_connected: int | None = None


class DashboardHistory(BaseModel):
    bucket_seconds: int
    points: list[DashboardHistoryPoint]


class RouterPoll(BaseModel):
    # One successful poll of one router (UTC).
    t: datetime
    # None on rows backfilled from hourly data, which never stored it.
    clients_connected: int | None = None
    rx_bps: int
    tx_bps: int


class RouterPolls(BaseModel):
    router_id: int
    points: list[RouterPoll]


class RouterHistoryPoint(BaseModel):
    # Start of the bucket (UTC).
    t: datetime
    rx_bps: int
    tx_bps: int
    clients_connected: int | None = None
    # Averages of the polls that could read the router's resources; None when
    # none in the bucket could.
    cpu_load: float | None = None
    mem_percent: float | None = None
    hdd_percent: float | None = None


class RouterHistory(BaseModel):
    bucket_seconds: int
    points: list[RouterHistoryPoint]
