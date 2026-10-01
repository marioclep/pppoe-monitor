# Endurecimiento para producción — Diseño

Fecha: 2026-09-25
Estado: Aprobado por el usuario en conversación (secciones 1–3), spec pendiente de revisión

Extiende `2026-09-22-pppoe-monitor-design.md` y
`2026-09-24-sesiones-y-submuestreo-design.md`. Todo lo que este documento no
menciona sigue como está.

## Contexto y objetivo

El monitor funciona en la VM de pruebas con 6 routers y ~3.300 sesiones. El
objetivo es poder instalarlo en el servidor Ubuntu del ISP y olvidarse:

- que sobreviva a reinicios del servidor sin intervención;
- que no se pierdan datos (backups diarios restaurables);
- que actualizar no requiera trucos (hoy, tras un deploy, el navegador sigue
  mostrando la UI vieja hasta un refresco forzado);
- que un admin nuevo pueda levantarlo siguiendo solo el README.

### Decisiones del usuario

- Acceso **solo desde la LAN o por VPN**. Se mantiene **HTTP plano**; no hay HTTPS.
- **Backup diario local con rotación**. La copia fuera del servidor queda a
  cargo del usuario (se documenta cómo hacerla).

### Supuestos

- Un único servidor con Docker Compose.
- Se actualiza con `git pull && docker compose up -d --build`.
- Un solo usuario admin, sin roles.

### Criterios de éxito

1. En una VM limpia, siguiendo el README, el sistema queda funcionando.
2. Tras reiniciar el servidor, todos los servicios vuelven solos y sanos.
3. Un backup generado por el sistema se restaura con los comandos del README.
4. Tras actualizar, el navegador carga la versión nueva sin refresco forzado.

### Fuera de alcance

HTTPS, varios usuarios o roles, métricas tipo Prometheus, copia remota
automática de backups y el rediseño visual (va después, en su propia rama).

## 1. Despliegue e infraestructura

### nginx (`frontend/nginx.conf`)

- `location = /index.html` (y el fallback de SPA que termina sirviendo
  `index.html`): `Cache-Control: no-cache`. El navegador revalida siempre y
  ve la versión nueva apenas se despliega.
- `location /assets/`: los archivos llevan hash en el nombre (Vite), así que
  van con `Cache-Control: public, max-age=31536000, immutable`.
- `gzip on` para `text/css`, `application/javascript`, `application/json`,
  `image/svg+xml`, con `gzip_min_length 1024`.
- Cabeceras de seguridad en todas las respuestas: `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY` y `Referrer-Policy: same-origin`.
- `client_max_body_size 1m`: la API no recibe archivos.
- `server_tokens off`.
- En `/api/`, se agrega `proxy_set_header X-Forwarded-For
  $proxy_add_x_forwarded_for` a lo que ya se pasa, que sigue igual.
- Ojo: `add_header` en un `location` anula los heredados del `server`, así
  que las cabeceras de seguridad se repiten en cada `location` que agrega
  `Cache-Control` (vía un `include` de un snippet común).

### Docker Compose

- **Logs acotados.** Todos los servicios usan `logging: driver json-file`
  con `max-size: 10m` y `max-file: 3`, con un ancla YAML
  (`x-logging: &default-logging`).
- **Healthcheck del backend.** `python -c` con `urllib` contra
  `http://localhost:8000/health`, cada 30 s, con `start_period: 60s` porque
  las migraciones corren al arrancar. La imagen es slim y no trae `curl`.
- El **frontend** usa `depends_on: backend: condition: service_healthy`.
- **Healthcheck del frontend** con `wget -qO- http://localhost/`; la imagen
  alpine trae `wget`.
- El **puerto publicado** se configura: `"${HTTP_PORT:-80}:80"`.
- Postgres sigue **sin publicar puertos**.
- Se mantiene `restart: unless-stopped` en todos los servicios, incluido el
  de backup.

### `/health` real (`backend/app/main.py`)

- Ejecuta `SELECT 1` con una sesión de la base.
- Si funciona, responde `200 {"status": "ok"}`.
- Si falla, responde `503 {"status": "error", "detail": "database unavailable"}`
  y registra el error en el log.
