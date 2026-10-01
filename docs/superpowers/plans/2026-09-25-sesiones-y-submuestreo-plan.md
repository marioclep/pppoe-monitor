# Estado por sesión PPPoE y submuestreo — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Contar exactamente el tráfico de usuarios con varias sesiones PPPoE simultáneas o interfaces `<pppoe-usuario-N>`, y reducir `traffic_samples` guardando 7 días de muestras de 5 minutos más 90 días de resumen horario.

**Architecture:** El poller lee sesiones de `/rest/interface/pppoe-server` y guarda una fila de `session_state` por sesión (clave `(router_id, interface_id)`). En cada sondeo suma los deltas de las sesiones de un cliente y escribe una muestra y el acumulado. Un job horario resume `traffic_samples` en `traffic_hourly` con una marca de agua en `settings`. La purga borra por lotes y nunca toca muestras sin resumir. El historial largo de la API sale de `traffic_hourly` más un agregado al vuelo de las horas todavía no resumidas.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x (sync, psycopg2), Alembic, APScheduler, PostgreSQL 16, pytest. React 19 + TypeScript + Vite + Recharts.

**Spec:** `docs/superpowers/specs/2026-09-24-sesiones-y-submuestreo-design.md` (extiende `docs/superpowers/specs/2026-09-22-pppoe-monitor-design.md`).

## Global Constraints

- **Nunca instalar nada en la Mac.** Todo comando de Python, Node, Postgres o Docker corre en la VM con el wrapper, ejecutado desde la raíz del repo: `.superpowers/sdd/2026-09-22-pppoe-monitor-plan/remote '<comando>'`. Sincroniza el repo a `~/pppoe-monitor` en la VM y trae de vuelta los archivos creados. En este plan, `remote` es ese wrapper.
- Tests del backend: `remote 'cd backend && .venv/bin/pytest -q'` (o un archivo/test puntual). Corren contra el contenedor de desarrollo `pppoe-dev-db` (`localhost:5432/pppoe` en la VM), **nunca** contra la base real del stack (`pppoe-monitor-db-1`), que solo se toca en la Tarea 9.
- Alembic necesita `JWT_SECRET`: `remote 'cd backend && JWT_SECRET=pytest-only-jwt-secret-not-for-production-use-0123456789 .venv/bin/alembic upgrade head'`.
- Frontend: `remote 'cd frontend && npm run build && npm run lint'`.
- git corre en la Mac. Trabajar en la rama `sesiones-y-submuestreo` (crearla desde `main` antes de la Tarea 1). Mensajes de commit en español con prefijo convencional (`feat:`, `fix(backend):`, …), y el trailer en un párrafo aparte: `git commit -m "<asunto>" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`.
- Textos visibles para el usuario en español rioplatense; identificadores y comentarios de código en inglés, como el resto del código.
- La migración **no toca** `accumulation_periods` ni `traffic_samples`. Hay routers reales cargados en la VM.
- `raw_retention_days`: default **7**, mínimo 1, y `raw_retention_days <= retention_days` (si no, 422). `retention_days`: default **90**, aplica al resumen horario.
- Marca de agua: clave `hourly_rollup_until` en `settings`, instante UTC en ISO 8601, siempre al inicio de una hora. Interna: no se expone en la API.
- Las horas de `traffic_hourly` son horas UTC. El job de resumen corre al minuto **2** de cada hora y una vez al arrancar.
- Purga en lotes de **10.000** filas, con un commit por lote.
- `GET /clients/{id}/history`: `1 <= hours <= 2160`. Detalle de 5 minutos si `hours <= raw_retention_days × 24`; si no, un punto por hora.
- En la interfaz `pppoe-in`, TX es la **descarga** del cliente y RX su subida (la UI ya lo muestra así).

## Review Focus

1. **Una sesión cuya interfaz falta por un sondeo** (carrera entre los dos pedidos) debe conservar su estado, y el sondeo siguiente sigue contando deltas: no vuelve a tomar la base. → Tarea 2, test `test_poll_router_leaves_state_of_session_without_counters_untouched`.
2. **Después de reiniciar el router, un `.id` puede volver en la sesión de otro usuario**, incluso con uptime mayor. No debe calcularse un delta contra los contadores de otra persona. → Tarea 2, test `test_interface_id_reused_by_another_user_is_not_a_continuation`.
3. **El primer resumen corre sobre días de muestras existentes** (la base real de la VM). Debe procesar en tramos y no perder ni duplicar horas en los bordes entre tramos. → Tarea 5, test `test_rollup_without_watermark_starts_at_oldest_sample_and_crosses_chunks`.
4. **Una marca de agua ausente o corrupta** hace que el resumen recomience desde la muestra más vieja, y que la purga no borre ninguna muestra de 5 minutos. → Tareas 5 y 6, tests `test_garbage_watermark_reads_as_missing` y `test_purge_without_usable_watermark_deletes_no_samples`.
5. **Un historial largo con la hora en curso** calcula la velocidad promedio sobre los segundos transcurridos, no sobre 3600. Un historial sin marca de agua sale entero al vuelo, sin error. → Tarea 5, test `test_client_hourly_history_hour_in_progress_uses_elapsed_seconds`.

---

## Estructura de archivos

| Archivo | Cambio | Responsabilidad |
|---|---|---|
| `backend/app/services/mikrotik_client.py` | Modificar | Leer sesiones de `/interface/pppoe-server` y sus contadores de `/interface` |
| `backend/app/models/client.py` | Modificar | `SessionState` por sesión; `session_speed_subquery()` |
| `backend/app/models/traffic.py` | Modificar | Nuevo modelo `TrafficHourly` |
| `backend/app/models/__init__.py` | Modificar | Registrar `TrafficHourly` |
| `backend/alembic/versions/5d2b7e9c41a0_session_state_per_session_and_traffic_hourly.py` | Crear | Migración |
| `backend/app/services/polling.py` | Modificar | Algoritmo por sesión |
| `backend/app/api/routers.py` | Modificar | Deshabilitar router borra `session_state` por `router_id` |
| `backend/app/api/clients.py` | Modificar | Velocidad = suma de sesiones; historial horario |
| `backend/app/api/dashboard.py` | Modificar | Velocidad = suma de sesiones |
| `backend/app/services/app_settings.py` | Modificar | `get_raw_retention_days` |
| `backend/app/schemas/settings.py`, `backend/app/api/settings.py` | Modificar | Campo `raw_retention_days` y validación cruzada |
| `backend/app/services/rollup.py` | Crear | Job de resumen, marca de agua, historial horario de un cliente |
| `backend/app/services/scheduler.py` | Modificar | Registrar el job de resumen |
| `backend/app/services/purge.py` | Modificar | Dos retenciones, respeto de la marca de agua, lotes |
| `backend/app/schemas/client.py` | Modificar | `peak_rx_bps`/`peak_tx_bps` en `ClientHistoryPoint` |
| `frontend/src/pages/ClientDetail.tsx` | Modificar | Rangos 30/90 días, línea de pico |
| `frontend/src/pages/SettingsAdmin.tsx` | Modificar | Campo "Retención de detalle (días)" |
| `README.md` | Modificar | Documentar las dos retenciones |
| Tests en `backend/tests/` | Modificar/crear | `test_mikrotik_client.py`, `test_mikrotik_connection_check.py`, `test_polling.py`, `test_api_routers.py`, `test_api_clients.py`, `test_api_dashboard.py`, `test_api_settings.py`, `test_rollup.py` (nuevo), `test_purge.py`, `test_scheduler.py` |

---

### Task 1: Leer las sesiones desde `/interface/pppoe-server`

**Files:**
- Modify: `backend/app/services/mikrotik_client.py`
- Test: `backend/tests/test_mikrotik_client.py`, `backend/tests/test_mikrotik_connection_check.py`, `backend/tests/test_polling.py` (solo el helper `_session`)

**Interfaces:**
- Produces: `MikrotikSession(username: str, uptime_seconds: int, interface_id: str | None, interface_name: str, rx_bytes: int | None, tx_bytes: int | None)`, con `has_counters` true solo si `interface_id`, `rx_bytes` y `tx_bytes` no son `None`. `fetch_active_sessions(router) -> list[MikrotikSession]` devuelve **una entrada por sesión** (puede haber varias con el mismo `username`). `check_connection` cuenta sesiones de `/rest/interface/pppoe-server`.
- Produces (tests): helper `_session(username, uptime_seconds, rx_bytes, tx_bytes, interface_id=None, interface_name=None)` en `test_polling.py`.

- [ ] **Step 1: Crear la rama**

```bash
git checkout -b sesiones-y-submuestreo main
```

- [ ] **Step 2: Reemplazar los tests de `fetch_active_sessions`**

En `backend/tests/test_mikrotik_client.py`, dejar los imports, `_make_router` y los tests de `parse_uptime`. Borrar desde `def test_fetch_active_sessions_combines_ppp_and_interface_data` hasta el final del archivo (incluido `_latin1_ppp_active`) y agregar:

```python
def _fake_routeros(sessions_payload, interfaces_payload, requested: list | None = None):
    """Fake httpx.Client.get for /rest/interface/pppoe-server (one row per
    active PPPoE session) and /rest/interface (byte counters). A payload given
    as bytes is sent raw, e.g. to test non-UTF-8 text."""

    def fake_get(self, url, **kwargs):
        if requested is not None:
            requested.append(url)
        request = httpx.Request("GET", url)
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
    assert not any(url.endswith("/rest/ppp/active") for url in requested)


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
```

Y cambiar el import del principio del archivo por:

```python
from app.services.mikrotik_client import MikrotikError, MikrotikSession, fetch_active_sessions, parse_uptime
```

- [ ] **Step 3: Actualizar los tests de `check_connection`**

En `backend/tests/test_mikrotik_connection_check.py`:

1. En `_routeros_responses`, reemplazar la rama de `/rest/ppp/active`:

```python
        elif url.endswith("/rest/interface/pppoe-server"):
            payload = [
                {".id": "*1", "name": "<pppoe-c1>", "user": "c1", "uptime": "1h"},
                {".id": "*2", "name": "<pppoe-c2>", "user": "c2", "uptime": "5m"},
            ]
```

2. En `test_check_connection_reads_what_polling_needs`, reemplazar los dos `assert` finales por:

```python
    assert any(u.endswith("/rest/interface/pppoe-server") for u in requested)
    assert any(u.endswith("/rest/interface") for u in requested)
    assert not any(u.endswith("/rest/ppp/active") for u in requested)
```

3. En `test_check_connection_missing_permissions` y `test_check_connection_accepts_non_utf8_router_text`, cambiar la clave de override `"/rest/ppp/active"` por `"/rest/interface/pppoe-server"`.

- [ ] **Step 4: Adaptar `test_polling.py` al nuevo `MikrotikSession`**

Agregar debajo de los imports de `backend/tests/test_polling.py`:

```python
def _session(
    username: str,
    uptime_seconds: int,
    rx_bytes: int | None,
    tx_bytes: int | None,
    interface_id: str | None = None,
    interface_name: str | None = None,
) -> MikrotikSession:
    """A PPPoE session as fetch_active_sessions returns it. By default the
    user has one session on "<pppoe-USERNAME>" with an id derived from that
    name; with rx/tx None the interface wasn't found, so there is no id."""
    if interface_name is None:
        interface_name = f"<pppoe-{username}>"
    if interface_id is None and rx_bytes is not None:
        interface_id = f"*{interface_name}"
    return MikrotikSession(
        username=username,
        uptime_seconds=uptime_seconds,
        interface_id=interface_id,
        interface_name=interface_name,
        rx_bytes=rx_bytes,
        tx_bytes=tx_bytes,
    )
```

Después reescribir todas las construcciones posicionales del archivo (todas tienen la forma `MikrotikSession(username="…", uptime_seconds=…, rx_bytes=…, tx_bytes=…)` en una línea):

