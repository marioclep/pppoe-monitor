# Rediseño de la interfaz y dashboard con gráficos — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aplicar el estilo "pizarra moderna" con temas claro y oscuro a todas las pantallas y convertir el dashboard en un tablero de 5 paneles, alimentado por una tabla nueva de estadísticas por sondeo.

**Architecture:**
- **Backend.** Suma la tabla `router_poll_stats`, que se escribe dentro de la transacción de cada sondeo, se purga a los `retention_days` y se rellena desde los datos existentes en la migración. Suma también un servicio de historia agregada por intervalos (`GET /dashboard/history`) y dos campos nuevos en `/dashboard/summary`.
- **Frontend.** Los estilos pasan a tokens CSS por tema (`data-theme` en `<html>`) y se arman componentes reutilizables (`Panel`, `StatCard`, `RangeSelector`, `StatusDot`, `TrafficChart`…). Cada página se rearma con esas piezas.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 (`date_bin`), pytest; React 19 + TypeScript, react-router 7, Recharts 3, CSS propio.

**Spec:** `docs/superpowers/specs/2026-09-25-rediseno-interfaz-design.md`

## Global Constraints

- **Rama:** `rediseno-interfaz`, que parte de `endurecimiento-produccion`.
- **Mac:** no instalar nada. Todo corre en la VM con `.superpowers/sdd/2026-09-22-pppoe-monitor-plan/remote '<cmd>'`, que ejecuta dentro de `~/pppoe-monitor`.
- **Comandos de verificación:**
  - Tests del backend: `remote 'cd backend && .venv/bin/pytest -q'`.
  - Migración de la base de desarrollo que usan los tests: `remote 'cd backend && .venv/bin/alembic upgrade head'`.
  - Build del frontend: `remote 'cd frontend && npm run build'`.
- **Dependencias:** no agregar ninguna, ni de npm ni de Python. Sin fuentes externas; íconos como SVG propios.
- **Paleta oscura:**

  | Uso | Color |
  |---|---|
  | Fondo | `#0f172a` |
  | Lateral | `#0b1222` |
  | Paneles | `#111c33` |
  | Bordes | `#1e293b` |
  | Texto | `#e2e8f0` |
  | Acento | `#60a5fa` |

- **Paleta clara:**

  | Uso | Color |
  |---|---|
  | Fondo | `#f1f5f9` |
  | Paneles y lateral | `#ffffff` |
  | Bordes | `#e2e8f0` |
  | Texto | `#0f172a` |
  | Acento | `#3b82f6` |
  | Menú activo | `#1d4ed8` |

- **Series de los gráficos:**

  | Serie | Oscuro | Claro |
  |---|---|---|
  | Descarga | `#60a5fa` | `#3b82f6` |
  | Subida | `#f472b6` | `#db2777` |
  | Conectados | `#34d399` | `#059669` |

- **Tema:**
  - `data-theme="light|dark"` en `<html>`.
  - Se guarda en `localStorage` con la clave `pppoe_theme`, siempre dentro de try/catch.
  - Por defecto sigue `prefers-color-scheme`.
  - Un script en línea en `index.html` lo aplica antes del primer render.
- **Intervalos de `/dashboard/history`:**

  | `hours` | Ancho del intervalo |
  |---|---|
  | 24 | 300 s |
  | 168 | 1800 s |
  | 720 | 7200 s |
  | 2160 | 21600 s |

  Cualquier otro valor devuelve 422. Los intervalos se alinean con `date_bin(..., '2000-01-01 00:00:00+00')`.
- **Huecos en los gráficos:** se corta la línea si entre dos puntos pasan más de 2,5 × el ancho del intervalo. Los puntos horarios del detalle usan 2,5 × 3600 s. El gráfico acumulado no se corta.
- **Estado de un router**, según la edad del último sondeo exitoso:
  - `ok`: menos de 2 × el intervalo de sondeo;
  - `late`: menos de 6 × el intervalo;
  - `down`: 6 × el intervalo o más, o nunca sondeado.
- **Refresco del dashboard:** cada 60 s, en pausa mientras la pestaña está oculta.
- **Textos:** los de la interfaz en español rioplatense; el código y los logs en inglés.
- **Commits:** en español con el formato `tipo(ámbito): descripción`, terminados con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Datos reales:** la VM tiene 6 routers y unas 3.300 sesiones reales. La migración se prueba primero sobre una copia.

## Review Focus

1. **Relleno en la hora de la primera muestra.** La hora que contiene la primera muestra de 5 min de un router no puede contarse dos veces (desde `traffic_samples` y desde `traffic_hourly`). Solo entran horas **completas** anteriores a esa muestra. Se verifica en la Tarea 1, paso 5.
2. **Intervalos con conectados NULL mezclados.** Si un router tiene `clients_connected` NULL y otro no, la suma no puede anularse ni tomar el NULL como 0 por error. Test en la Tarea 4.
3. **`localStorage` bloqueado o vacío.** El tema tiene que funcionar igual, siguiendo el del sistema, sin excepciones que rompan el render. Se revisa a mano en la Tarea 10 y lo cubre el código de la Tarea 6.
4. **Historia vacía:** router recién agregado o migración sin muestras. El gráfico muestra "Sin datos para el rango elegido" en lugar de un error o un eje roto. Lo cubren las Tareas 6 y 7.
5. **Ancho de celular (375 px).** La barra lateral tiene que pasar a menú y las tablas a tarjetas, sin scroll horizontal de la página. Se revisa a mano en la Tarea 10.

---

### Tarea 1: Modelo `RouterPollStat` y migración con relleno

**Files:**
- Create: `backend/app/models/poll_stats.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/7c1e4a9b2d30_router_poll_stats.py`

**Interfaces:**
- Produces: `RouterPollStat` (tabla `router_poll_stats`) con `id`, `router_id`, `polled_at`, `clients_connected: int | None`, `rx_bps: int` y `tx_bps: int`.

- [ ] **Step 1: Modelo** `backend/app/models/poll_stats.py`

```python
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RouterPollStat(Base):
    """Totals of one successful poll of one router: what the dashboard's
    history charts are drawn from (a few hundred thousand rows for 90 days,
    instead of aggregating millions of per-client samples). clients_connected
    is NULL on rows backfilled from traffic_hourly, which never stored it."""

    __tablename__ = "router_poll_stats"
    __table_args__ = (
        Index("ix_router_poll_stats_polled_at", "polled_at"),
        Index("ix_router_poll_stats_router_time", "router_id", "polled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clients_connected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
```

Agregar a `backend/app/models/__init__.py`:

```python
from app.models.poll_stats import RouterPollStat  # noqa: F401
```

- [ ] **Step 2: Migración** `backend/alembic/versions/7c1e4a9b2d30_router_poll_stats.py`

```python
"""router_poll_stats: per-poll router totals for the dashboard history

Backfill:
- from traffic_samples: one row per (router, poll) -- a router's poll
  writes all its samples with the same sampled_at;
- from traffic_hourly: one row per (router, hour) for the whole hours
  *before* that router's first 5-minute sample (so no hour is counted from
  both sources), with clients_connected NULL (never stored).

Revision ID: 7c1e4a9b2d30
Revises: 5d2b7e9c41a0
Create Date: 2026-09-25 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1e4a9b2d30"
down_revision: Union[str, None] = "5d2b7e9c41a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "router_poll_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("router_id", sa.Integer(), sa.ForeignKey("routers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("polled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clients_connected", sa.Integer(), nullable=True),
        sa.Column("rx_bps", sa.BigInteger(), nullable=False),
        sa.Column("tx_bps", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_router_poll_stats_polled_at", "router_poll_stats", ["polled_at"])
    op.create_index("ix_router_poll_stats_router_time", "router_poll_stats", ["router_id", "polled_at"])

    op.execute(
        """
        INSERT INTO router_poll_stats (router_id, polled_at, clients_connected, rx_bps, tx_bps)
        SELECT c.router_id, s.sampled_at, COUNT(DISTINCT s.client_id), SUM(s.rx_bps), SUM(s.tx_bps)
        FROM traffic_samples s
        JOIN pppoe_clients c ON c.id = s.client_id
        GROUP BY c.router_id, s.sampled_at
        """
    )
    op.execute(
        """
        WITH first_sample AS (
            SELECT c.router_id, MIN(s.sampled_at) AS first_at
            FROM traffic_samples s
            JOIN pppoe_clients c ON c.id = s.client_id
            GROUP BY c.router_id
        )
        INSERT INTO router_poll_stats (router_id, polled_at, clients_connected, rx_bps, tx_bps)
        SELECT c.router_id, h.hour_start, NULL, SUM(h.rx_bytes) * 8 / 3600, SUM(h.tx_bytes) * 8 / 3600
        FROM traffic_hourly h
        JOIN pppoe_clients c ON c.id = h.client_id
        LEFT JOIN first_sample f ON f.router_id = c.router_id
        WHERE f.first_at IS NULL OR h.hour_start + INTERVAL '1 hour' <= f.first_at
        GROUP BY c.router_id, h.hour_start
        """
    )


def downgrade() -> None:
    op.drop_index("ix_router_poll_stats_router_time", table_name="router_poll_stats")
    op.drop_index("ix_router_poll_stats_polled_at", table_name="router_poll_stats")
    op.drop_table("router_poll_stats")
```

- [ ] **Step 3: Aplicar a la base de desarrollo y correr la suite**

