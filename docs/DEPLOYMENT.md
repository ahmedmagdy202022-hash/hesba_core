# Deploying Hesba (OPS-001)

A single Django app. Production = gunicorn + WhiteNoise (static files) behind any
HTTPS proxy, with PostgreSQL (SQLite works for a single-shop pilot).

## 1. Configure
Copy `.env.example` to `.env` on the server and fill it in. Never commit `.env`.

| Key | Notes |
|---|---|
| `SECRET_KEY` | long random string (`python -c "import secrets; print(secrets.token_urlsafe(50))"`) |
| `DEBUG` | `False` in production |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | your domain |
| `DATABASE_BACKEND` + `POSTGRES_*` | or leave SQLite and set `SQLITE_PATH` |
| `ADMIN_URL` | a private path for the Django admin (superusers only); not `admin/` |
| `BACKUP_DIR`, `BACKUP_KEEP` | where nightly backups go, and how many to keep (default 14) |
| `LOG_LEVEL` | `WARNING` by default; logs go to stdout |
| `SESSION_IDLE_HOURS` | idle sign-out (default 10 h; each request extends it) |
| `LOGIN_MAX_FAILURES`, `LOGIN_MAX_FAILURES_PER_IP`, `LOGIN_LOCK_MINUTES` | sign-in lock after wrong passwords (defaults 5 per user, 20 per address, 15 min) |

## 2. Install and release
```bash
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py createsuperuser        # first owner account, then finish /setup/ in the browser
python manage.py check --deploy
```
Platforms that read a `Procfile` run the `release` line automatically.

## 3. Run
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60 --access-logfile -
```
Health check for the platform / uptime monitor: `GET /healthz/` → `{"status": "ok"}` (503 when the
database does not answer). It needs no login and reveals nothing else.

## 4. Backups (do this on day one)
```bash
# cron, every night at 02:15
15 2 * * * cd /srv/hesba && python manage.py backup_data && python manage.py verify_backup "$(ls -t $BACKUP_DIR/hesba-*.json.gz | head -1)"
```
- `backup_data` writes `hesba-YYYYmmdd-HHMMSS.json.gz` (all business data, natural keys) and, on
  SQLite, a consistent `.sqlite3` copy; it keeps the newest `BACKUP_KEEP` of each.
- `verify_backup <file>` fails (non-zero exit) if the file is unreadable or any table's row count
  differs from the database. Wire the cron mail / alert to that exit status.
- Copy `BACKUP_DIR` off the server (another disk, object storage). A backup on the same disk is not a backup.

### Restore
```bash
# into an EMPTY database
python manage.py migrate --noinput
python manage.py loaddata /var/backups/hesba/hesba-20260926-021500.json.gz
```
On SQLite you can instead stop the app and put the `.sqlite3` copy in place of the database file.
Rehearse a restore on a spare machine once before going live.
