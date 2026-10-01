import ssl

import httpx
import pytest

from app.models.router import Router
from app.services.mikrotik_client import check_connection


def _make_router(port: int = 443, use_tls: bool = True) -> Router:
    return Router(
        name="Test Router",
        host="10.0.0.1",
        port=port,
        api_username="monitor",
        api_password_encrypted="",
        use_tls=use_tls,
        verify_tls=False,
        enabled=True,
    )


def _routeros_responses(overrides: dict | None = None):
    """Fake httpx.Client.get answering like a healthy RouterOS 7 REST API.
    `overrides` maps a URL suffix to an httpx.Response (or an exception to raise)."""
    overrides = overrides or {}

    def fake_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        for suffix, outcome in overrides.items():
            if url.endswith(suffix):
                if isinstance(outcome, Exception):
                    raise outcome
                return httpx.Response(outcome.status_code, content=outcome.content, request=request)
        if url.endswith("/rest/system/resource"):
            payload = {"version": "7.16.1 (stable)", "board-name": "RB4011iGS+"}
        elif url.endswith("/rest/interface/pppoe-server"):
            payload = [
                {".id": "*1", "name": "<pppoe-c1>", "user": "c1", "uptime": "1h"},
                {".id": "*2", "name": "<pppoe-c2>", "user": "c2", "uptime": "5m"},
            ]
        elif url.endswith("/rest/interface"):
            payload = [{"name": "<pppoe-c1>"}, {"name": "<pppoe-c2>"}]
        else:
            raise AssertionError(f"unexpected url {url}")
        return httpx.Response(200, json=payload, request=request)

    return fake_get


def _connect_error_caused_by(cause: BaseException) -> httpx.ConnectError:
    try:
        try:
            raise cause
        except BaseException as inner:
            raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as exc:
        return exc


