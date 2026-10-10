from pathlib import Path

from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = config("DEBUG", default=True, cast=bool)
_DEVELOPMENT_SECRET_KEY = "dev-only-change-before-production"
SECRET_KEY = config("SECRET_KEY", default=_DEVELOPMENT_SECRET_KEY)
if not DEBUG and SECRET_KEY == _DEVELOPMENT_SECRET_KEY:
    raise ImproperlyConfigured("Set a strong SECRET_KEY whenever DEBUG is false.")

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,.app.github.dev",
    cast=Csv(),
)
# DEMO-001: Render publishes the service's own hostname; accept it without a manual setting.
RENDER_EXTERNAL_HOSTNAME = config("RENDER_EXTERNAL_HOSTNAME", default="").strip()
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "accounts",
    "permissions",
    "settings_core",
    "master_data",
    "inventory",
    "purchases",
    "sales",
    "cashboxes",
    "reports",
    "closing",
    "audit",
    "imports",
    "barcode",
    "expenses",
    "pricing",
    "taxes",
    "einvoice",
    "units",
    "batches",
    "variants",
    "serials",
    "installments",
    "fixed_assets",
    "shifts",
    "parties",
    "search",
    "offline_pos",
    "staff",
    "appointments",
    "restaurant",
    "projects",
    "manufacturing",
    "printing",
    "entities",
    "ledger",
    "medical",
    "feedback",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # OPS-001: serves collected static files in production without a separate web server.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # DEMO-SANDBOX: on a showcase install, each visitor works in a private copy
    # of the demo database. A no-op everywhere else; must run before sessions.
    "config.demo_sandbox.DemoSandboxMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "settings_core.module_gate.ModuleGateMiddleware",
    # ENT-002: which entity the signed-in user is working in (menu, words, books).
    "entities.current.CurrentEntityMiddleware",
    # DEMO-TRACK: on a showcase, note which pages each visitor opens. A no-op elsewhere.
    "feedback.tracking.DemoVisitMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.NoStoreHtmlMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.demo.demo_context",
            ],
        },
    },
]

DATABASE_BACKEND = config("DATABASE_BACKEND", default="sqlite").strip().lower()
# DEPLOY-001: one connection string, pasted as the provider shows it
# (e.g. Supabase -> Connect -> Session pooler). It wins over the POSTGRES_* parts.
DATABASE_URL = config("DATABASE_URL", default="").strip()
if DATABASE_URL:
    from urllib.parse import parse_qs, unquote, urlsplit

    _url = urlsplit(DATABASE_URL)
    if _url.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must start with postgresql://")
    _query = {key: values[-1] for key, values in parse_qs(_url.query).items()}
    _local = (_url.hostname or "") in {"localhost", "127.0.0.1", "::1"}
    DATABASE_BACKEND = "postgresql"
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(_url.path.lstrip("/")) or "postgres",
            "USER": unquote(_url.username or ""),
            "PASSWORD": unquote(_url.password or ""),
            "HOST": _url.hostname or "",
            "PORT": str(_url.port or 5432),
            "CONN_MAX_AGE": config("DATABASE_CONN_MAX_AGE", default=60, cast=int),
            "CONN_HEALTH_CHECKS": True,
            # A remote database is always reached over TLS unless the URL says otherwise.
            "OPTIONS": {"sslmode": _query.get("sslmode", "prefer" if _local else "require")},
        }
    }
elif DATABASE_BACKEND == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": config("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
        }
    }
elif DATABASE_BACKEND in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("POSTGRES_DB"),
            "USER": config("POSTGRES_USER"),
            "PASSWORD": config("POSTGRES_PASSWORD"),
            "HOST": config("POSTGRES_HOST", default="localhost"),
            "PORT": config("POSTGRES_PORT", default="5432"),
            "CONN_MAX_AGE": config("DATABASE_CONN_MAX_AGE", default=60, cast=int),
        }
    }
    postgres_sslmode = config("POSTGRES_SSLMODE", default="").strip()
    if postgres_sslmode:
        DATABASES["default"]["OPTIONS"] = {"sslmode": postgres_sslmode}
