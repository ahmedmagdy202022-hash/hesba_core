#!/usr/bin/env bash
# DEMO-001: start command for a showcase on Render (docs/DEMO_ON_RENDER.md).
# Every start builds a fresh SQLite demo shop, the template. With
# DEMO_SANDBOXES (on by default here) each visitor works in a private copy of
# it, so testers never see or reset each other's work (DEMO-SANDBOX). Testers'
# notes go to the feedback database; set FEEDBACK_DATABASE_URL to keep them
# across restarts. Never point this at a client's database (DEMO_MODE refuses
# to seed a filled one anyway).
set -o errexit

export DEMO_SANDBOXES="${DEMO_SANDBOXES:-True}"

python manage.py migrate --noinput
python manage.py prepare_demo
python manage.py migrate --database feedback --noinput
# Threads, so one tester's slow page never holds up another's.
exec gunicorn config.wsgi:application --bind 0.0.0.0:"${PORT:-8000}" --workers 2 --threads 4 --worker-class gthread --timeout 90 --access-logfile -
