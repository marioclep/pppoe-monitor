# Monitor PPPoE

**Español** · [English](README.en.md)

Monitor web para ISPs con routers **MikroTik**: muestra qué clientes PPPoE
están conectados, cuánto tráfico usan ahora y cuánto acumularon en el mes,
para detectar a los **heavy-users**.

- **Mucha descarga** puede indicar reventa del servicio (un cliente que
  comparte su conexión con vecinos).
- **Mucha subida** puede indicar un equipo infectado, típicamente un TV box
  que forma parte de una botnet.

Lee los contadores de cada sesión PPPoE por la API REST de RouterOS, guarda
el historial en Postgres y avisa por email o Telegram cuando un cliente
supera el umbral del mes.

![Dashboard: conectados, tráfico actual, tráfico total de 24 h y estado de cada router](docs/screenshots/dashboard.png)

## Qué no hace

- **No es DPI ni NetFlow:** no sabe qué sitios visita un cliente ni qué
  protocolos usa; solo cuántos bytes pasan por su sesión.
- **No corta ni limita el servicio:** solo lee. Nunca escribe en los
  routers.
- **Solo ve clientes PPPoE:** los que se conectan por DHCP, Hotspot o IP
  estática no aparecen.
- **No soporta RouterOS v6:** usa la API REST, que existe desde RouterOS 7.1.

## Funciones

- **Dashboard:** clientes conectados, tráfico actual y del mes, estado de
  cada router y ranking de consumo, con gráficos de 24 h, 7, 30 y 90 días.
  Al pasar el cursor por un router se ve lo que midió en cada sondeo de las
  últimas 24 h.
- **Página de cada router:** modelo, versión de RouterOS, uptime, CPU,
  memoria y disco; su tráfico, sus conectados y la tabla de sus clientes.
- **Clientes:** búsqueda por usuario o IP, filtros y orden. El detalle de un
  cliente muestra descarga y subida (actuales y del mes) y sus gráficos.
- **Alertas:** umbrales mensuales de descarga y de subida, por separado,
  globales o por cliente. Avisos por email o Telegram y una sección con el
  historial de alertas.
- **Usuarios con roles:** Completo (ve y modifica todo) y Solo lectura.
- **Servidor:** CPU, memoria y disco de la máquina donde corre el monitor.
- **Interfaz en español e inglés**, con tema claro y oscuro.
- **Backups diarios** automáticos de la base.

| Un cliente con mucha subida (posible TV box infectado) | Un cliente con mucha descarga, en inglés y tema oscuro |
|---|---|
| ![Detalle de un cliente: 163 GB de subida en el mes contra 11 GB de descarga](docs/screenshots/client-upload.png) | ![Detalle de un cliente en inglés y tema oscuro: 324 GB de descarga en pocas horas](docs/screenshots/client-dark-en.png) |

![Página de un router: CPU, memoria, disco, conectados, tráfico y recursos](docs/screenshots/router.png)

## Compatibilidad

- **Routers:** MikroTik con **RouterOS 7.1 o superior**. Probado en
  producción con CCR1036, CCR2004, CCR2116, CCR2216 y RB5009, con RouterOS
  7.23 y 7.24.
- **Servidor:** cualquier Linux con Docker. Probado en Ubuntu Server 24.04 y
  26.04.
- **Recursos:** con 2 vCPU y 3 GB de RAM alcanza para unos 2000 clientes
  repartidos en 10 routers.

## Requisitos de los routers

- El servicio web habilitado en **IP > Services**: `www-ssl`, o `www` si no se
  usa TLS (puede estar en otro puerto, por ejemplo 8090). Conviene limitar
  *Available From* a la IP del monitor.
- Un usuario en un grupo con las políticas **`read`** y **`rest-api`**. No
  necesita permisos de escritura.
- En cada sondeo se leen:
  - `/rest/interface/pppoe-server`: las sesiones activas;
  - `/rest/interface`: los contadores de bytes de cada sesión;
  - `/rest/ppp/active`: la IP de cada cliente;
  - `/rest/system/resource`: CPU, memoria, disco, uptime, versión y modelo.
