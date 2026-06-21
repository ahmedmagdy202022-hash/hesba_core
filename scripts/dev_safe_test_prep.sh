#!/usr/bin/env bash
set -euo pipefail

python manage.py check
python manage.py showmigrations
python manage.py migrate --plan
python manage.py makemigrations --check --dry-run
