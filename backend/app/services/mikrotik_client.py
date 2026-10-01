import json
import logging
import re
import ssl
from dataclasses import dataclass

import httpx

from app.core.crypto import decrypt
from app.models.router import Router

logger = logging.getLogger(__name__)

# RouterOS 7 REST API unit-token duration, e.g. "10m", "3h25m10s",
# "1d2h3m4s", "1w2d3h4m5s". Any subset of tokens, always in this order.
_UPTIME_TOKEN_RE = re.compile(
    r"^(?:(?P<weeks>\d+)w)?(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?"
    r"(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$"
)

# Legacy colon-separated duration, e.g. "1d02:03:04", "02:03:04", "00:10:00".
_UPTIME_COLON_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?P<hours>\d+):(?P<minutes>\d+):(?P<seconds>\d+)$"
)


class MikrotikError(RuntimeError):
    pass


@dataclass(frozen=True)
class MikrotikSession:
    """One active PPPoE-server session. A user can hold several at once."""

    username: str
    uptime_seconds: int
    # RouterOS id (".id", e.g. "*80020DCC") and name of the session's dynamic
    # interface. interface_id is None when that interface wasn't found in
    # /interface this poll -- and then the byte counters are unknown too
    # (None, NOT zero).
    interface_id: str | None
    interface_name: str
    rx_bytes: int | None
    tx_bytes: int | None
    # Client IP (from /ppp/active) and MAC, when known.
    address: str | None = None
    mac: str | None = None

    @property
    def has_counters(self) -> bool:
        return self.interface_id is not None and self.rx_bytes is not None and self.tx_bytes is not None


