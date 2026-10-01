import httpx
import pytest

from app.models.router import Router
from app.services.mikrotik_client import (
    MikrotikError,
    MikrotikSession,
    RouterResources,
    fetch_active_sessions,
    fetch_resources,
    parse_uptime,
)


def _make_router() -> Router:
    return Router(
        id=1,
        name="Test Router",
        host="10.0.0.1",
        port=443,
        api_username="admin",
        api_password_encrypted="",
        use_tls=True,
        verify_tls=False,
        enabled=True,
    )


def test_parse_uptime_handles_days_hours_minutes_seconds():
    assert parse_uptime("1d02:03:04") == 1 * 86400 + 2 * 3600 + 3 * 60 + 4


def test_parse_uptime_handles_hours_minutes_seconds_only():
    assert parse_uptime("02:03:04") == 2 * 3600 + 3 * 60 + 4


def test_parse_uptime_handles_routeros7_hours_minutes_seconds():
    assert parse_uptime("3h25m10s") == 3 * 3600 + 25 * 60 + 10


def test_parse_uptime_handles_routeros7_weeks_days_hours_minutes_seconds():
    assert parse_uptime("1w2d3h4m5s") == (
        1 * 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    )


def test_parse_uptime_handles_routeros7_seconds_only():
    assert parse_uptime("45s") == 45


def test_parse_uptime_raises_mikrotik_error_on_garbage():
    with pytest.raises(MikrotikError):
        parse_uptime("garbage")


def _fake_routeros(
    sessions_payload,
    interfaces_payload,
    requested: list | None = None,
    ppp_active_payload=None,
    ppp_active_status: int = 200,
):
    """Fake httpx.Client.get for /rest/interface/pppoe-server (one row per
    active PPPoE session), /rest/interface (byte counters) and /rest/ppp/active
    (client IPs; empty unless given). A payload given as bytes is sent raw,
    e.g. to test non-UTF-8 text."""

    def fake_get(self, url, **kwargs):
        if requested is not None:
            requested.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/rest/ppp/active"):
            return httpx.Response(ppp_active_status, json=ppp_active_payload or [], request=request)
        if url.endswith("/rest/interface/pppoe-server"):
            payload = sessions_payload
        elif url.endswith("/rest/interface"):
            payload = interfaces_payload
        else:
            raise AssertionError(f"unexpected url {url}")
        if isinstance(payload, bytes):
            return httpx.Response(200, content=payload, request=request)
        return httpx.Response(200, json=payload, request=request)

    return fake_get


def test_fetch_active_sessions_reads_pppoe_server_sessions_with_interface_counters(monkeypatch):
    requested: list[str] = []
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [{".id": "*1A", "name": "<pppoe-cliente1>", "user": "cliente1", "uptime": "10m"}],
            [
                {".id": "*1", "name": "ether1", "rx-byte": "9", "tx-byte": "9"},
                {".id": "*80020DCC", "name": "<pppoe-cliente1>", "rx-byte": "1000", "tx-byte": "2000"},
            ],
            requested,
        ),
    )

    sessions = fetch_active_sessions(_make_router())

    assert sessions == [
        MikrotikSession(
            username="cliente1",
            uptime_seconds=600,
            interface_id="*80020DCC",
            interface_name="<pppoe-cliente1>",
            rx_bytes=1000,
            tx_bytes=2000,
        )
    ]
    assert sessions[0].has_counters


def test_fetch_active_sessions_matches_interface_name_with_numeric_suffix(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [{".id": "*2", "name": "<pppoe-amado.lorena-1>", "user": "amado.lorena", "uptime": "2h"}],
            [{".id": "*8002", "name": "<pppoe-amado.lorena-1>", "rx-byte": "500", "tx-byte": "7000"}],
        ),
    )

    (session,) = fetch_active_sessions(_make_router())

    assert (session.username, session.interface_id, session.rx_bytes, session.tx_bytes) == (
        "amado.lorena",
        "*8002",
        500,
        7000,
    )


