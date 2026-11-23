web: gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --timeout 60 --access-logfile -
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
