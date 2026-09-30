#!/usr/bin/env bash
# DEMO-001: start command for a throw-away showcase on Render's free plan.
# The free instance has no persistent disk, so every start builds a fresh
# SQLite demo shop; nothing a visitor types survives a restart. Never point
# this at a client's database (DEMO_MODE refuses to seed a filled one anyway).
set -o errexit

python manage.py migrate --noinput
python manage.py prepare_demo
exec gunicorn config.wsgi:application --bind 0.0.0.0:"${PORT:-8000}" --workers 2 --timeout 60 --access-logfile -
