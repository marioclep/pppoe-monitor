# Monitor PPPoE — guía para asistentes de IA

Este archivo es para un asistente de IA (Claude Code, Codex, Cursor, Copilot,
Gemini, etc.) que ayuda a **instalar, actualizar, diagnosticar o modificar**
el Monitor PPPoE. Qué hace el sistema y todas sus opciones están en el
[`README.md`](README.md); el historial, en [`CHANGELOG.md`](CHANGELOG.md).

## Qué es

Monitor web para ISPs con routers MikroTik: lee por la **API REST de RouterOS
7** el tráfico de cada sesión PPPoE, lo guarda en Postgres y avisa cuando un
cliente supera un umbral mensual. Corre con Docker Compose en cuatro
contenedores: `db` (Postgres 16), `backend` (FastAPI + scheduler), `frontend`
(nginx) y `backup` (`pg_dump` diario).

- **Solo lee los routers**, con pedidos GET. Nunca escribe en ellos.
- **RouterOS v6 no está soportado** (no tiene API REST).

## Reglas para el asistente

- **Nunca cambiar `MASTER_ENCRYPTION_KEY`** en un `.env` existente: con ella
  se cifran las contraseñas de los routers guardadas en la base. Si cambia,
  quedan ilegibles y hay que volver a cargarlas todas.
- **Nunca borrar el `.env` ni el volumen `pppoe_db_data`** (`docker compose
  down -v` borra la base entera).
- **Antes de actualizar, hacer un backup** (`docker compose run --rm backup now`).
- **No agregar `--workers` a uvicorn** ni levantar dos backends contra la
  misma base: el tráfico se contaría varias veces.
- **No publicar la web a internet.** Está pensada para la LAN o una VPN y
  sirve por HTTP simple.
- **No cambiar la configuración de los routers** más allá de lo indicado en
  "Preparar cada MikroTik", y siempre mostrándole al usuario el comando antes.
- **Los tests del backend borran datos:** nunca correrlos contra una base de
  producción.
- El usuario habla en español: responder en español.

## Instalación

### 1. Verificar requisitos

| Requisito | Cómo comprobarlo |
|---|---|
| Linux con Docker y Docker Compose v2 (probado en Ubuntu Server 24.04 y 26.04) | `docker --version` y `docker compose version` |
| Acceso root o usuario en el grupo `docker` | `docker ps` sin error de permisos |
| Al menos 2 vCPU y 3 GB de RAM para unos 2000 clientes | `nproc` y `free -h` |
| Puerto web libre (`HTTP_PORT`, 80 por defecto) | `sudo ss -ltnp \| grep ':80 '` no debe mostrar nada |
| git y openssl | `git --version` y `openssl version` |
| El servidor llega a cada router por el puerto del servicio web | `nc -zv <IP_DEL_ROUTER> 443` (o el puerto que use `www`/`www-ssl`) |

Si falta Docker:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

El grupo `docker` recién aplica en una sesión nueva. En una sesión SSH no
interactiva, volver a conectarse o usar `sudo docker ...` mientras tanto.

### 2. Clonar y crear el `.env`

```bash
git clone https://github.com/marioclep/pppoe-monitor.git ~/pppoe-monitor
cd ~/pppoe-monitor
cp .env.example .env && chmod 600 .env
```

Generar los secretos y completarlos en `.env`:

```bash
openssl rand -hex 24   # POSTGRES_PASSWORD (hex: no rompe la URL de DATABASE_URL)
openssl rand -hex 32   # JWT_SECRET (mínimo 32 caracteres)
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # MASTER_ENCRYPTION_KEY
```

- La contraseña de `POSTGRES_PASSWORD` tiene que repetirse dentro de
  `DATABASE_URL` (`postgresql://pppoe:<la misma>@db:5432/pppoe`).
- Ajustar `TZ` (zona IANA del ISP; define cuándo se reinicia el acumulado
  mensual) y, si el 80 está ocupado, `HTTP_PORT`.
- El backend **se niega a arrancar** si `JWT_SECRET` o
  `MASTER_ENCRYPTION_KEY` quedaron vacíos, cortos o con el valor de ejemplo.
- Recordarle al usuario que **guarde una copia del `.env` fuera del
  servidor**: sin `MASTER_ENCRYPTION_KEY`, un backup no recupera las
  contraseñas de los routers.

### 3. Levantar y verificar

```bash
docker compose up -d --build
docker compose ps
```