- Si falla la lectura de `/ppp/active` o de `/system/resource`, el sondeo
  sigue igual: solo falta ese dato, que queda como un hueco en los gráficos.

## Instalación (Ubuntu Server)

1. Instalar Docker y Docker Compose:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
   (cerrar la sesión y volver a entrar para que tome el grupo `docker`).
2. Clonar el repo:
   ```bash
   git clone https://github.com/marioclep/pppoe-monitor.git ~/pppoe-monitor
   cd ~/pppoe-monitor
   ```
3. Copiar `.env.example` a `.env` y completar:
   - `POSTGRES_PASSWORD`: contraseña fuerte para la base (y reflejarla en
     `DATABASE_URL`).
   - `JWT_SECRET` (**obligatorio**): string aleatorio de al menos 32
     caracteres (`openssl rand -hex 32`). El backend **se niega a arrancar**
     si está vacío, si es más corto o si quedó el placeholder del ejemplo.
   - `MASTER_ENCRYPTION_KEY` (**obligatorio**): generar con
     `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
     (o, sin Python en el host, con
     `docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"`).
     Se usa para cifrar las contraseñas de API de los routers guardadas en la
     base. El backend se niega a arrancar si no es una clave Fernet válida.
     **Guardala aparte**: sin ella, un backup no sirve para recuperar las
     contraseñas de los routers.
   - `TZ` (opcional, default `America/Argentina/Cordoba`): zona horaria IANA
     usada para el reseteo mensual del acumulado (ver "Reseteo mensual").
   - `TOKEN_EXPIRE_MINUTES` (opcional, default 480).
   - `LOG_LEVEL` (opcional, default `INFO`): nivel de los logs del backend.
   - `HTTP_PORT` (opcional, default `80`): puerto del host donde se publica la web.
   - `BACKUP_TIME` / `BACKUP_KEEP_DAYS` (opcionales, default `03:00` / `14`):
     hora local del backup diario y cuántos días se conservan (ver "Backups").

   `docker compose` aborta con un mensaje claro si falta alguna variable
   obligatoria (`POSTGRES_*`, `DATABASE_URL`, `JWT_SECRET`,
   `MASTER_ENCRYPTION_KEY`).
4. Levantar el stack:
   ```bash
   docker compose up -d --build
   ```
5. Verificar que todo esté sano (la primera vez puede tardar hasta un minuto,
   mientras corren las migraciones):
   ```bash
   docker compose ps
   ```
   `db`, `backend` y `frontend` tienen que figurar `(healthy)` y `backup`, `Up`.
6. Crear el primer usuario, con rol Completo (pide la contraseña dos veces,
   mínimo 10 caracteres). Los demás usuarios se crean desde la web, en
   "Usuarios":
   ```bash
   docker compose exec backend python -m app.cli create-admin admin
   ```
7. Entrar a `http://<ip-del-servidor>/` (o `:<HTTP_PORT>`) y cargar los
   routers desde "Routers" con sus credenciales de API REST. El botón
   "Probar conexión" confirma que el usuario y el servicio web estén bien
   configurados.

Todos los servicios tienen `restart: unless-stopped`: después de un reinicio
del servidor vuelven solos, siempre que el servicio de Docker arranque con el
sistema (el instalador oficial lo deja así).

## Actualizar

```bash
cd ~/pppoe-monitor
docker compose run --rm backup now   # backup antes de tocar nada
git pull
docker compose up -d --build
docker compose ps                     # todo tiene que volver a (healthy)
```

Las migraciones de la base corren solas al arrancar el backend. Si solo
cambió el frontend, el sondeo no se interrumpe; si cambió el backend, se
corta alrededor de un minuto mientras reinicia. El navegador carga la versión
nueva solo (el `index.html` se sirve sin caché). Los cambios de cada versión
están en [`CHANGELOG.md`](CHANGELOG.md).

## Configuración

Desde la pantalla "Configuración" de la web:

- **Idioma / Language** (`es` o `en`, default `es`): el de toda la interfaz,
  para todos los usuarios, y el de los avisos de alerta y los mensajes de
  "Probar conexión". La pantalla de login usa el último idioma visto en ese
  navegador (o el del navegador).
