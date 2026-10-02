# PPPoE Monitor

[Español](README.md) · **English**

Web monitor for ISPs running **MikroTik** routers: it shows which PPPoE
clients are online, how much traffic they are using right now and how much
they have used this month, so you can spot **heavy users**.

- **Heavy download** may mean the service is being resold (a client sharing
  their connection with neighbors).
- **Heavy upload** may mean an infected device, typically a TV box that is
  part of a botnet.

It reads each PPPoE session's counters through the RouterOS REST API, keeps
the history in Postgres, and alerts you by email or Telegram when a client
goes over the monthly threshold.

> Installing with an AI assistant? Ask it to read [`AGENTS.md`](AGENTS.md):
> it has the step-by-step install, how to verify it and what not to touch
> (written in Spanish; most assistants handle it fine).

![Dashboard: online clients, current traffic, 24 h total traffic and the status of each router](docs/screenshots/dashboard.png)

*(The screenshots show the Spanish interface, except the dark one.)*

## What it does not do

- **It is not DPI or NetFlow:** it doesn't know which sites a client visits
  or which protocols they use; only how many bytes go through their session.
- **It doesn't cut or throttle service:** it only reads. It never writes to
  the routers.
- **It only sees PPPoE clients:** clients on DHCP, Hotspot or static IPs
  don't show up.
- **RouterOS v6 is not supported:** it uses the REST API, available since
  RouterOS 7.1.

## Features

- **Dashboard:** online clients, current and monthly traffic, status of each
  router and a top-usage ranking, with 24 h, 7, 30 and 90 day charts.
  Hovering over a router shows what it measured on every poll of the last
  24 h.
- **Per-router page:** model, RouterOS version, uptime, CPU, memory and
  disk; its traffic, its online clients and the table of its clients.
- **Clients:** search by username or IP, filters and sorting. A client's
  detail page shows download and upload (current and monthly) and its
  charts.
- **Alerts:** separate monthly download and upload thresholds, global or per
  client. Notifications by email or Telegram and a section with the alert
  history.
- **Users with roles:** Full (sees and changes everything) and Read-only.
- **Server:** CPU, memory and disk of the machine running the monitor.
- **Spanish and English interface**, with light and dark themes.
- **Automatic daily backups** of the database.

| A client with heavy upload (possibly an infected TV box) | A client with heavy download, in English and dark theme |
|---|---|
| ![Client detail: 163 GB uploaded this month versus 11 GB downloaded](docs/screenshots/client-upload.png) | ![Client detail in English and dark theme: 324 GB downloaded in a few hours](docs/screenshots/client-dark-en.png) |

![Router page: CPU, memory, disk, online clients, traffic and resources](docs/screenshots/router.png)

## Compatibility

- **Routers:** MikroTik with **RouterOS 7.1 or later**. Tested in production
  with CCR1036, CCR2004, CCR2116, CCR2216 and RB5009, on RouterOS 7.23 and
  7.24.
- **Server:** any Linux with Docker. Tested on Ubuntu Server 24.04 and 26.04.
- **Resources:** 2 vCPU and 3 GB of RAM are enough for about 2000 clients
  across 10 routers.

## Router requirements

- The web service enabled under **IP > Services**: `www-ssl`, or `www` if
  you don't use TLS (it can be on another port, e.g. 8090). It's a good idea
  to limit *Available From* to the monitor's IP.
- A user in a group with the **`read`** and **`rest-api`** policies. It
  doesn't need write permissions.
- On every poll it reads:
  - `/rest/interface/pppoe-server`: the active sessions;
  - `/rest/interface`: each session's byte counters;
  - `/rest/ppp/active`: each client's IP;
  - `/rest/system/resource`: CPU, memory, disk, uptime, version and model.
- If reading `/ppp/active` or `/system/resource` fails, the poll goes on:
  only that piece of data is missing, which shows as a gap in the charts.

## Installation (Ubuntu Server)

1. Install Docker and Docker Compose:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
   (log out and back in so the `docker` group takes effect).
2. Clone the repo:
   ```bash
   git clone https://github.com/marioclep/pppoe-monitor.git ~/pppoe-monitor
   cd ~/pppoe-monitor
   ```
