#!/bin/bash
# Start the local demo (after build.sh). Stop with: pkill -f "gunicorn config.wsgi --bind 127.0.0.1:8099"
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/env.sh"
cd "$HERE/../retail-src"
exec /home/user/venv/bin/python -m gunicorn config.wsgi --bind 127.0.0.1:$PORT --workers 2 --timeout 120 > "$HERE/env/server.log" 2>&1