Run: `remote 'cd backend && .venv/bin/alembic upgrade head && .venv/bin/alembic current && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: `7c1e4a9b2d30 (head)` y la suite completa pasa (213).

- [ ] **Step 4: Probar la migración sobre una copia de la base real**

Run:
```bash
remote 'set -e; . ./.env; U=$POSTGRES_USER; docker compose run --rm -T backup now >/dev/null 2>&1; F=$(ls -t backups/pppoe-*.dump | head -1); docker compose exec -T db dropdb -U $U --if-exists pppoe_migtest; docker compose exec -T db createdb -U $U pppoe_migtest; docker compose exec -T db pg_restore -U $U -d pppoe_migtest < $F; docker compose build -q backend; docker compose run --rm -T --no-deps -e DATABASE_URL=postgresql://$U:$POSTGRES_PASSWORD@db:5432/pppoe_migtest backend alembic upgrade head'
```
Expected: la migración corre sin error hasta `7c1e4a9b2d30`.

- [ ] **Step 5: Verificar el relleno sobre la copia** (Review Focus 1)

Run:
```bash
remote '. ./.env; Q(){ docker compose exec -T db psql -U $POSTGRES_USER -d pppoe_migtest -Atc "$1"; }; echo "rows (with clients / null):"; Q "select count(*) filter (where clients_connected is not null), count(*) filter (where clients_connected is null) from router_poll_stats"; echo "sample polls:"; Q "select count(*) from (select distinct c.router_id, s.sampled_at from traffic_samples s join pppoe_clients c on c.id=s.client_id) x"; echo "overlap hours (must be 0):"; Q "select count(*) from router_poll_stats h join router_poll_stats s on s.router_id=h.router_id and h.clients_connected is null and s.clients_connected is not null and s.polled_at >= h.polled_at and s.polled_at < h.polled_at + interval \$\$1 hour\$\$"; echo "one poll check:"; Q "select p.clients_connected, p.rx_bps, (select count(distinct s.client_id) from traffic_samples s join pppoe_clients c on c.id=s.client_id where c.router_id=p.router_id and s.sampled_at=p.polled_at), (select sum(s.rx_bps) from traffic_samples s join pppoe_clients c on c.id=s.client_id where c.router_id=p.router_id and s.sampled_at=p.polled_at) from router_poll_stats p where p.clients_connected is not null order by p.polled_at desc limit 1"; docker compose exec -T db dropdb -U $POSTGRES_USER pppoe_migtest'
```
Expected:
- las filas con conectados coinciden con la cantidad de sondeos distintos de las muestras;
- `overlap hours` da `0`;
- en `one poll check` los pares coinciden: conectados = conteo manual y rx = suma manual.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/poll_stats.py backend/app/models/__init__.py backend/alembic/versions/7c1e4a9b2d30_router_poll_stats.py
git commit -m "feat(backend): tabla router_poll_stats con relleno desde muestras y resumen horario"
```

---

### Tarea 2: El sondeo escribe `router_poll_stats`

**Files:**
- Modify: `backend/app/services/polling.py` (`poll_router`)
- Test: `backend/tests/test_polling.py`

**Interfaces:**
- Consumes: `RouterPollStat` (Tarea 1).
- Produces: una fila por sondeo exitoso con `polled_at == router.last_polled_at`, `clients_connected = len(clientes vistos)`, y `rx_bps`/`tx_bps` = suma de las velocidades de las sesiones medidas.

- [ ] **Step 1: Leer los helpers de `tests/test_polling.py`** (fixtures `router`, `db`, `_serve` y `_delete_router_data`) para reusarlos exactamente. `_delete_router_data` tiene que borrar también las `RouterPollStat` del router. Agregar al principio de esa función:

```python
    db.query(RouterPollStat).filter(RouterPollStat.router_id == router_id).delete(synchronize_session=False)
```

y el import `from app.models.poll_stats import RouterPollStat`.

- [ ] **Step 2: Escribir los tests que fallan.** Agregar al final de `backend/tests/test_polling.py`, usando el mismo fixture de router que los tests existentes (se llama `router` en este archivo; confirmarlo en el Step 1 y ajustar el nombre si difiere):

```python
def _poll_stats(db: Session, router_id: int) -> list[RouterPollStat]:
    db.expire_all()
    return db.query(RouterPollStat).filter_by(router_id=router_id).order_by(RouterPollStat.polled_at).all()


def test_successful_poll_writes_router_poll_stats(db, router, monkeypatch):
    _serve(monkeypatch, _session("ana", 100, 1_000, 2_000), _session("beto", 100, 5_000, 6_000))
    poll_router(db, router)
    _serve(monkeypatch, _session("ana", 400, 31_000, 62_000), _session("beto", 400, 5_000, 6_000))
    db.expire_all()
    router_row = db.get(Router, router.id)
    router_row.last_polled_at = router_row.last_polled_at - timedelta(seconds=300)
    db.commit()
    poll_router(db, router_row)

    stats = _poll_stats(db, router.id)
    assert len(stats) == 2
    last = stats[-1]
    assert last.polled_at == db.get(Router, router.id).last_polled_at
    assert last.clients_connected == 2
    ana_state = db.query(SessionState).filter_by(client_id=_client(db, router, "ana").id).one()
    beto_state = db.query(SessionState).filter_by(client_id=_client(db, router, "beto").id).one()
    assert last.rx_bps == ana_state.last_rx_bps + beto_state.last_rx_bps
    assert last.tx_bps == ana_state.last_tx_bps + beto_state.last_tx_bps
    assert last.rx_bps > 0


def test_poll_counts_a_client_with_two_sessions_once(db, router, monkeypatch):
    _serve(
        monkeypatch,
        _session("doble", 100, 1_000, 1_000, interface_id="*a", interface_name="<pppoe-doble>"),
        _session("doble", 100, 1_000, 1_000, interface_id="*b", interface_name="<pppoe-doble-1>"),
    )
    poll_router(db, router)
    assert _poll_stats(db, router.id)[-1].clients_connected == 1


def test_failed_poll_writes_no_router_poll_stats(db, router, monkeypatch):
    def boom(r):
        raise MikrotikError("unreachable")

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", boom)
    poll_router(db, router)
    assert _poll_stats(db, router.id) == []
```

Agregar el import `from app.models.poll_stats import RouterPollStat` arriba del archivo.

- [ ] **Step 3: Correr y ver que fallan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_polling.py -q 2>&1 | tail -4'`
Expected: `test_successful_poll_writes_router_poll_stats` y `test_poll_counts_a_client_with_two_sessions_once` FAIL (sin filas). `test_failed_poll_writes_no_router_poll_stats` pasa desde el principio: es una guarda contra una regresión y se confirma con la mutación del Step 5.

- [ ] **Step 4: Implementar** en `backend/app/services/polling.py`

Import: `from app.models.poll_stats import RouterPollStat`.

Junto a `seen_client_ids: set[int] = set()` agregar:

```python
    router_rx_bps = router_tx_bps = 0
```

Justo después de `measured = True` (dentro del `for session`) no se toca nada. Después del bloque `if not measured: continue`, junto a `db.add(TrafficSample(...))`, acumular:

```python
        router_rx_bps += rx_bps_total
        router_tx_bps += tx_bps_total
```

Antes de `db.commit()` (después del bucle de `stale_clients`):

```python
    db.add(
        RouterPollStat(
            router_id=router.id,
            polled_at=now,
            clients_connected=len(seen_client_ids),
            rx_bps=router_rx_bps,
            tx_bps=router_tx_bps,
        )
    )
```

- [ ] **Step 5: Correr, verificar y probar la mutación**

Run: `remote 'cd backend && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: toda la suite pasa.

Mutación: mover temporalmente el `db.add(RouterPollStat(...))` antes del `try` de `fetch_active_sessions` (con `now` definido ahí) y verificar que `test_failed_poll_writes_no_router_poll_stats` falla. Después revertir y volver a correr la suite.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/polling.py backend/tests/test_polling.py
git commit -m "feat(backend): guardar totales de cada sondeo por router en router_poll_stats"
```

---

### Tarea 3: Purga de `router_poll_stats`

**Files:**
- Modify: `backend/app/services/purge.py`
- Test: `backend/tests/test_purge.py`

**Interfaces:**
- Produces: `PurgeResult` con un campo nuevo `poll_stats: int`. `purge_old_samples` borra `router_poll_stats` con `polled_at < today - retention_days`, por lotes.

- [ ] **Step 1: Escribir el test que falla.** Agregar a `backend/tests/test_purge.py` (el fixture `client_id` crea un router y un cliente; leerlo para obtener el `router_id` con `db.get(PPPoEClient, client_id).router_id`):

```python
def test_purge_trims_router_poll_stats_to_retention_days(db, client_id):
    router_id = db.get(PPPoEClient, client_id).router_id
    _write_settings(db, {"retention_days": "90"})
    now = datetime.now(timezone.utc)
    for age in (timedelta(days=91), timedelta(days=89), timedelta(hours=1)):
        db.add(RouterPollStat(router_id=router_id, polled_at=now - age, clients_connected=1, rx_bps=1, tx_bps=1))
    db.commit()

    result = purge_old_samples(db, today=now)

    remaining = sorted(
        (now - s.polled_at).days
        for s in db.query(RouterPollStat).filter_by(router_id=router_id)
    )
    assert remaining == [0, 89]
    assert result.poll_stats >= 1
```

Import: `from app.models.poll_stats import RouterPollStat`.

- [ ] **Step 2: Correr y ver que falla**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_purge.py -q 2>&1 | tail -3'`
Expected: FAIL (queda la fila de 91 días, o `AttributeError: poll_stats`).

- [ ] **Step 3: Implementar** en `backend/app/services/purge.py`

```python
_DELETE_POLL_STATS_BATCH = text(
    "DELETE FROM router_poll_stats WHERE id IN "
    "(SELECT id FROM router_poll_stats WHERE polled_at < :cutoff LIMIT :batch_size)"
)
```

```python
@dataclass(frozen=True)
class PurgeResult:
    samples: int
    hourly: int
    poll_stats: int
```

Al final de `purge_old_samples`:

```python
    hourly_cutoff = today - timedelta(days=get_retention_days(db))
    hourly = _delete_in_batches(db, _DELETE_HOURS_BATCH, hourly_cutoff, batch_size)
    # The dashboard history covers the same span as the hourly rollup.
    poll_stats = _delete_in_batches(db, _DELETE_POLL_STATS_BATCH, hourly_cutoff, batch_size)
    return PurgeResult(samples=samples, hourly=hourly, poll_stats=poll_stats)
```

Si algún test existente construye `PurgeResult(samples=..., hourly=...)` o lo compara por igualdad, corregirlo para incluir `poll_stats` (buscar con `grep -n "PurgeResult(" backend/tests`).

