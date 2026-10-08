#!/usr/bin/env bash
# DEMO-002: the demo shop inside a GitHub Codespace (see .devcontainer/).
# The first start builds a fresh throw-away SQLite shop in /tmp; nothing typed
# here is kept. Never use this for a client's real data.
#
# Codespaces runs this again every time the editor (re)attaches. If the demo
# is already serving, leave its database alone: wiping it under the running
# server signs everyone out mid-setup (DEMO-FLOW). To start over, use
# "ابدأ من الأول" on the login page instead.
set -o errexit

PORT=8000
export DEMO_MODE=True
export DEBUG=True
export SQLITE_PATH=/tmp/hesba_demo.sqlite3
# DEMO-SANDBOX: every visitor gets a private copy of this demo.
export DEMO_SANDBOXES="${DEMO_SANDBOXES:-True}"
export DEMO_SANDBOX_DIR="${DEMO_SANDBOX_DIR:-/tmp/hesba_sandboxes}"
export FEEDBACK_SQLITE_PATH="${FEEDBACK_SQLITE_PATH:-/tmp/hesba_feedback.sqlite3}"

if python - "$PORT" <<'PY'
import socket, sys
with socket.socket() as s:
    s.settimeout(1)
    sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
then
    echo "Hesba demo is already running on port $PORT (open the Ports tab). Sign in as owner / Demo-pass-1."
    exit 0
fi

rm -f "$SQLITE_PATH"
python manage.py migrate --noinput -v0
python manage.py prepare_demo
python manage.py prepare_demo_fresh --force -v0
python manage.py migrate --database feedback --noinput -v0
echo
echo "Hesba demo: open the 'Ports' tab, port $PORT (it opens by itself). Sign in as owner / Demo-pass-1."
echo
exec python manage.py runserver 0.0.0.0:$PORT
