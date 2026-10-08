#!/usr/bin/env bash
# DEMO-002: the demo shop inside a GitHub Codespace (see .devcontainer/).
# Every start builds a fresh throw-away SQLite shop in /tmp, so each visit
# begins from the activity choice; nothing typed here is kept. Never use
# this for a client's real data.
set -o errexit

export DEMO_MODE=True
export DEBUG=True
export SQLITE_PATH=/tmp/hesba_demo.sqlite3
rm -f "$SQLITE_PATH"

python manage.py migrate --noinput -v0
python manage.py prepare_demo
echo
echo "Hesba demo: open the 'Ports' tab, port 8000 (it opens by itself). Sign in as owner / Demo-pass-1."
echo
exec python manage.py runserver 0.0.0.0:8000