def test_check_connection_reports_version_board_and_session_count(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", _routeros_responses())

    result = check_connection(_make_router(), "secret")

    assert result.ok is True
    assert result.routeros_version == "7.16.1 (stable)"
    assert result.board_name == "RB4011iGS+"
    assert result.active_sessions == 2
    assert "7.16.1" in result.message and "2 sesiones" in result.message


def test_check_connection_session_count_excludes_static_bindings(monkeypatch):
    overrides = {
        "/rest/interface/pppoe-server": httpx.Response(
            200,
            content=(
                b'[{".id":"*1","name":"<pppoe-c1>","user":"c1","uptime":"1h"},'
                b'{".id":"*9","name":"<pppoe-estatico>","user":"estatico","running":"false"}]'
            ),
        )
    }
    monkeypatch.setattr(httpx.Client, "get", _routeros_responses(overrides))

    result = check_connection(_make_router(), "secret")

    assert result.ok is True
    assert result.active_sessions == 1


def test_check_connection_reads_what_polling_needs(monkeypatch):
    requested = []
    healthy = _routeros_responses()

    def recording_get(self, url, **kwargs):
        requested.append(url)
        return healthy(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", recording_get)

    check_connection(_make_router(), "secret")

    assert any(u.endswith("/rest/interface/pppoe-server") for u in requested)
    assert any(u.endswith("/rest/interface") for u in requested)
    assert not any(u.endswith("/rest/ppp/active") for u in requested)


def test_check_connection_uses_given_password(monkeypatch):
    seen_auth = []
    healthy = _routeros_responses()

    def recording_get(self, url, **kwargs):
        seen_auth.append(self.auth)
        return healthy(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", recording_get)

    check_connection(_make_router(), "the-password")

    def authorization(auth: httpx.Auth) -> str:
        return next(auth.auth_flow(httpx.Request("GET", "https://x/"))).headers["Authorization"]

    assert authorization(seen_auth[0]) == authorization(httpx.BasicAuth("monitor", "the-password"))


def test_check_connection_bad_credentials(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "get", _routeros_responses({"/rest/system/resource": httpx.Response(401)})
    )

    result = check_connection(_make_router(), "wrong")

    assert result.ok is False
    assert "Usuario o contraseña incorrectos" in result.message


def test_check_connection_missing_permissions(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "get", _routeros_responses({"/rest/interface/pppoe-server": httpx.Response(403)})
    )

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "permisos" in result.message


def test_check_connection_rest_not_available(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "get", _routeros_responses({"/rest/system/resource": httpx.Response(404)})
    )

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "7.1" in result.message


def test_check_connection_unexpected_http_status(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "get", _routeros_responses({"/rest/interface": httpx.Response(500)})
    )

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "500" in result.message


def test_check_connection_timeout_means_unreachable(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _routeros_responses({"/rest/system/resource": httpx.ConnectTimeout("timed out")}),
    )

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "No responde" in result.message


def test_check_connection_refused_means_service_disabled(monkeypatch):
    error = _connect_error_caused_by(ConnectionRefusedError(111, "Connection refused"))
    monkeypatch.setattr(httpx.Client, "get", _routeros_responses({"/rest/system/resource": error}))

    result = check_connection(_make_router(port=443), "secret")

    assert result.ok is False
    assert "rechazó" in result.message
    assert "www-ssl" in result.message


def test_check_connection_tls_error(monkeypatch):
    error = _connect_error_caused_by(ssl.SSLCertVerificationError("certificate verify failed"))
    monkeypatch.setattr(httpx.Client, "get", _routeros_responses({"/rest/system/resource": error}))

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "TLS" in result.message


def test_check_connection_non_http_port_hints_classic_api(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _routeros_responses(
            {"/rest/system/resource": httpx.RemoteProtocolError("Server disconnected")}
        ),
    )

    result = check_connection(_make_router(port=8728, use_tls=False), "secret")

    assert result.ok is False
    assert "8728" in result.message
    assert "API clásica" in result.message


def test_check_connection_read_timeout_hints_classic_api(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _routeros_responses({"/rest/system/resource": httpx.ReadTimeout("read timed out")}),
    )

    result = check_connection(_make_router(port=8728, use_tls=False), "secret")

    assert result.ok is False
    assert "API clásica" in result.message


def test_check_connection_non_json_response(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _routeros_responses(
            {"/rest/system/resource": httpx.Response(200, content=b"<html>WebFig</html>")}
        ),
    )

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "respuesta inesperada" in result.message.lower()


@pytest.mark.parametrize("use_tls,expected_scheme", [(True, "https"), (False, "http")])
def test_check_connection_honors_tls_flag(monkeypatch, use_tls, expected_scheme):
    requested = []
    healthy = _routeros_responses()

    def recording_get(self, url, **kwargs):
        requested.append(str(self.base_url))
        return healthy(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", recording_get)

    check_connection(_make_router(port=8080, use_tls=use_tls), "secret")

    assert requested[0].startswith(f"{expected_scheme}://10.0.0.1:8080")


def test_check_connection_json_of_the_wrong_shape(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _routeros_responses({"/rest/system/resource": httpx.Response(200, content=b"[1, 2]")}),
    )

    result = check_connection(_make_router(), "secret")

    assert result.ok is False
    assert "respuesta inesperada" in result.message.lower()


def test_check_connection_accepts_non_utf8_router_text(monkeypatch):
    latin1 = httpx.Response(
        200, content=b'[{"name":"a","user":"a","uptime":"1h","comment":"D\xedaz"}]'
    )
    monkeypatch.setattr(httpx.Client, "get", _routeros_responses({"/rest/interface/pppoe-server": latin1}))

    result = check_connection(_make_router(), "secret")

    assert result.ok is True
    assert result.active_sessions == 1


# --- messages in English (installation language "en") -----------------------


def test_check_connection_success_in_english(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", _routeros_responses())
    result = check_connection(_make_router(), "secret", lang="en")
    assert result.message == "Connected — RouterOS 7.16.1 (stable), RB4011iGS+, 2 active PPPoE sessions."


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "Wrong username or password."),
        (403, "The user has no permission to read /rest/system/resource (it needs read and rest-api)."),
        (404, "The router does not expose the REST API (it needs RouterOS 7.1 or later)."),
        (500, "The router answered HTTP 500 when asked for /rest/system/resource."),
    ],
)
def test_check_connection_http_errors_in_english(monkeypatch, status, expected):
    monkeypatch.setattr(
        httpx.Client, "get", _routeros_responses({"/rest/system/resource": httpx.Response(status)})
    )
    assert check_connection(_make_router(), "secret", lang="en").message == expected


def test_check_connection_transport_errors_in_english(monkeypatch):
    cases = [
        (httpx.ConnectTimeout("timed out"), "Not responding"),
        (_connect_error_caused_by(ConnectionRefusedError(111, "refused")), "refused the connection"),
        (_connect_error_caused_by(ssl.SSLCertVerificationError("bad cert")), "TLS error"),
        (httpx.ReadTimeout("read timed out"), "classic API"),
    ]
    for error, expected in cases:
        monkeypatch.setattr(httpx.Client, "get", _routeros_responses({"/rest/system/resource": error}))
        message = check_connection(_make_router(), "secret", lang="en").message
        assert expected in message, message


def test_check_connection_unexpected_response_in_english(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        _routeros_responses({"/rest/system/resource": httpx.Response(200, content=b"<html>hi</html>")}),
    )
    message = check_connection(_make_router(), "secret", lang="en").message
    assert message.startswith("Unexpected response")
