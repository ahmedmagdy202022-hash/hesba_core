#!/usr/bin/env bash
# DEPLOY-002: build step for Render (and any host that runs one build script).
# Migrations run here because the free plan has no separate pre-deploy step;
# the build environment already has DATABASE_URL.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py check --deploy --fail-level ERROR
