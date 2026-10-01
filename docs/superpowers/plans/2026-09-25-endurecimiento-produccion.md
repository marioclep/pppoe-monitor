# Endurecimiento para producción — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar el monitor PPPoE listo para instalarse en un servidor Ubuntu y olvidarse. Eso incluye backups diarios restaurables, nginx sin caché del `index.html`, healthchecks reales, logs acotados, límite de intentos de login, una CLI de admin y un README de producción.

**Architecture:** Casi todo es configuración de despliegue: `nginx.conf`, `docker-compose.yml`, un script de backup en `sh` que corre en un servicio `postgres:16` y el README. El backend suma cuatro piezas chicas y aisladas: `/health` con consulta a la base, `configure_logging()`, `LoginThrottle` en memoria con su uso en `POST /auth/login`, y `app/cli.py` con `argparse`. El frontend solo agrega el mensaje para el 429.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2, pytest, nginx 1.27-alpine, Docker Compose, postgres:16 (`pg_dump` / `pg_restore`), React + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-25-endurecimiento-produccion-design.md`

## Global Constraints

- Nunca instalar nada en el Mac. Todo se corre en la VM con el wrapper `remote` (`.superpowers/sdd/2026-09-22-pppoe-monitor-plan/remote '<cmd>'`), que ejecuta el comando dentro de `~/pppoe-monitor`.
- Tests del backend: `remote 'cd backend && .venv/bin/pytest -q'`. Build del frontend: `remote 'cd frontend && npm run build'`. El frontend no tiene tests unitarios.
- Acceso solo LAN/VPN: **HTTP plano, sin HTTPS**.
- uvicorn corre con **un solo proceso**. No agregar `--workers`.
- Backups: `pg_dump -Fc`, primero a `.partial` y después `mv` a `.dump`. Valores por defecto: `BACKUP_TIME=03:00`, `BACKUP_KEEP_DAYS=14`. Van a `./backups/` del host.
- Throttle de login: 5 fallos por IP en 900 s dan 429 con `{"detail": "Too many failed login attempts, try again later"}`.
- Contraseña de admin por CLI: mínimo 10 caracteres. Variable de entorno alternativa: `PPPOE_ADMIN_PASSWORD`.
- Formato de log: `%(asctime)s %(levelname)s %(name)s: %(message)s`. El nivel sale de `LOG_LEVEL`, con `INFO` por defecto.
- No agregar dependencias de Python ni de npm.
- Textos para el usuario y README en español rioplatense (vos). Nombres de código y mensajes de log en inglés, como el código existente.
- Mensajes de commit en español, con el formato `tipo(ámbito): descripción` del repo y el trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- La VM tiene datos reales (6 routers, ~3.300 sesiones): ningún paso puede borrar volúmenes ni la base.

**Desvío menor respecto de la spec:** el servicio de backup usa `entrypoint: ["sh", "/backup.sh"]`, así que el backup a demanda se invoca con `docker compose run --rm backup now` y no con `/backup.sh now`. De esta forma no depende del bit de ejecución del archivo montado. El README usa esta forma.

## Review Focus

1. **Arranque con la base todavía no lista, o caída después.** `/health` tiene que devolver 503 y no 500 ni una excepción sin manejar, y el healthcheck de Docker tiene que marcar al backend como `unhealthy`. Lo cubre el test de la Tarea 1.
2. **TestClient y peers no-IP en el throttle.** `request.client.host` puede ser `"testclient"` o faltar (`None`), y no puede romper `client_ip`. Lo cubren los tests de la Tarea 3.
3. **Interacción entre `add_header` de nginx y la herencia.** Si un `location` agrega `Cache-Control`, pierde las cabeceras de seguridad del `server`. Se verifica con `curl -I` sobre `/`, `/index.html`, una ruta SPA (`/clients/1`), un asset y `/api/health` en la Tarea 6.
4. **`BACKUP_TIME` inválido o `pg_dump` fallando.** Con un valor inválido, el script sale con un error claro. Si falla el dump, no queda ningún `.dump` corrupto y el bucle sigue. Se verifica en la Tarea 7 con `BACKUP_TIME=25:99` y con un `PGPASSWORD` incorrecto.
5. **Estado del throttle entre tests.** Es un objeto de módulo: sin limpieza, los fallos de un test bloquean a otro. Un fixture `autouse` en `conftest.py` lo limpia (Tarea 3).

---

### Tarea 0: Proteger `backups/` en el wrapper `remote` (sin commit: el archivo está en `.gitignore`)

**Files:**
- Modify: `.superpowers/sdd/2026-09-22-pppoe-monitor-plan/remote` (array `EXCLUDES`)

- [ ] **Step 1: Agregar `--exclude backups` a `EXCLUDES`**

```bash
EXCLUDES=(--exclude .git --exclude .superpowers --exclude .claude --exclude .venv
          --exclude node_modules --exclude dist --exclude __pycache__
          --exclude .pytest_cache --exclude .DS_Store --exclude '*.pyc'
          --exclude backups)
```

Sin esto, el `rsync --delete` hacia la VM borraría los backups de la VM, y el `rsync` de vuelta los copiaría al Mac.

- [ ] **Step 2: Verificar**

Run: `remote 'mkdir -p backups && touch backups/keep-test && ls backups'`, then `remote 'ls backups && rm backups/keep-test'`
Expected: `keep-test` sigue apareciendo en la segunda llamada y no aparece en `backups/` del Mac.

---

### Tarea 1: `/health` con verificación de la base

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `GET /health` → `200 {"status": "ok"}` o `503 {"status": "error", "detail": "database unavailable"}`. Lo usa el healthcheck de Docker (Tarea 7).

- [ ] **Step 1: Escribir el test que falla** (reemplazar `backend/tests/test_main.py`)

```python
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app