else:
    raise ImproperlyConfigured(
        "DATABASE_BACKEND must be either 'sqlite' or 'postgresql'."
    )

# DEMO-FEEDBACK: testers' notes live outside every demo copy, in their own
# database. FEEDBACK_DATABASE_URL (PostgreSQL, e.g. a free Supabase project)
# keeps them across restarts; otherwise a local SQLite file.
FEEDBACK_DATABASE_URL = config("FEEDBACK_DATABASE_URL", default="").strip()
if FEEDBACK_DATABASE_URL:
    from urllib.parse import parse_qs as _fb_qs, unquote as _fb_unquote, urlsplit as _fb_split

    _fb = _fb_split(FEEDBACK_DATABASE_URL)
    if _fb.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("FEEDBACK_DATABASE_URL must start with postgresql://")
    _fb_query = {key: values[-1] for key, values in _fb_qs(_fb.query).items()}
    DATABASES["feedback"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _fb_unquote(_fb.path.lstrip("/")) or "postgres",
        "USER": _fb_unquote(_fb.username or ""),
        "PASSWORD": _fb_unquote(_fb.password or ""),
        "HOST": _fb.hostname or "",
        "PORT": str(_fb.port or 5432),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"sslmode": _fb_query.get("sslmode", "require")},
    }
else:
    DATABASES["feedback"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": config("FEEDBACK_SQLITE_PATH", default=str(BASE_DIR / "feedback.sqlite3")),
    }
DATABASE_ROUTERS = ["feedback.router.FeedbackRouter"]

# USERS-001: passwords set from the users screen and the password page are
# checked here; there were no rules before.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ar"
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / config("STATIC_ROOT", default="staticfiles")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# OPS-001: hashed, compressed static files once DEBUG is off (run collectstatic
# on deploy). Development and the test suite keep the plain storage.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# OPS-001: nightly backups (manage.py backup_data) land here; keep the last N.
BACKUP_DIR = Path(config("BACKUP_DIR", default=str(BASE_DIR / "backups")))
BACKUP_KEEP = config("BACKUP_KEEP", default=14, cast=int)
# POS-003: an offline sale older than this is refused at sync (enter it by hand).
POS_OFFLINE_MAX_DAYS = config("POS_OFFLINE_MAX_DAYS", default=7, cast=int)
# USAGE-002: the client's database plan limit (Supabase free plan: 500 MB).
DATABASE_SIZE_LIMIT_MB = config("DATABASE_SIZE_LIMIT_MB", default=500, cast=int)
# BACKUP-003: the nightly encrypted backup goes to the client's own Google Drive.
# The OAuth client belongs to the Hesba app (Google Cloud console, "Web application",
# publishing status "In production", scope drive.file only); see docs/BACKUP_DRIVE_SETUP.md.
GOOGLE_OAUTH_CLIENT_ID = config("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = config("GOOGLE_OAUTH_CLIENT_SECRET", default="")
BACKUP_DRIVE_KEEP = config("BACKUP_DRIVE_KEEP", default=30, cast=int)
# Secret for /ops/nightly/, called once a day by an external scheduler on hosts without cron.
# Empty disables the endpoint.
NIGHTLY_TOKEN = config("NIGHTLY_TOKEN", default="")

# DIGEST-002: the daily follow-up email. Any SMTP provider works (Gmail with an
# app password, Brevo, Resend, Zoho...). Empty EMAIL_HOST means "not set up":
# the settings screen says so and nothing is sent.
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=False, cast=bool)
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "hesba@localhost")
# The address the app is reached at, for links inside emails (e.g. https://shop.onrender.com).
PUBLIC_BASE_URL = config("PUBLIC_BASE_URL", default="").rstrip("/")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # The level sits on the handler: records propagated up from "django.*"
    # skip the root logger's own level check.
    "handlers": {"console": {"class": "logging.StreamHandler", "level": config("LOG_LEVEL", default="ERROR" if DEBUG else "WARNING")}},
    "root": {"handlers": ["console"], "level": "DEBUG"},
}
# R2: demo feedback notes are logged as a backup copy (Render -> Logs); they
# must reach the console whatever LOG_LEVEL the rest of the app uses.
LOGGING["handlers"]["feedback_console"] = {"class": "logging.StreamHandler", "level": "INFO"}
LOGGING["loggers"] = {"hesba.feedback": {"handlers": ["feedback_console"], "level": "INFO", "propagate": False}}