- No requiere autenticación, igual que hoy.
- Lo usa el healthcheck de Docker dentro del contenedor. Desde afuera
  también responde en `/api/health` a través de nginx, y eso es aceptable
  porque no revela nada.

### Servicio `backup`

- **Imagen:** `postgres:16`, la misma que `db`, para que `pg_dump` coincida
  siempre con la versión del servidor.
- **Script:** `backup/backup.sh`, montado de solo lectura. Es un bucle en
  `sh`:
  1. Calcula cuántos segundos faltan hasta la próxima `BACKUP_TIME`, en hora
     local según `TZ`.
  2. Hace `sleep` hasta ese momento.
  3. Ejecuta `pg_dump -Fc` hacia
     `/backups/pppoe-YYYYMMDD-HHMM.dump.partial` y, si termina bien, hace
     `mv` a `.dump`. Así un archivo a medio escribir nunca parece válido.
  4. Borra los `pppoe-*.dump` de más de `BACKUP_KEEP_DAYS` días
     (`find -mtime +N`).
  5. Registra en stdout el inicio, el fin, el tamaño y los archivos borrados.
- **Si `pg_dump` falla:** borra el `.partial`, registra `ERROR` y vuelve a
  esperar hasta el día siguiente. El contenedor no se cae, y el error queda
  visible en `docker compose logs backup`.
- **Backup a demanda:** el mismo script con el argumento `now` hace un
  backup inmediato y termina:
  `docker compose run --rm backup /backup.sh now`. El README lo indica antes
  de cada actualización.
- **Variables en `.env`:** `BACKUP_TIME=03:00` y `BACKUP_KEEP_DAYS=14`, con
  esos valores por defecto. La conexión usa `PGHOST=db` y las mismas
  `POSTGRES_USER`, `POSTGRES_PASSWORD` y `POSTGRES_DB`.
- **Volumen:** `./backups:/backups` (bind mount en el directorio del
  proyecto). `.gitignore` incluye `backups/`.
- `depends_on: db: condition: service_healthy`.

## 2. Acceso y operación del backend

### CLI de administración (`backend/app/cli.py`)

Se invoca con `docker compose exec backend python -m app.cli <comando>`.

- **`create-admin <usuario>`:**
  - Pide la contraseña dos veces con `getpass`.
  - Exige al menos 10 caracteres.
  - Si el usuario ya existe, falla con un mensaje claro y código 1.
- **`reset-password <usuario>`:** mismo ingreso de contraseña. Si el usuario
  no existe, falla con código 1.
- También acepta la contraseña por la variable `PPPOE_ADMIN_PASSWORD`, para
  scripts y tests; si está definida, no se pregunta.
- Se implementa con `argparse`, sin dependencias nuevas.

### Límite de intentos de login

- **Módulo** `backend/app/core/login_throttle.py` con una clase
  `LoginThrottle(max_failures=5, window_seconds=900, clock=time.monotonic)`.
  Guarda los timestamps de los fallos por IP en memoria y los protege con un
  `threading.Lock`.
- **API de la clase:**
  - `is_blocked(ip)`: `True` si hay 5 o más fallos dentro de la ventana.
  - `register_failure(ip)`.
  - `reset(ip)`: se llama tras un login exitoso.
- Se descartan las entradas vencidas en cada operación, para que no crezca
  sin límite.
- **En `POST /auth/login`:**
  - Si la IP está bloqueada, responde `429 {"detail": "Too many failed login
    attempts, try again later"}` sin verificar la contraseña.
  - Si falla el login, registra el fallo y escribe `WARNING login failed
    user=<u> ip=<ip>`.
  - Si el login es exitoso, hace `reset(ip)`.
- **IP del cliente:**
  - `X-Real-IP` si el peer directo (`request.client.host`) pertenece a una
    red privada o de loopback, que es donde está nginx en la red de Docker.
  - Si no, se usa `request.client.host`.
  - No se configura una lista de proxies de confianza: el backend no publica
    puertos y solo lo alcanza nginx.
- **Frontend:** `Login.tsx` muestra "Demasiados intentos fallidos, esperá
  unos minutos" ante un 429.