client = TestClient(app)


def test_health_returns_ok_when_database_answers():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class _BrokenSession:
    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_health_returns_503_when_database_is_unavailable():
    def broken_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {"status": "error", "detail": "database unavailable"}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_main.py -v'`
Expected: `test_health_returns_503_when_database_is_unavailable` FAIL (devuelve 200).

- [ ] **Step 3: Implementar** en `backend/app/main.py`

Agregar los imports y reemplazar la función `health`:

```python
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# ... imports de routers existentes sin cambios ...
from app.database import get_db

logger = logging.getLogger(__name__)
```

```python
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
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_main.py -v'`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py
git commit -m "feat(backend): /health verifica la conexión a la base y responde 503 si falla"
```

---

### Tarea 2: Logging a stdout con `LOG_LEVEL`

**Files:**
- Create: `backend/app/logging_config.py`
- Modify: `backend/app/config.py` (campo `LOG_LEVEL` y su validador)
- Modify: `backend/app/main.py` (llamar a `configure_logging` al importar)
- Test: `backend/tests/test_logging_config.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces: `configure_logging(level: str) -> None`; `settings.LOG_LEVEL: str` (normalizado a mayúsculas, uno de `DEBUG|INFO|WARNING|ERROR|CRITICAL`).

- [ ] **Step 1: Escribir los tests que fallan**

`backend/tests/test_logging_config.py`:

```python
import logging

import pytest

from app.logging_config import HANDLER_NAME, configure_logging


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    yield root
    root.setLevel(saved_level)
    root.handlers[:] = saved_handlers


def _ours(root):
    return [h for h in root.handlers if h.get_name() == HANDLER_NAME]


def test_sets_level_and_adds_one_stdout_handler(restore_root_logger):
    root = restore_root_logger
    root.handlers[:] = [h for h in root.handlers if h.get_name() != HANDLER_NAME]

    configure_logging("WARNING")

    assert root.level == logging.WARNING
    assert len(_ours(root)) == 1
    assert _ours(root)[0].formatter._fmt == "%(asctime)s %(levelname)s %(name)s: %(message)s"


def test_is_idempotent_and_updates_level(restore_root_logger):
    root = restore_root_logger
    configure_logging("INFO")
    configure_logging("DEBUG")

    assert root.level == logging.DEBUG
    assert len(_ours(root)) == 1
```

Agregar a `backend/tests/test_config.py`:

```python
def test_log_level_defaults_to_info_and_is_normalized():
    assert _settings().LOG_LEVEL == "INFO"
    assert _settings(LOG_LEVEL="debug").LOG_LEVEL == "DEBUG"


def test_rejects_unknown_log_level():
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        _settings(LOG_LEVEL="verbose")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_logging_config.py tests/test_config.py -v'`
Expected: FAIL (`ModuleNotFoundError: app.logging_config`; `Settings` no tiene `LOG_LEVEL`).

- [ ] **Step 3: Implementar**

`backend/app/logging_config.py`:

```python
"""Send the app's log records (polling, rollup, purge, reset...) to stdout.

Without this only WARNING+ records reach the console (Python's last-resort
handler), so `docker compose logs backend` hides the INFO lines that show
what the scheduler is doing. uvicorn's own loggers don't propagate to the
root logger, so they are not duplicated.
"""
import logging
import sys

HANDLER_NAME = "pppoe-monitor-stdout"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if any(h.get_name() == HANDLER_NAME for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
```

En `backend/app/config.py`, agregar a `Settings` después de `TZ`:

```python
    # Root log level for the app's own loggers (stdout).
    LOG_LEVEL: str = "INFO"
```

y el validador, junto a los otros:

```python
    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                f"LOG_LEVEL={value!r} is not valid (use DEBUG, INFO, WARNING, ERROR or CRITICAL)."
            )
        return normalized
```

En `backend/app/main.py`, agregar después de los imports (antes de `logger = ...`):

```python
from app.config import settings
from app.logging_config import configure_logging

configure_logging(settings.LOG_LEVEL)
```

- [ ] **Step 4: Correr toda la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: todo pasa (ningún test existente se rompe; 4 tests nuevos).

- [ ] **Step 5: Commit**

```bash
git add backend/app/logging_config.py backend/app/config.py backend/app/main.py backend/tests/test_logging_config.py backend/tests/test_config.py
git commit -m "feat(backend): logs de la app a stdout con nivel configurable por LOG_LEVEL"
```

---

### Tarea 3: `LoginThrottle` y la IP del cliente

**Files:**
- Create: `backend/app/core/login_throttle.py`
- Modify: `backend/tests/conftest.py` (fixture autouse)
- Test: `backend/tests/test_login_throttle.py`

**Interfaces:**
- Produces:
  - `class LoginThrottle(max_failures: int = 5, window_seconds: float = 900, clock: Callable[[], float] = time.monotonic)` con los métodos `is_blocked(key: str) -> bool`, `register_failure(key: str) -> None`, `reset(key: str) -> None` y `clear() -> None`.
  - `login_throttle: LoginThrottle`: instancia del módulo que usa la API.
  - `client_ip(request: starlette.requests.Request) -> str`.

- [ ] **Step 1: Escribir los tests que fallan**

`backend/tests/test_login_throttle.py`:

