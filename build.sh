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
        # DEMO-FIRST-RUN: one ready shop per demo activity (~10 s and ~5 MB each), so a
        # tap on the landing page copies a file instead of seeding while the visitor waits.
        # One activity failing must not stop the deploy: the others are still built, and a
        # missing one is built on demand behind the "بنجهّز نسختك…" page.
        python manage.py prepare_demo_templates --force \
            || echo "WARNING: some demo activity templates failed to build; they will be built on demand." >&2
        # The notes database is a separate free project that can be paused. A paused
        # one must not stop the demo: notes then fail softly (feedback/views.py).
        python manage.py migrate --database feedback --noinput \
            || echo "WARNING: the feedback database is unreachable; the demo runs without saving testers' notes." >&2
        ;;
esac
python manage.py check --deploy --fail-level ERROR