```bash
perl -0pi -e 's/MikrotikSession\(username=("[^"]*"), uptime_seconds=([^,]+), rx_bytes=([^,]+), tx_bytes=([^)]+)\)/_session($1, $2, $3, $4)/g' backend/tests/test_polling.py
grep -n "MikrotikSession(" backend/tests/test_polling.py
```

Esperado: `grep` muestra solo la línea dentro de `_session`.

- [ ] **Step 5: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_mikrotik_client.py tests/test_mikrotik_connection_check.py tests/test_polling.py'`
Expected: FAIL. `MikrotikSession.__init__() got an unexpected keyword argument 'interface_id'` y `unexpected url .../rest/interface/pppoe-server`.

- [ ] **Step 6: Implementar**

En `backend/app/services/mikrotik_client.py`:

1. Reemplazar la dataclass `MikrotikSession`:

```python
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

    @property
    def has_counters(self) -> bool:
        return self.interface_id is not None and self.rx_bytes is not None and self.tx_bytes is not None
```

2. Borrar `_match_interface_name` y reemplazar `fetch_active_sessions` por:

```python
SESSIONS_PATH = "/rest/interface/pppoe-server"
INTERFACES_PATH = "/rest/interface"


def fetch_active_sessions(router: Router) -> list[MikrotikSession]:
    try:
        with _client_for(router) as client:
            sessions_response = client.get(SESSIONS_PATH)
            sessions_response.raise_for_status()
            interface_response = client.get(INTERFACES_PATH)
            interface_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MikrotikError(f"Failed to reach router {router.name}: {exc}") from exc

    interfaces_by_name = {row["name"]: row for row in _routeros_json(interface_response)}

    sessions = []
    for entry in _routeros_json(sessions_response):
        # One row per active PPPoE-server session. "name" is the real name of
        # its dynamic interface: "<pppoe-USER>", or "<pppoe-USER-1>" when the
        # user holds (or just held) another session.
        username = entry["user"]
        interface_name = entry["name"]
        uptime_seconds = parse_uptime(entry["uptime"])
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
            )
        )
    return sessions
```

3. En `check_connection`, cambiar la tupla de paths y el conteo:

```python
    paths = ("/rest/system/resource", SESSIONS_PATH, INTERFACES_PATH)
```

```python
    sessions = len(bodies[SESSIONS_PATH])
```

El poller no cambia en esta tarea: sigue usando `username`, `uptime_seconds`, `rx_bytes` y `tx_bytes`, y todavía deduplica por usuario.

- [ ] **Step 7: Correr toda la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: PASS (toda la suite).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/mikrotik_client.py backend/tests/test_mikrotik_client.py backend/tests/test_mikrotik_connection_check.py backend/tests/test_polling.py
git commit -m "feat(backend): leer sesiones PPPoE de /interface/pppoe-server con su interfaz real" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Estado por sesión (modelo, migración y poller)

**Files:**
- Modify: `backend/app/models/client.py`, `backend/app/models/traffic.py`, `backend/app/models/__init__.py`, `backend/app/services/polling.py`, `backend/app/api/routers.py`
- Create: `backend/alembic/versions/5d2b7e9c41a0_session_state_per_session_and_traffic_hourly.py`
- Test: `backend/tests/test_polling.py`, `backend/tests/test_api_routers.py`, `backend/tests/test_api_clients.py`, `backend/tests/test_api_dashboard.py`

**Interfaces:**
- Consumes: `MikrotikSession` de la Tarea 1 (`interface_id`, `interface_name`, `has_counters`).
- Produces: modelo `SessionState(id, router_id, client_id, interface_id: str, interface_name: str, last_uptime_seconds, last_rx_bytes, last_tx_bytes, last_poll_at, last_rx_bps, last_tx_bps)` con única `(router_id, interface_id)`. Modelo `TrafficHourly(client_id, hour_start, rx_bytes, tx_bytes, peak_rx_bps, peak_tx_bps)` con PK `(client_id, hour_start)`. Revisión Alembic `5d2b7e9c41a0`, que siembra `raw_retention_days = 7`. `polling._get_or_create_client(db, router_id, username, now)` mantiene su firma (un test la parchea).

- [ ] **Step 1: Escribir los tests del poller que fallan**

En `backend/tests/test_polling.py`, agregar debajo de `_session`:

```python
def _states(db: Session, client_id: int) -> list[SessionState]:
    return db.query(SessionState).filter_by(client_id=client_id).order_by(SessionState.interface_id).all()


def _serve(monkeypatch, *sessions: MikrotikSession) -> None:
    monkeypatch.setattr("app.services.polling.fetch_active_sessions", lambda r: list(sessions))


def _client(db: Session, router: Router, username: str) -> PPPoEClient:
    return db.query(PPPoEClient).filter_by(router_id=router.id, username=username).one()


def _period_totals(db: Session, client_id: int) -> tuple[int, int]:
    db.expire_all()
    period = db.query(AccumulationPeriod).filter_by(client_id=client_id, period_end=None).one()
    return period.rx_bytes_total, period.tx_bytes_total
```

Borrar el test `test_poll_router_dedupes_duplicate_usernames_keeping_lowest_uptime` completo y agregar al final del archivo:

```python
def test_two_sessions_of_the_same_user_are_summed_in_sample_and_period(db, router, monkeypatch):
    """RouterOS lets a user hold two PPPoE sessions at once (e.g. castro.melina
    from two consecutive MACs). Each has its own interface and counters; the
    client's traffic is the sum of both."""
    _serve(
        monkeypatch,
        _session("castro.melina", 100, 1000, 100, interface_id="*A"),
        _session("castro.melina", 50, 3000, 300, interface_id="*B", interface_name="<pppoe-castro.melina-1>"),
    )
    poll_router(db, router)

    client = _client(db, router, "castro.melina")
    sample = db.query(TrafficSample).filter_by(client_id=client.id).one()
    assert (sample.rx_bytes_delta, sample.tx_bytes_delta) == (4000, 400)
    assert sample.rx_bps == int(1000 * 8 / 100) + int(3000 * 8 / 50)
    assert [s.interface_id for s in _states(db, client.id)] == ["*A", "*B"]

    _serve(
        monkeypatch,
        _session("castro.melina", 400, 1500, 150, interface_id="*A"),
        _session("castro.melina", 350, 3700, 370, interface_id="*B", interface_name="<pppoe-castro.melina-1>"),
    )
    poll_router(db, router)

    assert db.query(TrafficSample).filter_by(client_id=client.id).count() == 2
    assert _period_totals(db, client.id) == (4000 + 500 + 700, 400 + 50 + 70)


def test_when_one_of_two_sessions_drops_only_its_state_goes(db, router, monkeypatch):
    _serve(
        monkeypatch,
        _session("moyano.julio", 100, 1000, 100, interface_id="*A"),
        _session("moyano.julio", 100, 2000, 200, interface_id="*B", interface_name="<pppoe-moyano.julio-1>"),
    )
    poll_router(db, router)

    _serve(monkeypatch, _session("moyano.julio", 400, 1500, 150, interface_id="*A"))
    poll_router(db, router)

    client = _client(db, router, "moyano.julio")
    assert _period_totals(db, client.id) == (3000 + 500, 300 + 50)
    assert client.is_active is True
    assert [s.interface_id for s in _states(db, client.id)] == ["*A"]


def test_reconnect_with_a_new_interface_is_a_new_session(db, router, monkeypatch):
    _serve(monkeypatch, _session("reconecta", 100, 1000, 100, interface_id="*A"))
    poll_router(db, router)

    # Reconnected between polls: a new dynamic interface, counters from 0.
    _serve(monkeypatch, _session("reconecta", 30, 200, 20, interface_id="*B"))
    poll_router(db, router)

    client = _client(db, router, "reconecta")
    assert _period_totals(db, client.id) == (1200, 120)
    assert [s.interface_id for s in _states(db, client.id)] == ["*B"]


def test_same_interface_id_with_lower_uptime_counts_as_new_session(db, router, monkeypatch):
    """E.g. RouterOS handing out the same id again after a reboot."""
    _serve(monkeypatch, _session("reinicio", 3600 * 24, 50_000, 5_000, interface_id="*A"))
    poll_router(db, router)  # long-running session first seen: baseline only

    _serve(monkeypatch, _session("reinicio", 20, 700, 70, interface_id="*A"))
    poll_router(db, router)

    client = _client(db, router, "reinicio")
    assert _period_totals(db, client.id) == (700, 70)


def test_interface_id_reused_by_another_user_is_not_a_continuation(db, router, monkeypatch):
    _serve(monkeypatch, _session("ana", 20, 1000, 100, interface_id="*A"))
    poll_router(db, router)

    # After a reboot the id can come back on someone else's session, even
    # with a higher uptime: those counters are beto's, not a delta of ana's.
    # (Uptime 50 is within one polling interval: a first sighting counts it all.)
    _serve(monkeypatch, _session("beto", 50, 1500, 150, interface_id="*A"))
    poll_router(db, router)

    ana, beto = _client(db, router, "ana"), _client(db, router, "beto")
    assert _period_totals(db, ana.id) == (1000, 100)
    assert _period_totals(db, beto.id) == (1500, 150)
    assert ana.is_active is False
    assert _states(db, ana.id) == []
    assert [s.interface_id for s in _states(db, beto.id)] == ["*A"]


def test_first_sighting_rule_applies_to_each_session(db, router, monkeypatch):
    _serve(monkeypatch, _session("mixto", 100, 1000, 100, interface_id="*A"))
    poll_router(db, router)

    # *B is seen for the first time with a long uptime (its bytes predate our
    # watching it): baseline only, while *A keeps counting deltas.
    _serve(
        monkeypatch,
        _session("mixto", 400, 1500, 150, interface_id="*A"),
        _session("mixto", 3600 * 24, 9_000_000, 900_000, interface_id="*B", interface_name="<pppoe-mixto-1>"),
    )
    poll_router(db, router)

    client = _client(db, router, "mixto")
    assert _period_totals(db, client.id) == (1500, 150)
    (state_b,) = [s for s in _states(db, client.id) if s.interface_id == "*B"]
    assert (state_b.last_rx_bytes, state_b.last_rx_bps) == (9_000_000, 0)
```

Adaptar los tests existentes que leen `SessionState` por `client_id`:

1. En `test_poll_router_resets_session_state_after_offline_gap`, reemplazar `assert db.get(SessionState, client.id) is None` por `assert _states(db, client.id) == []`.

2. Reemplazar `test_poll_router_leaves_state_of_session_without_counters_untouched` completo por:

```python
def test_poll_router_leaves_state_of_session_without_counters_untouched(db, router, monkeypatch):
    """A session whose interface couldn't be found comes back with
    interface_id/rx_bytes/tx_bytes None. The client stays online, and that
    session's state (matched by interface name, the only key we have),
    samples and period totals must not be touched -- then the next poll with
    counters continues the deltas instead of taking a new baseline."""
    _serve(monkeypatch, _session("flaky", 300, 1000, 500))
    poll_router(db, router)
    client = _client(db, router, "flaky")
    (state_before,) = _states(db, client.id)
    snapshot = (state_before.last_uptime_seconds, state_before.last_rx_bytes, state_before.last_tx_bytes)
    samples_before = db.query(TrafficSample).filter_by(client_id=client.id).count()

    _serve(monkeypatch, _session("flaky", 600, None, None))
    poll_router(db, router)

    db.expire_all()
    assert db.get(PPPoEClient, client.id).is_active is True
    (state_after,) = _states(db, client.id)
    assert (state_after.last_uptime_seconds, state_after.last_rx_bytes, state_after.last_tx_bytes) == snapshot
    assert db.query(TrafficSample).filter_by(client_id=client.id).count() == samples_before
    assert _period_totals(db, client.id) == (1000, 500)

    _serve(monkeypatch, _session("flaky", 900, 1600, 800))
    poll_router(db, router)
    assert _period_totals(db, client.id) == (1600, 800)
```