```python
from starlette.requests import Request

from app.core.login_throttle import LoginThrottle, client_ip


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _throttle(clock):
    return LoginThrottle(max_failures=5, window_seconds=900, clock=clock)


def test_blocks_after_max_failures():
    t = _throttle(FakeClock())
    for _ in range(4):
        t.register_failure("10.0.0.1")
    assert not t.is_blocked("10.0.0.1")
    t.register_failure("10.0.0.1")
    assert t.is_blocked("10.0.0.1")


def test_unblocks_when_failures_leave_the_window():
    clock = FakeClock()
    t = _throttle(clock)
    for _ in range(5):
        t.register_failure("10.0.0.1")
    clock.now += 900
    assert not t.is_blocked("10.0.0.1")


def test_window_is_sliding():
    clock = FakeClock()
    t = _throttle(clock)
    t.register_failure("10.0.0.1")
    clock.now += 600
    for _ in range(4):
        t.register_failure("10.0.0.1")
    assert t.is_blocked("10.0.0.1")
    clock.now += 300  # the first failure leaves the window
    assert not t.is_blocked("10.0.0.1")


def test_reset_clears_one_key_only():
    t = _throttle(FakeClock())
    for _ in range(5):
        t.register_failure("10.0.0.1")
        t.register_failure("10.0.0.2")
    t.reset("10.0.0.1")
    assert not t.is_blocked("10.0.0.1")
    assert t.is_blocked("10.0.0.2")


def test_keys_are_independent():
    t = _throttle(FakeClock())
    for _ in range(5):
        t.register_failure("10.0.0.1")
    assert not t.is_blocked("10.0.0.2")


def test_expired_keys_are_dropped():
    clock = FakeClock()
    t = _throttle(clock)
    t.register_failure("10.0.0.1")
    clock.now += 901
    t.register_failure("10.0.0.2")
    assert list(t._failures) == ["10.0.0.2"]


def _request(peer, headers=None):
    scope = {
        "type": "http",
        "client": peer,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def test_client_ip_trusts_x_real_ip_from_private_peer():
    req = _request(("172.18.0.4", 51000), {"X-Real-IP": "192.168.10.25"})
    assert client_ip(req) == "192.168.10.25"


def test_client_ip_ignores_x_real_ip_from_public_peer():
    req = _request(("203.0.113.9", 51000), {"X-Real-IP": "192.168.10.25"})
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_without_header_uses_peer():
    assert client_ip(_request(("172.18.0.4", 51000))) == "172.18.0.4"


def test_client_ip_handles_non_ip_or_missing_peer():
    assert client_ip(_request(("testclient", 50000), {"X-Real-IP": "1.2.3.4"})) == "testclient"
    assert client_ip(_request(None)) == "unknown"
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_login_throttle.py -v'`
Expected: FAIL con `ModuleNotFoundError: app.core.login_throttle`.

- [ ] **Step 3: Implementar** `backend/app/core/login_throttle.py`

```python
"""In-memory limit on failed logins per client IP.

State lives in this process only: the backend runs a single uvicorn worker
(the scheduler lives inside it), and a restart clearing the counters is
acceptable for a LAN/VPN-only deployment.
"""
import ipaddress
import threading
import time
from collections import deque
from typing import Callable

from starlette.requests import Request


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 900,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key in list(self._failures):
            failures = self._failures[key]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            if not failures:
                del self._failures[key]

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            self._prune(self._clock())
            return len(self._failures.get(key, ())) >= self.max_failures

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._failures.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()


def _is_internal(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def client_ip(request: Request) -> str:
    """The real client IP: nginx's X-Real-IP when the direct peer is inside
    the Docker/private network (i.e. it is our nginx), else the peer itself.
    The backend publishes no port, so only nginx can reach it in production."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded and _is_internal(peer):
        return forwarded
    return peer
```

Agregar al final de `backend/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _clear_login_throttle():
    """The login throttle is process-wide state; don't let one test's failed
    logins block another test's."""
    from app.core.login_throttle import login_throttle

    login_throttle.clear()
    yield
    login_throttle.clear()
```

- [ ] **Step 4: Correr y ver que pasan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_login_throttle.py -v'`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/login_throttle.py backend/tests/test_login_throttle.py backend/tests/conftest.py
git commit -m "feat(backend): contador de logins fallidos por IP con ventana deslizante"
```

---

### Tarea 4: Aplicar el límite en `POST /auth/login` y mostrar el 429 en el frontend

**Files:**
- Modify: `backend/app/api/auth.py`
- Modify: `frontend/src/api/client.ts` (clase `ApiError`)
- Modify: `frontend/src/pages/Login.tsx`
- Test: `backend/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `login_throttle`, `client_ip` (Tarea 3).
- Produces: `POST /auth/login` → 429 `{"detail": "Too many failed login attempts, try again later"}` cuando la IP está bloqueada. Frontend: `export class ApiError extends Error { status: number }`.

- [ ] **Step 1: Escribir los tests que fallan**. Agregar a `backend/tests/test_api_auth.py`; `TestClient` usa `"testclient"` como peer, que es la clave del throttle:

```python
def _reset_user(username: str, password: str) -> None:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        _make_user(db, username, password)
    finally:
        db.close()


def test_login_is_blocked_after_five_failures_even_with_the_right_password():
    _reset_user("throttled", "right-password")
    for _ in range(5):
        r = client.post("/auth/login", json={"username": "throttled", "password": "wrong"})
        assert r.status_code == 401

    r = client.post("/auth/login", json={"username": "throttled", "password": "wrong"})
    assert r.status_code == 429
    assert r.json() == {"detail": "Too many failed login attempts, try again later"}

    r = client.post("/auth/login", json={"username": "throttled", "password": "right-password"})
    assert r.status_code == 429


def test_successful_login_resets_the_failure_count():
    _reset_user("resetme", "right-password")
    for _ in range(4):
        client.post("/auth/login", json={"username": "resetme", "password": "wrong"})
    assert client.post(
        "/auth/login", json={"username": "resetme", "password": "right-password"}
    ).status_code == 200

    for _ in range(4):
        r = client.post("/auth/login", json={"username": "resetme", "password": "wrong"})
        assert r.status_code == 401


def test_failed_login_is_logged_with_user_and_ip(caplog):
    with caplog.at_level("WARNING", logger="app.api.auth"):
        client.post("/auth/login", json={"username": "ghost", "password": "wrong"})
    assert "login failed user=ghost ip=testclient" in caplog.text
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_api_auth.py -v'`
Expected: los 3 tests nuevos FAIL (no hay 429 ni log).