- **Nombre de la instalación** (opcional, hasta 40 caracteres): se muestra en
  el título del Dashboard ("Dashboard ACME") y en la pestaña del navegador
  ("Monitor PPPoE · ACME").
- **Intervalo de sondeo** en segundos (default 300, mínimo 60). Se aplica en
  caliente, sin reiniciar el backend.
- **Día de reseteo mensual** del acumulado (default 1, rango 1–28 para que
  exista en todos los meses). Ver "Reseteo mensual".
- **Retención de historial** en días (default 90): cuánto se guarda el
  resumen por hora de cada cliente.
- **Retención de detalle** en días (default 7, no mayor que la de
  historial): cuánto se guardan las muestras de cada sondeo. Pasado ese plazo
  queda solo el resumen por hora. Los gráficos que entran en ese plazo usan
  cada sondeo; los más largos, un punto por hora con el promedio y el pico.
- **SMTP y Telegram** para los avisos de alerta. La contraseña SMTP y el
  token de Telegram no se muestran una vez guardados y solo se sobrescriben
  si se envía un valor nuevo. Dejar vacío un campo opcional y guardar lo
  borra.
- **Umbrales de alerta globales** de descarga y de subida del mes. Los de un
  cliente se cargan desde su detalle y reemplazan al global en esa dirección.

## Cómo se cuenta el tráfico

- La primera vez que se ve una sesión PPPoE (cliente nuevo, cliente que
  vuelve a conectarse, o router recién agregado o rehabilitado), sus bytes
  solo se suman al acumulado si la sesión **empezó después del sondeo exitoso
  anterior** de ese router. Si no, los contadores actuales se toman como
  línea base y solo se cuenta el tráfico desde ahí. Por eso, en el primer
  sondeo de un router todas las sesiones ya abiertas arrancan en 0: el primer
  mes subestima levemente a quienes ya estaban conectados, en lugar de
  inflarlo con todo el historial de la sesión.
- Cada sesión PPPoE se sigue por separado. Si un usuario tiene varias
  sesiones simultáneas, su tráfico es la suma de todas (incluidas las
  interfaces `<pppoe-USUARIO-1>`).
- Si no se encuentra la interfaz de una sesión, ese sondeo no toca su
  tráfico y se registra un warning con el router y el usuario.
- El tráfico "actual" es el medido en el último sondeo de cada cliente
  conectado; un cliente desconectado muestra 0.
- Deshabilitar un router deja de sondearlo, marca a sus clientes como
  desconectados y lo saca del dashboard; su historial se conserva.
  **Eliminar** un router borra también todo su historial (clientes,
  acumulados, muestras y alertas).
- "Descarga" es lo que el cliente baja (lo que el router transmite por la
  interfaz PPPoE) y "subida", lo que el cliente sube.
- Los tamaños se muestran en múltiplos de 1024 (1 GB = 1024³ bytes), como en
  Winbox.

## Reseteo mensual

- El acumulado de cada cliente se cierra y arranca de cero una vez por mes,
  el día configurado, alrededor de la medianoche **local** de la zona
  horaria `TZ`.
- El chequeo corre **cada hora** (minuto 5) y **una vez al arrancar** el
  backend. Es "por vencimiento": si el reseteo de este mes (o del anterior,
  si el día todavía no llegó) aún no se hizo, se hace en ese chequeo. Así, si
  el servidor estuvo apagado el día del reseteo, se recupera en el primer
  chequeo posterior en vez de perderse un mes.
- La fecha del último reseteo se guarda en la tabla `settings` (clave
  `last_reset_date`): correrlo de nuevo para la misma fecha no hace nada.
- Si se cambia el día de reseteo a uno que ya pasó en el mes en curso (y
  para esa fecha todavía no hubo reseteo), el reseteo se hace en el próximo
  chequeo.

## Alertas

- Cada dirección se evalúa por separado: la descarga del mes contra el
  umbral de descarga y la subida contra el de subida (no se suman).
- Hay como máximo un umbral global por dirección y uno por cliente y
  dirección. El de un cliente tiene prioridad sobre el global.
- Se avisa **una vez por dirección y por período**, la primera vez que el
  consumo supera el umbral.