3. En `test_first_poll_of_router_takes_existing_sessions_as_baseline`, reemplazar `state = db.get(SessionState, client.id)` por `(state,) = _states(db, client.id)`.

4. En `test_poll_router_persists_bps_on_session_state`, el mismo reemplazo.

En los tests de API, `SessionState` ahora necesita `router_id` e `interface_id`:

- `backend/tests/test_api_clients.py`, en `_seed_client`:

```python
        db.add(
            SessionState(
                router_id=router.id, client_id=c.id, interface_id=f"*{username}", last_rx_bps=rx_bps, last_tx_bps=tx_bps
            )
        )
```

- `backend/tests/test_api_dashboard.py`, en `_add_client`:

```python
        db.add(
            SessionState(
                router_id=router_id, client_id=c.id, interface_id=f"*{username}", last_rx_bps=bps[0], last_tx_bps=bps[1]
            )
        )
```

- `backend/tests/test_api_routers.py`, en `test_disabling_router_marks_clients_inactive_and_clears_session_state`, reemplazar el `db.add(SessionState(...))` y la aserción de estado:

```python
        db.add(
            SessionState(
                router_id=router.id, client_id=online.id, interface_id="*1", last_rx_bps=100, last_tx_bps=50
            )
        )
```

```python
            assert db.query(SessionState).filter_by(router_id=router_id).count() == 0
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_polling.py tests/test_api_routers.py'`
Expected: FAIL. `SessionState` no tiene `router_id`/`interface_id`, y los tests de dos sesiones no suman.

- [ ] **Step 3: Modelos**

En `backend/app/models/client.py`, reemplazar la clase `SessionState`:

```python
class SessionState(Base):
    """Counters of one live PPPoE session as of the last poll. A user can
    hold several sessions at once; each has its own row, keyed by the
    RouterOS id of its dynamic interface (names are reused on reconnect,
    ids are not). The row is deleted when the session disappears."""

    __tablename__ = "session_state"
    __table_args__ = (UniqueConstraint("router_id", "interface_id", name="uq_session_state_router_interface"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"), index=True)
    interface_id: Mapped[str] = mapped_column(String(32))
    # Only for logs/diagnostics, and to recognize a live session whose
    # interface (and so its id) was missing from one poll.
    interface_name: Mapped[str] = mapped_column(String(128), default="")
    last_uptime_seconds: Mapped[int] = mapped_column(Integer, default=0)
    last_rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Throughput of this session measured by the most recent poll. A
    # client's current bps is the sum over its sessions; with no sessions
    # (offline) it reads as 0.
    last_rx_bps: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    last_tx_bps: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
```

En `backend/app/models/traffic.py`, agregar al final:

```python
class TrafficHourly(Base):
    """Per-client traffic summed per UTC hour (see app/services/rollup.py).
    Kept for retention_days, while 5-minute samples are only kept for
    raw_retention_days."""

    __tablename__ = "traffic_hourly"
    __table_args__ = (Index("ix_traffic_hourly_hour_start", "hour_start"),)

    client_id: Mapped[int] = mapped_column(
        ForeignKey("pppoe_clients.id", ondelete="CASCADE"), primary_key=True
    )
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # Highest bps among the hour's 5-minute samples.
    peak_rx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    peak_tx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
```

En `backend/app/models/__init__.py`, cambiar la línea de traffic por:

```python
from app.models.traffic import AccumulationPeriod, TrafficHourly, TrafficSample  # noqa: F401
```

- [ ] **Step 4: Migración**

Crear `backend/alembic/versions/5d2b7e9c41a0_session_state_per_session_and_traffic_hourly.py`:

```python
"""session_state per PPPoE session, traffic_hourly, raw_retention_days

- session_state goes from one row per client to one row per live PPPoE
  session, keyed by (router_id, interface_id). The old per-client state
  can't be split into sessions, so it is dropped: every session is first
  seen again on the next poll, losing one polling interval of traffic
  (accepted in the spec).
- traffic_hourly holds the per-hour rollup of traffic_samples.
- Seeds raw_retention_days = 7 (days of 5-minute samples kept).

traffic_samples and accumulation_periods are not touched.

Revision ID: 5d2b7e9c41a0
Revises: ac6bc5da9228
Create Date: 2026-09-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d2b7e9c41a0'
down_revision: Union[str, None] = 'ac6bc5da9228'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('traffic_hourly',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('hour_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('tx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('peak_rx_bps', sa.BigInteger(), nullable=False),
    sa.Column('peak_tx_bps', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['pppoe_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('client_id', 'hour_start')
    )
    op.create_index('ix_traffic_hourly_hour_start', 'traffic_hourly', ['hour_start'], unique=False)

    op.drop_table('session_state')
    op.create_table('session_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('router_id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('interface_id', sa.String(length=32), nullable=False),
    sa.Column('interface_name', sa.String(length=128), nullable=False),
    sa.Column('last_uptime_seconds', sa.Integer(), nullable=False),
    sa.Column('last_rx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_tx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_rx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_tx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['pppoe_clients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['router_id'], ['routers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('router_id', 'interface_id', name='uq_session_state_router_interface')
    )
    op.create_index(op.f('ix_session_state_client_id'), 'session_state', ['client_id'], unique=False)

    op.execute(
        "INSERT INTO settings (key, value) VALUES ('raw_retention_days', '7') ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    # Back to one (empty) state row per client: every session is first seen
    # again on the next poll. The hourly rollup is dropped with its watermark.
    op.execute("DELETE FROM settings WHERE key IN ('raw_retention_days', 'hourly_rollup_until')")

    op.drop_index(op.f('ix_session_state_client_id'), table_name='session_state')
    op.drop_table('session_state')
    op.create_table('session_state',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('last_uptime_seconds', sa.Integer(), nullable=False),
    sa.Column('last_rx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_tx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_rx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_tx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['pppoe_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('client_id')
    )

    op.drop_index('ix_traffic_hourly_hour_start', table_name='traffic_hourly')
    op.drop_table('traffic_hourly')
```

- [ ] **Step 5: Poller por sesión**

Reemplazar `backend/app/services/polling.py` completo por:

```python
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient, SessionState
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.services.alerts import PendingNotification, evaluate_alerts
from app.services.app_settings import get_polling_interval_seconds
from app.services.delta import compute_delta
from app.services.mikrotik_client import MikrotikError, MikrotikSession, fetch_active_sessions
from app.services.notifications import notify

logger = logging.getLogger(__name__)


def _get_or_create_client(db: Session, router_id: int, username: str, now: datetime) -> PPPoEClient:
    client = (
        db.query(PPPoEClient)
        .filter_by(router_id=router_id, username=username)
        .first()
    )
    if client is None:
        client = PPPoEClient(router_id=router_id, username=username, first_seen=now, last_seen=now, is_active=True)
        db.add(client)
        db.flush()
    else:
        client.last_seen = now
        client.is_active = True
    return client


def _get_or_create_active_period(db: Session, client_id: int, now: datetime) -> AccumulationPeriod:
    period = (
        db.query(AccumulationPeriod)
        .filter_by(client_id=client_id, period_end=None)
        .first()
    )
    if period is None:
        period = AccumulationPeriod(client_id=client_id, period_start=now)
        db.add(period)
        db.flush()
    return period


def _compute_bps(delta_bytes: int, elapsed_seconds: float) -> int:
    if elapsed_seconds <= 0:
        return 0
    return int(delta_bytes * 8 / elapsed_seconds)


def _started_since_previous_poll(
    uptime_seconds: int, now: datetime, previous_poll_at: datetime | None, slack_seconds: int
) -> bool:
    """Whether a session seen for the first time began after the router's
    previous successful poll (plus one polling interval of slack for clock
    and scheduling jitter) -- i.e. all of its bytes are new traffic we have
    not accounted for yet. Always False on a router's very first poll."""
    if previous_poll_at is None:
        return False
    return uptime_seconds <= (now - previous_poll_at).total_seconds() + slack_seconds


def _session_delta(
    session: MikrotikSession,
    state: SessionState | None,
    now: datetime,
    previous_poll_at: datetime | None,
    slack_seconds: int,
) -> tuple[int, int, float]:
    """Bytes (rx, tx) the session transferred since it was last polled, and
    the seconds they span (0 when nothing is counted)."""
    if state is None:
        # First sighting of this session (new client, reconnect, or its
        # router was disabled). Only count its bytes if it started after our
        # previous successful poll of this router; otherwise its counters
        # include traffic from before we were watching (e.g. a months-long
        # session when monitoring starts), so they are just the baseline for
        # future deltas.
        if _started_since_previous_poll(session.uptime_seconds, now, previous_poll_at, slack_seconds):
            return session.rx_bytes, session.tx_bytes, session.uptime_seconds
        return 0, 0, 0
    rx_delta = compute_delta(session.uptime_seconds, session.rx_bytes, state.last_uptime_seconds, state.last_rx_bytes)
    tx_delta = compute_delta(session.uptime_seconds, session.tx_bytes, state.last_uptime_seconds, state.last_tx_bytes)
    # Same "session reset" condition compute_delta uses internally: with
    # uptime going backwards (an id reused after a router reboot)
    # last_poll_at is meaningless for this session, so use its own uptime.
    if session.uptime_seconds < state.last_uptime_seconds:
        return rx_delta, tx_delta, session.uptime_seconds
    return rx_delta, tx_delta, (now - state.last_poll_at).total_seconds()


def poll_router(db: Session, router: Router) -> None:
    try:
        sessions = fetch_active_sessions(router)
    except MikrotikError:
        logger.exception("Polling failed for router %s (%s)", router.name, router.host)
        return

    # Re-read the router under a row lock: if it was disabled (or deleted)
    # while we were talking to it, drop this poll -- otherwise we would
    # re-activate clients that PUT /routers just marked offline. The lock
    # also serializes us against a concurrent PUT until we commit.
    router = (
        db.query(Router)
        .filter(Router.id == router.id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if router is None or not router.enabled:
        db.rollback()
        return

    now = datetime.now(timezone.utc)
    previous_poll_at = router.last_polled_at
    router.last_polled_at = now
    slack_seconds = get_polling_interval_seconds(db)
    pending_notifications: list[PendingNotification] = []

    states = {state.interface_id: state for state in db.query(SessionState).filter_by(router_id=router.id)}
    live_interface_ids: set[str] = set()
    live_names_without_counters: set[str] = set()
    seen_client_ids: set[int] = set()

    sessions_by_username: dict[str, list[MikrotikSession]] = defaultdict(list)
    for session in sessions:
        sessions_by_username[session.username].append(session)

    for username, user_sessions in sessions_by_username.items():
        client = _get_or_create_client(db, router.id, username, now)
        seen_client_ids.add(client.id)

        rx_total = tx_total = rx_bps_total = tx_bps_total = 0
        measured = False
        for session in user_sessions:
            if not session.has_counters:
                # Interface not found (already logged by fetch_active_sessions):
                # the session is alive but its counters are unknown this poll.
                # Keep its state -- matched by name, the only key we have.
                live_names_without_counters.add(session.interface_name)
                continue
            live_interface_ids.add(session.interface_id)

            state = states.get(session.interface_id)
            # An id now carrying another user's session (ids can be reused
            # after a router reboot) is not a continuation of the old one.
            previous = state if state is not None and state.client_id == client.id else None
            rx_delta, tx_delta, elapsed_seconds = _session_delta(
                session, previous, now, previous_poll_at, slack_seconds
            )
            rx_bps = _compute_bps(rx_delta, elapsed_seconds)
            tx_bps = _compute_bps(tx_delta, elapsed_seconds)

            if state is None:
                state = SessionState(router_id=router.id, interface_id=session.interface_id)
                db.add(state)
                states[session.interface_id] = state
            state.client_id = client.id
            state.interface_name = session.interface_name
            state.last_uptime_seconds = session.uptime_seconds
            state.last_rx_bytes = session.rx_bytes
            state.last_tx_bytes = session.tx_bytes
            state.last_poll_at = now
            state.last_rx_bps = rx_bps
            state.last_tx_bps = tx_bps

            rx_total += rx_delta
            tx_total += tx_delta
            rx_bps_total += rx_bps
            tx_bps_total += tx_bps
            measured = True

        if not measured:
            # Online, but no session has known counters this poll: leave the
            # client's samples and totals untouched.
            continue

        db.add(
            TrafficSample(
                client_id=client.id,
                sampled_at=now,
                rx_bytes_delta=rx_total,
                tx_bytes_delta=tx_total,
                rx_bps=rx_bps_total,
                tx_bps=tx_bps_total,
                is_online=True,
            )
        )

        period = _get_or_create_active_period(db, client.id, now)
        period.rx_bytes_total += rx_total
        period.tx_bytes_total += tx_total

        pending_notifications.extend(evaluate_alerts(db, client.id))

    # Drop the state of sessions that ended, so a later sighting of the same
    # id is treated as a new session rather than a continuation (which would
    # corrupt the delta and bps once the counters restart).
    for interface_id, state in states.items():
        if interface_id not in live_interface_ids and state.interface_name not in live_names_without_counters:
            db.delete(state)

    stale_clients = (
        db.query(PPPoEClient)
        .filter(PPPoEClient.router_id == router.id, PPPoEClient.is_active.is_(True))
        .filter(~PPPoEClient.id.in_(seen_client_ids) if seen_client_ids else True)
        .all()
    )
    for client in stale_clients:
        client.is_active = False

    db.commit()

    # Only send notifications after the poll's data has been committed
    # successfully — if an earlier client's AlertEvent were flushed but a
    # later client in the same router raised, poll_all_routers would roll
    # back the whole router and the flushed AlertEvent would disappear while
    # the notification had already gone out, causing a duplicate on the next
    # poll. Sending here, post-commit, means a notification is only ever
    # sent for an AlertEvent that is durably persisted.
    for channel, subject, message in pending_notifications:
        try:
            notify(db, subject, message, channel)
        except Exception:
            logger.exception("Failed to send alert notification (%s)", subject)


def poll_all_routers() -> None:
    db = SessionLocal()
    try:
        routers = db.query(Router).filter_by(enabled=True).all()
        for router in routers:
            try:
                poll_router(db, router)
            except Exception:
                logger.exception("Unexpected error polling router %s (%s)", router.name, router.host)
                db.rollback()
    finally:
        db.close()
```