- [ ] **Step 3: Implementar** `backend/app/api/auth.py` completo

```python
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.login_throttle import client_ip, login_throttle
from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if login_throttle.is_blocked(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts, try again later",
        )
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        login_throttle.register_failure(ip)
        logger.warning("login failed user=%s ip=%s", payload.username, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    login_throttle.reset(ip)
    return TokenResponse(access_token=create_access_token(user.username))
```

- [ ] **Step 4: Correr toda la suite del backend**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: todo pasa.

- [ ] **Step 5: Frontend.** En `frontend/src/api/client.ts`, agregar la clase y lanzarla en lugar del `Error` genérico. El mensaje no cambia:

```ts
export class ApiError extends Error {
  status: number

  constructor(status: number, detail: string) {
    super(`API error ${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
  }
}
```

```ts
    const detail = await response.text()
    throw new ApiError(response.status, detail)
```

En `frontend/src/pages/Login.tsx`: importar `import { ApiError } from '../api/client'` y reemplazar el `catch`:

```tsx
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? 'Demasiados intentos fallidos, esperá unos minutos'
          : 'Usuario o contraseña incorrectos',
      )
    }
```

- [ ] **Step 6: Build del frontend**

Run: `remote 'cd frontend && npm run build'`
Expected: `✓ built`, sin errores de TypeScript.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/auth.py backend/tests/test_api_auth.py frontend/src/api/client.ts frontend/src/pages/Login.tsx
git commit -m "feat: bloquear el login tras 5 intentos fallidos por IP y avisarlo en la pantalla de ingreso"
```

---

### Tarea 5: CLI de administración (`create-admin`, `reset-password`)

**Files:**
- Create: `backend/app/cli.py`
- Test: `backend/tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`, que devuelve 0 si sale bien y 1 si hay error, con el mensaje en stderr. `PASSWORD_ENV_VAR = "PPPOE_ADMIN_PASSWORD"`, `MIN_PASSWORD_LENGTH = 10`. El README (Tarea 8) lo invoca como `docker compose exec backend python -m app.cli create-admin <usuario>`.

- [ ] **Step 1: Escribir los tests que fallan** en `backend/tests/test_cli.py`

```python
import pytest

from app import cli
from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.models.user import User

USERNAME = "cli-test-user"


def _delete_user():
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == USERNAME).delete()
        db.commit()
    finally:
        db.close()


def _stored_hash():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == USERNAME).first()
        return user.password_hash if user else None
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_user():
    _delete_user()
    yield
    _delete_user()


def test_create_admin_with_env_password(monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "long-enough-pass")
    assert cli.main(["create-admin", USERNAME]) == 0
    assert verify_password("long-enough-pass", _stored_hash())
    assert USERNAME in capsys.readouterr().out


def test_create_admin_prompts_twice(monkeypatch):
    monkeypatch.delenv(cli.PASSWORD_ENV_VAR, raising=False)
    answers = iter(["typed-password-1", "typed-password-1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-admin", USERNAME]) == 0
    assert verify_password("typed-password-1", _stored_hash())


def test_create_admin_rejects_mismatched_prompts(monkeypatch, capsys):
    monkeypatch.delenv(cli.PASSWORD_ENV_VAR, raising=False)
    answers = iter(["typed-password-1", "typed-password-2"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-admin", USERNAME]) == 1
    assert _stored_hash() is None
    assert "no coinciden" in capsys.readouterr().err


def test_create_admin_rejects_short_password(monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "short")
    assert cli.main(["create-admin", USERNAME]) == 1
    assert _stored_hash() is None
    assert "10 caracteres" in capsys.readouterr().err


def test_create_admin_fails_if_user_exists(monkeypatch, capsys):
    db = SessionLocal()
    db.add(User(username=USERNAME, password_hash=hash_password("original-pass")))
    db.commit()
    db.close()
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "another-long-pass")

    assert cli.main(["create-admin", USERNAME]) == 1
    assert verify_password("original-pass", _stored_hash())
    assert "ya existe" in capsys.readouterr().err


def test_reset_password_changes_the_hash(monkeypatch):
    db = SessionLocal()
    db.add(User(username=USERNAME, password_hash=hash_password("original-pass")))
    db.commit()
    db.close()
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "brand-new-password")

    assert cli.main(["reset-password", USERNAME]) == 0
    assert verify_password("brand-new-password", _stored_hash())


def test_reset_password_fails_for_unknown_user(monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "brand-new-password")
    assert cli.main(["reset-password", USERNAME]) == 1
    assert "no existe" in capsys.readouterr().err
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_cli.py -v'`
Expected: FAIL con `ImportError: cannot import name 'cli'`.

- [ ] **Step 3: Implementar** `backend/app/cli.py`