def test_fetch_active_sessions_returns_every_session_of_the_same_user(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [
                {".id": "*3", "name": "<pppoe-castro.melina>", "user": "castro.melina", "uptime": "3d"},
                {".id": "*4", "name": "<pppoe-castro.melina-1>", "user": "castro.melina", "uptime": "5m"},
            ],
            [
                {".id": "*A1", "name": "<pppoe-castro.melina>", "rx-byte": "70000", "tx-byte": "80000"},
                {".id": "*A2", "name": "<pppoe-castro.melina-1>", "rx-byte": "10", "tx-byte": "20"},
            ],
        ),
    )

    sessions = fetch_active_sessions(_make_router())

    assert [(s.username, s.interface_id, s.rx_bytes) for s in sessions] == [
        ("castro.melina", "*A1", 70000),
        ("castro.melina", "*A2", 10),
    ]


def test_fetch_active_sessions_without_interface_row_has_unknown_counters(monkeypatch, caplog):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [
                {".id": "*5", "name": "<pppoe-matched>", "user": "matched", "uptime": "10m"},
                {".id": "*6", "name": "<pppoe-ghost>", "user": "ghost", "uptime": "5m"},
            ],
            [{".id": "*B1", "name": "<pppoe-matched>", "rx-byte": "10", "tx-byte": "20"}],
        ),
    )

    with caplog.at_level("WARNING", logger="app.services.mikrotik_client"):
        sessions = fetch_active_sessions(_make_router())

    by_name = {s.username: s for s in sessions}
    assert by_name["matched"].has_counters
    ghost = by_name["ghost"]
    # Unknown counters are None (not 0) so polling can skip them; the
    # interface name is still known, the RouterOS id is not.
    assert (ghost.interface_id, ghost.interface_name, ghost.rx_bytes, ghost.tx_bytes) == (
        None,
        "<pppoe-ghost>",
        None,
        None,
    )
    assert not ghost.has_counters
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "<pppoe-ghost>" in warnings[0]
    assert "Test Router" in warnings[0]


def test_fetch_active_sessions_skips_static_bindings_and_rows_without_user(monkeypatch, caplog):
    # RouterOS can also list STATIC PPPoE-server interface bindings
    # (/interface pppoe-server add name=... user=...). When the client is
    # disconnected such a row has no uptime (and running == "false"); these
    # are not active sessions and must not abort the poll.
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [
                {".id": "*1A", "name": "<pppoe-cliente1>", "user": "cliente1", "uptime": "10m"},
                {".id": "*9", "name": "<pppoe-estatico>", "user": "estatico", "running": "false"},
                {".id": "*10", "name": "<pppoe-nouser>", "uptime": "5m"},
            ],
            [
                {".id": "*80020DCC", "name": "<pppoe-cliente1>", "rx-byte": "1000", "tx-byte": "2000"},
            ],
        ),
    )

    with caplog.at_level("WARNING", logger="app.services.mikrotik_client"):
        sessions = fetch_active_sessions(_make_router())

    assert [s.username for s in sessions] == ["cliente1"]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Test Router" in warnings[0]
    assert "2" in warnings[0]


def test_fetch_active_sessions_raises_mikrotik_error_on_http_failure(monkeypatch):
    def fake_get(self, url, **kwargs):
        raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    with pytest.raises(MikrotikError):
        fetch_active_sessions(_make_router())


def test_fetch_active_sessions_accepts_non_utf8_router_text(monkeypatch):
    # RouterOS sends user-entered text (comments, names) in the router's
    # legacy 8-bit encoding, not UTF-8: 0xED is "í" in Latin-1/Windows-1252.
    sessions_body = (
        b'[{".id":"*7","name":"<pppoe-diaz.romina>","user":"diaz.romina",'
        b'"uptime":"3d13h2m18s","comment":"D\xedaz Romina"}]'
    )
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            sessions_body,
            [{".id": "*C1", "name": "<pppoe-diaz.romina>", "rx-byte": "10", "tx-byte": "20"}],
        ),
    )

    sessions = fetch_active_sessions(_make_router())

    assert [(s.username, s.rx_bytes) for s in sessions] == [("diaz.romina", 10)]


def test_fetch_active_sessions_keeps_utf8_usernames_intact(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [{".id": "*8", "name": "<pppoe-muñoz>", "user": "muñoz", "uptime": "1h"}],
            [{".id": "*D1", "name": "<pppoe-muñoz>", "rx-byte": "1", "tx-byte": "2"}],
        ),
    )

    assert fetch_active_sessions(_make_router())[0].username == "muñoz"


