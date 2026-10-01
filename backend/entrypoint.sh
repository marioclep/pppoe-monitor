#!/bin/sh
set -e

alembic upgrade head
# One process on purpose: the APScheduler jobs (polling, rollup, purge,
# reset) run inside the app. With --workers N every poll would run N times
# and traffic would be counted N times.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