```python
"""Admin commands, run inside the backend container:

    docker compose exec backend python -m app.cli create-admin <username>
    docker compose exec backend python -m app.cli reset-password <username>

The password is prompted twice (not echoed), or taken from the
PPPOE_ADMIN_PASSWORD environment variable for scripted use.
"""
import argparse
import getpass
import os
import sys

from app.core.security import hash_password
from app.database import SessionLocal
from app.models.user import User

PASSWORD_ENV_VAR = "PPPOE_ADMIN_PASSWORD"
MIN_PASSWORD_LENGTH = 10


class CliError(Exception):
    pass


def _read_password() -> str:
    password = os.environ.get(PASSWORD_ENV_VAR)
    if password is None:
        password = getpass.getpass("Contraseña: ")
        if getpass.getpass("Repetir contraseña: ") != password:
            raise CliError("Las contraseñas no coinciden.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CliError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    return password


def _run(command: str, username: str) -> str:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if command == "create-admin":
            if user is not None:
                raise CliError(
                    f"El usuario {username!r} ya existe. Usá reset-password para cambiarle la contraseña."
                )
            db.add(User(username=username, password_hash=hash_password(_read_password())))
            db.commit()
            return f"Usuario {username!r} creado."
        if user is None:
            raise CliError(f"El usuario {username!r} no existe.")
        user.password_hash = hash_password(_read_password())
        db.commit()
        return f"Contraseña de {username!r} actualizada."
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administración de usuarios")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-admin", help="Crear un usuario admin").add_argument("username")
    commands.add_parser("reset-password", help="Cambiar la contraseña de un usuario").add_argument("username")
    args = parser.parse_args(argv)

    try:
        print(_run(args.command, args.username))
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Correr toda la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q'`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add backend/app/cli.py backend/tests/test_cli.py
git commit -m "feat(backend): comandos create-admin y reset-password para administrar usuarios"
```

---

### Tarea 6: nginx con caché correcta, gzip y cabeceras de seguridad

**Files:**
- Modify: `frontend/nginx.conf`
- Create: `frontend/nginx-security-headers.conf`
- Modify: `frontend/Dockerfile`

**Interfaces:**
- Produces: `index.html` (y cualquier ruta SPA) con `Cache-Control: no-cache`; `/assets/*` con `Cache-Control: public, max-age=31536000, immutable`; todas las respuestas con `X-Content-Type-Options`, `X-Frame-Options` y `Referrer-Policy`.

- [ ] **Step 1: Crear `frontend/nginx-security-headers.conf`**

```nginx
# Included at server level AND in every location that calls add_header:
# an add_header inside a location drops all add_header inherited from server.
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "same-origin" always;
```

- [ ] **Step 2: Reemplazar `frontend/nginx.conf`**

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    server_tokens off;
    client_max_body_size 1m;

    gzip on;
    gzip_min_length 1024;
    gzip_types text/css application/javascript application/json image/svg+xml;

    include /etc/nginx/snippets/security-headers.conf;

    location /api/ {
        proxy_pass http://backend:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Vite puts a content hash in every asset file name, so they never change.
    location /assets/ {
        include /etc/nginx/snippets/security-headers.conf;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # "/" and every SPA route end up here (index / try_files fallback).
    # no-cache = always revalidate, so a deploy is seen without a hard refresh.
    location = /index.html {
        include /etc/nginx/snippets/security-headers.conf;
        add_header Cache-Control "no-cache";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 3: Copiar el snippet en `frontend/Dockerfile`** (etapa nginx, junto al `COPY nginx.conf`)

```dockerfile
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-security-headers.conf /etc/nginx/snippets/security-headers.conf
```

- [ ] **Step 4: Validar la sintaxis**

Run: `remote 'docker compose build frontend && docker compose run --rm --no-deps frontend nginx -t'`
Expected: `syntax is ok` y `test is successful`. Si `nginx -t` no resuelve `backend` al correr sin dependencias, usar `docker compose up -d frontend` y `docker compose exec frontend nginx -t`.

- [ ] **Step 5: Desplegar y verificar las cabeceras** (Review Focus 3)

Run:
```bash
remote 'docker compose up -d --build frontend && sleep 3 && A=$(docker compose exec -T frontend sh -c "ls /usr/share/nginx/html/assets | grep js$ | head -1") && for p in / /index.html /clients/1 /assets/$A /api/health; do echo "== $p"; curl -sI -H "Accept-Encoding: gzip" http://localhost:${HTTP_PORT:-80}$p | grep -iE "^HTTP|cache-control|x-content|x-frame|referrer|content-encoding|^server"; done'
```
Expected:
- `/`, `/index.html` y `/clients/1`: `200`, `Cache-Control: no-cache` y las 3 cabeceras de seguridad.
- El asset `.js`: `200`, `Cache-Control: public, max-age=31536000, immutable`, `Content-Encoding: gzip` y las 3 cabeceras.
- `/api/health`: `200` y las 3 cabeceras.
- Ninguna respuesta con `Server: nginx/1.27.x` (solo `nginx`).

- [ ] **Step 6: Commit**

```bash
git add frontend/nginx.conf frontend/nginx-security-headers.conf frontend/Dockerfile
git commit -m "feat(frontend): nginx sin caché para index.html, assets inmutables, gzip y cabeceras de seguridad"
```

---

### Tarea 7: Compose (logs, healthchecks, puerto) y servicio de backup

**Files:**
- Create: `backup/backup.sh`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `backend/entrypoint.sh` (comentario de un solo worker)

**Interfaces:**
- Consumes: `GET /health` (Tarea 1); `LOG_LEVEL` (Tarea 2).
- Produces: `docker compose run --rm backup now`, que deja `backups/pppoe-YYYYMMDD-HHMM.dump`. Variables `HTTP_PORT`, `LOG_LEVEL`, `BACKUP_TIME` y `BACKUP_KEEP_DAYS`.

- [ ] **Step 1: Crear `backup/backup.sh`**

```sh
#!/bin/sh
# Daily pg_dump of the monitor database, with rotation.
#
#   (no argument)  loop forever: dump every day at BACKUP_TIME (HH:MM, local TZ)
#   now            dump once right away and exit (use before upgrading)
#
# Connection comes from PGHOST/PGUSER/PGPASSWORD/PGDATABASE. Dumps are written
# as <name>.partial and renamed only when pg_dump succeeds, so a file named
# pppoe-*.dump is always complete.
set -u

BACKUP_DIR=/backups
BACKUP_TIME="${BACKUP_TIME:-03:00}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"
}

run_backup() {
    target="$BACKUP_DIR/pppoe-$(date +%Y%m%d-%H%M).dump"
    log "starting $target"
    if pg_dump -Fc -f "$target.partial"; then
        mv "$target.partial" "$target"
        log "done $target ($(du -h "$target" | cut -f1))"
    else
        rm -f "$target.partial"
        log "ERROR pg_dump failed, no backup written"
        return 1
    fi
    find "$BACKUP_DIR" -maxdepth 1 -name 'pppoe-*.dump' -mtime +"$BACKUP_KEEP_DAYS" -print -delete |
        while read -r removed; do log "removed old backup $removed"; done
    find "$BACKUP_DIR" -maxdepth 1 -name 'pppoe-*.dump.partial' -mtime +0 -delete
}

seconds_until_next_run() {
    now=$(date +%s)
    next=$(date -d "today $BACKUP_TIME" +%s 2>/dev/null) || return 1
    if [ "$next" -le "$now" ]; then
        next=$(date -d "tomorrow $BACKUP_TIME" +%s) || return 1
    fi
    echo $((next - now))
}

case "${1:-}" in
    now)
        run_backup
        exit $?
        ;;
    "")
        ;;
    *)
        echo "usage: backup.sh [now]" >&2
        exit 2
        ;;
esac

case "$BACKUP_TIME" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *) log "ERROR invalid BACKUP_TIME=$BACKUP_TIME (expected HH:MM)"; exit 1 ;;
esac

log "daily backup at $BACKUP_TIME (TZ=${TZ:-UTC}), keeping $BACKUP_KEEP_DAYS days in $BACKUP_DIR"
while true; do
    wait_seconds=$(seconds_until_next_run) || {
        log "ERROR invalid BACKUP_TIME=$BACKUP_TIME (expected HH:MM)"
        exit 1
    }
    log "next backup in ${wait_seconds}s"
    sleep "$wait_seconds"
    run_backup || true
done
```

- [ ] **Step 2: Reemplazar `docker-compose.yml`**

```yaml
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

services:
  db:
    image: postgres:16
    restart: unless-stopped
    logging: *default-logging
    environment:
      POSTGRES_USER: ${POSTGRES_USER:?set POSTGRES_USER in .env}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:?set POSTGRES_DB in .env}
    volumes:
      - pppoe_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10

  backend:
    build: ./backend
    restart: unless-stopped
    logging: *default-logging
    environment:
      DATABASE_URL: ${DATABASE_URL:?set DATABASE_URL in .env}
      JWT_SECRET: ${JWT_SECRET:?set JWT_SECRET in .env (openssl rand -hex 32)}
      MASTER_ENCRYPTION_KEY: ${MASTER_ENCRYPTION_KEY:?set MASTER_ENCRYPTION_KEY in .env (a Fernet key)}
      TOKEN_EXPIRE_MINUTES: ${TOKEN_EXPIRE_MINUTES:-480}
      TZ: ${TZ:-America/Argentina/Cordoba}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    depends_on:
      db:
        condition: service_healthy
    expose:
      - "8000"
    healthcheck:
      # The slim image has no curl; urlopen raises on the 503 of a failed check.
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  frontend:
    build: ./frontend
    restart: unless-stopped
    logging: *default-logging
    depends_on:
      backend:
        condition: service_healthy
    ports:
      - "${HTTP_PORT:-80}:80"
    healthcheck:
      # 127.0.0.1, not localhost: busybox wget may try ::1 and nginx listens on IPv4 only.
      test: ["CMD", "wget", "-qO", "/dev/null", "http://127.0.0.1/"]
      interval: 30s
      timeout: 5s
      retries: 3

  backup:
    image: postgres:16
    restart: unless-stopped
    logging: *default-logging
    init: true
    entrypoint: ["sh", "/backup.sh"]
    environment:
      PGHOST: db
      PGUSER: ${POSTGRES_USER}
      PGPASSWORD: ${POSTGRES_PASSWORD}
      PGDATABASE: ${POSTGRES_DB}
      TZ: ${TZ:-America/Argentina/Cordoba}
      BACKUP_TIME: ${BACKUP_TIME:-03:00}
      BACKUP_KEEP_DAYS: ${BACKUP_KEEP_DAYS:-14}
    volumes:
      - ./backup/backup.sh:/backup.sh:ro
      - ./backups:/backups
    depends_on:
      db:
        condition: service_healthy

volumes:
  pppoe_db_data:
```

- [ ] **Step 3: Agregar al final de `.env.example`**

```bash
# Log level of the backend (DEBUG, INFO, WARNING, ERROR, CRITICAL).
LOG_LEVEL=INFO
# Host port where the web UI is published.
HTTP_PORT=80
# Daily database backup: local time (HH:MM, in TZ) and how many days to keep.
# Dumps go to ./backups/ on the host.
BACKUP_TIME=03:00
BACKUP_KEEP_DAYS=14
```

Agregar a `.gitignore`:

```
# Database backups (docker compose "backup" service)
backups/
```

- [ ] **Step 4: Comentario en `backend/entrypoint.sh`**, justo antes de `exec uvicorn`

```sh
# One process on purpose: the APScheduler jobs (polling, rollup, purge,
# reset) run inside the app. With --workers N every poll would run N times
# and traffic would be counted N times.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 5: Validar el compose**

Run: `remote 'docker compose config -q && echo OK'`
Expected: `OK`.

- [ ] **Step 6: Desplegar sobre los datos existentes y verificar los healthchecks** (Review Focus 1)

Run: `remote 'docker compose up -d --build && sleep 75 && docker compose ps'`
Expected: `db`, `backend` y `frontend` en `(healthy)`, y `backup` en `Up`. `docker compose logs backend | tail -20` muestra líneas `INFO app.services...` con el formato nuevo.

Luego: `remote 'docker compose stop db && sleep 45 && docker compose ps backend; docker compose start db && sleep 40 && docker compose ps backend'`
Expected: el backend pasa a `(unhealthy)` con la base detenida y vuelve a `(healthy)` cuando la base arranca.

- [ ] **Step 7: Backup a demanda y restauración de prueba**

Run:
```bash
remote 'set -e; docker compose run --rm backup now; ls -l backups/; F=$(ls -t backups/pppoe-*.dump | head -1); . ./.env; docker compose exec -T db dropdb -U "$POSTGRES_USER" --if-exists pppoe_restore_test; docker compose exec -T db createdb -U "$POSTGRES_USER" pppoe_restore_test; docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d pppoe_restore_test < "$F"; for db in "$POSTGRES_DB" pppoe_restore_test; do docker compose exec -T db psql -U "$POSTGRES_USER" -d "$db" -Atc "select (select count(*) from pppoe_clients), (select count(*) from session_state), (select count(*) from traffic_hourly)"; done; docker compose exec -T db dropdb -U "$POSTGRES_USER" pppoe_restore_test'
```
Expected: se crea un `.dump` y no queda ningún `.partial`. Las dos líneas de conteos coinciden; si hay diferencia, que sea solo en `session_state` o `traffic_hourly` por un sondeo ocurrido en el medio, y no más que las filas de un sondeo.

- [ ] **Step 8: Casos de error del script** (Review Focus 4)

Run: `remote 'docker compose run --rm -e BACKUP_TIME=25:99 backup; echo exit=$?; docker compose run --rm -e PGPASSWORD=wrong backup now; echo exit=$?; ls backups/ | grep -c partial || true'`
Expected:
- El primer comando registra `ERROR invalid BACKUP_TIME=25:99` y sale con `exit=1`: `25:99` no respeta el patrón `[0-2][0-9]:[0-5][0-9]`.
  Además, `date -d` rechaza valores como `29:00`, que pasan el patrón.
- El segundo registra `ERROR pg_dump failed` y sale con `exit=1`.
- El conteo de `.partial` es `0`.

- [ ] **Step 9: Programación diaria**

La hora se calcula dentro del contenedor para usar su `TZ`:

Run: `remote 'docker compose run --rm --entrypoint sh backup -c "BACKUP_TIME=\$(date -d \"+2 min\" +%H:%M) timeout 190 sh /backup.sh"; ls -lt backups | head -3'`
Expected: registra `next backup in <180s`, después `starting` y `done`, y aparece un `.dump` nuevo. Además, `docker compose logs backup` del servicio real muestra `next backup in ...` calculado hasta las 03:00 locales. Si la hora sale corrida, verificar que la imagen `postgres:16` tenga zoneinfo (`docker compose exec backup date`); si no la tiene, montar `/usr/share/zoneinfo` del host de solo lectura y documentarlo.

- [ ] **Step 10: Limpiar los dumps de prueba y hacer el commit**

Run: `remote 'ls backups/'` y borrar solo los dumps de prueba si molestan. Los reales de la VM se pueden conservar.

```bash
git add backup/backup.sh docker-compose.yml .env.example .gitignore backend/entrypoint.sh
git commit -m "feat(deploy): backup diario con rotación, healthchecks, logs acotados y puerto configurable"
```

---

### Tarea 8: README de producción

**Files:**
- Modify: `README.md`: reescribir "Despliegue en producción (Ubuntu Server)", "Backups" y "Seguridad en producción", y agregar "Actualizar" y "Diagnóstico". El resto queda igual.

**Interfaces:**
- Consumes: `python -m app.cli create-admin|reset-password` (Tarea 5), `docker compose run --rm backup now` y las variables de la Tarea 7, el mensaje 429 (Tarea 4) y `LOG_LEVEL` (Tarea 2).

- [ ] **Step 1: Reemplazar la sección "Despliegue en producción (Ubuntu Server)"**. Mantener los pasos 1 a 4 existentes y agregar al paso 3 las variables opcionales nuevas. Reemplazar el paso 5 (el Python pegado a mano) y agregar la verificación:

````markdown
   - `LOG_LEVEL` (opcional, default `INFO`): nivel de los logs del backend.
   - `HTTP_PORT` (opcional, default `80`): puerto del host donde se publica la web.
   - `BACKUP_TIME` / `BACKUP_KEEP_DAYS` (opcionales, default `03:00` / `14`):
     hora local del backup diario y cuántos días se conservan (ver "Backups").
````

````markdown
5. Verificar que todo esté sano (puede tardar hasta un minuto la primera vez,
   mientras corren las migraciones):
   ```bash
   docker compose ps
   ```
   `db`, `backend` y `frontend` tienen que figurar `(healthy)` y `backup`, `Up`.
6. Crear el primer usuario admin (pide la contraseña dos veces, mínimo 10
   caracteres):
   ```bash
   docker compose exec backend python -m app.cli create-admin admin
   ```
7. Acceder a `http://<ip-del-servidor>/` (o `:<HTTP_PORT>`) y cargar los
   routers desde "Routers" ... (mantener el texto del paso 6 anterior)
````

Todos los servicios tienen `restart: unless-stopped`: después de un reinicio del servidor vuelven solos, siempre que el servicio de Docker arranque con el sistema, que es lo que hace el instalador oficial.

- [ ] **Step 2: Agregar la sección "Actualizar"** después de "Despliegue en producción"

````markdown
## Actualizar

1. Backup a demanda antes de tocar nada:
   ```bash
   docker compose run --rm backup now
   ```
2. Traer la versión nueva y reconstruir:
   ```bash
   git pull
   docker compose up -d --build
   ```
   Las migraciones de la base corren solas al arrancar el backend.
3. Verificar con `docker compose ps` que todo vuelva a `(healthy)`. El
   navegador carga la versión nueva sola (el `index.html` se sirve sin caché).
````

- [ ] **Step 3: Reemplazar la sección "Backups"**

````markdown
## Backups

El servicio `backup` hace un `pg_dump` completo **todos los días** a la hora
`BACKUP_TIME` (hora local de `TZ`) y lo deja en `./backups/` del directorio
del proyecto, como `pppoe-AAAAMMDD-HHMM.dump`. Borra los de más de
`BACKUP_KEEP_DAYS` días. Un archivo `.dump` siempre está completo: mientras
se escribe se llama `.partial`.

- Ver qué hizo: `docker compose logs backup`.
- Backup inmediato (por ejemplo, antes de actualizar):
  `docker compose run --rm backup now`.
- Si un backup falla, queda `ERROR` en el log y se reintenta al día
  siguiente; no se detiene el servicio.

**Los backups quedan en el mismo servidor.** Copialos a otro equipo, por
ejemplo con un cron en un NAS o en otra máquina:
```bash
rsync -a usuario@servidor:/ruta/al/proyecto/backups/ /destino/pppoe-backups/
```

Guardá también el `.env` (sobre todo `MASTER_ENCRYPTION_KEY`) en un lugar
seguro aparte: sin esa clave, las contraseñas de API de los routers de un
backup no se pueden descifrar.

### Restaurar

Reemplaza **todo** el contenido de la base por el del backup:
```bash
docker compose stop backend
docker compose exec -T db pg_restore -U pppoe -d pppoe --clean --if-exists < backups/pppoe-AAAAMMDD-HHMM.dump
docker compose start backend
```
(usar el `POSTGRES_USER` y `POSTGRES_DB` de tu `.env` si no son `pppoe`).
````

- [ ] **Step 4: Agregar la sección "Diagnóstico"** antes de "Seguridad en producción"

````markdown
## Diagnóstico

- Estado de los servicios: `docker compose ps` (`unhealthy` en `backend`
  suele indicar que no llega a la base).
- Logs en vivo del backend (sondeos, resumen por hora, purga, reseteo):
  `docker compose logs -f backend`. Para más detalle, `LOG_LEVEL=DEBUG` en
  `.env` y `docker compose up -d`. Los logs de cada servicio se rotan solos
  (3 archivos de 10 MB).
- Un router con "último sondeo" viejo en el dashboard no está respondiendo:
  buscar su nombre en los logs del backend y probar la conexión desde
  "Routers".
- Contraseña del admin olvidada:
  `docker compose exec backend python -m app.cli reset-password admin`.
- Tras 5 intentos de login fallidos desde la misma IP en 15 minutos, esa IP
  queda bloqueada hasta que pase la ventana (reiniciar el backend también la
  libera). Los intentos fallidos quedan en el log con usuario e IP.
- **No** agregar `--workers` a uvicorn: el scheduler de sondeos corre dentro
  del backend y con varios procesos el tráfico se contaría varias veces.
````

- [ ] **Step 5: Ajustar "Seguridad en producción".** Reemplazar el punto de HTTPS por:

```markdown
- El sistema está pensado para usarse **solo desde la red interna o una
  VPN** y sirve por HTTP simple. No publicarlo a internet; si alguna vez
  hiciera falta, poner delante un reverse proxy con HTTPS.
```

Cambiar "exponer únicamente el puerto 80" por "exponer únicamente el `HTTP_PORT` (80 por defecto) y solo hacia la LAN/VPN".

- [ ] **Step 6: Revisar que el README no mencione el Python pegado a mano ni el backup `.sql` viejo**

Run: `grep -n "python3 -c\|backup-\$(date\|psql -U pppoe pppoe" README.md`
Expected: sin coincidencias, salvo el comando de `Fernet.generate_key`, que es legítimo.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: guía de producción con instalación, actualización, backups, restauración y diagnóstico"
```

---

### Tarea 9: Verificación final en la VM

**Files:** ninguno, salvo que algo falle.

- [ ] **Step 1: Suite completa y build**

Run: `remote 'cd backend && .venv/bin/pytest -q' && remote 'cd frontend && npm run build'`
Expected: todos los tests pasan y el build sale sin errores.

- [ ] **Step 2: Límite de login contra el stack real**

Run: `remote 'for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"wrong-$i\"}" http://localhost:${HTTP_PORT:-80}/api/auth/login; done; docker compose logs backend | grep "login failed" | tail -2'`
Expected: `401` cinco veces y después `429`. El log muestra `login failed user=admin ip=<IP de la VM vista por nginx>`, no la IP interna de nginx. Después: `remote 'docker compose restart backend'` para liberar la IP.

- [ ] **Step 3: Reinicio de la VM**

Run: `remote 'sudo reboot'` (el comando se corta). Esperar 2 minutos y correr `remote 'docker compose ps && docker compose logs backend --since 3m | grep -i poll | tail -3'`
Expected: todos los servicios `(healthy)` (o `Up` para `backup`) y líneas de polling posteriores al reinicio. Si `sudo` pide contraseña, usar `echo mario | sudo -S reboot`.

- [ ] **Step 4: Comprobar en el navegador** que la UI carga en `http://<ip-de-pruebas>/` y que el login funciona. Si el usuario usa otro puerto, ajustarlo. Es una verificación manual que le toca al usuario.
