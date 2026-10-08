#!/usr/bin/env bash
# DEPLOY-002: build step for Render (and any host that runs one build script).
# Migrations run here because the free plan has no separate pre-deploy step;
# the build environment already has DATABASE_URL.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
# DEMO-FAST: a showcase builds its demo here, where there is CPU to spare, so
# waking the small server only tops up today's trade (start_demo.sh).
case "$(echo "${DEMO_MODE:-}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|on)
        python manage.py prepare_demo
        python manage.py prepare_demo_fresh --force
        python manage.py migrate --database feedback --noinput
        ;;
esac
python manage.py check --deploy --fail-level ERROR