3. Copy `.env.example` to `.env` and fill it in:
   - `POSTGRES_PASSWORD`: a strong database password (also update it in
     `DATABASE_URL`).
   - `JWT_SECRET` (**required**): a random string of at least 32 characters
     (`openssl rand -hex 32`). The backend **refuses to start** if it is
     empty, shorter, or still the example placeholder.
   - `MASTER_ENCRYPTION_KEY` (**required**): generate it with
     `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
     (or, without Python on the host,
     `docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"`).
     It encrypts the routers' API passwords stored in the database. The
     backend refuses to start unless it is a valid Fernet key. **Keep a copy
     somewhere else**: without it, a backup can't recover the routers'
     passwords.
   - `TZ` (optional, default `America/Argentina/Cordoba`): IANA time zone
     used for the monthly reset (see "Monthly reset").
   - `TOKEN_EXPIRE_MINUTES` (optional, default 480).
   - `LOG_LEVEL` (optional, default `INFO`): backend log level.
   - `HTTP_PORT` (optional, default `80`): host port where the web UI is
     published.
   - `BACKUP_TIME` / `BACKUP_KEEP_DAYS` (optional, default `03:00` / `14`):
     local time of the daily backup and how many days to keep (see
     "Backups").

   `docker compose` stops with a clear message if a required variable is
   missing (`POSTGRES_*`, `DATABASE_URL`, `JWT_SECRET`,
   `MASTER_ENCRYPTION_KEY`).
4. Start the stack:
   ```bash
   docker compose up -d --build
   ```
5. Check that everything is healthy (the first time it can take up to a
   minute while the migrations run):
   ```bash
   docker compose ps
   ```
   `db`, `backend` and `frontend` must show `(healthy)` and `backup`, `Up`.
6. Create the first user, with the Full role (it asks for the password
   twice, at least 10 characters). Other users are created from the web UI,
   under "Users":
   ```bash
   docker compose exec backend python -m app.cli create-admin admin
   ```
7. Open `http://<server-ip>/` (or `:<HTTP_PORT>`), switch the language under
   Configuración → Idioma if you like, and add the routers under "Routers"
   with their REST API credentials. The "Test connection" button confirms
   that the user and the web service are set up correctly.

All services use `restart: unless-stopped`: after a server reboot they come
back on their own, as long as the Docker service starts with the system (the
official installer sets it up that way).

## Updating

```bash
cd ~/pppoe-monitor
docker compose run --rm backup now   # backup before touching anything
git pull
docker compose up -d --build
docker compose ps                     # everything must be (healthy) again
```

Database migrations run on their own when the backend starts. If only the
frontend changed, polling isn't interrupted; if the backend changed, it
stops for about a minute while it restarts. Browsers pick up the new version
on their own (`index.html` is served without caching). What changed in each
version is in [`CHANGELOG.md`](CHANGELOG.md) (in Spanish).

## Settings

From the "Settings" page of the web UI:

- **Language** (`es` or `en`, default `es`): for the whole interface, for
  every user, and for alert notifications and "Test connection" messages.
  The login page uses the last language seen in that browser (or the
  browser's language).
- **Installation name** (optional, up to 40 characters): shown in the
  Dashboard title ("Dashboard ACME") and in the browser tab ("PPPoE Monitor ·
  ACME").
- **Polling interval** in seconds (default 300, minimum 60). Applied live,
  without restarting the backend.
- **Monthly reset day** (default 1, range 1–28 so it exists in every
  month). See "Monthly reset".
- **History retention** in days (default 90): how long each client's hourly
  summary is kept.
- **Detail retention** in days (default 7, no longer than the history
  retention): how long each poll's samples are kept. After that only the
  hourly summary remains. Charts within that window use every poll; longer
  ones use one point per hour with the average and the peak.
- **SMTP and Telegram** for alert notifications. The SMTP password and the
  Telegram token are not shown once saved and are only overwritten when a
  new value is sent. Saving an optional field empty clears it.
- **Global alert thresholds** for monthly download and upload. A client's
  own thresholds are set from their detail page and replace the global one
  for that direction.

## How traffic is counted

- The first time a PPPoE session is seen (new client, client reconnecting,
  or router just added or re-enabled), its bytes are only added to the
  monthly total if the session **started after that router's previous
  successful poll**. Otherwise the current counters are taken as a baseline
  and only traffic from then on is counted. So on a router's first poll
  every session that was already open starts at 0: the first month slightly
  undercounts clients who were already online, instead of inflating it with
  the session's whole history.
- Each PPPoE session is tracked separately. If a user has several sessions
  at once, their traffic is the sum of all of them (including
  `<pppoe-USER-1>` interfaces).
- If a session's interface can't be found, that poll leaves its traffic
  untouched and logs a warning with the router and the user.
- "Current" traffic is what was measured on each online client's last poll;
  an offline client shows 0.
- Disabling a router stops polling it, marks its clients as offline and
  removes it from the dashboard; its history is kept. **Deleting** a router
  also deletes all of its history (clients, totals, samples and alerts).
- "Download" is what the client receives (what the router transmits on the
  PPPoE interface) and "upload", what the client sends.
- Sizes are shown in multiples of 1024 (1 GB = 1024³ bytes), like Winbox.

## Monthly reset

- Each client's total is closed and starts again from zero once a month, on
  the configured day, around **local** midnight in the `TZ` time zone.
- The check runs **every hour** (at minute 5) and **once when the backend
  starts**. It is "due-based": if this month's reset (or last month's, if the
  day hasn't come yet) hasn't happened, it happens on that check. So if the
  server was down on the reset day, the reset is caught up on the next check
  instead of skipping a month.
- The date of the last reset is stored in the `settings` table (key
  `last_reset_date`): running it again for the same date does nothing.
- If the reset day is changed to a day that has already passed this month
  (and there was no reset for that date yet), the reset happens on the next
  check.

## Alerts

- Each direction is checked separately: the month's download against the
  download threshold and the upload against the upload one (they are not
  added together).
- There is at most one global threshold per direction and one per client
  and direction. A client's threshold takes priority over the global one.
- You are notified **once per direction and per period**, the first time
  usage goes over the threshold.
- The notification is sent **only** through the channel chosen on the
  threshold (`email` or `telegram`; if that channel isn't configured, nothing
  is sent). It names the PPPoE user, the router, the usage and the threshold.
- Every alert is listed in the **Alerts** section of the web UI (the latest
  200), even with no channel configured, and is kept even if the threshold
  is later deleted.

## Users and roles

- **Full:** sees and changes everything (routers, settings, alert
  thresholds) and manages users.
- **Read-only:** sees everything, including the Routers and Settings pages,
  but without buttons to change anything. The backend answers 403 to any
  change it attempts.
- Role changes and deletions take effect immediately. Changing a password
  doesn't close sessions that were already open.
- Nobody can delete themselves, and there is always at least one Full user.
- `app.cli create-admin` creates Full users; `reset-password` works for any
  user.

## Router and server resources

- **Routers:** resources are read from `/system/resource` on every poll and
  stored alongside the traffic. CPU is the load at the moment of the poll,
  not an average.
- **Server:** every minute the backend stores the machine's CPU, memory and
  disk, using `psutil` from the container (`/proc` and the disk Docker lives
  on already describe the host). CPU is that minute's average.
- Both histories are purged with the same retention as the traffic history.

## Backups

The `backup` service runs a full `pg_dump` **every day** at `BACKUP_TIME`
(local time in `TZ`) and leaves it in `./backups/` inside the project
directory, as `pppoe-YYYYMMDD-HHMMSS.dump`. Dumps older than
`BACKUP_KEEP_DAYS` days are deleted. A `.dump` is always complete: while it is
being written it is named `.partial`.

- See what it did: `docker compose logs backup`.
- Back up right now: `docker compose run --rm backup now`.
- If a backup fails, `ERROR` is logged and it is retried the next day; the
  service keeps running.
- The folder and its files belong to `root` (Docker creates them): your
  regular user can read and copy them, but deleting them by hand needs
  `sudo`.

**Backups stay on the same server.** Copy them to another machine, for
example with a cron job on a NAS or another host:
```bash
rsync -a user@server:/path/to/project/backups/ /destination/pppoe-backups/
```

Also keep the `.env` (above all `MASTER_ENCRYPTION_KEY`) somewhere safe.

### Restoring

This replaces **all** the database contents with the backup's. The schema is
emptied first, so no tables from a newer version are left behind:
```bash
docker compose stop backend
docker compose exec -T db psql -U pppoe -d pppoe -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
docker compose exec -T db pg_restore -U pppoe -d pppoe < backups/pppoe-YYYYMMDD-HHMMSS.dump
docker compose start backend
```
(use your `.env`'s `POSTGRES_USER` and `POSTGRES_DB` if they aren't `pppoe`).

If the backup comes from an older version of the system, also move the code
back to that version (`git checkout <version>`) before
`docker compose start backend`, or let the backend apply the pending
migrations when it starts.

## Troubleshooting

- Service status: `docker compose ps` (`unhealthy` on `backend` usually
  means it can't reach the database).
- Live backend logs (polls, hourly summary, purge, reset):
  `docker compose logs -f backend`. For more detail, set `LOG_LEVEL=DEBUG` in
  `.env` and run `docker compose up -d`. Each service's logs rotate on their
  own (3 files of 10 MB).
- A router whose "last poll" is old on the dashboard isn't answering: look
  for its name in the backend logs and test the connection from "Routers".
- Forgotten password:
  `docker compose exec backend python -m app.cli reset-password admin`.
- After 5 failed logins from the same IP within 15 minutes, that IP is
  blocked until the window passes (restarting the backend also clears it).
  Failed attempts are logged with the username and IP.
- **Don't** add `--workers` to uvicorn: the polling scheduler runs inside the
  backend, and with several processes traffic would be counted several
  times.

## Security

- Use strong, unique secrets in `.env` (`POSTGRES_PASSWORD`, `JWT_SECRET`,
  `MASTER_ENCRYPTION_KEY`); don't reuse the example values.
- The system is meant to be used **only from the internal network or a
  VPN** and serves plain HTTP. Don't expose it to the internet; if you ever
  need to, put a reverse proxy with HTTPS in front of it.
- Host firewall: expose only `HTTP_PORT`, and only to the LAN or the VPN.
  Postgres and the backend don't publish any ports to the host.
- The routers' API passwords are stored encrypted with
  `MASTER_ENCRYPTION_KEY`; web users' passwords, with bcrypt.

## Architecture

Four containers with Docker Compose (`docker-compose.yml`):

- `db`: Postgres 16.
- `backend`: FastAPI + APScheduler (router polling, hourly summary, purge,
  monthly reset and alerts). Listens on port 8000 only inside the Compose
  network.
- `frontend`: static React (Vite) build served by nginx, which also forwards
  `/api/*` to the backend.
- `backup`: the daily `pg_dump`.

The original design and implementation plans (in Spanish) are in
[`docs/superpowers/`](docs/superpowers/).

## Development

Requirements: Python 3.12 and Node 22 (the Vite version used by the frontend
needs Node >= 20.19 or 22).

Backend:
```bash
docker run -d --name pppoe-dev-db -e POSTGRES_USER=pppoe -e POSTGRES_PASSWORD=pppoe -e POSTGRES_DB=pppoe -p 5432:5432 postgres:16
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```
(`requirements-dev.txt` includes `requirements.txt` plus the test tools,
which are not installed in the production image.)
Create `backend/.env` (the defaults in `app/config.py` already point to
`postgresql://pppoe:pppoe@localhost:5432/pppoe`, i.e. the container above, so
you only need `MASTER_ENCRYPTION_KEY` and `JWT_SECRET`):
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

Every visible text of the interface lives in `frontend/src/i18n/es.ts` and
`en.ts`, with the same keys; the build fails if a translation is missing.

### Tests

The backend tests write (and delete) data directly in the database given by
`DATABASE_URL`. Always point it at the development database and **never** at
production. As a safeguard, `backend/tests/conftest.py` **refuses to run**
unless `DATABASE_URL` points to `localhost`/`127.0.0.1`/`::1` **and** to a
database named `pppoe`, `pppoe_dev` or `pppoe_test`. To run them against
another throwaway database you must explicitly export
`ALLOW_DESTRUCTIVE_TESTS=1`.

```bash
cd backend && .venv/bin/pytest -q
cd frontend && npm run lint && npm run build
```

The tests need `MASTER_ENCRYPTION_KEY` in `backend/.env`; `conftest.py` fills
in a test-only `JWT_SECRET` if none is set.

## License

Developed by **[MKE Solutions](mailto:info@mkesolutions.net)**.

This project is free software under the **GNU Affero General Public License
v3.0** (see [`LICENSE`](LICENSE)). You may use, modify and redistribute it;
if you offer a modified version to others over a network, you must also
publish its source code under the same license.