La primera vez puede tardar varios minutos (build) y hasta un minuto más
mientras corren las migraciones. Está bien cuando `db`, `backend` y
`frontend` figuran `(healthy)` y `backup`, `Up`. Comprobar además:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:${HTTP_PORT:-80}/   # 200
docker compose logs --tail 30 backend                                          # sin errores
```

### 4. Crear el primer usuario

Con rol Completo; la contraseña necesita al menos 10 caracteres. De forma
interactiva:

```bash
docker compose exec backend python -m app.cli create-admin admin
```

Sin terminal interactiva (por ejemplo, desde un asistente por SSH), pasar la
contraseña por variable de entorno:

```bash
PASS=$(openssl rand -base64 18)
docker compose exec -e PPPOE_ADMIN_PASSWORD="$PASS" backend python -m app.cli create-admin admin
echo "$PASS"
```

Pasarle la contraseña al usuario una sola vez y recomendarle cambiarla desde
la web. Los demás usuarios se crean en la pantalla **Usuarios**.

## Preparar cada MikroTik

Necesita RouterOS 7.1 o superior, el servicio web (`www-ssl`, o `www` sin
TLS) y un usuario con las políticas `read` y `rest-api`. Se leen
`/rest/interface/pppoe-server`, `/rest/interface`, `/rest/ppp/active` y
`/rest/system/resource`.

Antes de cambiar nada, mirar cómo están los servicios:

```
/system resource print
/ip service print where name~"www"
```

El servicio web suele usarse también para WebFig. **No cambiar su puerto ni
deshabilitarlo sin preguntar**, y si ya tiene direcciones permitidas
(`address`), **agregar** la IP del monitor a la lista en lugar de
reemplazarla.

```
/user group add name=monitor policy=read,rest-api
/user add name=monitor group=monitor password=<CONTRASEÑA> address=<IP_DEL_MONITOR>/32
```

Si `www-ssl` no tiene certificado, se puede crear uno propio (y en el monitor
destildar **Verificar certificado TLS**):

```
/certificate add name=monitor-https common-name=<NOMBRE_DEL_ROUTER>
/certificate sign monitor-https
/ip service set www-ssl certificate=monitor-https disabled=no
```

Después, en la web: **Routers → Agregar router**, con host, puerto del
servicio web, usuario, contraseña y las opciones de TLS, y **Probar
conexión** para confirmar. Si falla, el mensaje dice qué revisar (servicio,
TLS, credenciales o una API clásica en lugar de la REST).

## Operación

| Tarea | Comando (desde la carpeta del proyecto) |
|---|---|
| Estado | `docker compose ps` |
| Logs del backend | `docker compose logs -f backend` |
| Backup inmediato | `docker compose run --rm backup now` (queda en `./backups/`) |
| Actualizar | `docker compose run --rm backup now && git pull && docker compose up -d --build && docker compose ps` |
| Contraseña olvidada | `docker compose exec backend python -m app.cli reset-password <usuario>` (acepta `PPPOE_ADMIN_PASSWORD`) |
| Restaurar un backup | Ver "Restaurar" en el `README.md`: reemplaza toda la base |

Las migraciones corren solas al arrancar el backend.

## Problemas comunes

| Síntoma | Causa probable y qué hacer |
|---|---|
| `docker compose` aborta con `set ... in .env` | Falta una variable obligatoria en `.env`. |
| `backend` reinicia en bucle o queda `unhealthy` | `docker compose logs backend`: secretos inválidos (`JWT_SECRET`, `MASTER_ENCRYPTION_KEY`) o `DATABASE_URL` con una contraseña distinta de `POSTGRES_PASSWORD`. |
| `permission denied` al usar `docker` | El usuario no está en el grupo `docker` o falta una sesión nueva. |
| "Probar conexión" falla con timeout o conexión rechazada | Servicio `www`/`www-ssl` deshabilitado, otro puerto, firewall o `address` que no incluye al monitor. |
| "Probar conexión" falla por TLS | Certificado propio: destildar **Verificar certificado TLS**, o usar `www` sin TLS. |
| "Probar conexión" falla por credenciales | Usuario o contraseña, el usuario no tiene la política `rest-api`, o su `address` no incluye al monitor. |
| El router responde pero no aparecen clientes | RouterOS anterior a 7.1 (sin API REST) o no hay sesiones PPPoE en ese router. |
| Un router con "último sondeo" viejo | Buscar su nombre en `docker compose logs backend` y probar la conexión. |
| Login bloqueado | 5 intentos fallidos en 15 minutos bloquean esa IP; esperar o reiniciar el backend. |

## Desarrollo

### Estructura

| Qué | Dónde |
|---|---|
| API (FastAPI) | `backend/app/api/` |
| Lógica: sondeo, deltas, resumen por hora, purga, reseteo mensual, alertas | `backend/app/services/` |
| Modelos (SQLAlchemy) y esquemas (Pydantic) | `backend/app/models/`, `backend/app/schemas/` |
| Migraciones | `backend/alembic/versions/` |
| CLI (`create-admin`, `reset-password`) | `backend/app/cli.py` |
| Tests del backend | `backend/tests/` |
| Pantallas y componentes (React + Vite) | `frontend/src/pages/`, `frontend/src/components/` |
| Textos de la interfaz | `frontend/src/i18n/es.ts` y `en.ts` |
| Backup diario | `backup/backup.sh` |
| Diseño y planes originales (históricos) | `docs/superpowers/` |

### Comandos

- Tests del backend: `cd backend && .venv/bin/pytest -q` (ver en el README
  qué base aceptan: nunca la de producción).
- Frontend: `cd frontend && npm run lint && npm run build`.
- Migración nueva: `cd backend && .venv/bin/alembic revision -m "..."` y
  después `.venv/bin/alembic upgrade head`. Probarla subiendo, bajando y
  volviendo a subir sobre una copia de una base real.

### Convenciones

- **Idioma:** la documentación y los mensajes de commit van en español, con
  el estilo `feat(frontend): ...`. El código y sus comentarios, en inglés.
- **Textos de la interfaz:** todo texto visible va en `frontend/src/i18n/es.ts`
  y `en.ts` (mismas claves; `en` está tipado contra `es`, así que el build
  falla si falta una traducción). En componentes se usa `useT()`; fuera de
  React, `t()` de `i18n/core`. Los textos del backend que llegan al usuario
  (avisos de alerta, "Probar conexión") usan `get_language(db)`.
- **TDD:** primero el test que falla y después la implementación.
- **Interfaz:** sin una herramienta de navegador, los cambios visuales se
  verifican con lint, build y pedidos a la API; decirlo explícitamente.
- **Repo público:** nada de credenciales, IPs reales ni nombres de clientes.
  Para ejemplos, usar `192.0.2.0/24` y `2001:db8::/32`.

### Trampas conocidas

- **Comandos sueltos en el backend:** su `ENTRYPOINT` ignora los argumentos
  de `docker compose run`, así que hay que usar `--entrypoint`. Un
  `run backend alembic ...` levanta una segunda app entera.
- **`psutil.cpu_percent()`** guarda su referencia por hilo, y el scheduler
  corre cada tarea en cualquier hilo del pool. Por eso existe `CpuMeter` en
  `app/services/server_stats.py`.
- **`Row.t` de SQLAlchemy:** es un atributo propio de las filas, así que no
  sirve como alias de columna en SQL crudo. Usar, por ejemplo, `bucket_start`.
- **No agregar `--workers` a uvicorn:** con varios procesos el tráfico se
  cuenta varias veces.
- **RouterOS v6 no está soportado** (no tiene API REST). Se decidió no sumar
  la API clásica (puerto 8728).
- **Pedidos al router:** `/ppp/active` y `/system/resource` son opcionales.
  Si fallan, el sondeo sigue; nunca deben hacerlo fallar.
- **Solo lectura en los routers:** el monitor hace únicamente pedidos GET.
  No agregar nada que escriba en un router.
- **Unidades:** la interfaz muestra los tamaños en base 1024 con la etiqueta
  "GB" (`frontend/src/utils/format.ts`), y los umbrales de alerta usan la
  misma base (`frontend/src/utils/alerts.ts`).

### Problemas conocidos (menores)

- Un solo estado de error compartido en el dashboard.
- No hay control de orden en celular.
- Accesibilidad del menú lateral y de las filas de routers.
- Con cero routers se muestra "0 / 0 todos al día".
- Un gráfico con un solo punto se ve vacío.
- Los ids de gradiente de los gráficos se repiten.
- Los intervalos con valores nulos mezclados cuentan de menos a los
  conectados.
- Los gráficos por hora interpolan sobre los huecos en que el sistema estuvo
  apagado.
- En inglés, las etiquetas del eje vertical del gráfico de velocidad se
  parten en dos líneas ("600.00" / "Mbps") y la de arriba queda pisada
  contra el borde.