def parse_uptime(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        raise MikrotikError(f"Unrecognized uptime format: {raw!r}")

    match = _UPTIME_COLON_RE.fullmatch(raw)
    if match:
        parts = match.groupdict(default="0")
        return (
            int(parts["days"]) * 86400
            + int(parts["hours"]) * 3600
            + int(parts["minutes"]) * 60
            + int(parts["seconds"])
        )

    match = _UPTIME_TOKEN_RE.fullmatch(raw)
    if match and any(match.groupdict().values()):
        parts = match.groupdict(default="0")
        return (
            int(parts["weeks"]) * 604800
            + int(parts["days"]) * 86400
            + int(parts["hours"]) * 3600
            + int(parts["minutes"]) * 60
            + int(parts["seconds"])
        )

    raise MikrotikError(f"Unrecognized uptime format: {raw!r}")


def _client_for(router: Router, password: str | None = None) -> httpx.Client:
    scheme = "https" if router.use_tls else "http"
    base_url = f"{scheme}://{router.host}:{router.port}"
    if password is None:
        password = decrypt(router.api_password_encrypted) if router.api_password_encrypted else ""
    return httpx.Client(
        base_url=base_url,
        auth=(router.api_username, password),
        verify=router.verify_tls,
        timeout=10.0,
    )


def _routeros_json(response: httpx.Response):
    """Parse a RouterOS REST body. RouterOS stores user-entered text (comments,
    names) as raw bytes in the legacy 8-bit encoding Winbox used, so a body can
    be invalid UTF-8 (e.g. 0xED for "í"). Fall back to Latin-1, which maps every
    byte 1:1 and never fails, so usernames stay stable across polls."""
    try:
        text = response.content.decode("utf-8")
    except UnicodeDecodeError:
        text = response.content.decode("latin-1")
    return json.loads(text)


SESSIONS_PATH = "/rest/interface/pppoe-server"
RESOURCE_PATH = "/rest/system/resource"
INTERFACES_PATH = "/rest/interface"
PPP_ACTIVE_PATH = "/rest/ppp/active"


def _is_active_session(entry: dict) -> bool:
    """RouterOS's /interface/pppoe-server also lists STATIC PPPoE-server
    interface bindings (/interface pppoe-server add name=... user=...). When
    the client is disconnected such a row has no (or empty) uptime and
    running == "false"; user may also be missing. These are not active
    sessions. running being absent entirely (unexpected shape) is not by
    itself a reason to skip a row."""
    if not entry.get("user"):
        return False
    if not entry.get("uptime"):
        return False
    if entry.get("running") == "false":
        return False
    return True


def _fetch_addresses(client: httpx.Client, router: Router) -> dict[tuple[str, str | None], str]:
    """Client IP of each active PPP session, keyed by (user, MAC): the MAC
    tells apart the sessions of a user holding several (/ppp/active "caller-id"
    is /interface/pppoe-server "remote-address"). IPs are optional: when the
    API user can't read /ppp/active the poll goes on without them."""
    response = client.get(PPP_ACTIVE_PATH)
    if response.is_error:
        logger.warning(
            "Router %s (%s): %s answered HTTP %d; client IPs unavailable this poll",
            router.name,
            router.host,
            PPP_ACTIVE_PATH,
            response.status_code,
        )
        return {}
    return {
        (row.get("name"), row.get("caller-id") or None): row["address"]
        for row in _routeros_json(response)
        if row.get("address")
    }


def fetch_active_sessions(router: Router) -> list[MikrotikSession]:
    try:
        with _client_for(router) as client:
            sessions_response = client.get(SESSIONS_PATH)
            sessions_response.raise_for_status()
            interface_response = client.get(INTERFACES_PATH)
            interface_response.raise_for_status()
            addresses = _fetch_addresses(client, router)
    except httpx.HTTPError as exc:
        raise MikrotikError(f"Failed to reach router {router.name}: {exc}") from exc

    interfaces_by_name = {row["name"]: row for row in _routeros_json(interface_response)}

    raw_entries = _routeros_json(sessions_response)
    skipped = sum(1 for entry in raw_entries if not _is_active_session(entry))
    if skipped:
        logger.warning(
            "Router %s (%s): skipped %d non-session row(s) from %s (static PPPoE-server "
            "bindings without an active connection)",
            router.name,
            router.host,
            skipped,
            SESSIONS_PATH,
        )

    sessions = []
    for entry in raw_entries:
        if not _is_active_session(entry):
            continue
        # One row per active PPPoE-server session. "name" is the real name of
        # its dynamic interface: "<pppoe-USER>", or "<pppoe-USER-1>" when the
        # user holds (or just held) another session.
        username = entry["user"]
        interface_name = entry["name"]
        uptime_seconds = parse_uptime(entry["uptime"])
        mac = entry.get("remote-address") or None
        address = addresses.get((username, mac))
        iface = interfaces_by_name.get(interface_name)
        if iface is None:
            # Without its interface we have no byte counters for this
            # session. Reporting 0 bytes would corrupt its delta/bps state, so
            # return it with unknown counters: the poll keeps the client
            # online but leaves this session's state untouched.
            logger.warning(
                "Router %s (%s): interface %s of PPPoE session %r not found in /interface; "
                "skipping its traffic this poll",
                router.name,
                router.host,
                interface_name,
                username,
            )
            sessions.append(
                MikrotikSession(
                    username=username,
                    uptime_seconds=uptime_seconds,
                    interface_id=None,
                    interface_name=interface_name,
                    rx_bytes=None,
                    tx_bytes=None,
                    address=address,
                    mac=mac,
                )
            )
            continue
        sessions.append(
            MikrotikSession(
                username=username,
                uptime_seconds=uptime_seconds,
                interface_id=iface[".id"],
                interface_name=interface_name,
                rx_bytes=int(iface.get("rx-byte", 0)),
                tx_bytes=int(iface.get("tx-byte", 0)),
                address=address,
                mac=mac,
            )
        )
    return sessions


@dataclass(frozen=True)
class RouterResources:
    """The router's own health, from /system/resource. Any field RouterOS
    didn't send (or sent in an unexpected shape) is None."""

    cpu_load: int | None
    free_memory: int | None
    total_memory: int | None
    free_hdd: int | None
    total_hdd: int | None
    uptime_seconds: int | None
    version: str | None
    board_name: str | None


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _uptime_or_none(value) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_uptime(value)
    except MikrotikError:
        return None


def fetch_resources(router: Router) -> RouterResources | None:
    """CPU, memory, storage, uptime, version and model of the router. Never
    raises: a router that can't answer this (no access, timeout, odd body)
    must not cost the poll its traffic, so failures are logged and give None."""
    try:
        with _client_for(router) as client:
            response = client.get(RESOURCE_PATH)
            response.raise_for_status()
        body = _routeros_json(response)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Router %s (%s): could not read %s: %s", router.name, router.host, RESOURCE_PATH, exc)
        return None
    if not isinstance(body, dict):
        logger.warning("Router %s (%s): unexpected %s body", router.name, router.host, RESOURCE_PATH)
        return None
    return RouterResources(
        cpu_load=_int_or_none(body.get("cpu-load")),
        free_memory=_int_or_none(body.get("free-memory")),
        total_memory=_int_or_none(body.get("total-memory")),
        free_hdd=_int_or_none(body.get("free-hdd-space")),
        total_hdd=_int_or_none(body.get("total-hdd-space")),
        uptime_seconds=_uptime_or_none(body.get("uptime")),
        version=body.get("version") or None,
        board_name=body.get("board-name") or None,
    )


@dataclass(frozen=True)
class ConnectionCheckResult:
    ok: bool
    message: str
    routeros_version: str | None = None
    board_name: str | None = None
    active_sessions: int | None = None


# Messages for the admin UI, in the installation's language.
_MESSAGES = {
    "es": {
        "classic_hint": (
            "Si es el puerto de la API clásica (8728/8729): este sistema usa la API REST, "
            "que se sirve por el servicio www (80) o www-ssl (443)."
        ),
        "timeout": "No responde {where} (revisá IP, puerto, ruteo y firewall).",
        "not_http": "{where} aceptó la conexión pero no respondió como HTTP. {hint}",
        "tls": (
            "Error TLS con {where}: {exc}. Probá destildar 'Verificar certificado', o "
            "desactivar TLS si el puerto es del servicio www (HTTP). {hint}"
        ),
        "refused": (
            "{where} rechazó la conexión: el puerto está cerrado. Verificá que el servicio "
            "{service} esté habilitado en /ip service y permita la IP de este servidor."
        ),
        "other": "No se pudo conectar a {where}: {exc}",
        "401": "Usuario o contraseña incorrectos.",
        "403": "El usuario no tiene permisos para leer {path} (necesita read y rest-api).",
        "404": "El router no expone la API REST (requiere RouterOS 7.1 o superior).",
        "http": "El router respondió HTTP {status} al pedir {path}.",
        "unexpected": "Respuesta inesperada: el puerto responde HTTP pero no es la API REST de RouterOS.",
        "ok": "Conectado — {details}, {sessions} sesiones PPPoE activas.",
    },
    "en": {
        "classic_hint": (
            "If this is the classic API port (8728/8729): this system uses the REST API, "
            "served by the www (80) or www-ssl (443) service."
        ),
        "timeout": "Not responding: {where} (check IP, port, routing and firewall).",
        "not_http": "{where} accepted the connection but did not answer as HTTP. {hint}",
        "tls": (
            "TLS error with {where}: {exc}. Try unchecking 'Verify certificate', or "
            "turning TLS off if the port belongs to the www (HTTP) service. {hint}"
        ),
        "refused": (
            "{where} refused the connection: the port is closed. Check that the {service} "
            "service is enabled in /ip service and allows this server's IP."
        ),
        "other": "Could not connect to {where}: {exc}",
        "401": "Wrong username or password.",
        "403": "The user has no permission to read {path} (it needs read and rest-api).",
        "404": "The router does not expose the REST API (it needs RouterOS 7.1 or later).",
        "http": "The router answered HTTP {status} when asked for {path}.",
        "unexpected": "Unexpected response: the port answers HTTP but it is not the RouterOS REST API.",
        "ok": "Connected — {details}, {sessions} active PPPoE sessions.",
    },
}


def _caused_by(exc: BaseException, kind: type) -> bool:
    seen = exc
    while seen is not None:
        if isinstance(seen, kind):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _describe_transport_error(router: Router, exc: httpx.HTTPError, lang: str) -> str:
    m = _MESSAGES[lang]
    where = f"{router.host}:{router.port}"
    if isinstance(exc, httpx.ConnectTimeout):
        return m["timeout"].format(where=where)
    if isinstance(exc, (httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return m["not_http"].format(where=where, hint=m["classic_hint"])
    if _caused_by(exc, ssl.SSLError):
        return m["tls"].format(where=where, exc=exc, hint=m["classic_hint"])
    if _caused_by(exc, ConnectionRefusedError):
        return m["refused"].format(where=where, service="www-ssl" if router.use_tls else "www")
    return m["other"].format(where=where, exc=exc)


def _describe_http_status(path: str, status_code: int, lang: str) -> str:
    m = _MESSAGES[lang]
    if str(status_code) in m:
        return m[str(status_code)].format(path=path)
    return m["http"].format(status=status_code, path=path)


def check_connection(router: Router, password: str, lang: str = "es") -> ConnectionCheckResult:
    """Try the router with the given settings, reading the same endpoints the
    poller needs. Never raises for connection problems: failures come back as
    ok=False with a human-readable message, in `lang`, for the admin UI."""
    if lang not in _MESSAGES:
        lang = "es"
    paths = (RESOURCE_PATH, SESSIONS_PATH, INTERFACES_PATH)
    bodies = {}
    try:
        with _client_for(router, password) as client:
            for path in paths:
                response = client.get(path)
                if response.status_code != 200:
                    return ConnectionCheckResult(
                        ok=False, message=_describe_http_status(path, response.status_code, lang)
                    )
                bodies[path] = _routeros_json(response)
        if not isinstance(bodies[RESOURCE_PATH], dict) or not all(
            isinstance(bodies[path], list) for path in paths[1:]
        ):
            raise ValueError("not a RouterOS REST response")
    except httpx.HTTPError as exc:
        return ConnectionCheckResult(ok=False, message=_describe_transport_error(router, exc, lang))
    except ValueError:
        return ConnectionCheckResult(
            ok=False,
            message=_MESSAGES[lang]["unexpected"],
        )

    resource = bodies[RESOURCE_PATH]
    version = resource.get("version")
    board = resource.get("board-name")
    sessions = sum(1 for entry in bodies[SESSIONS_PATH] if _is_active_session(entry))
    details = ", ".join(part for part in (f"RouterOS {version}" if version else None, board) if part)
    return ConnectionCheckResult(
        ok=True,
        message=_MESSAGES[lang]["ok"].format(details=details, sessions=sessions),
        routeros_version=version,
        board_name=board,
        active_sessions=sessions,
    )