- **Límites conocidos:** los contadores viven en un solo proceso. Un
  reinicio los borra, y en una LAN eso es aceptable.

### Logging

- **`app/logging_config.py`:** función `configure_logging()`, llamada al
  importar `app.main`.
  - Configura el root logger con un handler a stdout, formato
    `%(asctime)s %(levelname)s %(name)s: %(message)s` y nivel tomado de
    `settings.LOG_LEVEL` (por defecto `INFO`).
  - Es idempotente: no duplica handlers si se llama dos veces.
- **Nueva configuración:** `LOG_LEVEL` en `Settings`, `.env.example` y
  `docker-compose.yml`.
- Los mensajes `INFO` ya existentes de polling, rollup, purga y reseteo pasan
  a verse en `docker compose logs backend`.

### Un solo worker

`entrypoint.sh` lleva un comentario que explica que uvicorn corre con un solo
proceso a propósito: el scheduler vive dentro de la app y con varios workers
cada sondeo se haría varias veces. El README lo repite en "Diagnóstico".

## 3. README y procedimientos

El README se reorganiza como guía de producción:

1. **Requisitos:** Ubuntu Server, Docker Engine y el plugin compose, más el
   acceso a la API REST de los routers (se mantiene lo que ya está).
2. **Instalación:** clonar el repo, `cp .env.example .env` y generar cada
   secreto con el comando indicado junto a cada variable. Después,
   `docker compose up -d --build`, `docker compose ps` (todos `healthy`) y
   `python -m app.cli create-admin admin`.
3. **Actualizar:**
   1. Backup a demanda.
   2. `git pull`.
   3. `docker compose up -d --build`.
   4. Verificar `docker compose ps`.
   Las migraciones corren solas al arrancar el backend.
4. **Backups:**
   - Dónde quedan y cómo cambiar la hora y la retención.
   - **Cómo restaurar**, con los comandos exactos:
     1. `docker compose stop backend`.
     2. `docker compose exec -T db pg_restore --clean --if-exists -U ... -d ... < backups/<archivo>.dump`.
     3. `docker compose start backend`.
   - Cómo copiarlos fuera del servidor (ejemplo con `rsync`).
5. **Diagnóstico:**
   - `docker compose logs -f backend`, `docker compose ps`.
   - Qué indica un router con "último sondeo" viejo.
   - El aviso de no subir workers.
   - El reseteo de contraseña con `reset-password`.

## Pruebas

### Tests automáticos (backend, pytest)

- **`/health`:** 200 con la base disponible; 503 si la sesión lanza
  `OperationalError`, simulado con un override de la dependencia.
- **`LoginThrottle`,** con reloj falso:
  - bloquea al quinto fallo;
  - se desbloquea al vencer la ventana;
  - `reset` limpia;
  - las IPs son independientes.
- **`POST /auth/login`:**
  - 429 tras 5 fallos;
  - un login correcto reinicia el contador;
  - usa `X-Real-IP` cuando el peer es privado.
- **CLI:**
  - `create-admin` crea el usuario con el hash correcto;
  - falla si el usuario ya existe;
  - falla con una contraseña corta;
  - `reset-password` cambia el hash;
  - falla con un usuario inexistente.
- **`configure_logging`:** aplica el nivel y es idempotente.

### Verificación en la VM (manual, documentada en el plan)

1. `docker compose config` sin errores.
2. Despliegue en `~/pppoe-monitor` sobre los datos existentes: las
   migraciones no cambian y los datos se conservan.
3. `curl -I` a `/`, `/index.html` y un archivo de `/assets/`: cabeceras de
   caché y de seguridad correctas, y gzip presente.
4. `docker compose ps`: todos los servicios `healthy`.
5. `docker compose run --rm backup /backup.sh now` genera un `.dump`. Se
   restaura en una base temporal y el conteo de filas de `pppoe_clients`,
   `session_state` y `traffic_hourly` coincide con el de la base original.
6. Seis intentos de login con contraseña incorrecta: el sexto devuelve 429.
7. Reinicio de la VM: todos los servicios vuelven `healthy` y el polling
   continúa.