- [ ] **Step 6: Deshabilitar un router borra el estado por `router_id`**

En `backend/app/api/routers.py`, dentro de `if data.get("enabled") is False:`, reemplazar las dos primeras sentencias (`client_ids = …` y el `delete` de `SessionState`) por:

```python
        db.query(SessionState).filter(SessionState.router_id == router_id).delete(synchronize_session=False)
```

y en el comentario de arriba, cambiar "drop their session state" por "drop its sessions' state".

- [ ] **Step 7: Migrar la base de desarrollo y correr la suite**

Run:

```bash
remote 'cd backend && export JWT_SECRET=pytest-only-jwt-secret-not-for-production-use-0123456789 && .venv/bin/alembic upgrade head && .venv/bin/alembic check && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head && .venv/bin/pytest -q'
```

Expected: `Running upgrade ac6bc5da9228 -> 5d2b7e9c41a0`, `No new upgrade operations detected.`, el downgrade y el upgrade de nuevo sin errores, y toda la suite en PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models backend/alembic/versions/5d2b7e9c41a0_session_state_per_session_and_traffic_hourly.py backend/app/services/polling.py backend/app/api/routers.py backend/tests/test_polling.py backend/tests/test_api_routers.py backend/tests/test_api_clients.py backend/tests/test_api_dashboard.py
git commit -m "feat(backend): guardar el estado de cada sesión PPPoE y sumar las sesiones de un cliente" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Velocidad actual = suma de las sesiones

**Files:**
- Modify: `backend/app/models/client.py`, `backend/app/api/clients.py`, `backend/app/api/dashboard.py`
- Test: `backend/tests/test_api_clients.py`, `backend/tests/test_api_dashboard.py`

**Interfaces:**
- Consumes: `SessionState` de la Tarea 2.
- Produces: `session_speed_subquery()` en `app/models/client.py`, que devuelve una subquery con columnas `client_id`, `rx_bps` y `tx_bps` (sumas por cliente).

- [ ] **Step 1: Escribir los tests que fallan**

Al final de `backend/tests/test_api_clients.py`:

```python
def test_current_speed_is_the_sum_of_all_sessions_and_client_listed_once():
    db = SessionLocal()
    try:
        double_id = _seed_client(db, "two_sessions_user", 100, rx_bps=1000, tx_bps=100)
        router_id = db.get(PPPoEClient, double_id).router_id
        db.add(
            SessionState(
                router_id=router_id,
                client_id=double_id,
                interface_id="*two_sessions_user-1",
                last_rx_bps=500,
                last_tx_bps=50,
            )
        )
        db.commit()
        _seed_client(db, "one_session_user", 100, rx_bps=1200, tx_bps=200)
    finally:
        db.close()

    try:
        response = client.get(
            "/clients?q=_session&sort_by=current&dir=desc&page_size=200", headers=_auth_headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        items = body["items"]
        assert [c["username"] for c in items] == ["two_sessions_user", "one_session_user"]
        assert (items[0]["current_rx_bps"], items[0]["current_tx_bps"]) == (1500, 150)

        single = client.get(f"/clients/{double_id}", headers=_auth_headers()).json()
        assert (single["current_rx_bps"], single["current_tx_bps"]) == (1500, 150)
    finally:
        _cleanup()
```

Al final de `backend/tests/test_api_dashboard.py`:

```python
def test_dashboard_counts_a_client_with_two_sessions_once_and_sums_its_speed():
    db: Session = SessionLocal()
    try:
        router = Router(name="Dash Two Sessions", host="10.0.0.11", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        client_id = _add_client(db, router.id, "doble", True, (1000, 100))
        db.add(
            SessionState(
                router_id=router.id, client_id=client_id, interface_id="*doble-1", last_rx_bps=500, last_tx_bps=50
            )
        )
        db.commit()
        router_id = router.id
    finally:
        db.close()

    try:
        response = client.get("/dashboard/summary", headers=_auth_headers())
        assert response.status_code == 200
        entry = next(r for r in response.json()["by_router"] if r["router_id"] == router_id)
        assert entry["clients_connected"] == 1
        assert (entry["current_rx_bps"], entry["current_tx_bps"]) == (1500, 150)
    finally:
        _cleanup([router_id])
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_api_clients.py tests/test_api_dashboard.py'`
Expected: FAIL. El cliente aparece dos veces (`total == 3`) y el dashboard cuenta 2 conectados.

- [ ] **Step 3: Implementar**

En `backend/app/models/client.py`, agregar `func` y `select` a los imports de `sqlalchemy` y, al final:

```python
def session_speed_subquery():
    """Current speed per client: the sum of its live sessions' last bps.
    A client without sessions (offline) has no row here -- coalesce to 0."""
    return (
        select(
            SessionState.client_id.label("client_id"),
            func.sum(SessionState.last_rx_bps).label("rx_bps"),
            func.sum(SessionState.last_tx_bps).label("tx_bps"),
        )
        .group_by(SessionState.client_id)
        .subquery("session_speed")
    )
```

En `backend/app/api/clients.py`:

1. Cambiar el import a `from app.models.client import PPPoEClient, session_speed_subquery`.
2. Reemplazar el comentario y las definiciones de `_RX_BPS`/`_TX_BPS`:

```python
# Current bps comes from session_state (one row per live PPPoE session,
# written by every poll, deleted when the session ends), summed per client
# -- no scan of traffic_samples.
_SESSION_SPEED = session_speed_subquery()
_RX_TOTAL = func.coalesce(AccumulationPeriod.rx_bytes_total, 0)
_TX_TOTAL = func.coalesce(AccumulationPeriod.tx_bytes_total, 0)
_RX_BPS = func.coalesce(_SESSION_SPEED.c.rx_bps, 0)
_TX_BPS = func.coalesce(_SESSION_SPEED.c.tx_bps, 0)
```

3. En `_clients_query`, reemplazar `.outerjoin(SessionState, SessionState.client_id == PPPoEClient.id)` por:

```python
        .outerjoin(_SESSION_SPEED, _SESSION_SPEED.c.client_id == PPPoEClient.id)
```

En `backend/app/api/dashboard.py`:

1. Cambiar el import a `from app.models.client import PPPoEClient, session_speed_subquery`.
2. Reemplazar el cuerpo de la consulta de `summary` (hasta `.all()`):

```python
    # Current throughput comes from session_state (one row per live PPPoE
    # session, summed per client), so this never scans traffic_samples.
    # Disabled routers are excluded.
    speed = session_speed_subquery()
    rows = (
        db.query(
            Router.id,
            Router.name,
            Router.last_polled_at,
            func.count(PPPoEClient.id),
            func.coalesce(func.sum(speed.c.rx_bps), 0),
            func.coalesce(func.sum(speed.c.tx_bps), 0),
        )
        .filter(Router.enabled.is_(True))
        .outerjoin(PPPoEClient, (PPPoEClient.router_id == Router.id) & (PPPoEClient.is_active.is_(True)))
        .outerjoin(speed, speed.c.client_id == PPPoEClient.id)
        .group_by(Router.id, Router.name, Router.last_polled_at)
        .order_by(Router.name)
        .all()
    )
```

- [ ] **Step 4: Correr la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/client.py backend/app/api/clients.py backend/app/api/dashboard.py backend/tests/test_api_clients.py backend/tests/test_api_dashboard.py
git commit -m "feat(backend): velocidad actual como suma de las sesiones del cliente" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Configuración `raw_retention_days`

**Files:**
- Modify: `backend/app/services/app_settings.py`, `backend/app/schemas/settings.py`, `backend/app/api/settings.py`
- Test: `backend/tests/test_api_settings.py`

**Interfaces:**
- Produces: `DEFAULT_RAW_RETENTION_DAYS = 7` y `get_raw_retention_days(db) -> int` en `app/services/app_settings.py`. Campo `raw_retention_days` en `SettingsOut` y `SettingsUpdate`.

- [ ] **Step 1: Escribir los tests que fallan**

En `backend/tests/test_api_settings.py`:

1. `_ALL_TOUCHED` pasa a ser:

```python
_ALL_TOUCHED = ["polling_interval_seconds", "reset_day_of_month", "retention_days", "raw_retention_days", *_OPTIONAL_KEYS]
```

2. En `_ui_payload`, agregar `"raw_retention_days": 7,` después de `"retention_days": 90,`.
3. En el `parametrize` de `test_put_settings_rejects_null_for_numeric_settings`, agregar `"raw_retention_days"`.
4. En `test_get_settings_tolerates_garbage_stored_values`, agregar `"raw_retention_days": "x",` al `_restore` y `assert body["raw_retention_days"] == 7` a las aserciones.
5. Agregar al final:

```python
def test_get_settings_defaults_raw_retention_to_seven_days():
    snapshot = _snapshot(["raw_retention_days"])
    _restore({"raw_retention_days": None})
    try:
        response = client.get("/settings", headers=_auth_headers())
        assert response.status_code == 200
        assert response.json()["raw_retention_days"] == 7
    finally:
        _restore(snapshot)


def test_put_settings_updates_raw_retention_days():
    headers = _auth_headers()
    snapshot = _snapshot(["raw_retention_days", "retention_days"])
    _restore({"retention_days": "90"})
    try:
        response = client.put("/settings", json={"raw_retention_days": 3}, headers=headers)
        assert response.status_code == 200
        assert response.json()["raw_retention_days"] == 3
        assert _snapshot(["raw_retention_days"]) == {"raw_retention_days": "3"}
    finally:
        _restore(snapshot)


def test_put_settings_rejects_raw_retention_below_minimum():
    response = client.put("/settings", json={"raw_retention_days": 0}, headers=_auth_headers())
    assert response.status_code == 422


def test_put_settings_rejects_raw_retention_longer_than_retention():
    headers = _auth_headers()
    snapshot = _snapshot(["raw_retention_days", "retention_days"])
    _restore({"raw_retention_days": "7", "retention_days": "90"})
    try:
        too_long = client.put("/settings", json={"raw_retention_days": 91}, headers=headers)
        assert too_long.status_code == 422
        # Lowering retention below the stored raw retention is rejected too.
        too_short = client.put("/settings", json={"retention_days": 5}, headers=headers)
        assert too_short.status_code == 422
        assert _snapshot(["raw_retention_days", "retention_days"]) == {
            "raw_retention_days": "7",
            "retention_days": "90",
        }
        # Both at once, consistent: accepted.
        both = client.put("/settings", json={"raw_retention_days": 30, "retention_days": 30}, headers=headers)
        assert both.status_code == 200
    finally:
        _restore(snapshot)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_api_settings.py'`
Expected: FAIL (`KeyError: 'raw_retention_days'`, y el 422 no aparece).

- [ ] **Step 3: Implementar**

En `backend/app/services/app_settings.py`, agregar `DEFAULT_RAW_RETENTION_DAYS = 7` debajo de `DEFAULT_RETENTION_DAYS` y, al final:

```python
def get_raw_retention_days(db: Session) -> int:
    """Days of 5-minute samples kept; older traffic survives only as the
    hourly rollup (kept for get_retention_days)."""
    return get_int_setting(db, "raw_retention_days", DEFAULT_RAW_RETENTION_DAYS, min_value=1)
```

En `backend/app/schemas/settings.py`:
- `SettingsOut`: agregar `raw_retention_days: int` después de `retention_days: int`.
- `SettingsUpdate`: agregar `raw_retention_days: int | None = Field(default=None, ge=1)` después de `retention_days`, y sumar `"raw_retention_days"` al `@field_validator(...)` de `_reject_explicit_null`.

En `backend/app/api/settings.py`:
1. Importar también `DEFAULT_RAW_RETENTION_DAYS` desde `app.services.app_settings`, y `HTTPException` desde `fastapi`.
2. Agregar `"raw_retention_days",` a `_ALL_KEYS` después de `"retention_days",`.
3. En `get_settings`, después de `retention_days=…`:

```python
        raw_retention_days=parse_int_setting(
            "raw_retention_days", values.get("raw_retention_days"), DEFAULT_RAW_RETENTION_DAYS, min_value=1
        ),
```

4. En `update_settings`, justo después del bucle que descarta `SECRET_MASK`:

```python
    # 5-minute samples can't outlive the hourly rollup they are summed into.
    current = get_settings(db)
    raw_days = updates.get("raw_retention_days", current.raw_retention_days)
    retention_days = updates.get("retention_days", current.retention_days)
    if raw_days > retention_days:
        raise HTTPException(
            status_code=422,
            detail="La retención de detalle no puede ser mayor que la retención de historial.",
        )
```

- [ ] **Step 4: Correr la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/app_settings.py backend/app/schemas/settings.py backend/app/api/settings.py backend/tests/test_api_settings.py
git commit -m "feat(backend): configurar los días de retención de las muestras de 5 minutos" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Resumen horario (`services/rollup.py`) y su job

**Files:**
- Create: `backend/app/services/rollup.py`, `backend/tests/test_rollup.py`
- Modify: `backend/app/services/scheduler.py`, `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `TrafficHourly` (Tarea 2), `AppSetting`.
- Produces, en `app/services/rollup.py`:
  - `HOURLY_ROLLUP_KEY = "hourly_rollup_until"`
  - `floor_hour(moment: datetime) -> datetime` (UTC)
  - `get_rollup_watermark(db) -> datetime | None` (None si falta o es inválida)
  - `run_hourly_rollup(db, now: datetime | None = None) -> datetime | None` (devuelve la marca nueva)
  - `run_rollup_job() -> None`
  - `HourlyPoint(hour_start, rx_bytes, tx_bytes, rx_bps, tx_bps, peak_rx_bps, peak_tx_bps)` y `client_hourly_history(db, client_id: int, since: datetime, now: datetime) -> list[HourlyPoint]`

- [ ] **Step 1: Escribir los tests que fallan**

Crear `backend/tests/test_rollup.py`:

```python
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import TrafficHourly, TrafficSample
from app.services.rollup import (
    HOURLY_ROLLUP_KEY,
    client_hourly_history,
    get_rollup_watermark,
    run_hourly_rollup,
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def keep_watermark(db):
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    original = row.value if row else None
    yield
    db.rollback()
    _set_watermark(db, original)


@pytest.fixture
def client_id(db):
    router = Router(name="Rollup Test Router", host="10.0.0.50", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="rollup_user")
    db.add(client)
    db.commit()
    yield client.id
    db.rollback()
    # ON DELETE CASCADE removes the client, its samples and hourly rows.
    db.query(Router).filter(Router.id == router.id).delete()
    db.commit()


def _set_watermark(db: Session, value: datetime | str | None) -> None:
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    if value is None:
        if row is not None:
            db.delete(row)
    else:
        text_value = value.isoformat() if isinstance(value, datetime) else value
        if row is None:
            db.add(AppSetting(key=HOURLY_ROLLUP_KEY, value=text_value))
        else:
            row.value = text_value
    db.commit()


def _sample(db: Session, client_id: int, at: datetime, rx: int, tx: int, rx_bps: int = 0, tx_bps: int = 0) -> None:
    db.add(
        TrafficSample(
            client_id=client_id, sampled_at=at, rx_bytes_delta=rx, tx_bytes_delta=tx, rx_bps=rx_bps, tx_bps=tx_bps
        )
    )
    db.commit()


def _hourly(db: Session, client_id: int) -> list[tuple]:
    db.expire_all()
    rows = db.query(TrafficHourly).filter_by(client_id=client_id).order_by(TrafficHourly.hour_start).all()
    return [(r.hour_start, r.rx_bytes, r.tx_bytes, r.peak_rx_bps, r.peak_tx_bps) for r in rows]


def test_rollup_sums_bytes_and_keeps_peak_per_hour(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10, rx_bps=50, tx_bps=5)
    _sample(db, client_id, _at(1, 10, 35), 200, 20, rx_bps=80, tx_bps=3)
    _sample(db, client_id, _at(1, 11, 10), 400, 40, rx_bps=20, tx_bps=9)
    _set_watermark(db, _at(1, 10))

    new_watermark = run_hourly_rollup(db, now=_at(1, 12, 30))

    assert _hourly(db, client_id) == [
        (_at(1, 10), 300, 30, 80, 5),
        (_at(1, 11), 400, 40, 20, 9),
    ]
    assert new_watermark == _at(1, 12)
    assert get_rollup_watermark(db) == _at(1, 12)


def test_rollup_never_summarizes_the_hour_in_progress(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10)
    _sample(db, client_id, _at(1, 11, 10), 400, 40)
    _set_watermark(db, _at(1, 10))

    run_hourly_rollup(db, now=_at(1, 11, 20))

    assert [row[0] for row in _hourly(db, client_id)] == [_at(1, 10)]
    assert get_rollup_watermark(db) == _at(1, 11)


def test_rollup_rerun_replaces_instead_of_adding(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 100, 10, rx_bps=50, tx_bps=5)
    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))

    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))

    assert _hourly(db, client_id) == [(_at(1, 10), 100, 10, 50, 5)]


def test_rollup_without_watermark_starts_at_oldest_sample_and_crosses_chunks(db, client_id):
    """The first run on an existing database covers days of samples in
    several committed chunks: no hour may be lost or doubled at a border."""
    _sample(db, client_id, _at(1, 10, 5), 100, 1)
    _sample(db, client_id, _at(2, 9, 55), 200, 2)
    _sample(db, client_id, _at(2, 10, 5), 300, 3)
    _sample(db, client_id, _at(2, 23, 55), 400, 4)
    _sample(db, client_id, _at(3, 0, 5), 500, 5)
    _set_watermark(db, None)

    run_hourly_rollup(db, now=_at(3, 1, 30))

    assert [(row[0], row[1]) for row in _hourly(db, client_id)] == [
        (_at(1, 10), 100),
        (_at(2, 9), 200),
        (_at(2, 10), 300),
        (_at(2, 23), 400),
        (_at(3, 0), 500),
    ]
    assert get_rollup_watermark(db) == _at(3, 1)


def test_garbage_watermark_reads_as_missing(db):
    _set_watermark(db, "garbage")
    assert get_rollup_watermark(db) is None
    _set_watermark(db, "2026-01-01T10:00:00")  # no timezone
    assert get_rollup_watermark(db) is None


