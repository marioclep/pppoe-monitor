# Sistema de Monitoreo de Consumo PPPoE — Diseño

Fecha: 2026-09-22
Estado: Aprobado por el usuario, pendiente de plan de implementación

> **Status (2026-09-23):** implementación completa (Tasks 1-29). Ver
> `README.md` para despliegue y desarrollo. Pendiente: verificar contra un
> router Mikrotik real la convención de nombres de interfaz PPPoE asumida en
> `backend/app/services/mikrotik_client.py` (`_match_interface_name`), hasta
> ahora solo probada con respuestas simuladas.

## Contexto y objetivo

El usuario administra varios routers Mikrotik que validan clientes por PPPoE.
Necesita un sistema web que:

- Permita cargar routers Mikrotik a monitorear vía su API.
- Cada 5 minutos (configurable) consulte cada router, obtenga cantidad de
  clientes PPPoE conectados, tráfico actual y acumulado.
- Muestre gráficos de consumo por cliente y permita ordenar por tráfico
  acumulado, para detectar "heavy-users".
- Resetee el acumulado periódicamente (configurable, por defecto el día 1 de
  cada mes), sin perder continuidad ante reconexiones de sesión PPPoE.
- Muestre en algún lugar visible el total global de clientes conectados.
- Tenga autenticación simple para acceder a la interfaz web.

### Por qué no hacerlo directamente en Mikrotik
1. Los gráficos de interfaces están deshabilitados en los routers para no
   consumir recursos.
2. RouterOS no lleva "accounting": si una sesión PPPoE se reinicia, se pierde
   el acumulado. Este sistema debe resolver eso llevando su propio registro
   persistente fuera del router.

### Escala esperada
5-20 routers Mikrotik, 500-3000 clientes PPPoE en total. Suficiente para
justificar PostgreSQL desde el inicio, pero no tanto como para requerir
particionado/TimescaleDB ni una arquitectura distribuida (worker+broker).

## Arquitectura

Monolito de 3 contenedores Docker Compose:

```
┌─────────────┐      REST API (HTTPS)      ┌──────────────────┐
│  Mikrotik 1  │◄────────────────────────────│                  │
│  Mikrotik 2  │◄────────────────────────────│  Backend FastAPI │
│  ...         │◄────────────────────────────│  + APScheduler   │──► PostgreSQL
└─────────────┘                              │  (polling c/5min)│
                                              └────────┬─────────┘
                                                        │ REST API (JWT)
                                              ┌─────────▼─────────┐
                                              │ Frontend React/Vite│
                                              │ (servido por Nginx)│
                                              └────────────────────┘
```

- **`db`**: PostgreSQL 16 con volumen persistente.
- **`backend`**: FastAPI. Expone la API REST y corre internamente
  APScheduler para el polling, el reseteo periódico y la purga de datos
  históricos. Un solo proceso concentra toda la lógica de negocio, evitando
  doble-poll y simplificando el despliegue.
- **`frontend`**: build de React servido por Nginx, que hace proxy de
  `/api` al backend.

Se eligió este enfoque monolítico (sobre una alternativa con
worker/broker tipo Celery+Redis) porque la escala del proyecto no lo
justifica: es complejidad operativa innecesaria para 20 routers con
polling cada 5 minutos.

## Modelo de datos (PostgreSQL)

**`users`** — administradores del sistema (login web)
- `id, username, password_hash, created_at`

**`routers`** — routers Mikrotik a monitorear
- `id, name, host, port, api_username, api_password_encrypted, use_tls,
  verify_tls, enabled, created_at`

**`pppoe_clients`** — identifica a cada cliente por su **username PPPoE**,
no por IP ni sesión (la IP/sesión puede cambiar en cada reconexión, el
username no)
- `id, router_id, username, first_seen, last_seen, is_active`
- único: `(router_id, username)`

**`session_state`** — último estado conocido de cada cliente, usado para
calcular deltas correctamente entre polls
- `client_id, last_uptime_seconds, last_rx_bytes, last_tx_bytes,
  last_poll_at`

**`traffic_samples`** — una fila cada 5 min por cliente, retención 3 meses,
usada para los gráficos históricos
- `id, client_id, sampled_at, rx_bytes_delta, tx_bytes_delta, rx_bps,
  tx_bps, is_online`

**`accumulation_periods`** — el "acumulado" que se resetea periódicamente
- `id, client_id, period_start, period_end (null=activo), rx_bytes_total,
  tx_bytes_total`

**`alert_thresholds`** — umbrales de alerta (global por defecto + override
opcional por cliente)
- `id, client_id (nullable=global), bytes_threshold, notify_channel`

**`alert_events`** — historial de alertas disparadas, evita reenviar el
mismo aviso repetidamente dentro de un mismo período
- `id, client_id, threshold_id, triggered_at, accumulated_bytes_at_trigger`

