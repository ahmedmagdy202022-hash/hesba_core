"""USAGE-002: how much of the database plan is used, measured, not guessed.

The client's database has a size limit (the Supabase free plan: 500 MB,
``DATABASE_SIZE_LIMIT_MB``). This reads the real size from the database
itself, turns it into the same Green / Yellow / Orange / Red levels the
dashboard already shows, estimates when the limit would be reached from the
pace so far, and lists the biggest tables so the owner sees *what* grows.

The only cleanup offered removes throw-away rows (expired sessions, old
failed sign-in attempts). Accounting records, ledgers and the audit trail are
never deleted: an old month stays readable forever; when space runs short the
answer is a bigger plan, not fewer books.
"""

from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import UsageStatusLevel


MB = 1024 * 1024
# Share of the limit at which each level starts.
LEVELS = ((Decimal("0.90"), UsageStatusLevel.RED), (Decimal("0.75"), UsageStatusLevel.ORANGE), (Decimal("0.50"), UsageStatusLevel.YELLOW))
LEVEL_ORDER = [UsageStatusLevel.GREEN, UsageStatusLevel.YELLOW, UsageStatusLevel.ORANGE, UsageStatusLevel.RED]


def limit_bytes():
    return int(getattr(settings, "DATABASE_SIZE_LIMIT_MB", 500)) * MB


def database_size_bytes():
    """The database's real size on disk, or None if this engine cannot say."""

    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute("select pg_database_size(current_database())")
            return int(cursor.fetchone()[0])
        if connection.vendor == "sqlite":
            cursor.execute("pragma page_count")
            pages = cursor.fetchone()[0]
            cursor.execute("pragma page_size")
            return int(pages) * int(cursor.fetchone()[0])
    return None


def biggest_tables(limit=6):
    """[(model verbose name, bytes)] for the largest Hesba tables (PostgreSQL only)."""

    if connection.vendor != "postgresql":
        return []
    names = {model._meta.db_table: str(model._meta.verbose_name_plural) for model in apps.get_models()}
    with connection.cursor() as cursor:
        cursor.execute(
            "select c.relname, pg_total_relation_size(c.oid) from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where c.relkind = 'r' and n.nspname = current_schema() order by 2 desc limit %s",
            [limit],
        )
        return [(names.get(table, table), int(size)) for table, size in cursor.fetchall()]


def level_for(used, limit):
    if not used or not limit:
        return UsageStatusLevel.GREEN
    share = Decimal(used) / Decimal(limit)
    for threshold, level in LEVELS:
        if share >= threshold:
            return level
    return UsageStatusLevel.GREEN


def worse(*levels):
    return max(levels, key=LEVEL_ORDER.index)


def _first_business_day():
    from cashboxes.models import CashboxMovement
    from sales.models import SalesInvoice

    dates = [d for d in (SalesInvoice.objects.order_by("invoice_date").values_list("invoice_date", flat=True).first(),
                         CashboxMovement.objects.order_by("movement_date").values_list("movement_date", flat=True).first()) if d]
    return min(dates) if dates else None


def months_left(used, limit, today=None):
    """Months until the limit at the pace so far; None when there is too little history to say."""

    today = today or timezone.localdate()
    first = _first_business_day()
    if not used or not first or used >= limit:
        return 0 if used and used >= limit else None
    months_in_use = Decimal((today - first).days) / Decimal("30.4")
    if months_in_use < 1:
        return None  # a first month says nothing about the pace
    per_month = Decimal(used) / months_in_use
    return int((Decimal(limit - used) / per_month).to_integral_value()) if per_month > 0 else None


def status():
    used, limit = database_size_bytes(), limit_bytes()
    percent = int(Decimal(used) * 100 / Decimal(limit)) if used is not None and limit else None
    return {
        "used": used, "limit": limit, "percent": percent, "level": level_for(used, limit),
        "used_mb": round(used / MB, 1) if used is not None else None, "limit_mb": round(limit / MB),
        "months_left": months_left(used, limit) if used is not None else None, "tables": biggest_tables(),
    }


def clean_temporary(user=None):
    """Remove expired sessions and failed sign-ins older than a day. Returns rows removed."""

    from django.contrib.sessions.models import Session

    from accounts.models import LoginFailure
    from audit.models import AuditEventType, AuditLog

    sessions, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    failures, _ = LoginFailure.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user if getattr(user, "is_authenticated", False) else None,
                            module="settings", action="clean_temporary_data", object_type="database", object_id="default",
                            after_data={"expired_sessions": sessions, "old_login_failures": failures})
    # PostgreSQL reuses the freed space itself (autovacuum); nothing more to run by hand.
    return sessions + failures