def test_fetch_active_sessions_pairs_each_session_with_its_ip_by_user_and_mac(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [
                {"name": "<pppoe-castro>", "user": "castro", "uptime": "3d", "remote-address": "AA:AA:AA:AA:AA:01"},
                {"name": "<pppoe-castro-1>", "user": "castro", "uptime": "5m", "remote-address": "AA:AA:AA:AA:AA:02"},
                {"name": "<pppoe-sin-ip>", "user": "sin-ip", "uptime": "1h", "remote-address": "BB:BB:BB:BB:BB:01"},
            ],
            [
                {".id": "*A1", "name": "<pppoe-castro>", "rx-byte": "1", "tx-byte": "1"},
                {".id": "*A2", "name": "<pppoe-castro-1>", "rx-byte": "1", "tx-byte": "1"},
                {".id": "*A3", "name": "<pppoe-sin-ip>", "rx-byte": "1", "tx-byte": "1"},
            ],
            ppp_active_payload=[
                {"name": "castro", "caller-id": "AA:AA:AA:AA:AA:02", "address": "10.0.0.2"},
                {"name": "castro", "caller-id": "AA:AA:AA:AA:AA:01", "address": "10.0.0.1"},
            ],
        ),
    )

    sessions = fetch_active_sessions(_make_router())

    assert [(s.interface_name, s.address, s.mac) for s in sessions] == [
        ("<pppoe-castro>", "10.0.0.1", "AA:AA:AA:AA:AA:01"),
        ("<pppoe-castro-1>", "10.0.0.2", "AA:AA:AA:AA:AA:02"),
        ("<pppoe-sin-ip>", None, "BB:BB:BB:BB:BB:01"),
    ]


def test_fetch_active_sessions_without_access_to_ppp_active_keeps_polling(monkeypatch, caplog):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_routeros(
            [{"name": "<pppoe-ana>", "user": "ana", "uptime": "1h", "remote-address": "AA:AA:AA:AA:AA:01"}],
            [{".id": "*A1", "name": "<pppoe-ana>", "rx-byte": "5", "tx-byte": "6"}],
            ppp_active_status=403,
        ),
    )

    with caplog.at_level("WARNING"):
        (session,) = fetch_active_sessions(_make_router())

    assert (session.rx_bytes, session.address) == (5, None)
    assert "/ppp/active" in caplog.text


def _fake_resource(status: int = 200, payload=None):
    def fake_get(self, url, **kwargs):
        assert url.endswith("/rest/system/resource"), url
        return httpx.Response(status, json=payload if payload is not None else {}, request=httpx.Request("GET", url))

    return fake_get


def test_fetch_resources_reads_system_resource(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _fake_resource(
            payload={
                "cpu-load": "7",
                "free-memory": "1000",
                "total-memory": "4000",
                "free-hdd-space": "50",
                "total-hdd-space": "128",
                "uptime": "1w2d3h4m5s",
                "version": "7.24.2 (stable)",
                "board-name": "CCR2004-1G-12S+2XS",
            }
        ),
    )

    assert fetch_resources(_make_router()) == RouterResources(
        cpu_load=7,
        free_memory=1000,
        total_memory=4000,
        free_hdd=50,
        total_hdd=128,
        uptime_seconds=604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5,
        version="7.24.2 (stable)",
        board_name="CCR2004-1G-12S+2XS",
    )


def test_fetch_resources_leaves_missing_or_odd_fields_empty(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", _fake_resource(payload={"cpu-load": "x", "uptime": "??"}))

    assert fetch_resources(_make_router()) == RouterResources(
        cpu_load=None,
        free_memory=None,
        total_memory=None,
        free_hdd=None,
        total_hdd=None,
        uptime_seconds=None,
        version=None,
        board_name=None,
    )


@pytest.mark.parametrize("status", [401, 403, 500])
def test_fetch_resources_returns_none_when_the_router_refuses(monkeypatch, caplog, status):
    monkeypatch.setattr(httpx.Client, "get", _fake_resource(status=status))

    assert fetch_resources(_make_router()) is None
    assert "system/resource" in caplog.text


def test_fetch_resources_returns_none_when_unreachable(monkeypatch):
    def unreachable(self, url, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(httpx.Client, "get", unreachable)

    assert fetch_resources(_make_router()) is None


def test_fetch_resources_returns_none_on_a_non_object_body(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", _fake_resource(payload=[1, 2]))

    assert fetch_resources(_make_router()) is None