- [ ] **Step 4: Correr toda la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/purge.py backend/tests/test_purge.py
git commit -m "feat(backend): purgar router_poll_stats con la retención de historial"
```

---

### Tarea 4: Historia agregada del dashboard (`GET /dashboard/history`)

**Files:**
- Create: `backend/app/services/dashboard_history.py`
- Modify: `backend/app/schemas/client.py` (schemas nuevos)
- Modify: `backend/app/api/dashboard.py`
- Test: `backend/tests/test_dashboard_history.py`

**Interfaces:**
- Produces:
  - `BUCKET_SECONDS: dict[int, int] = {24: 300, 168: 1800, 720: 7200, 2160: 21600}`.
  - `get_dashboard_history(db: Session, hours: int, now: datetime | None = None) -> DashboardHistory`.
  - Schemas `DashboardHistoryPoint(t: datetime, rx_bps: int, tx_bps: int, clients_connected: int | None)` y `DashboardHistory(bucket_seconds: int, points: list[DashboardHistoryPoint])`.
  - Endpoint `GET /dashboard/history?hours=` que devuelve `DashboardHistory`.

- [ ] **Step 1: Escribir los tests que fallan** en `backend/tests/test_dashboard_history.py`. Usan un `now` en el año 2001 para no chocar con otros datos de la base de desarrollo:

```python
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.poll_stats import RouterPollStat
from app.models.router import Router
from app.models.user import User
from app.services.dashboard_history import get_dashboard_history