- El aviso se manda **solo** por el canal elegido en el umbral (`email` o
  `telegram`; si ese canal no está configurado, no se envía). Nombra al
  usuario PPPoE, el router, el consumo y el umbral.
- Todas las alertas quedan en la sección **Alertas** de la web (las últimas
  200), aunque no haya ningún canal configurado, y se conservan aunque
  después se borre el umbral.

## Usuarios y roles

- **Completo:** ve y modifica todo (routers, configuración, umbrales de
  alerta) y administra usuarios.
- **Solo lectura:** ve todo, incluidas las pantallas Routers y
  Configuración, pero sin botones para modificar. El backend responde 403 a
  cualquier cambio que intente.
- Los cambios de rol y las bajas valen en el acto. Cambiar una contraseña no
  cierra las sesiones que ya estaban abiertas.
- Nadie puede borrarse a sí mismo y siempre queda al menos un usuario
  Completo.
- `app.cli create-admin` crea usuarios con rol Completo; `reset-password`
  sirve para cualquier usuario.

## Recursos de los routers y del servidor

- **Routers:** los recursos se leen de `/system/resource` en cada sondeo y se
  guardan junto al tráfico. El CPU es la carga en el momento del sondeo, no
  un promedio.
- **Servidor:** el backend guarda cada minuto el CPU, la memoria y el disco
  de la máquina, usando `psutil` desde el contenedor (`/proc` y el disco
  donde vive Docker ya describen al host). El CPU es el promedio de ese
  minuto.
- Ambos historiales se purgan con la misma retención que el historial de
  tráfico.

## Backups

El servicio `backup` hace un `pg_dump` completo **todos los días** a la hora
`BACKUP_TIME` (hora local de `TZ`) y lo deja en `./backups/` dentro del
directorio del proyecto, como `pppoe-AAAAMMDD-HHMMSS.dump`. Borra los de más de
`BACKUP_KEEP_DAYS` días. Un `.dump` siempre está completo: mientras se
escribe se llama `.partial`.

- Ver qué hizo: `docker compose logs backup`.
- Backup inmediato: `docker compose run --rm backup now`.
- Si un backup falla, queda `ERROR` en el log y se reintenta al día
  siguiente; el servicio no se detiene.
- La carpeta y los archivos pertenecen a `root` (los crea Docker): se pueden
  leer y copiar con el usuario normal, pero para borrarlos a mano hace falta
  `sudo`.

**Los backups quedan en el mismo servidor.** Copialos a otro equipo, por
ejemplo con un cron en un NAS o en otra máquina:
```bash
rsync -a usuario@servidor:/ruta/al/proyecto/backups/ /destino/pppoe-backups/
```

Guardá también el `.env` (sobre todo `MASTER_ENCRYPTION_KEY`) en un lugar
seguro aparte.

### Restaurar

Reemplaza **todo** el contenido de la base por el del backup. Primero se
vacía el esquema, así no quedan tablas de una versión más nueva:
```bash
docker compose stop backend
docker compose exec -T db psql -U pppoe -d pppoe -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
docker compose exec -T db pg_restore -U pppoe -d pppoe < backups/pppoe-AAAAMMDD-HHMMSS.dump
docker compose start backend
```
(usar el `POSTGRES_USER` y el `POSTGRES_DB` de tu `.env` si no son `pppoe`).

Si el backup es de una versión anterior del sistema, volvé también el código
a esa versión (`git checkout <versión>`) antes de `docker compose start backend`,
o dejá que el backend aplique las migraciones pendientes al arrancar.

## Diagnóstico

- Estado de los servicios: `docker compose ps` (`unhealthy` en `backend`
  suele indicar que no llega a la base).
- Logs en vivo del backend (sondeos, resumen por hora, purga, reseteo):
  `docker compose logs -f backend`. Para más detalle, poner `LOG_LEVEL=DEBUG`
  en `.env` y correr `docker compose up -d`. Los logs de cada servicio rotan
  solos (3 archivos de 10 MB).
- Un router con "último sondeo" viejo en el dashboard no está respondiendo:
  buscar su nombre en los logs del backend y probar la conexión desde
  "Routers".