def test_client_hourly_history_mixes_rollup_and_live_samples(db, client_id):
    _sample(db, client_id, _at(1, 10, 5), 1_800_000, 900_000, rx_bps=9_000, tx_bps=4_000)
    _set_watermark(db, _at(1, 10))
    run_hourly_rollup(db, now=_at(1, 11, 30))
    # Hour 11 is not rolled up yet: it comes from the samples on the fly.
    _sample(db, client_id, _at(1, 11, 5), 450_000, 0, rx_bps=7_000, tx_bps=0)
    _sample(db, client_id, _at(1, 11, 10), 450_000, 0, rx_bps=5_000, tx_bps=0)

    points = client_hourly_history(db, client_id, since=_at(1, 9, 30), now=_at(1, 12, 30))

    assert [(p.hour_start, p.rx_bytes, p.rx_bps, p.peak_rx_bps) for p in points] == [
        (_at(1, 10), 1_800_000, 1_800_000 * 8 // 3600, 9_000),
        (_at(1, 11), 900_000, 900_000 * 8 // 3600, 7_000),
    ]


def test_client_hourly_history_hour_in_progress_uses_elapsed_seconds(db, client_id):
    _set_watermark(db, None)  # nothing rolled up: the whole range is live
    _sample(db, client_id, _at(1, 10, 10), 1800, 900)

    (point,) = client_hourly_history(db, client_id, since=_at(1, 8), now=_at(1, 10, 30))

    assert point.hour_start == _at(1, 10)
    # 30 minutes (1800 s) of the hour have elapsed.
    assert (point.rx_bps, point.tx_bps) == (1800 * 8 // 1800, 900 * 8 // 1800)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_rollup.py'`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.services.rollup'`).

- [ ] **Step 3: Implementar `app/services/rollup.py`**

```python
"""Hourly rollup of traffic_samples into traffic_hourly.

5-minute samples are only kept for raw_retention_days (see purge.py); long
history ranges are served from these per-hour sums. A watermark in the
settings table (HOURLY_ROLLUP_KEY) records how far the rollup is complete:
every closed hour before it is in traffic_hourly, and purge never deletes a
sample at or after it.

Samples are always written with sampled_at = now, so a closed hour never
gets new samples: recomputing an hour replaces it with the same values.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.models.traffic import TrafficHourly, TrafficSample

logger = logging.getLogger(__name__)

HOURLY_ROLLUP_KEY = "hourly_rollup_until"
# A first rollup over days of existing samples is split into chunks, each
# committed together with its watermark, so an interruption keeps progress.
ROLLUP_CHUNK = timedelta(days=1)

# One row per (client, UTC hour) with samples in [:since, :until).
_HOURLY_BUCKETS = """
    SELECT client_id,
           date_trunc('hour', sampled_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS hour_start,
           SUM(rx_bytes_delta) AS rx_bytes,
           SUM(tx_bytes_delta) AS tx_bytes,
           MAX(rx_bps) AS peak_rx_bps,
           MAX(tx_bps) AS peak_tx_bps
    FROM traffic_samples
    WHERE sampled_at >= :since AND sampled_at < :until {client_filter}
    GROUP BY 1, 2
"""

_UPSERT_HOURS = text(
    "INSERT INTO traffic_hourly (client_id, hour_start, rx_bytes, tx_bytes, peak_rx_bps, peak_tx_bps)"
    + _HOURLY_BUCKETS.format(client_filter="")
    + """
    ON CONFLICT (client_id, hour_start) DO UPDATE SET
        rx_bytes = EXCLUDED.rx_bytes,
        tx_bytes = EXCLUDED.tx_bytes,
        peak_rx_bps = EXCLUDED.peak_rx_bps,
        peak_tx_bps = EXCLUDED.peak_tx_bps
    """
)

_CLIENT_HOURS = text(_HOURLY_BUCKETS.format(client_filter="AND client_id = :client_id") + " ORDER BY 2")


def floor_hour(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def get_rollup_watermark(db: Session) -> datetime | None:
    """The instant (UTC, on the hour) up to which traffic_hourly is complete,
    or None if there is none -- or it is unreadable, which is treated the
    same: the rollup starts over from the oldest sample (harmless, hours are
    replaced) and purge keeps every sample."""
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    if row is None or not row.value.strip():
        return None
    try:
        value = datetime.fromisoformat(row.value)
    except ValueError:
        value = None
    if value is None or value.tzinfo is None:
        logger.warning("Setting %s has invalid value %r; treating it as missing", HOURLY_ROLLUP_KEY, row.value)
        return None
    return floor_hour(value)


def _set_rollup_watermark(db: Session, value: datetime) -> None:
    row = db.get(AppSetting, HOURLY_ROLLUP_KEY)
    if row is None:
        db.add(AppSetting(key=HOURLY_ROLLUP_KEY, value=value.isoformat()))
    else:
        row.value = value.isoformat()


def run_hourly_rollup(db: Session, now: datetime | None = None) -> datetime | None:
    """Roll up every closed hour from the watermark (or the oldest sample)
    to the start of the current hour -- the hour in progress never is --
    and return the new watermark."""
    until = floor_hour(now or datetime.now(timezone.utc))
    since = get_rollup_watermark(db)
    if since is None:
        oldest = db.query(func.min(TrafficSample.sampled_at)).scalar()
        since = floor_hour(oldest) if oldest is not None else until
        if since >= until:
            # Nothing closed to roll up yet: everything before `until` is
            # (trivially) complete.
            _set_rollup_watermark(db, until)
            db.commit()
            return until
    while since < until:
        chunk_end = min(since + ROLLUP_CHUNK, until)
        db.execute(_UPSERT_HOURS, {"since": since, "until": chunk_end})
        _set_rollup_watermark(db, chunk_end)
        db.commit()
        since = chunk_end
    return get_rollup_watermark(db)


def run_rollup_job() -> None:
    db = SessionLocal()
    try:
        run_hourly_rollup(db)
    finally:
        db.close()


@dataclass(frozen=True)
class HourlyPoint:
    hour_start: datetime
    rx_bytes: int
    tx_bytes: int
    # Average over the hour -- or over its elapsed part, for the hour in
    # progress.
    rx_bps: int
    tx_bps: int
    peak_rx_bps: int
    peak_tx_bps: int


def client_hourly_history(db: Session, client_id: int, since: datetime, now: datetime) -> list[HourlyPoint]:
    """One point per hour with traffic, from the hour containing `since` to
    now: rolled-up hours from traffic_hourly, the rest (not rolled up yet,
    including the hour in progress) aggregated on the fly from the samples."""
    start = floor_hour(since)
    watermark = get_rollup_watermark(db)
    boundary = max(start, watermark) if watermark is not None else start

    stored = (
        db.query(TrafficHourly)
        .filter(
            TrafficHourly.client_id == client_id,
            TrafficHourly.hour_start >= start,
            TrafficHourly.hour_start < boundary,
        )
        .order_by(TrafficHourly.hour_start)
        .all()
    )
    rows = [(h.hour_start, h.rx_bytes, h.tx_bytes, h.peak_rx_bps, h.peak_tx_bps) for h in stored]
    current_hour = floor_hour(now)
    live = db.execute(
        _CLIENT_HOURS,
        {"since": boundary, "until": current_hour + timedelta(hours=1), "client_id": client_id},
    )
    rows += [(r.hour_start, r.rx_bytes, r.tx_bytes, r.peak_rx_bps, r.peak_tx_bps) for r in live]

    points = []
    for hour_start, rx_bytes, tx_bytes, peak_rx_bps, peak_tx_bps in rows:
        seconds = 3600 if hour_start < current_hour else max((now - hour_start).total_seconds(), 1)
        points.append(
            HourlyPoint(
                hour_start=hour_start,
                rx_bytes=int(rx_bytes),
                tx_bytes=int(tx_bytes),
                rx_bps=int(int(rx_bytes) * 8 / seconds),
                tx_bps=int(int(tx_bytes) * 8 / seconds),
                peak_rx_bps=int(peak_rx_bps),
                peak_tx_bps=int(peak_tx_bps),
            )
        )
    return points
```

- [ ] **Step 4: Correr los tests de rollup**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_rollup.py'`
Expected: PASS.

- [ ] **Step 5: Test del scheduler que falla**

En `backend/tests/test_scheduler.py`, agregar debajo del fixture `reset_job_calls`:

```python
@pytest.fixture(autouse=True)
def rollup_job_calls(monkeypatch):
    """start_scheduler runs the rollup job immediately; never let it touch
    the dev database from these tests. Records calls instead."""
    called = threading.Event()
    monkeypatch.setattr("app.services.scheduler.run_rollup_job", called.set)
    return called
```

y al final:

```python
def test_scheduler_runs_rollup_at_startup_and_hourly(rollup_job_calls):
    scheduler = start_scheduler()
    try:
        assert rollup_job_calls.wait(timeout=5), "rollup job did not run at startup"
        job = scheduler.get_job("run_rollup_job")
        assert job.next_run_time.minute == 2
    finally:
        stop_scheduler(scheduler)
```

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_scheduler.py'`
Expected: FAIL (`AttributeError: ... has no attribute 'run_rollup_job'`).

- [ ] **Step 6: Registrar el job**

En `backend/app/services/scheduler.py`, importar `from app.services.rollup import run_rollup_job` y agregar, antes del job de purga:

```python
    # Hourly rollup of the 5-minute samples (see app/services/rollup.py), a
    # couple of minutes after the hour so the hour's last poll has committed;
    # and once at startup to catch up.
    scheduler.add_job(
        run_rollup_job,
        trigger=CronTrigger(minute=2, timezone=tz),
        id="run_rollup_job",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(tz),
    )
```

- [ ] **Step 7: Correr la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/rollup.py backend/app/services/scheduler.py backend/tests/test_rollup.py backend/tests/test_scheduler.py
git commit -m "feat(backend): resumir el tráfico por hora con marca de agua" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Purga con dos retenciones y por lotes

**Files:**
- Modify: `backend/app/services/purge.py`, `README.md`
- Test: `backend/tests/test_purge.py` (reescribir)

**Interfaces:**
- Consumes: `get_raw_retention_days`, `get_retention_days` (Tarea 4) y `get_rollup_watermark`, `floor_hour`, `HOURLY_ROLLUP_KEY` (Tarea 5).
- Produces: `BATCH_SIZE = 10_000`, `PurgeResult(samples: int, hourly: int)` y `purge_old_samples(db, today=None, batch_size=BATCH_SIZE) -> PurgeResult`. `run_purge_job()` sin cambios.

- [ ] **Step 1: Escribir los tests que fallan**

Reemplazar `backend/tests/test_purge.py` completo:

```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import TrafficHourly, TrafficSample
from app.services.purge import purge_old_samples
from app.services.rollup import HOURLY_ROLLUP_KEY, floor_hour

_KEYS = ["retention_days", "raw_retention_days", HOURLY_ROLLUP_KEY]


def _write_settings(db: Session, values: dict[str, str | None]) -> None:
    for key, value in values.items():
        row = db.get(AppSetting, key)
        if value is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()


@pytest.fixture
def db():
    session = SessionLocal()
    snapshot = {key: (row.value if (row := session.get(AppSetting, key)) else None) for key in _KEYS}
    _write_settings(session, {"retention_days": "90", "raw_retention_days": "7"})
    yield session
    session.rollback()
    _write_settings(session, snapshot)
    session.close()


@pytest.fixture
def client_id(db):
    router = Router(name="Purge Test", host="10.0.0.6", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="purge_user")
    db.add(client)
    db.commit()
    yield client.id
    db.rollback()
    db.query(Router).filter(Router.id == router.id).delete()  # cascades to client data
    db.commit()


NOW = datetime.now(timezone.utc)


def _samples(db: Session, client_id: int, *ages: timedelta) -> list[int]:
    rows = [TrafficSample(client_id=client_id, sampled_at=NOW - age) for age in ages]
    db.add_all(rows)
    db.commit()
    return [row.id for row in rows]


def _hours(db: Session, client_id: int, *ages: timedelta) -> None:
    db.add_all(
        TrafficHourly(
            client_id=client_id, hour_start=floor_hour(NOW - age), rx_bytes=1, tx_bytes=1, peak_rx_bps=1, peak_tx_bps=1
        )
        for age in ages
    )
    db.commit()


def _remaining_samples(db: Session, client_id: int) -> list[int]:
    return [row.id for row in db.query(TrafficSample).filter_by(client_id=client_id).order_by(TrafficSample.id)]


def _remaining_hours(db: Session, client_id: int) -> list[datetime]:
    rows = db.query(TrafficHourly).filter_by(client_id=client_id).order_by(TrafficHourly.hour_start)
    return [row.hour_start for row in rows]


def test_purge_keeps_samples_for_raw_retention_and_hours_for_retention(db, client_id):
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(NOW).isoformat()})
    _old, recent = _samples(db, client_id, timedelta(days=8), timedelta(days=6))
    _hours(db, client_id, timedelta(days=91), timedelta(days=89))

    result = purge_old_samples(db, today=NOW)

    assert _remaining_samples(db, client_id) == [recent]
    assert _remaining_hours(db, client_id) == [floor_hour(NOW - timedelta(days=89))]
    assert result.samples >= 1 and result.hourly >= 1

    again = purge_old_samples(db, today=NOW)
    assert _remaining_samples(db, client_id) == [recent]
    assert again.samples == 0 and again.hourly == 0


def test_purge_never_deletes_samples_not_rolled_up_yet(db, client_id):
    # The rollup lags 10 days behind (e.g. the job was failing).
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(NOW - timedelta(days=10)).isoformat()})
    _rolled_up, pending = _samples(db, client_id, timedelta(days=12), timedelta(days=9))

    purge_old_samples(db, today=NOW)

    assert _remaining_samples(db, client_id) == [pending]


@pytest.mark.parametrize("watermark", [None, "garbage"])
def test_purge_without_usable_watermark_deletes_no_samples(db, client_id, watermark):
    _write_settings(db, {HOURLY_ROLLUP_KEY: watermark})
    ids = _samples(db, client_id, timedelta(days=30))
    _hours(db, client_id, timedelta(days=91))

    purge_old_samples(db, today=NOW)

    assert _remaining_samples(db, client_id) == ids
    assert _remaining_hours(db, client_id) == []  # the hourly retention still applies


def test_purge_deletes_in_batches(db, client_id, monkeypatch):
    _write_settings(db, {HOURLY_ROLLUP_KEY: floor_hour(NOW).isoformat()})
    _samples(db, client_id, *[timedelta(days=20, minutes=i) for i in range(5)])
    batches: list[int] = []
    real_execute = db.execute

    def counting_execute(statement, params=None, *args, **kwargs):
        if params and "batch_size" in params:
            batches.append(params["batch_size"])
        return real_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(db, "execute", counting_execute)

    purge_old_samples(db, today=NOW, batch_size=2)

    assert _remaining_samples(db, client_id) == []
    # 5 old samples in batches of 2: at least 3 sample batches + 1 hourly batch.
    assert len(batches) >= 4 and set(batches) == {2}
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_purge.py'`
Expected: FAIL (`purge_old_samples() got an unexpected keyword argument 'batch_size'`; el resultado es un `int`).

- [ ] **Step 3: Implementar**

Reemplazar `backend/app/services/purge.py` completo:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.app_settings import get_raw_retention_days, get_retention_days
from app.services.rollup import get_rollup_watermark

BATCH_SIZE = 10_000

_DELETE_SAMPLES_BATCH = text(
    "DELETE FROM traffic_samples WHERE id IN "
    "(SELECT id FROM traffic_samples WHERE sampled_at < :cutoff LIMIT :batch_size)"
)
_DELETE_HOURS_BATCH = text(
    "DELETE FROM traffic_hourly WHERE (client_id, hour_start) IN "
    "(SELECT client_id, hour_start FROM traffic_hourly WHERE hour_start < :cutoff LIMIT :batch_size)"
)


@dataclass(frozen=True)
class PurgeResult:
    samples: int
    hourly: int


def _delete_in_batches(db: Session, statement, cutoff: datetime, batch_size: int) -> int:
    """Delete in batches with a commit after each, so a large backlog never
    holds one huge transaction (and its locks) open against the poller."""
    total = 0
    while True:
        deleted = db.execute(statement, {"cutoff": cutoff, "batch_size": batch_size}).rowcount
        db.commit()
        total += deleted
        if deleted < batch_size:
            return total


def purge_old_samples(db: Session, today: datetime | None = None, batch_size: int = BATCH_SIZE) -> PurgeResult:
    today = today or datetime.now(timezone.utc)

    # 5-minute samples live raw_retention_days -- but a sample not yet summed
    # into traffic_hourly is never deleted: stop at the rollup watermark, and
    # without a (readable) watermark delete none.
    samples = 0
    watermark = get_rollup_watermark(db)
    if watermark is not None:
        cutoff = min(today - timedelta(days=get_raw_retention_days(db)), watermark)
        samples = _delete_in_batches(db, _DELETE_SAMPLES_BATCH, cutoff, batch_size)

    hourly_cutoff = today - timedelta(days=get_retention_days(db))
    hourly = _delete_in_batches(db, _DELETE_HOURS_BATCH, hourly_cutoff, batch_size)
    return PurgeResult(samples=samples, hourly=hourly)


def run_purge_job() -> None:
    db = SessionLocal()
    try:
        purge_old_samples(db)
    finally:
        db.close()
```

- [ ] **Step 4: Actualizar el README**

En `README.md`, sección "Configuración post-despliegue", reemplazar la línea `- Retención del historial de gráficos en días (default 90, mínimo 1).` por:

```markdown
- Retención de historial en días (default 90, mínimo 1): cuánto tiempo se
  guarda el resumen de tráfico por hora de cada cliente.
- Retención de detalle en días (default 7, mínimo 1, no mayor que la
  retención de historial): cuánto tiempo se guardan las muestras de cada
  sondeo (5 minutos). Pasado ese plazo queda solo el resumen por hora. Los
  gráficos de hasta 7 días usan el detalle; los de 30 y 90 días, un punto por
  hora con el promedio y el pico.
```

y en la misma sección, cambiar `Los tres valores numéricos (polling, día de reseteo, retención)` por `Los valores numéricos (polling, día de reseteo, retenciones)`.

- [ ] **Step 5: Correr la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/purge.py backend/tests/test_purge.py README.md
git commit -m "feat(backend): purgar por lotes con retención de detalle y de resumen horario" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Historial horario en la API

**Files:**
- Modify: `backend/app/schemas/client.py`, `backend/app/api/clients.py`
- Test: `backend/tests/test_api_clients.py`

**Interfaces:**
- Consumes: `get_raw_retention_days` (Tarea 4), `client_hourly_history`, `floor_hour`, `HOURLY_ROLLUP_KEY` (Tarea 5).
- Produces: `ClientHistoryPoint` con `peak_rx_bps: int | None = None` y `peak_tx_bps: int | None = None`. `GET /clients/{id}/history?hours=N` con `1 <= N <= 2160`.

- [ ] **Step 1: Escribir los tests que fallan**

En `backend/tests/test_api_clients.py`, sumar `from app.models.settings import AppSetting`, `from app.models.traffic import TrafficHourly` (a la línea de traffic existente) y `from app.services.rollup import HOURLY_ROLLUP_KEY, floor_hour`. Agregar al final:

```python
def _settings_snapshot(keys: list[str]) -> dict[str, str | None]:
    db = SessionLocal()
    try:
        return {key: (row.value if (row := db.get(AppSetting, key)) else None) for key in keys}
    finally:
        db.close()


def _write_settings(values: dict[str, str | None]) -> None:
    db = SessionLocal()
    try:
        for key, value in values.items():
            row = db.get(AppSetting, key)
            if value is None:
                if row is not None:
                    db.delete(row)
            elif row is None:
                db.add(AppSetting(key=key, value=value))
            else:
                row.value = value
        db.commit()
    finally:
        db.close()


def _bare_client(db: Session, username: str) -> int:
    router = Router(name=f"History Router {username}", host="10.0.0.12", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    _created_router_ids.append(router.id)
    c = PPPoEClient(router_id=router.id, username=username)
    db.add(c)
    db.commit()
    _created_client_ids.append(c.id)
    return c.id


def test_client_history_short_range_returns_samples_without_peaks():
    snapshot = _settings_snapshot(["raw_retention_days"])
    _write_settings({"raw_retention_days": "7"})
    db = SessionLocal()
    try:
        client_id = _bare_client(db, "short_history_user")
        db.add(TrafficSample(client_id=client_id, sampled_at=datetime.now(timezone.utc), rx_bps=10, tx_bps=20))
        db.commit()
    finally:
        db.close()

    try:
        response = client.get(f"/clients/{client_id}/history?hours=168", headers=_auth_headers())
        assert response.status_code == 200
        (point,) = response.json()
        assert (point["rx_bps"], point["tx_bps"]) == (10, 20)
        assert point["peak_rx_bps"] is None and point["peak_tx_bps"] is None
    finally:
        _cleanup()
        _write_settings(snapshot)


def test_client_history_long_range_is_hourly_from_rollup_and_live_samples():
    snapshot = _settings_snapshot(["raw_retention_days", HOURLY_ROLLUP_KEY])
    current = floor_hour(datetime.now(timezone.utc))
    previous = current - timedelta(hours=1)
    _write_settings({"raw_retention_days": "7", HOURLY_ROLLUP_KEY: previous.isoformat()})
    db = SessionLocal()
    try:
        client_id = _bare_client(db, "long_history_user")
        # A rolled-up hour two days ago: 450 kB down in the hour = 1000 bps.
        db.add(
            TrafficHourly(
                client_id=client_id,
                hour_start=current - timedelta(hours=48),
                rx_bytes=450_000,
                tx_bytes=900_000,
                peak_rx_bps=5_000,
                peak_tx_bps=6_000,
            )
        )
        # A stale row at/after the watermark must be ignored: those hours
        # always come from the samples.
        db.add(
            TrafficHourly(
                client_id=client_id,
                hour_start=previous,
                rx_bytes=999_999_999,
                tx_bytes=999_999_999,
                peak_rx_bps=1,
                peak_tx_bps=1,
            )
        )
        db.add_all(
            [
                TrafficSample(
                    client_id=client_id,
                    sampled_at=previous + timedelta(minutes=10),
                    rx_bytes_delta=360_000,
                    tx_bytes_delta=450_000,
                    rx_bps=8_000,
                    tx_bps=7_000,
                ),
                TrafficSample(
                    client_id=client_id,
                    sampled_at=previous + timedelta(minutes=20),
                    rx_bytes_delta=90_000,
                    tx_bytes_delta=450_000,
                    rx_bps=3_000,
                    tx_bps=9_000,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    try:
        response = client.get(f"/clients/{client_id}/history?hours=720", headers=_auth_headers())
        assert response.status_code == 200
        points = response.json()
        assert [datetime.fromisoformat(p["sampled_at"]) for p in points] == [current - timedelta(hours=48), previous]
        stored, live = points
        assert (stored["rx_bytes_delta"], stored["rx_bps"], stored["tx_bps"]) == (450_000, 1000, 2000)
        assert (stored["peak_rx_bps"], stored["peak_tx_bps"]) == (5_000, 6_000)
        assert (live["rx_bytes_delta"], live["tx_bytes_delta"]) == (450_000, 900_000)
        assert (live["rx_bps"], live["tx_bps"]) == (1000, 2000)
        assert (live["peak_rx_bps"], live["peak_tx_bps"]) == (8_000, 9_000)
    finally:
        _cleanup()
        _write_settings(snapshot)


def test_client_history_validates_hours():
    db = SessionLocal()
    try:
        client_id = _bare_client(db, "hours_validation_user")
    finally:
        db.close()

    try:
        headers = _auth_headers()
        assert client.get(f"/clients/{client_id}/history?hours=0", headers=headers).status_code == 422
        assert client.get(f"/clients/{client_id}/history?hours=2161", headers=headers).status_code == 422
        assert client.get(f"/clients/{client_id}/history?hours=2160", headers=headers).status_code == 200
    finally:
        _cleanup()
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `remote 'cd backend && .venv/bin/pytest -q tests/test_api_clients.py'`
Expected: FAIL (`KeyError: 'peak_rx_bps'`, el historial largo devuelve muestras y `hours=0` responde 200).

- [ ] **Step 3: Implementar**

En `backend/app/schemas/client.py`, dentro de `ClientHistoryPoint`, reemplazar el comentario y los campos de velocidad:

```python
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
```

En `backend/app/api/clients.py`:
1. Imports: `from app.services.app_settings import get_raw_retention_days` y `from app.services.rollup import client_hourly_history`.
2. Debajo de `router = APIRouter(...)`: `MAX_HISTORY_HOURS = 90 * 24`.
3. Reemplazar `client_history`:

```python
@router.get("/{client_id}/history", response_model=list[ClientHistoryPoint])
def client_history(
    client_id: int,
    hours: int = Query(24, ge=1, le=MAX_HISTORY_HOURS),
    db: Session = Depends(get_db),
):
    client = db.get(PPPoEClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    if hours > get_raw_retention_days(db) * 24:
        # Past the 5-minute retention: one point per hour.
        return [
            ClientHistoryPoint(
                sampled_at=p.hour_start,
                rx_bytes_delta=p.rx_bytes,
                tx_bytes_delta=p.tx_bytes,
                rx_bps=p.rx_bps,
                tx_bps=p.tx_bps,
                peak_rx_bps=p.peak_rx_bps,
                peak_tx_bps=p.peak_tx_bps,
            )
            for p in client_hourly_history(db, client_id, since, now)
        ]

    samples = (
        db.query(TrafficSample)
        .filter(TrafficSample.client_id == client_id, TrafficSample.sampled_at >= since)
        .order_by(TrafficSample.sampled_at)
        .all()
    )
    return [
        ClientHistoryPoint(
            sampled_at=s.sampled_at,
            rx_bytes_delta=s.rx_bytes_delta,
            tx_bytes_delta=s.tx_bytes_delta,
            rx_bps=s.rx_bps,
            tx_bps=s.tx_bps,
        )
        for s in samples
    ]
```

- [ ] **Step 4: Correr la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/client.py backend/app/api/clients.py backend/tests/test_api_clients.py
git commit -m "feat(backend): historial por hora con promedio y pico para rangos largos" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Interfaz — rangos largos y retención de detalle

**Files:**
- Modify: `frontend/src/pages/ClientDetail.tsx`, `frontend/src/pages/SettingsAdmin.tsx`

**Interfaces:**
- Consumes: `peak_rx_bps`/`peak_tx_bps` en el historial (Tarea 7) y `raw_retention_days` en `/settings` (Tarea 4).

- [ ] **Step 1: Detalle del cliente**

En `frontend/src/pages/ClientDetail.tsx`:

1. En `interface HistoryPoint`, agregar después de `tx_bps: number`:

```ts
  // Only on hourly points (30/90-day ranges): the fastest 5-minute sample.
  peak_rx_bps: number | null
  peak_tx_bps: number | null
```

2. Reemplazar `RANGES`:

```ts
const RANGES = [
  { label: 'Últimas 24h', hours: 24 },
  { label: 'Últimos 7 días', hours: 24 * 7 },
  { label: 'Últimos 30 días', hours: 24 * 30 },
  { label: 'Últimos 90 días', hours: 24 * 90 },
]
```

3. Reemplazar `speedData`:

```ts
  // Long ranges come as one point per hour: the hour's average speed plus
  // its peak, drawn dashed.
  const hourly = points.some((p) => p.peak_tx_bps !== null)
  const speedData = points.map((p) => ({
    time: new Date(p.sampled_at).toLocaleString(),
    Descarga: p.tx_bps,
    Subida: p.rx_bps,
    'Descarga (pico)': p.peak_tx_bps ?? undefined,
    'Subida (pico)': p.peak_rx_bps ?? undefined,
  }))
```

4. Reemplazar el `<h2>Velocidad (promedio de cada sondeo)</h2>` por:

```tsx
          <h2>{hourly ? 'Velocidad (promedio y pico de cada hora)' : 'Velocidad (promedio de cada sondeo)'}</h2>
```

5. En ese mismo `LineChart` de velocidad, después de la `<Line … dataKey="Subida" …/>`:

```tsx
              {hourly && (
                <Line
                  type="monotone"
                  dataKey="Descarga (pico)"
                  stroke={COLORS.download}
                  strokeDasharray="5 5"
                  dot={false}
                />
              )}
              {hourly && (
                <Line type="monotone" dataKey="Subida (pico)" stroke={COLORS.upload} strokeDasharray="5 5" dot={false} />
              )}
```

El gráfico de consumo acumulado no cambia.

- [ ] **Step 2: Configuración**

En `frontend/src/pages/SettingsAdmin.tsx`:

1. En `interface SettingsData`, agregar `raw_retention_days: number` después de `retention_days: number`.
2. En `saveSettings`, antes de `const payload`:

```ts
    if (settings.raw_retention_days > settings.retention_days) {
      setSettingsError('La retención de detalle no puede ser mayor que la retención de historial.')
      return
    }
```

y en `payload`, agregar `raw_retention_days: settings.raw_retention_days,` después de `retention_days`.

3. Reemplazar el `<label>` de "Retención de historial (días)" por:

```tsx
        <label>
          Retención de historial (días)
          <input
            type="number"
            min={1}
            value={settings.retention_days}
            onChange={(e) => setSettings({ ...settings, retention_days: Number(e.target.value) })}
          />
          <small>Cuánto se guarda el resumen de tráfico por hora.</small>
        </label>
        <label>
          Retención de detalle (días)
          <input
            type="number"
            min={1}
            max={settings.retention_days}
            value={settings.raw_retention_days}
            onChange={(e) => setSettings({ ...settings, raw_retention_days: Number(e.target.value) })}
          />
          <small>
            Cuánto se guarda cada sondeo. No puede ser mayor que la retención de historial.
          </small>
        </label>
```

- [ ] **Step 3: Build y lint**

Run: `remote 'cd frontend && npm run build && npm run lint'`
Expected: build sin errores de TypeScript; `oxlint` sin errores.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ClientDetail.tsx frontend/src/pages/SettingsAdmin.tsx
git commit -m "feat(frontend): rangos de 30 y 90 días con pico por hora y retención de detalle" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Verificar sobre una copia de la base real y desplegar en la VM

Esta tarea la hace el controlador (no un subagente), porque toca la base real de la VM. Todos los comandos corren con `remote` desde la raíz del repo.

**Files:** ninguno (solo verificación y despliegue).

- [ ] **Step 1: Backup de la base real**

```bash
remote 'echo mario | sudo -S docker exec pppoe-monitor-db-1 pg_dump -U pppoe -Fc pppoe > ~/pppoe-pre-sesiones.dump && ls -lh ~/pppoe-pre-sesiones.dump'
```

Expected: el archivo existe y pesa más de 0 bytes.

- [ ] **Step 2: Restaurar la copia en el contenedor de desarrollo**

```bash
remote 'echo mario | sudo -S docker exec pppoe-dev-db psql -U pppoe -d pppoe -c "DROP DATABASE IF EXISTS pppoe_migcheck" -c "CREATE DATABASE pppoe_migcheck" && echo mario | sudo -S docker cp ~/pppoe-pre-sesiones.dump pppoe-dev-db:/tmp/pre.dump && echo mario | sudo -S docker exec pppoe-dev-db pg_restore -U pppoe -d pppoe_migcheck --no-owner /tmp/pre.dump'
```

- [ ] **Step 3: Totales antes de migrar**

```bash
remote 'echo mario | sudo -S docker exec pppoe-dev-db psql -U pppoe -d pppoe_migcheck -At -c "SELECT client_id, rx_bytes_total, tx_bytes_total FROM accumulation_periods WHERE period_end IS NULL ORDER BY client_id" > ~/totals-before.txt && wc -l ~/totals-before.txt'
```

- [ ] **Step 4: Migrar la copia y comparar**

```bash
remote 'cd backend && export JWT_SECRET=pytest-only-jwt-secret-not-for-production-use-0123456789 DATABASE_URL=postgresql://pppoe:pppoe@localhost:5432/pppoe_migcheck && .venv/bin/alembic upgrade head && echo mario | sudo -S docker exec pppoe-dev-db psql -U pppoe -d pppoe_migcheck -At -c "SELECT client_id, rx_bytes_total, tx_bytes_total FROM accumulation_periods WHERE period_end IS NULL ORDER BY client_id" > ~/totals-after.txt && diff ~/totals-before.txt ~/totals-after.txt && echo TOTALES_IDENTICOS'
```

Expected: `Running upgrade ac6bc5da9228 -> 5d2b7e9c41a0` y `TOTALES_IDENTICOS`.

- [ ] **Step 5: Resumir la copia y cruzar con las muestras**

```bash
remote 'cd backend && export JWT_SECRET=pytest-only-jwt-secret-not-for-production-use-0123456789 DATABASE_URL=postgresql://pppoe:pppoe@localhost:5432/pppoe_migcheck && .venv/bin/python -c "from app.database import SessionLocal; from app.services.rollup import run_hourly_rollup; print(run_hourly_rollup(SessionLocal()))"'
remote 'echo mario | sudo -S docker exec pppoe-dev-db psql -U pppoe -d pppoe_migcheck -At -c "WITH w AS (SELECT value::timestamptz AS until FROM settings WHERE key = '"'"'hourly_rollup_until'"'"'), s AS (SELECT client_id, SUM(rx_bytes_delta) AS rx, SUM(tx_bytes_delta) AS tx FROM traffic_samples, w WHERE sampled_at < w.until GROUP BY client_id), h AS (SELECT client_id, SUM(rx_bytes) AS rx, SUM(tx_bytes) AS tx FROM traffic_hourly GROUP BY client_id) SELECT count(*) FROM s FULL JOIN h USING (client_id) WHERE s.rx IS DISTINCT FROM h.rx OR s.tx IS DISTINCT FROM h.tx"'
```

Expected: la primera imprime una marca de agua al inicio de la hora actual (UTC); la segunda imprime `0`.

- [ ] **Step 6: Downgrade y upgrade sobre la copia, y limpieza**

```bash
remote 'cd backend && export JWT_SECRET=pytest-only-jwt-secret-not-for-production-use-0123456789 DATABASE_URL=postgresql://pppoe:pppoe@localhost:5432/pppoe_migcheck && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head && echo mario | sudo -S docker exec pppoe-dev-db psql -U pppoe -d pppoe -c "DROP DATABASE pppoe_migcheck"'
```

Expected: ambos pasos sin errores. Si algo de los pasos 4 a 6 falla, **no desplegar**: volver a la tarea que corresponda.

- [ ] **Step 7: Desplegar**

```bash
remote 'echo mario | sudo -S docker compose up -d --build && sleep 15 && echo mario | sudo -S docker compose logs backend --tail 40'
```

Expected: en los logs, `Running upgrade ac6bc5da9228 -> 5d2b7e9c41a0` y uvicorn arrancado sin tracebacks.

- [ ] **Step 8: Comprobar en vivo (esperar dos sondeos, ~10 minutos)**

```bash
remote 'echo mario | sudo -S docker exec pppoe-monitor-db-1 psql -U pppoe -d pppoe -c "SELECT c.username, s.interface_name, s.interface_id, s.last_rx_bps, s.last_tx_bps FROM session_state s JOIN pppoe_clients c ON c.id = s.client_id WHERE c.username IN ('"'"'castro.melina'"'"', '"'"'moyano.julio'"'"', '"'"'amado.lorena'"'"') ORDER BY 1, 2" -c "SELECT c.username, t.sampled_at, t.rx_bytes_delta, t.tx_bytes_delta FROM traffic_samples t JOIN pppoe_clients c ON c.id = t.client_id WHERE c.username = '"'"'amado.lorena'"'"' ORDER BY t.sampled_at DESC LIMIT 3" -c "SELECT key, value FROM settings WHERE key IN ('"'"'raw_retention_days'"'"', '"'"'hourly_rollup_until'"'"')"'
```

Expected:
- `castro.melina` y `moyano.julio` con dos filas cada uno (una con sufijo `-1`), si siguen con dos sesiones; `amado.lorena` con su interfaz `-1`.
- Las muestras nuevas de `amado.lorena` con bytes > 0 (si está usando la conexión).
- `raw_retention_days = 7` y `hourly_rollup_until` en la hora actual.

Comparar además los acumulados reales contra `~/totals-before.txt`: cada cliente debe tener hoy un total **mayor o igual** que antes de la migración.

```bash
remote 'echo mario | sudo -S docker exec pppoe-monitor-db-1 psql -U pppoe -d pppoe -At -c "SELECT client_id, rx_bytes_total, tx_bytes_total FROM accumulation_periods WHERE period_end IS NULL ORDER BY client_id" > ~/totals-live.txt && awk -F"|" '"'"'NR==FNR {rx[$1]=$2; tx[$1]=$3; next} ($1 in rx) && ($2 < rx[$1] || $3 < tx[$1]) {bad++} END {print (bad ? bad " clientes con totales menores" : "ACUMULADOS_OK")}'"'"' ~/totals-before.txt ~/totals-live.txt'
```

Expected: `ACUMULADOS_OK`.

- [ ] **Step 9: Pedirle al usuario la revisión visual**

Pedirle que abra el detalle de `castro.melina` y de `amado.lorena` y pruebe los rangos de 30 y 90 días (línea punteada de pico), y la pantalla de Configuración con el campo nuevo. Después, usar superpowers:finishing-a-development-branch para integrar la rama.