**`settings`** — configuración global: día de reseteo mensual (default
día 1), intervalo de polling (default 5 min), retención de samples
(default 3 meses), configuración de canal de notificación (SMTP /
Telegram)

## Lógica de cálculo de tráfico (evitar pérdida de acumulado)

En cada poll, para cada sesión PPPoE activa en el router
(`GET /rest/ppp/active`), se compara el `uptime` actual contra el último
guardado en `session_state`:

- Si `uptime` actual **≥** anterior → misma sesión continúa. Delta =
  `rx_bytes_actual - rx_bytes_anterior` (y análogo para tx).
- Si `uptime` actual **<** anterior → la sesión se reinició (el contador
  del router volvió a 0). Delta = `rx_bytes_actual` directo, ya que
  representa todo lo transmitido desde que arrancó la nueva sesión.

El delta calculado se aplica en dos lugares:
1. Se escribe como fila nueva en `traffic_samples` (para el gráfico
   histórico).
2. Se suma a `accumulation_periods.rx_bytes_total` /
   `tx_bytes_total` del período activo del cliente (el acumulado que
   luego se resetea).

Así el acumulado nunca se pierde aunque la sesión PPPoE se caiga y
reconecte, resolviendo la limitación #2 del enunciado.

Clientes que estaban activos y dejan de aparecer en `/ppp/active` se
marcan `is_active=false`, conservando su acumulado intacto.

## Jobs programados (APScheduler, dentro del backend)

1. **Polling** (cada 5 min, configurable): para cada router `enabled=true`,
   en paralelo (asyncio, con límite de concurrencia), llama a la REST API
   de RouterOS 7 y aplica la lógica de deltas descrita arriba. Si un router
   falla (timeout, error de auth, etc.), se loguea el error y se continúa
   con el resto — un router caído no debe frenar el polling de los demás.
   Tras cada poll, evalúa `alert_thresholds` y dispara notificaciones si
   corresponde (sin duplicar alertas ya emitidas en el período).
2. **Reseteo periódico** (cron interno, default 1º de cada mes 00:00,
   configurable): cierra el `accumulation_period` activo de cada cliente
   (`period_end = now`) y abre uno nuevo en cero. Los `traffic_samples`
   históricos no se borran en este paso.
3. **Purga** (diario): borra `traffic_samples` con más de la retención
   configurada (default 3 meses).

## API REST (backend, protegida con JWT salvo `/auth/login`)

- `POST /auth/login`
- `GET/POST/PUT/DELETE /routers` — CRUD de routers a monitorear
- `GET /clients` — lista de clientes con filtros (router, activo/inactivo)
  y orden por acumulado
- `GET /clients/{id}` — detalle + serie histórica para gráfico
- `GET /dashboard/summary` — total de clientes conectados (global y por
  router), tráfico agregado actual
- `GET/PUT /settings` — intervalo de polling, día de reseteo, retención,
  canal de alertas
- `GET/POST/DELETE /alerts/thresholds`
- `GET /alerts/events`

## Frontend (React + Vite + TypeScript)

- **Login**: usuario/clave → JWT.
- **Dashboard principal**: total de clientes conectados (global,
  destacado), desglose por router, tráfico agregado actual.
- **Tabla de clientes**: username, router, estado (online/offline),
  tráfico actual, acumulado del período — ordenable por cualquier columna,
  con foco en ordenar por acumulado para detectar heavy-users.
- **Detalle de cliente**: gráfico de consumo histórico (Recharts), con
  selector de rango (24h / 7 días / período actual).
- **Administración**: CRUD de routers, umbrales de alerta, configuración
  general (día de reseteo, canal de notificaciones).
- Diseño simple, responsive.

## Notificaciones

Módulo en el backend con dos canales opcionales (se pueden activar
independientemente uno, otro, ambos o ninguno):
- Email vía SMTP
- Telegram (bot token + chat id)

Configurables desde `settings`.

## Despliegue

Docker Compose en el Ubuntu Server:

```yaml
services:
  db:        # postgres:16, volumen persistente, healthcheck
  backend:   # FastAPI + Alembic migrations + APScheduler, :8000 interno
  frontend:  # build React servido por nginx, :80/:443, proxea /api
```

- Variables sensibles (passwords de routers, SMTP, Telegram token, JWT
  secret) vía `.env`, nunca commiteadas.
- Passwords de routers **encriptados en la base** (Fernet/AES con clave
  maestra en `.env`), nunca en texto plano.
- HTTPS en el Nginx del frontend (Let's Encrypt/certbot o autofirmado si
  es solo LAN interna — a definir al montar el servidor).
- Backups: `pg_dump` programado (cron del host).

## Fuera de alcance (fase 1)

- Multi-tenancy / múltiples organizaciones.
- SSO/LDAP (queda como usuario/clave simple).
- Particionado de base de datos / TimescaleDB.
- Escalado horizontal del backend (worker separado).