LOGIN_URL = "/login/"

# ADMIN-001: Django Admin is a developer tool. It is open to superusers only
# (see config/urls.py), and its address can be moved off the default.
ADMIN_URL = config("ADMIN_URL", default="admin/")
if not ADMIN_URL.endswith("/"):
    ADMIN_URL += "/"
# /start/ decides between setup and the dashboard. Pointing straight at either
# one is what used to trap a finished installation on its own first-run screen.
LOGIN_REDIRECT_URL = "/start/"
LOGOUT_REDIRECT_URL = "/login/"
# A stale token (usually a tab rendered before the last login) gets a readable
# Arabic/English page instead of Django's raw 403.
CSRF_FAILURE_VIEW = "accounts.views.csrf_failure"


CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="https://*.app.github.dev,https://localhost:8000,http://localhost:8000,https://localhost:8010,http://localhost:8010",
    cast=Csv(),
)
if RENDER_EXTERNAL_HOSTNAME and f"https://{RENDER_EXTERNAL_HOSTNAME}" not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# DEMO-001: a throw-away showcase install. The start script fills an empty
# database with sample business and the login page shows the demo logins.
# Never set this on a client's database.
DEMO_MODE = config("DEMO_MODE", default=False, cast=bool)
DEMO_PASSWORD = config("DEMO_PASSWORD", default="Demo-pass-1")
# DEMO-SANDBOX: each visitor of a showcase gets a private copy of the demo
# database (SQLite only), so testers never see or reset each other's work.
DEMO_SANDBOXES = config("DEMO_SANDBOXES", default=False, cast=bool)
DEMO_SANDBOX_DIR = config("DEMO_SANDBOX_DIR", default="")
DEMO_SANDBOX_TTL_DAYS = config("DEMO_SANDBOX_TTL_DAYS", default=10, cast=int)
DEMO_SANDBOX_MAX = config("DEMO_SANDBOX_MAX", default=200, cast=int)
# DEMO-FEEDBACK: the inbox of testers' notes opens only with this key.
FEEDBACK_VIEW_TOKEN = config("FEEDBACK_VIEW_TOKEN", default="")
FEEDBACK_NOTIFY_EMAIL = config("FEEDBACK_NOTIFY_EMAIL", default="")

# Secure defaults activate automatically in production and remain independently
# configurable for platforms that terminate TLS at a trusted reverse proxy.
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=not DEBUG, cast=bool)
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=not DEBUG, cast=bool)
# SEC-001: a session left idle for SESSION_IDLE_HOURS signs out; every request
# pushes the expiry forward, so an active cashier is never thrown out mid-shift.
SESSION_COOKIE_AGE = config("SESSION_IDLE_HOURS", default=10, cast=int) * 3600
SESSION_SAVE_EVERY_REQUEST = True
LOGIN_MAX_FAILURES = config("LOGIN_MAX_FAILURES", default=5, cast=int)
LOGIN_MAX_FAILURES_PER_IP = config("LOGIN_MAX_FAILURES_PER_IP", default=20, cast=int)
LOGIN_LOCK_MINUTES = config("LOGIN_LOCK_MINUTES", default=15, cast=int)
# SEC-002: how long, in seconds, the password step waits for the authenticator code.
TWO_FACTOR_PENDING_SECONDS = config("TWO_FACTOR_PENDING_SECONDS", default=200, cast=int)
TWO_FACTOR_ISSUER = "Hesba"
# SHARE-001: how long a shared document link keeps working.
SHARE_LINK_DAYS = config("SHARE_LINK_DAYS", default=60, cast=int)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=not DEBUG, cast=bool)
SECURE_HSTS_SECONDS = config(
    "SECURE_HSTS_SECONDS", default=0 if DEBUG else 31536000, cast=int
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=not DEBUG, cast=bool
)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)
if config("TRUST_PROXY_SSL_HEADER", default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
