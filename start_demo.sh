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
# What "start over" copies (DEMO-FAST); already built by build.sh on Render.
python manage.py prepare_demo_fresh
# The notes database is a separate free project that can be paused. A paused
# one must not stop the demo: notes then fail softly (feedback/views.py).
python manage.py migrate --database feedback --noinput \
    || echo "WARNING: the feedback database is unreachable; the demo runs without saving testers' notes." >&2
# DEMO-FIRST-RUN: the ready shop of every demo activity. build.sh built them on Render;
# anything still missing is built here in the background, after the server is up, and a
# visitor who asks for one before it is done sees a short "بنجهّز نسختك…" page instead.
# --top-up brings the ones build.sh made up to today: the disk goes back to the build
# image on every wake, so without it "today" would be empty days after the deploy.
# Double-forked, so the job belongs to init rather than to gunicorn (which would reap
# it and log it as a dead worker); its output still goes to the service log.
( (sleep 5 && python manage.py prepare_demo_templates --top-up) & )
# Threads, so one tester's slow page never holds up another's.
exec gunicorn config.wsgi:application --bind 0.0.0.0:"${PORT:-8000}" --workers 2 --threads 4 --worker-class gthread --timeout 90 --access-logfile -