- Contraseña olvidada:
  `docker compose exec backend python -m app.cli reset-password admin`.
- Después de 5 intentos de login fallidos desde la misma IP en 15 minutos,
  esa IP queda bloqueada hasta que pase la ventana (reiniciar el backend
  también la libera). Los intentos fallidos quedan en el log con usuario e IP.
- **No** agregar `--workers` a uvicorn: el scheduler de sondeos corre dentro
  del backend y con varios procesos el tráfico se contaría varias veces.

## Seguridad

- Usar secretos fuertes y únicos en `.env` (`POSTGRES_PASSWORD`, `JWT_SECRET`,
  `MASTER_ENCRYPTION_KEY`); no reutilizar los valores de ejemplo.
- El sistema está pensado para usarse **solo desde la red interna o una
  VPN** y sirve por HTTP simple. No publicarlo a internet; si alguna vez
  hiciera falta, poner delante un reverse proxy con HTTPS.
- Firewall en el host: exponer únicamente el `HTTP_PORT` y solo hacia la LAN
  o la VPN. Postgres y el backend no publican puertos al host.
- Las contraseñas de API de los routers se guardan cifradas con
  `MASTER_ENCRYPTION_KEY`; las de los usuarios de la web, con bcrypt.

## Arquitectura

Cuatro contenedores con Docker Compose (`docker-compose.yml`):

- `db`: Postgres 16.
- `backend`: FastAPI + APScheduler (sondeo periódico de los routers, resumen
  por hora, purga, reseteo mensual y alertas). Escucha en el puerto 8000
  solo dentro de la red de Compose.
- `frontend`: build estático de React (Vite) servido por nginx, que además
  pasa `/api/*` al backend.
- `backup`: el `pg_dump` diario.

El diseño original y los planes de implementación están en
[`docs/superpowers/`](docs/superpowers/).

## Desarrollo

Requisitos: Python 3.12 y Node 22 (la versión de Vite usada por el frontend
requiere Node >= 20.19 o 22).

Backend:
```bash
docker run -d --name pppoe-dev-db -e POSTGRES_USER=pppoe -e POSTGRES_PASSWORD=pppoe -e POSTGRES_DB=pppoe -p 5432:5432 postgres:16
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```
(`requirements-dev.txt` incluye `requirements.txt` más las herramientas de
test, que no se instalan en la imagen de producción.)
Crear `backend/.env` (los defaults de `app/config.py` ya apuntan a
`postgresql://pppoe:pppoe@localhost:5432/pppoe`, es decir al contenedor de
arriba, así que alcanza con definir `MASTER_ENCRYPTION_KEY` y `JWT_SECRET`):
```bash
echo "MASTER_ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" > .env
echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env
```
```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

Todo texto visible de la interfaz está en `frontend/src/i18n/es.ts` y
`en.ts`, con las mismas claves; el build falla si falta una traducción.

### Tests

Los tests del backend escriben (y borran) datos directamente en la base
indicada por `DATABASE_URL`. Apuntá siempre a la base de desarrollo y
**nunca** a la de producción. Como protección, `backend/tests/conftest.py`
**se niega a correr** salvo que `DATABASE_URL` apunte a
`localhost`/`127.0.0.1`/`::1` **y** a una base llamada `pppoe`, `pppoe_dev` o
`pppoe_test`. Para correrlos contra otra base descartable hay que exportar
explícitamente `ALLOW_DESTRUCTIVE_TESTS=1`.

```bash
cd backend && .venv/bin/pytest -q
cd frontend && npm run lint && npm run build
```

Los tests necesitan `MASTER_ENCRYPTION_KEY` en `backend/.env`; `JWT_SECRET`
lo completa `conftest.py` con un valor solo para tests si no está definido.

## Licencia

Desarrollado por **[MKE Solutions](mailto:info@mkesolutions.net)**.

Este proyecto es software libre bajo la licencia
**GNU Affero General Public License v3.0** (ver [`LICENSE`](LICENSE)). Podés
usarlo, modificarlo y redistribuirlo; si ofrecés una versión modificada a
otros a través de la red, tenés que publicar también su código fuente bajo
la misma licencia.
