from datetime import datetime

from pydantic import BaseModel


class RouterCreate(BaseModel):
    name: str
    host: str
    port: int = 443
    api_username: str
    api_password: str
    use_tls: bool = True
    verify_tls: bool = False
    enabled: bool = True


class RouterUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    api_username: str | None = None
    api_password: str | None = None
    use_tls: bool | None = None
    verify_tls: bool | None = None
    enabled: bool | None = None


class RouterOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    api_username: str
    use_tls: bool
    verify_tls: bool
    enabled: bool
    created_at: datetime
    last_polled_at: datetime | None = None

    model_config = {"from_attributes": True}


class RouterConnectionTest(BaseModel):
    host: str
    port: int = 443
    api_username: str
    # Omitted while editing = use the stored password of `router_id`.
    api_password: str | None = None
    use_tls: bool = True
    verify_tls: bool = False
    router_id: int | None = None


class RouterConnectionTestResult(BaseModel):
    ok: bool
    message: str
    routeros_version: str | None = None
    board_name: str | None = None
    active_sessions: int | None = None


class RouterOverview(BaseModel):
    """Everything the router page shows above its charts."""

    id: int
    name: str
    host: str
    port: int
    enabled: bool
    last_polled_at: datetime | None = None
    polling_interval_seconds: int
    board_name: str | None = None
    routeros_version: str | None = None
    # As read at resources_at; the page adds the time since.
    uptime_seconds: int | None = None
    resources_at: datetime | None = None
    clients_connected: int
    clients_total: int
    current_rx_bps: int
    current_tx_bps: int
    # Open accumulation periods of its clients: this month so far.
    month_rx_bytes: int
    month_tx_bytes: int
    # Latest poll that could read the resources (None before the first).
    resources_polled_at: datetime | None = None
    cpu_load: int | None = None
    mem_free_bytes: int | None = None
    mem_total_bytes: int | None = None
    hdd_free_bytes: int | None = None
    hdd_total_bytes: int | None = None