NOW = datetime(2001, 1, 2, 0, 0, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def routers(db):
    made = [
        Router(name=f"Hist {n}", host=f"10.9.0.{n}", api_username="a", api_password_encrypted="", enabled=enabled)
        for n, enabled in ((1, True), (2, True), (3, False))
    ]
    db.add_all(made)
    db.commit()
    yield made
    db.query(Router).filter(Router.id.in_([r.id for r in made])).delete(synchronize_session=False)
    db.commit()


def _stat(db, router, minutes_ago, rx, tx, clients):
    db.add(
        RouterPollStat(
            router_id=router.id,
            polled_at=NOW - timedelta(minutes=minutes_ago),
            clients_connected=clients,
            rx_bps=rx,
            tx_bps=tx,
        )
    )


def test_24h_uses_5_minute_buckets_and_sums_routers(db, routers):
    r1, r2, _ = routers
    _stat(db, r1, 12, rx=100, tx=1000, clients=10)  # bucket NOW-15m
    _stat(db, r2, 11, rx=50, tx=500, clients=5)  # same bucket
    _stat(db, r1, 2, rx=300, tx=3000, clients=12)  # bucket NOW-5m
    db.commit()

    history = get_dashboard_history(db, 24, now=NOW)

    assert history.bucket_seconds == 300
    assert [(p.t, p.rx_bps, p.tx_bps, p.clients_connected) for p in history.points] == [
        (NOW - timedelta(minutes=15), 150, 1500, 15),
        (NOW - timedelta(minutes=5), 300, 3000, 12),
    ]


def test_7d_averages_each_router_within_the_bucket_then_sums(db, routers):
    r1, r2, _ = routers
    # One 30-minute bucket [NOW-30m, NOW): r1 polled twice, r2 once.
    _stat(db, r1, 25, rx=100, tx=100, clients=10)
    _stat(db, r1, 20, rx=300, tx=300, clients=20)
    _stat(db, r2, 22, rx=40, tx=40, clients=4)
    db.commit()

    history = get_dashboard_history(db, 168, now=NOW)

    assert history.bucket_seconds == 1800
    assert [(p.rx_bps, p.clients_connected) for p in history.points] == [(240, 19)]


def test_null_clients_of_one_router_keep_the_others(db, routers):
    r1, r2, _ = routers
    _stat(db, r1, 30, rx=10, tx=10, clients=None)  # backfilled from hourly
    _stat(db, r2, 30, rx=20, tx=20, clients=7)
    db.commit()

    points = get_dashboard_history(db, 720, now=NOW).points

    assert [(p.rx_bps, p.clients_connected) for p in points] == [(30, 7)]


def test_all_null_clients_stay_null(db, routers):
    r1, _, _ = routers
    _stat(db, r1, 30, rx=10, tx=10, clients=None)
    db.commit()

    assert get_dashboard_history(db, 720, now=NOW).points[0].clients_connected is None


def test_disabled_routers_and_rows_outside_the_range_are_excluded(db, routers):
    r1, _, disabled = routers
    _stat(db, disabled, 5, rx=999, tx=999, clients=99)
    _stat(db, r1, 60 * 25, rx=5, tx=5, clients=1)  # 25h ago: outside 24h
    db.commit()

    assert get_dashboard_history(db, 24, now=NOW).points == []


@pytest.mark.parametrize("hours,bucket", [(24, 300), (168, 1800), (720, 7200), (2160, 21600)])
def test_bucket_per_range(db, hours, bucket):
    assert get_dashboard_history(db, hours, now=NOW).bucket_seconds == bucket


def _auth_headers() -> dict:
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "hist-tester").delete()
        db.add(User(username="hist-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('hist-tester')}"}


def test_endpoint_returns_history_and_validates_hours():
    headers = _auth_headers()
    ok = client.get("/dashboard/history?hours=24", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["bucket_seconds"] == 300
    assert isinstance(ok.json()["points"], list)
    assert client.get("/dashboard/history?hours=25", headers=headers).status_code == 422


def test_endpoint_requires_auth():
    assert client.get("/dashboard/history?hours=24").status_code == 401
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_dashboard_history.py -q 2>&1 | tail -3'`
Expected: error de colección con `ModuleNotFoundError: app.services.dashboard_history`.

- [ ] **Step 3: Schemas.** Agregar a `backend/app/schemas/client.py`:

```python
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
```

- [ ] **Step 4: Servicio** `backend/app/services/dashboard_history.py`

```python
"""Network-wide traffic and connected clients over time, from
router_poll_stats. Each bucket first averages every router's polls inside
it (so a router polled twice in a bucket isn't counted twice), then sums
the routers. Empty buckets are simply absent: the frontend draws a gap."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.client import DashboardHistory, DashboardHistoryPoint

BUCKET_SECONDS: dict[int, int] = {24: 300, 168: 1800, 720: 7200, 2160: 21600}

_HISTORY = text(
    """
    WITH per_router AS (
        SELECT date_bin(make_interval(secs => :bucket), s.polled_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS t,
               s.router_id,
               AVG(s.rx_bps) AS rx_bps,
               AVG(s.tx_bps) AS tx_bps,
               AVG(s.clients_connected) AS clients
        FROM router_poll_stats s
        JOIN routers r ON r.id = s.router_id
        WHERE r.enabled AND s.polled_at >= :since AND s.polled_at < :until
        GROUP BY 1, 2
    )
    SELECT t, SUM(rx_bps) AS rx_bps, SUM(tx_bps) AS tx_bps, SUM(clients) AS clients
    FROM per_router
    GROUP BY t
    ORDER BY t
    """
)


def get_dashboard_history(db: Session, hours: int, now: datetime | None = None) -> DashboardHistory:
    bucket = BUCKET_SECONDS[hours]
    until = now or datetime.now(timezone.utc)
    rows = db.execute(
        _HISTORY, {"bucket": bucket, "since": until - timedelta(hours=hours), "until": until}
    ).all()
    return DashboardHistory(
        bucket_seconds=bucket,
        points=[
            DashboardHistoryPoint(
                t=row.t,
                rx_bps=round(row.rx_bps),
                tx_bps=round(row.tx_bps),
                clients_connected=None if row.clients is None else round(row.clients),
            )
            for row in rows
        ],
    )
```

- [ ] **Step 5: Endpoint.** En `backend/app/api/dashboard.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query
```

```python
from app.schemas.client import DashboardHistory, DashboardSummary, RouterSummary
from app.services.dashboard_history import BUCKET_SECONDS, get_dashboard_history
```

```python
@router.get("/history", response_model=DashboardHistory)
def history(hours: int = Query(24), db: Session = Depends(get_db)):
    if hours not in BUCKET_SECONDS:
        raise HTTPException(status_code=422, detail=f"hours must be one of {sorted(BUCKET_SECONDS)}")
    return get_dashboard_history(db, hours)
```

- [ ] **Step 6: Correr toda la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: todo pasa.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/dashboard_history.py backend/app/schemas/client.py backend/app/api/dashboard.py backend/tests/test_dashboard_history.py
git commit -m "feat(backend): historia del dashboard por intervalos desde router_poll_stats"
```

---

### Tarea 5: `/dashboard/summary` con intervalo de sondeo y clientes vistos

**Files:**
- Modify: `backend/app/schemas/client.py` (`DashboardSummary`)
- Modify: `backend/app/api/dashboard.py` (`summary`)
- Test: `backend/tests/test_api_dashboard.py`

**Interfaces:**
- Produces: dos campos nuevos en `DashboardSummary`:
  - `polling_interval_seconds: int`;
  - `clients_seen_this_period: int` (cantidad de `accumulation_periods` abiertos de clientes de routers habilitados).

- [ ] **Step 1: Escribir el test que falla** en `backend/tests/test_api_dashboard.py`

```python
def test_dashboard_summary_reports_polling_interval_and_clients_seen_this_period():
    from app.models.traffic import AccumulationPeriod
    from app.services.app_settings import get_polling_interval_seconds

    db: Session = SessionLocal()
    try:
        before = client.get("/dashboard/summary", headers=_auth_headers()).json()["clients_seen_this_period"]
        router = Router(name="Dash Seen", host="10.0.0.40", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        online = _add_client(db, router.id, "seen_on", True, (1, 1))
        offline = _add_client(db, router.id, "seen_off", False, None)
        db.add_all([AccumulationPeriod(client_id=online), AccumulationPeriod(client_id=offline)])
        db.commit()
        router_id = router.id
        interval = get_polling_interval_seconds(db)
    finally:
        db.close()

    try:
        body = client.get("/dashboard/summary", headers=_auth_headers()).json()
        assert body["polling_interval_seconds"] == interval
        assert body["clients_seen_this_period"] == before + 2
    finally:
        _cleanup([router_id])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `remote 'cd backend && .venv/bin/pytest tests/test_api_dashboard.py -q 2>&1 | tail -3'`
Expected: FAIL con `KeyError: 'clients_seen_this_period'`.

- [ ] **Step 3: Implementar**

En `DashboardSummary`:

```python
class DashboardSummary(BaseModel):
    total_clients_connected: int
    current_rx_bps: int
    current_tx_bps: int
    by_router: list[RouterSummary]
    # Router status (ok / late / no answer) is judged against this.
    polling_interval_seconds: int
    # Clients with an open accumulation period: seen since the last reset.
    clients_seen_this_period: int
```

En `backend/app/api/dashboard.py`: importar `AccumulationPeriod` de `app.models.traffic` y `get_polling_interval_seconds` de `app.services.app_settings`. Antes del `return` de `summary`:

```python
    clients_seen = (
        db.query(func.count(AccumulationPeriod.id))
        .join(PPPoEClient, PPPoEClient.id == AccumulationPeriod.client_id)
        .join(Router, Router.id == PPPoEClient.router_id)
        .filter(AccumulationPeriod.period_end.is_(None), Router.enabled.is_(True))
        .scalar()
    )
```

y en el `DashboardSummary(...)` del `return`:

```python
        polling_interval_seconds=get_polling_interval_seconds(db),
        clients_seen_this_period=clients_seen,
```

- [ ] **Step 4: Correr toda la suite**

Run: `remote 'cd backend && .venv/bin/pytest -q 2>&1 | tail -1'`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/client.py backend/app/api/dashboard.py backend/tests/test_api_dashboard.py
git commit -m "feat(backend): el resumen del dashboard informa intervalo de sondeo y clientes vistos en el período"
```

---

### Tarea 6: Base visual del frontend (tema, layout y componentes)

**Files:**
- Modify: `frontend/index.html`
- Create: `frontend/src/theme.ts`
- Create: `frontend/src/context/ThemeContext.tsx`
- Modify: `frontend/src/main.tsx`
- Replace: `frontend/src/index.css`
- Create: `frontend/src/components/Icon.tsx`, `ThemeToggle.tsx`, `PageHeader.tsx`, `Panel.tsx`, `StatCard.tsx`, `RangeSelector.tsx`, `StatusDot.tsx` y `TrafficChart.tsx`
- Replace: `frontend/src/components/Layout.tsx`
- Create: `frontend/src/utils/gaps.ts`, `frontend/src/utils/routerStatus.ts`
- Create: `frontend/src/hooks/useAutoRefresh.ts`
- Modify: `frontend/src/utils/format.ts` (agregar `formatChartTime`)

**Interfaces (las usan las Tareas 7 a 9):**
- `useTheme(): { theme: 'light' | 'dark'; toggle: () => void }`
- `<ThemeToggle />`
- `<PageHeader title: string>{acciones opcionales}</PageHeader>`
- `<Panel title?: ReactNode, action?: ReactNode, className?: string>{children}</Panel>`
- `<StatCard label: string, value: ReactNode, sub?: ReactNode />`
- `RANGES: { hours: number; label: string }[]` y `<RangeSelector value: number, onChange: (hours: number) => void />`
- `<StatusDot tone: 'ok' | 'late' | 'down' | 'off', label: string />`
- `routerStatus(lastPolledAt: string | null, intervalSeconds: number, now?: number): 'ok' | 'late' | 'down'` y `ROUTER_STATUS_LABEL`
- `withGaps<T extends { t: number }>(rows: T[], maxGapSeconds: number, blank: (t: number) => T): T[]`
- `<TrafficChart data: ChartRow[], series: ChartSeries[], format: (v: number) => string, hours: number, height?: number />`, con `ChartRow = { t: number; [key: string]: number | null }` y `ChartSeries = { key: string; label: string; color: string; dashed?: boolean; fill?: boolean }`
- `useAutoRefresh(callback: () => void, intervalMs: number, deps: unknown[]): void`
- `formatChartTime(t: number, hours: number): string`
- Variables CSS de color para series: `var(--series-download)`, `var(--series-upload)` y `var(--series-clients)`.

- [ ] **Step 1: `frontend/index.html`**

```html
<!doctype html>
<html lang="es">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Monitor PPPoE</title>
    <script>
      // Apply the saved (or system) theme before the first paint: no flash
      // of the wrong theme. Same key and values as src/theme.ts.
      (function () {
        var theme = null
        try {
          theme = localStorage.getItem('pppoe_theme')
        } catch (e) {}
        if (theme !== 'light' && theme !== 'dark') {
          theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
        }
        document.documentElement.dataset.theme = theme
      })()
    </script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: `frontend/src/theme.ts` y `frontend/src/context/ThemeContext.tsx`**

```ts
export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'pppoe_theme'

export function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

export function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function saveTheme(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* not persisted: the next visit follows the system theme */
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
}
```

```tsx
import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { applyTheme, saveTheme, storedTheme, systemTheme } from '../theme'
import type { Theme } from '../theme'

interface ThemeContextValue {
  theme: Theme
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme())

  useEffect(() => applyTheme(theme), [theme])

  // Without an explicit choice, follow the system theme as it changes.
  useEffect(() => {
    if (storedTheme()) return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const follow = () => {
      if (!storedTheme()) setTheme(media.matches ? 'dark' : 'light')
    }
    media.addEventListener('change', follow)
    return () => media.removeEventListener('change', follow)
  }, [])

  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark'
    saveTheme(next)
    setTheme(next)
  }

  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
```

En `frontend/src/main.tsx`, envolver `<App />` con `<ThemeProvider>` (import de `./context/ThemeContext`).

- [ ] **Step 3: Reemplazar `frontend/src/index.css`**

```css
/* ---- Tokens ------------------------------------------------------------ */

:root,
:root[data-theme='dark'] {
  color-scheme: dark;
  --bg: #0f172a;
  --sidebar: #0b1222;
  --panel: #111c33;
  --panel-2: #0f1a30;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #60a5fa;
  --accent-soft: #1e293b;
  --accent-text: #93c5fd;
  --on-accent: #0b1222;
  --danger: #f87171;
  --success: #34d399;
  --warning: #fbbf24;
  --shadow: none;
  --series-download: #60a5fa;
  --series-upload: #f472b6;
  --series-clients: #34d399;
  --status-ok: #34d399;
  --status-late: #fbbf24;
  --status-down: #f87171;
  --status-off: #64748b;
}

:root[data-theme='light'] {
  color-scheme: light;
  --bg: #f1f5f9;
  --sidebar: #ffffff;
  --panel: #ffffff;
  --panel-2: #f8fafc;
  --border: #e2e8f0;
  --text: #0f172a;
  --muted: #64748b;
  --accent: #3b82f6;
  --accent-soft: #eff6ff;
  --accent-text: #1d4ed8;
  --on-accent: #ffffff;
  --danger: #dc2626;
  --success: #059669;
  --warning: #d97706;
  --shadow: 0 1px 2px rgb(15 23 42 / 0.06);
  --series-download: #3b82f6;
  --series-upload: #db2777;
  --series-clients: #059669;
  --status-ok: #10b981;
  --status-late: #f59e0b;
  --status-down: #ef4444;
  --status-off: #94a3b8;
}

:root {
  font: 15px/1.5 system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  color: var(--text);
  background: var(--bg);
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
}

a {
  color: var(--accent-text);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

h1,
h2,
h3 {
  margin: 0;
  line-height: 1.25;
}

.num {
  font-variant-numeric: tabular-nums;
}

.muted {
  color: var(--muted);
}

.error {
  color: var(--danger);
  margin: 0;
  font-size: 14px;
}

.success {
  color: var(--success);
  margin: 0;
  font-size: 14px;
}

.empty {
  color: var(--muted);
  padding: 24px 0;
  text-align: center;
  font-size: 14px;
}

/* ---- Icons --------------------------------------------------------------- */

.icon {
  width: 18px;
  height: 18px;
  flex: none;
}

/* ---- App layout ------------------------------------------------------------ */

.app {
  min-height: 100svh;
  display: flex;
}

.sidebar {
  width: 200px;
  flex: none;
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: sticky;
  top: 0;
  height: 100svh;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  padding: 4px 10px 16px;
}

.brand-mark {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-soft);
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  color: var(--muted);
  font-size: 14px;
}

.nav-link:hover {
  background: var(--accent-soft);
  color: var(--text);
  text-decoration: none;
}

.nav-link.active {
  background: var(--accent-soft);
  color: var(--accent-text);
  font-weight: 600;
}

.sidebar .spacer {
  flex: 1;
}

.content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  display: none;
}

main.page {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.page-header h1 {
  font-size: 22px;
  flex: 1;
  min-width: 0;
}

.page-header .header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.scrim {
  display: none;
}

@media (max-width: 800px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 20;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }

  .app.menu-open .sidebar {
    transform: none;
  }

  .app.menu-open .scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 10;
    background: rgb(0 0 0 / 0.45);
  }

  .topbar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    background: var(--sidebar);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 5;
  }

  main.page {
    padding: 16px;
  }
}

/* ---- Panels & cards ---------------------------------------------------------- */

.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 14px 16px;
  min-width: 0;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.stat-card .stat-label {
  font-size: 13px;
  color: var(--muted);
}

.stat-card .stat-value {
  font-size: 26px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  margin-top: 2px;
}

.stat-card .stat-sub {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 900px) {
  .grid-2 {
    grid-template-columns: minmax(0, 1fr);
  }
}

.legend {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--muted);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.legend-swatch {
  width: 12px;
  height: 3px;
  border-radius: 2px;
}

.legend-swatch.dashed {
  background: repeating-linear-gradient(90deg, currentColor 0 4px, transparent 4px 7px) !important;
}

/* ---- Controls ------------------------------------------------------------------ */

button {
  font: inherit;
  font-size: 14px;
  padding: 7px 14px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: var(--accent);
  color: var(--on-accent);
  font-weight: 600;
  cursor: pointer;
}

button:hover {
  filter: brightness(1.08);
}

button:disabled {
  opacity: 0.55;
  cursor: default;
  filter: none;
}

button.secondary,
button.ghost {
  background: var(--panel);
  color: var(--text);
  border-color: var(--border);
  font-weight: 500;
}

button.ghost {
  background: transparent;
}

button.danger {
  background: transparent;
  color: var(--danger);
  border-color: var(--border);
  font-weight: 500;
}

button.icon-button {
  padding: 6px;
  display: inline-flex;
  background: transparent;
  color: var(--text);
  border-color: var(--border);
}

.segmented {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--panel);
}

.segmented button {
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--muted);
  font-weight: 500;
  padding: 6px 12px;
}

.segmented button + button {
  border-left: 1px solid var(--border);
}

.segmented button[aria-pressed='true'] {
  background: var(--accent-soft);
  color: var(--accent-text);
  font-weight: 600;
}

input,
select {
  font: inherit;
  font-size: 14px;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
}

input:focus,
select:focus,
button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

input[type='checkbox'] {
  accent-color: var(--accent);
  width: 16px;
  height: 16px;
  padding: 0;
}

label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--muted);
}

label.checkbox {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  color: var(--text);
  font-size: 14px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 460px;
}

form h2 {
  font-size: 15px;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

form h2:first-child {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}

form.inline {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: flex-end;
  max-width: none;
}

.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* ---- Tables ------------------------------------------------------------------------ */

.table-wrap {
  overflow-x: auto;
  margin: 0 -16px;
  padding: 0 16px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th {
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

th.sortable {
  cursor: pointer;
  user-select: none;
}

th.sortable:hover {
  color: var(--text);
}

td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}

tbody tr:last-child td {
  border-bottom: none;
}

tr.clickable {
  cursor: pointer;
}

tr.clickable:hover td {
  background: var(--accent-soft);
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}

.status-dot.ok {
  background: var(--status-ok);
}

.status-dot.late {
  background: var(--status-late);
}

.status-dot.down {
  background: var(--status-down);
}

.status-dot.off {
  background: var(--status-off);
}

.bar {
  height: 6px;
  border-radius: 3px;
  background: var(--accent-soft);
  min-width: 60px;
}

.bar > span {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--series-download);
}

.rank {
  color: var(--muted);
  width: 24px;
}

@media (max-width: 600px) {
  .table-wrap table,
  .table-wrap thead,
  .table-wrap tbody,
  .table-wrap th,
  .table-wrap td,
  .table-wrap tr {
    display: block;
  }

  .table-wrap thead tr {
    display: none;
  }

  .table-wrap tr {
    border-bottom: 1px solid var(--border);
    padding: 8px 0;
  }

  .table-wrap td {
    border: none;
    padding: 3px 0;
  }

  .table-wrap td[data-label]::before {
    content: attr(data-label);
    display: inline-block;
    width: 130px;
    color: var(--muted);
    font-size: 12px;
  }
}

/* ---- Charts ---------------------------------------------------------------------------- */

.chart-tooltip {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  box-shadow: 0 4px 12px rgb(0 0 0 / 0.2);
}

.chart-tooltip .tooltip-time {
  color: var(--muted);
  margin-bottom: 4px;
}

/* ---- Login ------------------------------------------------------------------------------ */

.login-page {
  min-height: 100svh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  position: relative;
}

.login-page .corner {
  position: absolute;
  top: 16px;
  right: 16px;
}

.login-card {
  width: 100%;
  max-width: 360px;
  padding: 28px;
}

.login-card form {
  max-width: none;
}

.login-card .brand {
  padding: 0 0 8px;
  font-size: 18px;
}

.login-card button[type='submit'] {
  width: 100%;
  padding: 9px;
}
```

- [ ] **Step 4: Componentes base**

`frontend/src/components/Icon.tsx`:

```tsx
const PATHS = {
  dashboard: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  clients:
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  routers: 'M2 14h20v6H2zM6 17h.01M10 17h.01M12 14V9M8.5 6.5a5 5 0 0 1 7 0M5.5 3.5a9 9 0 0 1 13 0',
  settings: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  menu: 'M3 6h18M3 12h18M3 18h18',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42',
  moon: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z',
  back: 'M19 12H5M12 19l-7-7 7-7',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name }: { name: IconName }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
```

`frontend/src/components/ThemeToggle.tsx`:

```tsx
import { useTheme } from '../context/ThemeContext'
import { Icon } from './Icon'

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const label = theme === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro'
  return (
    <button type="button" className="icon-button" onClick={toggle} title={label} aria-label={label}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
    </button>
  )
}
```

`frontend/src/components/PageHeader.tsx`:

```tsx
import type { ReactNode } from 'react'
import { ThemeToggle } from './ThemeToggle'

export function PageHeader({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      <div className="header-actions">
        {children}
        <ThemeToggle />
      </div>
    </div>
  )
}
```

`frontend/src/components/Panel.tsx`:

```tsx
import type { ReactNode } from 'react'

interface PanelProps {
  title?: ReactNode
  action?: ReactNode
  className?: string
  children: ReactNode
}

export function Panel({ title, action, className, children }: PanelProps) {
  return (
    <section className={className ? `panel ${className}` : 'panel'}>
      {(title || action) && (
        <div className="panel-head">
          {title ? <h2 className="panel-title">{title}</h2> : <span />}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}
```

`frontend/src/components/StatCard.tsx`:

```tsx
import type { ReactNode } from 'react'

export function StatCard({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <section className="panel stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </section>
  )
}
```

`frontend/src/components/RangeSelector.tsx`:

```tsx
export const RANGES = [
  { hours: 24, label: '24h' },
  { hours: 24 * 7, label: '7d' },
  { hours: 24 * 30, label: '30d' },
  { hours: 24 * 90, label: '90d' },
]

export function RangeSelector({ value, onChange }: { value: number; onChange: (hours: number) => void }) {
  return (
    <div className="segmented" role="group" aria-label="Rango de tiempo">
      {RANGES.map((r) => (
        <button key={r.hours} type="button" aria-pressed={value === r.hours} onClick={() => onChange(r.hours)}>
          {r.label}
        </button>
      ))}
    </div>
  )
}
```

`frontend/src/components/StatusDot.tsx`:

```tsx
export type StatusTone = 'ok' | 'late' | 'down' | 'off'

export function StatusDot({ tone, label, showLabel = false }: { tone: StatusTone; label: string; showLabel?: boolean }) {
  return (
    <span className="status" title={label}>
      <span className={`status-dot ${tone}`} aria-hidden="true" />
      {showLabel ? label : <span className="visually-hidden">{label}</span>}
    </span>
  )
}
```

Agregar al CSS (sección Tables):

```css
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
```

- [ ] **Step 5: Utilidades**

`frontend/src/utils/routerStatus.ts`:

```ts
export type RouterStatus = 'ok' | 'late' | 'down'

export const ROUTER_STATUS_LABEL: Record<RouterStatus, string> = {
  ok: 'Al día',
  late: 'Sondeo atrasado',
  down: 'Sin respuesta',
}

/** ok < 2 polling intervals since the last successful poll, late < 6, else down. */
export function routerStatus(lastPolledAt: string | null, intervalSeconds: number, now: number = Date.now()): RouterStatus {
  if (!lastPolledAt) return 'down'
  const ageSeconds = (now - new Date(lastPolledAt).getTime()) / 1000
  if (ageSeconds < 2 * intervalSeconds) return 'ok'
  if (ageSeconds < 6 * intervalSeconds) return 'late'
  return 'down'
}
```

`frontend/src/utils/gaps.ts`:

```ts
/**
 * Insert a blank row (all series null) between two consecutive rows more than
 * maxGapSeconds apart, so the chart breaks the line where nothing was polled
 * instead of drawing a straight ramp across the gap. Rows must be sorted by t
 * (epoch ms).
 */
export function withGaps<T extends { t: number }>(rows: T[], maxGapSeconds: number, blank: (t: number) => T): T[] {
  const out: T[] = []
  for (const row of rows) {
    const previous = out.at(-1)
    if (previous && row.t - previous.t > maxGapSeconds * 1000) out.push(blank(previous.t + 1))
    out.push(row)
  }
  return out
}
```

Agregar a `frontend/src/utils/format.ts`:

```ts
/** Axis/tooltip time label: time of day for 24h, day + time for 7d, day for longer. */
export function formatChartTime(t: number, hours: number): string {
  const date = new Date(t)
  if (hours <= 24) return date.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
  if (hours <= 24 * 7)
    return date.toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  return date.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })
}
```

`frontend/src/hooks/useAutoRefresh.ts`:

```ts
import { useEffect, useRef } from 'react'

/** Call `callback` now, then every intervalMs while the tab is visible; refresh
 * as soon as it becomes visible again. Restarts when deps change. */
export function useAutoRefresh(callback: () => void, intervalMs: number, deps: unknown[]) {
  const saved = useRef(callback)
  saved.current = callback

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined
    const start = () => {
      stop()
      timer = setInterval(() => saved.current(), intervalMs)
    }
    const stop = () => {
      if (timer !== undefined) clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        saved.current()
        start()
      } else {
        stop()
      }
    }
    saved.current()
    if (document.visibilityState === 'visible') start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only on the caller's deps
  }, [intervalMs, ...deps])
}
```

- [ ] **Step 6: `frontend/src/components/TrafficChart.tsx`**

```tsx
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatChartTime } from '../utils/format'

export type ChartRow = { t: number } & Record<string, number | null>

export interface ChartSeries {
  key: string
  label: string
  /** A CSS color, usually var(--series-...) so it follows the theme. */
  color: string
  dashed?: boolean
  /** Area with a gradient below the line (default true unless dashed). */
  fill?: boolean
}

interface TrafficChartProps {
  data: ChartRow[]
  series: ChartSeries[]
  format: (value: number) => string
  hours: number
  height?: number
}

interface TooltipPayload {
  dataKey?: string | number
  value?: number | null
  color?: string
}

function ChartTooltip(props: {
  active?: boolean
  label?: number
  payload?: TooltipPayload[]
  series: ChartSeries[]
  format: (v: number) => string
}) {
  const { active, label, payload, series, format } = props
  if (!active || !payload?.length || label === undefined) return null
  return (
    <div className="chart-tooltip">
      <div className="tooltip-time">{new Date(label).toLocaleString('es-AR')}</div>
      {payload
        .filter((p) => p.value !== null && p.value !== undefined)
        .map((p) => {
          const s = series.find((x) => x.key === p.dataKey)
          return (
            <div key={String(p.dataKey)} className="legend-item">
              <span className="legend-swatch" style={{ background: s?.color }} />
              {s?.label}: <b className="num">{format(Number(p.value))}</b>
            </div>
          )
        })}
    </div>
  )
}

export function ChartLegend({ series }: { series: ChartSeries[] }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key} className="legend-item" style={{ color: s.color }}>
          <span className={s.dashed ? 'legend-swatch dashed' : 'legend-swatch'} style={{ background: s.color }} />
          <span style={{ color: 'var(--muted)' }}>{s.label}</span>
        </span>
      ))}
    </div>
  )
}

export function TrafficChart({ data, series, format, hours, height = 260 }: TrafficChartProps) {
  if (data.length === 0) return <p className="empty">Sin datos para el rango elegido.</p>
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <defs>
          {series.map((s) => (
            <linearGradient key={s.key} id={`fill-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={s.color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={s.color} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={(t: number) => formatChartTime(t, hours)}
          stroke="var(--muted)"
          tick={{ fill: 'var(--muted)', fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: 'var(--border)' }}
          minTickGap={40}
        />
        <YAxis
          tickFormatter={(v: number) => format(v)}
          width={82}
          stroke="var(--muted)"
          tick={{ fill: 'var(--muted)', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          content={(p) => (
            <ChartTooltip
              active={p.active}
              label={p.label as number | undefined}
              payload={p.payload as TooltipPayload[] | undefined}
              series={series}
              format={format}
            />
          )}
        />
        {series.map((s) =>
          s.dashed || s.fill === false ? (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={1.5}
              strokeDasharray={s.dashed ? '5 4' : undefined}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ) : (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              fill={`url(#fill-${s.key})`}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ),
        )}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
```

- [ ] **Step 7: Reemplazar `frontend/src/components/Layout.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { Icon } from './Icon'
import type { IconName } from './Icon'

const NAV: { to: string; label: string; icon: IconName }[] = [
  { to: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
  { to: '/clients', label: 'Clientes', icon: 'clients' },
  { to: '/routers', label: 'Routers', icon: 'routers' },
  { to: '/settings', label: 'Configuración', icon: 'settings' },
]

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true" />
      Monitor PPPoE
    </div>
  )
}

export function Layout() {
  const { logout } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  // Close the mobile menu after navigating.
  useEffect(() => setMenuOpen(false), [location.pathname])

  return (
    <div className={menuOpen ? 'app menu-open' : 'app'}>
      <aside className="sidebar">
        <Brand />
        <nav>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className="nav-link">
              <Icon name={item.icon} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        <button type="button" className="nav-link ghost" onClick={logout}>
          <Icon name="logout" />
          Salir
        </button>
      </aside>
      <div className="scrim" onClick={() => setMenuOpen(false)} />
      <div className="content">
        <header className="topbar">
          <button type="button" className="icon-button" aria-label="Abrir menú" onClick={() => setMenuOpen(true)}>
            <Icon name="menu" />
          </button>
          <Brand />
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
```

Agregar al CSS (sección App layout), para que el botón Salir se vea como los enlaces:

```css
.sidebar button.nav-link {
  width: 100%;
  border: none;
  background: transparent;
  font-weight: 500;
  text-align: left;
}

.topbar .brand {
  padding: 0;
}
```

- [ ] **Step 8: Build**

Run: `remote 'cd frontend && npm run build 2>&1 | grep -E "error|built"'`
Expected: `✓ built` y ninguna línea `error TS`. Las páginas todavía usan clases viejas: se ven sin estilo en algunos lugares, pero compilan. Si Recharts 3 rechaza el tipo de `content` del `Tooltip` o las props de `ChartTooltip`, ajustar los tipos a lo que pide el compilador sin cambiar el comportamiento, y dejarlo anotado en el registro.

- [ ] **Step 9: Commit**

```bash
git add frontend/index.html frontend/src/theme.ts frontend/src/context/ThemeContext.tsx frontend/src/main.tsx frontend/src/index.css frontend/src/components frontend/src/utils frontend/src/hooks
git commit -m "feat(frontend): tema claro/oscuro, barra lateral y componentes base del nuevo estilo"
```

---

### Tarea 7: Dashboard con los 5 paneles

**Files:**
- Replace: `frontend/src/pages/Dashboard.tsx`

**Interfaces:**
- Consumes: `GET /dashboard/summary` (Tarea 5), `GET /dashboard/history` (Tarea 4), `GET /clients?sort_by=download&dir=desc&page_size=10`, y los componentes y utilidades de la Tarea 6.

- [ ] **Step 1: Reemplazar `frontend/src/pages/Dashboard.tsx`**

```tsx
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { RangeSelector, RANGES } from '../components/RangeSelector'
import { StatCard } from '../components/StatCard'
import { StatusDot } from '../components/StatusDot'
import { ChartLegend, TrafficChart } from '../components/TrafficChart'
import type { ChartRow, ChartSeries } from '../components/TrafficChart'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import { formatBps, formatBytes, formatSince } from '../utils/format'
import { withGaps } from '../utils/gaps'
import { ROUTER_STATUS_LABEL, routerStatus } from '../utils/routerStatus'

interface RouterSummary {
  router_id: number
  router_name: string
  clients_connected: number
  current_rx_bps: number
  current_tx_bps: number
  last_polled_at: string | null
}

interface DashboardSummary {
  total_clients_connected: number
  current_rx_bps: number
  current_tx_bps: number
  by_router: RouterSummary[]
  polling_interval_seconds: number
  clients_seen_this_period: number
}

interface HistoryPoint {
  t: string
  rx_bps: number
  tx_bps: number
  clients_connected: number | null
}

interface DashboardHistory {
  bucket_seconds: number
  points: HistoryPoint[]
}

interface TopClient {
  id: number
  username: string
  router_name: string
  accumulated_tx_bytes: number
}

const REFRESH_MS = 60_000

// PPPoE-server interface: TX = what the router sends the clients (their download).
const TRAFFIC_SERIES: ChartSeries[] = [
  { key: 'download', label: 'Descarga', color: 'var(--series-download)' },
  { key: 'upload', label: 'Subida', color: 'var(--series-upload)' },
]
const CLIENTS_SERIES: ChartSeries[] = [{ key: 'clients', label: 'Conectados', color: 'var(--series-clients)' }]

const formatCount = (v: number) => Math.round(v).toLocaleString('es-AR')

export function Dashboard() {
  const [hours, setHours] = useState(24)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [history, setHistory] = useState<DashboardHistory | null>(null)
  const [top, setTop] = useState<TopClient[]>([])
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useAutoRefresh(
    () => {
      apiFetch<DashboardSummary>('/dashboard/summary')
        .then((s) => {
          setSummary(s)
          setError(null)
        })
        .catch(() => setError('No se pudo cargar el resumen'))
      apiFetch<DashboardHistory>(`/dashboard/history?hours=${hours}`)
        .then(setHistory)
        .catch(() => setError('No se pudo cargar la historia de tráfico'))
      apiFetch<{ items: TopClient[] }>('/clients?sort_by=download&dir=desc&page_size=10')
        .then((page) => setTop(page.items))
        .catch(() => setError('No se pudo cargar el top de consumo'))
    },
    REFRESH_MS,
    [hours],
  )

  const rangeLabel = RANGES.find((r) => r.hours === hours)?.label ?? ''
  const maxGap = (history?.bucket_seconds ?? 300) * 2.5
  const points: ChartRow[] = (history?.points ?? []).map((p) => ({
    t: Date.parse(p.t),
    download: p.tx_bps,
    upload: p.rx_bps,
    clients: p.clients_connected,
  }))
  const trafficRows = withGaps(points, maxGap, (t) => ({ t, download: null, upload: null, clients: null }))
  const clientRows = withGaps(
    points.filter((p) => p.clients !== null),
    maxGap,
    (t) => ({ t, clients: null }),
  )
  const peakDownload = Math.max(0, ...points.map((p) => p.download ?? 0))
  const peakUpload = Math.max(0, ...points.map((p) => p.upload ?? 0))

  const interval = summary?.polling_interval_seconds ?? 300
  const statuses = (summary?.by_router ?? []).map((r) => routerStatus(r.last_polled_at, interval))
  const okCount = statuses.filter((s) => s === 'ok').length
  const routerCount = statuses.length
  const topMax = Math.max(1, ...top.map((c) => c.accumulated_tx_bytes))

  return (
    <>
      <PageHeader title="Dashboard">
        <RangeSelector value={hours} onChange={setHours} />
      </PageHeader>
      {error && <p className="error">{error}</p>}

      <div className="stat-grid">
        <StatCard
          label="Conectados"
          value={summary ? formatCount(summary.total_clients_connected) : '—'}
          sub={summary ? `de ${formatCount(summary.clients_seen_this_period)} vistos este mes` : undefined}
        />
        <StatCard
          label="Descarga ahora"
          value={summary ? formatBps(summary.current_tx_bps) : '—'}
          sub={points.length ? `pico ${rangeLabel}: ${formatBps(peakDownload)}` : undefined}
        />
        <StatCard
          label="Subida ahora"
          value={summary ? formatBps(summary.current_rx_bps) : '—'}
          sub={points.length ? `pico ${rangeLabel}: ${formatBps(peakUpload)}` : undefined}
        />
        <StatCard
          label="Routers"
          value={summary ? `${okCount} / ${routerCount}` : '—'}
          sub={summary ? (okCount === routerCount ? 'todos al día' : `${routerCount - okCount} con problemas`) : undefined}
        />
      </div>

      <Panel title="Tráfico total" action={<ChartLegend series={TRAFFIC_SERIES} />}>
        <TrafficChart data={trafficRows} series={TRAFFIC_SERIES} format={formatBps} hours={hours} height={280} />
      </Panel>

      <div className="grid-2">
        <Panel title="Clientes conectados">
          <TrafficChart data={clientRows} series={CLIENTS_SERIES} format={formatCount} hours={hours} height={220} />
        </Panel>

        <Panel title="Por router">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Router</th>
                  <th>Conectados</th>
                  <th>Descarga</th>
                  <th>Último sondeo</th>
                </tr>
              </thead>
              <tbody>
                {(summary?.by_router ?? []).map((r, i) => (
                  <tr
                    key={r.router_id}
                    className="clickable"
                    onClick={() => navigate(`/clients?router=${r.router_id}`)}
                  >
                    <td data-label="Router">
                      <StatusDot tone={statuses[i]} label={ROUTER_STATUS_LABEL[statuses[i]]} /> {r.router_name}
                    </td>
                    <td data-label="Conectados">{formatCount(r.clients_connected)}</td>
                    <td data-label="Descarga">{formatBps(r.current_tx_bps)}</td>
                    <td data-label="Último sondeo" className={statuses[i] === 'ok' ? 'muted' : undefined}>
                      {formatSince(r.last_polled_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {summary && summary.by_router.length === 0 && <p className="empty">No hay routers habilitados.</p>}
        </Panel>
      </div>

      <Panel title="Top consumidores del mes" action={<Link to="/clients">Ver todos</Link>}>
        <div className="table-wrap">
          <table>
            <tbody>
              {top.map((c, i) => (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/clients/${c.id}`)}>
                  <td className="rank num">{i + 1}</td>
                  <td data-label="Cliente">
                    <Link to={`/clients/${c.id}`} onClick={(e) => e.stopPropagation()}>
                      {c.username}
                    </Link>{' '}
                    <span className="muted">· {c.router_name}</span>
                  </td>
                  <td style={{ width: '40%' }}>
                    <div className="bar">
                      <span style={{ width: `${(c.accumulated_tx_bytes / topMax) * 100}%` }} />
                    </div>
                  </td>
                  <td data-label="Descarga" className="num" style={{ textAlign: 'right' }}>
                    {formatBytes(c.accumulated_tx_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {top.length === 0 && <p className="empty">Todavía no hay consumo en este período.</p>}
      </Panel>
    </>
  )
}
```

- [ ] **Step 2: Build**

Run: `remote 'cd frontend && npm run build 2>&1 | grep -E "error|built"'`
Expected: `✓ built` y ninguna línea `error TS`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
git commit -m "feat(frontend): dashboard con indicadores, tráfico, conectados, routers y top de consumo"
```

---

### Tarea 8: Clientes y detalle de cliente

**Files:**
- Modify: `frontend/src/pages/Clients.tsx` (solo el `return` y los imports)
- Replace: `frontend/src/pages/ClientDetail.tsx`

**Interfaces:**
- Consumes: `PageHeader`, `Panel`, `StatusDot`, `StatCard`, `RangeSelector`, `TrafficChart`, `ChartLegend`, `withGaps` (Tarea 6), y `polling_interval_seconds` de `/dashboard/summary` (Tarea 5).

- [ ] **Step 1: `Clients.tsx`.** Imports nuevos:

```tsx
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { StatusDot } from '../components/StatusDot'
```

Agregar `const navigate = useNavigate()` junto a los demás hooks. Reemplazar el `return (...)` completo por:

```tsx
  return (
    <>
      <PageHeader title="Clientes" />
      <Panel>
        <div className="controls" style={{ marginBottom: 12 }}>
          <input
            type="search"
            placeholder="Buscar usuario…"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <select value={routerId} onChange={(e) => update({ router: e.target.value })}>
            <option value="">Todos los routers</option>
            {routers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => update({ active: e.target.checked ? '1' : null })}
            />
            Solo conectados
          </label>
        </div>

        {error && <p className="error">{error}</p>}

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} className="sortable" onClick={() => toggleSort(col.key)}>
                    {col.label}
                    {sort === col.key ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/clients/${c.id}`)}>
                  <td data-label="Usuario">
                    <Link to={`/clients/${c.id}`} onClick={(e) => e.stopPropagation()}>
                      {c.username}
                    </Link>
                  </td>
                  <td data-label="Router">{c.router_name}</td>
                  <td data-label="Estado">
                    <StatusDot
                      tone={c.is_active ? 'ok' : 'off'}
                      label={c.is_active ? 'Conectado' : 'Desconectado'}
                      showLabel
                    />
                  </td>
                  <td data-label="Tráfico actual">
                    {formatBps(c.current_tx_bps)} ↓ / {formatBps(c.current_rx_bps)} ↑
                  </td>
                  <td data-label="Descarga acumulada">{formatBytes(c.accumulated_tx_bytes)}</td>
                  <td data-label="Subida acumulada">{formatBytes(c.accumulated_rx_bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && data.items.length === 0 && <p className="empty">No hay clientes que coincidan con el filtro.</p>}

        <div className="controls" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="secondary"
            disabled={page <= 1}
            onClick={() => update({ page: String(page - 1) })}
          >
            ‹ Anterior
          </button>
          <span className="muted">
            Página {Math.min(page, pageCount)} de {pageCount}
          </span>
          <button
            type="button"
            className="secondary"
            disabled={page >= pageCount}
            onClick={() => update({ page: String(page + 1) })}
          >
            Siguiente ›
          </button>
          <span className="muted">
            Mostrando {first.toLocaleString('es-AR')}–{last.toLocaleString('es-AR')} de{' '}
            {total.toLocaleString('es-AR')}
          </span>
          <label className="checkbox">
            Por página
            <select value={size} onChange={(e) => update({ size: e.target.value })}>
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Panel>
    </>
  )
```

- [ ] **Step 2: Reemplazar `ClientDetail.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { RangeSelector } from '../components/RangeSelector'
import { StatCard } from '../components/StatCard'
import { StatusDot } from '../components/StatusDot'
import { ChartLegend, TrafficChart } from '../components/TrafficChart'
import type { ChartRow, ChartSeries } from '../components/TrafficChart'
import { formatBps, formatBytes } from '../utils/format'
import { withGaps } from '../utils/gaps'

// On a PPPoE-server interface TX is what the router sends the client (its
// download) and RX is its upload.
interface HistoryPoint {
  sampled_at: string
  rx_bytes_delta: number
  tx_bytes_delta: number
  rx_bps: number
  tx_bps: number
  // Only on hourly points (30/90-day ranges): the fastest 5-minute sample.
  peak_rx_bps: number | null
  peak_tx_bps: number | null
}

interface ClientRow {
  id: number
  router_name: string
  username: string
  is_active: boolean
  current_rx_bps: number
  current_tx_bps: number
  accumulated_rx_bytes: number
  accumulated_tx_bytes: number
}

const HOUR_SECONDS = 3600

const SPEED_SERIES: ChartSeries[] = [
  { key: 'download', label: 'Descarga', color: 'var(--series-download)' },
  { key: 'upload', label: 'Subida', color: 'var(--series-upload)' },
]
const PEAK_SERIES: ChartSeries[] = [
  { key: 'peakDownload', label: 'Descarga (pico)', color: 'var(--series-download)', dashed: true },
  { key: 'peakUpload', label: 'Subida (pico)', color: 'var(--series-upload)', dashed: true },
]

// Running total since the start of the selected range: its slope is the
// speed chart. Not broken at gaps: a flat total across a gap is correct.
function toCumulative(points: HistoryPoint[]): ChartRow[] {
  const rows: ChartRow[] = []
  let download = 0
  let upload = 0
  for (const p of points) {
    download += p.tx_bytes_delta
    upload += p.rx_bytes_delta
    rows.push({ t: Date.parse(p.sampled_at), download, upload })
  }
  return rows
}

export function ClientDetail() {
  const { id } = useParams<{ id: string }>()
  const [hours, setHours] = useState(24)
  const [points, setPoints] = useState<HistoryPoint[]>([])
  const [client, setClient] = useState<ClientRow | null>(null)
  const [pollSeconds, setPollSeconds] = useState(300)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    apiFetch<HistoryPoint[]>(`/clients/${id}/history?hours=${hours}`)
      .then((p) => {
        setPoints(p)
        setError(null)
      })
      .catch(() => setError('No se pudo cargar el historial de tráfico'))
  }, [id, hours])

  useEffect(() => {
    if (!id) return
    apiFetch<ClientRow>(`/clients/${id}`)
      .then(setClient)
      .catch(() => {
        /* header falls back to showing the client id */
      })
  }, [id])

  useEffect(() => {
    apiFetch<{ polling_interval_seconds: number }>('/dashboard/summary')
      .then((s) => setPollSeconds(s.polling_interval_seconds))
      .catch(() => {
        /* keep the 5-minute default for gap detection */
      })
  }, [])

  // Long ranges come as one point per hour: the hour's average speed plus
  // its peak, drawn dashed.
  const hourly = points.some((p) => p.peak_tx_bps !== null)
  const maxGap = 2.5 * (hourly ? HOUR_SECONDS : pollSeconds)
  const speedRows = withGaps(
    points.map((p) => ({
      t: Date.parse(p.sampled_at),
      download: p.tx_bps,
      upload: p.rx_bps,
      peakDownload: p.peak_tx_bps,
      peakUpload: p.peak_rx_bps,
    })),
    maxGap,
    (t) => ({ t, download: null, upload: null, peakDownload: null, peakUpload: null }),
  )
  const speedSeries = hourly ? [...SPEED_SERIES, ...PEAK_SERIES] : SPEED_SERIES

  return (
    <>
      <p style={{ margin: 0 }}>
        <Link to="/clients" className="legend-item">
          <Icon name="back" /> Clientes
        </Link>
      </p>
      <PageHeader title={client ? client.username : `Cliente #${id}`}>
        {client && (
          <span className="muted">
            <StatusDot
              tone={client.is_active ? 'ok' : 'off'}
              label={client.is_active ? 'Conectado' : 'Desconectado'}
              showLabel
            />{' '}
            · {client.router_name}
          </span>
        )}
      </PageHeader>

      {client && (
        <div className="stat-grid">
          <StatCard
            label="Velocidad actual"
            value={client.is_active ? `${formatBps(client.current_tx_bps)} ↓` : 'Desconectado'}
            sub={client.is_active ? `${formatBps(client.current_rx_bps)} ↑ subida` : undefined}
          />
          <StatCard
            label="Acumulado del mes"
            value={`${formatBytes(client.accumulated_tx_bytes)} ↓`}
            sub={`${formatBytes(client.accumulated_rx_bytes)} ↑ subida`}
          />
        </div>
      )}

      <div className="controls">
        <RangeSelector value={hours} onChange={setHours} />
      </div>
      {error && <p className="error">{error}</p>}

      <Panel
        title={hourly ? 'Velocidad (promedio y pico de cada hora)' : 'Velocidad (promedio de cada sondeo)'}
        action={<ChartLegend series={speedSeries} />}
      >
        <TrafficChart data={error ? [] : speedRows} series={speedSeries} format={formatBps} hours={hours} />
      </Panel>

      <Panel title="Consumo acumulado en el período" action={<ChartLegend series={SPEED_SERIES} />}>
        <TrafficChart
          data={error ? [] : toCumulative(points)}
          series={SPEED_SERIES}
          format={formatBytes}
          hours={hours}
        />
      </Panel>
    </>
  )
}
```

- [ ] **Step 3: Build**

Run: `remote 'cd frontend && npm run build 2>&1 | grep -E "error|built"'`
Expected: `✓ built` y ninguna línea `error TS`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Clients.tsx frontend/src/pages/ClientDetail.tsx
git commit -m "feat(frontend): clientes y detalle de cliente con el nuevo estilo y gráficos con huecos"
```

---

### Tarea 9: Login, Routers y Configuración

**Files:**
- Modify: `frontend/src/pages/Login.tsx` (`return`)
- Modify: `frontend/src/pages/RoutersAdmin.tsx` (`return` de la página y `TestResult` sin cambios)
- Modify: `frontend/src/pages/SettingsAdmin.tsx` (`return`)

**Interfaces:**
- Consumes: `PageHeader`, `Panel`, `StatusDot`, `ThemeToggle`, `routerStatus` y `ROUTER_STATUS_LABEL` (Tarea 6), y `polling_interval_seconds` (Tarea 5).

- [ ] **Step 1: `Login.tsx`.** Importar `ThemeToggle` de `../components/ThemeToggle` y reemplazar el `return (...)`:

```tsx
  return (
    <div className="login-page">
      <div className="corner">
        <ThemeToggle />
      </div>
      <section className="panel login-card">
        <form onSubmit={handleSubmit}>
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            Monitor PPPoE
          </div>
          <label>
            Usuario
            <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            Contraseña
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">Ingresar</button>
        </form>
      </section>
    </div>
  )
```

- [ ] **Step 2: `RoutersAdmin.tsx`.**

Imports:

```tsx
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { StatusDot } from '../components/StatusDot'
import { ROUTER_STATUS_LABEL, routerStatus } from '../utils/routerStatus'
```

En el componente de página, junto a los otros `useState`:

```tsx
  const [pollSeconds, setPollSeconds] = useState(300)
  useEffect(() => {
    apiFetch<{ polling_interval_seconds: number }>('/dashboard/summary')
      .then((s) => setPollSeconds(s.polling_interval_seconds))
      .catch(() => {
        /* keep the default for the status dot */
      })
  }, [])
```

(`useEffect` y `useState` ya se importan en el archivo; confirmarlo y agregarlos si faltan.)

Reemplazar el `return (...)` de la página:

```tsx
  return (
    <>
      <PageHeader title="Routers">
        {open === null && (
          <button type="button" onClick={() => setOpen(NEW_ROUTER)}>
            + Agregar router
          </button>
        )}
      </PageHeader>

      {open && (
        <Panel>
          <RouterForm
            // Remount on switching routers so the form starts from fresh state.
            key={open.id ?? 'new'}
            initial={open}
            onSaved={() => {
              setOpen(null)
              reload()
            }}
            onCancel={() => setOpen(null)}
          />
        </Panel>
      )}

      {error && <p className="error">{error}</p>}

      <Panel>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Host</th>
                <th>Usuario API</th>
                <th>Último sondeo</th>
                <th>Habilitado</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {routers.map((r) => {
                const status = routerStatus(r.last_polled_at, pollSeconds)
                return (
                  <tr key={r.id}>
                    <td data-label="Nombre">
                      <StatusDot
                        tone={r.enabled ? status : 'off'}
                        label={r.enabled ? ROUTER_STATUS_LABEL[status] : 'Deshabilitado'}
                      />{' '}
                      {r.name}
                    </td>
                    <td data-label="Host" className="num">
                      {r.host}:{r.port}
                    </td>
                    <td data-label="Usuario API">{r.api_username}</td>
                    <td data-label="Último sondeo">{formatSince(r.last_polled_at)}</td>
                    <td data-label="Habilitado">
                      <input type="checkbox" checked={r.enabled} onChange={() => toggleEnabled(r)} />
                    </td>
                    <td data-label="Acciones">
                      <div className="actions">
                        <button type="button" className="secondary" onClick={() => setOpen(toFormState(r))}>
                          Editar
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          disabled={rowTests[r.id] === 'pending'}
                          onClick={() => testRow(r)}
                        >
                          Probar
                        </button>
                        <button type="button" className="danger" onClick={() => handleDelete(r)}>
                          Eliminar
                        </button>
                      </div>
                      <TestResult state={rowTests[r.id] ?? null} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {routers.length === 0 && <p className="empty">Todavía no hay routers cargados.</p>}
      </Panel>
    </>
  )
```

Si el tipo de fila de `routers` no se llama `r.enabled`, `r.port` o `r.last_polled_at`, usar los nombres que ya usa la tabla actual; son los mismos que se ven en el código existente.

- [ ] **Step 3: `SettingsAdmin.tsx`.** Importar `PageHeader` y `Panel`. En el `return (...)`:
  - Cambiar `<div className="settings-admin-page">` por `<>` y el `</div>` final por `</>`.
  - Reemplazar `<h1>Configuración</h1>` por `<PageHeader title="Configuración" />`.
  - Envolver el primer `<form onSubmit={saveSettings}>…</form>` en `<Panel>…</Panel>`. Los `<h2>` internos pasan a ser separadores de sección gracias al CSS `form h2`.
  - Envolver el bloque del umbral (desde `<form className="inline" onSubmit={addThreshold}>` hasta el `</table>` de umbrales, incluido el `thresholdError`) en `<Panel title="Umbrales de alerta">…</Panel>`.
  - Borrar el `<h2 style={{ width: '100%' }}>Umbral de alerta</h2>` interno, porque el título ahora lo pone el panel.
  - Envolver `<table>…</table>` en `<div className="table-wrap">…</div>`.
  - En la fila de umbrales, poner el botón `Eliminar` con `className="danger"`.

- [ ] **Step 4: Build**

Run: `remote 'cd frontend && npm run build 2>&1 | grep -E "error|built"'`
Expected: `✓ built` y ninguna línea `error TS`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Login.tsx frontend/src/pages/RoutersAdmin.tsx frontend/src/pages/SettingsAdmin.tsx
git commit -m "feat(frontend): login, routers y configuración con el nuevo estilo"
```

---

### Tarea 10: Despliegue en la VM y revisión visual

**Files:** ninguno, salvo que la revisión encuentre algo.

- [ ] **Step 1: Suite y build**

Run: `remote 'cd backend && .venv/bin/pytest -q 2>&1 | tail -1' && remote 'cd frontend && npm run build 2>&1 | grep -E "error TS|built"'`
Expected: todo pasa y el build sale bien.

- [ ] **Step 2: Desplegar sobre los datos reales**

Run: `remote 'docker compose run --rm -T backup now >/dev/null 2>&1; docker compose up -d --build 2>&1 | tail -2; sleep 70; docker compose ps --format "{{.Service}} {{.Status}}"; docker compose logs backend --since 3m | grep -iE "alembic|error|Traceback" | tail -5'`
Expected:
- backup previo hecho;
- todos los servicios `(healthy)` y `backup` en `Up`;
- el log de alembic muestra `Running upgrade 5d2b7e9c41a0 -> 7c1e4a9b2d30`, sin errores.

- [ ] **Step 3: Verificar la API real**

Run: `remote '. ./.env; TOKEN=$(docker compose exec -T -e PPPOE_ADMIN_PASSWORD=ui-check-pass-123 backend sh -c "python -m app.cli create-admin zz-ui-check >/dev/null; python -c \"from app.core.security import create_access_token; print(create_access_token(\\\"zz-ui-check\\\"))\""); for h in 24 168 720 2160; do curl -s -H "Authorization: Bearer $TOKEN" "http://localhost/api/dashboard/history?hours=$h" | python3 -c "import json,sys; d=json.load(sys.stdin); p=d[\"points\"]; print($h, d[\"bucket_seconds\"], len(p), p[-1] if p else None)"; done; curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/dashboard/summary | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[\"polling_interval_seconds\"], d[\"clients_seen_this_period\"], d[\"total_clients_connected\"])"; docker compose exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "delete from users where username = $$zz-ui-check$$"'`
Expected: cada rango devuelve su ancho de intervalo y entre ~250 y ~360 puntos cuando hay datos para todo el rango (menos si la historia es más corta), con conectados en el último punto. El resumen trae el intervalo, clientes vistos ≥ conectados y conectados > 0.

- [ ] **Step 4: Revisión visual**

Usar `anthropic-skills:chrome-browser` si está disponible. Si no, pedirle al usuario que abra `http://<ip-de-pruebas>/`. Revisar en tema oscuro y claro:
1. Login, con el selector de tema.
2. Dashboard: los 5 paneles con datos, el cambio de rango 24h/7d/30d/90d y que los huecos se vean cortados, no como rampas.
3. Clientes: filtro por router llegando desde el dashboard, búsqueda y paginación.
4. Detalle de un cliente: 24h y 30d (líneas de pico punteadas).
5. Routers y Configuración.
6. Ancho de 375 px: menú ☰, tablas como tarjetas y sin scroll horizontal (Review Focus 5).
7. Recargar con el tema claro elegido: no tiene que aparecer un destello oscuro (Review Focus 3).

Anotar en el registro cada problema encontrado y corregirlo con un commit `fix(frontend): …`.

- [ ] **Step 5: Revisión final de la rama y cierre**, según el flujo de ejecución elegido.
